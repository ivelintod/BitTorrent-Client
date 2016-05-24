import os
from setuptools import setup

# Utility function to read the README file.
# Used for the long_description.  It's nice, because now 1) we have a top level
# README file and 2) it's easier to type in the README file than to put a raw
# string in below ...
def read(fname):
    return open(os.path.join(os.path.dirname(__file__), fname)).read()

setup(
    name="an_example_pypi_project",
    version="0.0.1",
    author="Ivelin Todorov",
    author_email="ivelin_b@mail.bg",
    description=("BitTorrent Client in pure Python"),
    license="MIT",
    keywords="python bittorrent client bencode peers download upload",
    url="http://github.com/ivelintod/BitTorrent-Client",
    packages=['src', 'tests'],
    long_description=read('README.md'),
    classifiers=[
        "Development Status :: 1 - Alpha",
        "Topic :: Utilities",
        "License :: OSI Approved :: MIT License",
    ],
)
