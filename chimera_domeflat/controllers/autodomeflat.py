from __future__ import division
import copy
import json
import ntpath
import os
import re
import threading
import time
from datetime import timedelta
import numpy as np
from astropy.io import fits
from chimera.controllers.imageserver.imagerequest import ImageRequest
from chimera.controllers.imageserver.util import getImageServer
from chimera.core.event import event
from chimera.core.exceptions import ChimeraException, ProgramExecutionAborted
from chimera.interfaces.camera import Shutter
from chimera.interfaces.telescope import TelescopePier
from chimera.util.coord import Coord
from chimera.util.image import ImageUtil
from chimera.util.position import Position

from chimera.core import SYSTEM_CONFIG_DIRECTORY
from chimera.interfaces.telescope import TelescopePierSide
from chimera.interfaces.autoflat import IAutoFlat
from chimera.core.chimeraobject import ChimeraObject

class AutoDomeFlat(ChimeraObject, IAutoFlat):

    __config__ = {"flat_alt": 89,                       # Domeflat telescope position - Altitude. (degrees)
                  "flat_az": 78,                        # Domeflat telescope position - Azimuth. (degrees)
                  "dome_az": 90,                        # Domeflat dome position - Azimuth. (degrees)
                  "pier_side": None,                    # Pier Side to take Skyflat
                  "exptime_increment": 0.2,             # Exposure time increment on integration. (seconds)
                  "exptime_max": 300,                   # Maximum exposure time. (seconds)
                  "idealCounts": 25000,                 # Ideal flat CCD counts.
                  "config_file": "%s/domeflats.json" % SYSTEM_CONFIG_DIRECTORY
   }

    def __init__(self):
        ChimeraObject.__init__(self)

        self._abort = threading.Event()
        self._abort.clear()

    def _getSite(self):
        return self.getManager().getProxy(self["site"])

    def _getTel(self):
        return self.getManager().getProxy(self["telescope"])

    def _getDome(self):
        return self.getManager().getProxy(self["dome"])

    def _getCam(self):
        return self.getManager().getProxy(self["camera"])

    def _getFilterWheel(self):
        return self.getManager().getProxy(self["filterwheel"])

    def _getLamp(self,lamp):
        return self.getManager().getProxy(str(lamp))

    def _takeImage(self, exptime, filter, download=False):

        cam = self._getCam()
        if self["filterwheel"] is not None:
            fw = self._getFilterWheel()
            fw.setFilter(filter)
        self.log.debug("Start frame")
        request = ImageRequest(exptime=exptime, frames=1, shutter=Shutter.OPEN,
                               filename=os.path.basename(ImageUtil.makeFilename("domeflat-$DATE-$TIME")),
                               type='FLAT')
        self.log.debug('ImageRequest: {}'.format(request))
        frames = cam.expose(request)
        self.log.debug("End frame")

        # checking for aborting signal
        if self._abort.isSet():
            self.log.warning('Aborting exposure!')
            raise ProgramExecutionAborted()

        if frames:
            image = frames[0]
            image_path = image.filename()
            if download and not os.path.exists(image_path):  # If image is on a remote server, donwload it.

                #  If remote is windows, image_path will be c:\...\image.fits, so use ntpath instead of os.path.
                if ':\\' in image_path:
                    modpath = ntpath
                else:
                    modpath = os.path
                image_path = ImageUtil.makeFilename(os.path.join(getImageServer(self.getManager()).defaultNightDir(),
                                                                 modpath.basename(image_path)))
                t0 = time.time()
                self.log.debug('Downloading image from server to %s' % image_path)
                if not ImageUtil.download(image, image_path):
                    raise ChimeraException('Error downloading image %s from %s' % (image_path, image.http()))
                self.log.debug('Finished download. Took %3.2f seconds' % (time.time() - t0))
            return image_path, image
        else:
            raise Exception("Could not take an image")

    def _moveScope(self, pierSide=None):
        """
        Moves the scope, usually to zenith
        """
        tel = self._getTel()

        if pierSide is not None and tel.features(TelescopePier):
            self.log.debug("Setting telescope pier side to %s." % tel.getPierSide().__str__().lower())
            tel.setSideOfPier(self['pier_side'])
        else:
            self.log.warning("Telescope does not support pier side.")

        try:
            self.log.debug("Slewing scope to alt {} az {}".format(self["flat_alt"], self["flat_az"]))

            tel.slewToAltAz(Position.fromAltAz(Coord.fromD(self["flat_alt"]),
                                               Coord.fromD(self["flat_az"])))
            if tel.isTracking():
                tel.stopTracking()

        except:
            self.log.debug("Error moving the telescope")

    def _moveDome(self):

        dome = self._getDome()

        # from chimera.util.coord import Coord
        target = Coord.fromD(self["dome_az"])

        dome.stand()

        dome.slewToAz(target)

    def _switchLampOn(self,lamp):
        l = self._getLamp(lamp)

        l.switchOn()

    def _switchLampOff(self,lamp):
        l = self._getLamp(lamp)

        l.switchOff()

    def getFlats(self, filter_id, n_flats=None):
        """
        Take flats on filter_id filter.

        :param filter_id: Filter name to take the Flats
        :param n_flats: Number of flats to take. None for maximum on the sun interval.
        """

        # Read fresh coefficients from file.
        lamp, exptime = self.readConfigFile(self['config_file'])[filter_id]

        self._abort.clear()

        self._moveDome()

        self._moveScope(pierSide=self['pier_side'])

        self._switchLampOn(lamp)

        for i_flat in range(n_flats):


            filename, image = self._takeImage(exptime=exptime, filter=filter_id, download=True)

            flat_level = self.getFlatLevel(filename, image)

            self.exposeComplete(filter_id, i_flat, exptime, flat_level)
            self.log.debug('Done taking image, average counts = %f. '% flat_level)

            # checking for aborting signal
            if self._abort.isSet():
                self.log.warning('Aborting!')
                break

        self.log.debug("Done taking flats. Switching lamp off.")
        self._switchLampOff(lamp)

    def abort(self):
        self._abort.set()
        cam = copy.copy(self._getCam())
        cam.abortExposure()

    def getFlatLevel(self, filename, image):
        """
        Returns average counts from image
        """

        frame = fits.getdata(filename)
        img_mean = np.mean(frame)
        return img_mean

    @staticmethod
    def readConfigFile(filename):
        with open(filename) as f:
            config = json.loads(re.sub('#(.*)', '', f.read()))
        return config
