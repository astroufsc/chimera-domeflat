from distutils.core import setup

setup(
    name='chimera_domeflat',
    version='0.0.1',
    packages=['chimera_domeflat', 'chimera_domeflat.instruments', 'chimera_domeflat.controllers'],
    scripts=['scripts/chimera-domeflat'],
    url='http://github.com/astroufsc/chimera-domeflat',
    license='GPL v2',
    author='Tiago Ribeiro',
    author_email='tribeiro@ufs.br',
    description='Auto Dome flat controller.'
)
