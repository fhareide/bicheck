"""Entry point for the frozen app (PyInstaller): double-click to be asked for the files."""
import sys

from bicheck.cli import main

sys.exit(main())
