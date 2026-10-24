"""Build hook: ship the existing installer engine, not a second implementation."""
from pathlib import Path
import sys
from setuptools import setup
from setuptools.command.build_py import build_py

class BuildWithInstaller(build_py):
    def run(self):
        super().run()
        root=Path(__file__).resolve().parent
        sys.path.insert(0,str(root))
        from installer.build.context import write
        write(root,Path(self.build_lib))

setup(cmdclass={'build_py':BuildWithInstaller})
