from distutils.core import setup

setup(name='pylgbst',
      description='Python library to interact with LEGO Move Hub (from Lego BOOST set)',
      version='0.6',
      author='Andrey Pokhilko',
      author_email='apc4@ya.ru',
      packages=['pylgbst'],
      requires=[],
      extras_require={
          'gatt': ["gatt"],
          'gattlib': ["gattlib"],
          'pygatt': ["pygatt"],
      }
      )
