"""Build dist/bicheck.exe with PyInstaller (one file, with the report page and the data tables).

    python packaging/build_exe.py [extra PyInstaller options, e.g. --paths <folder with pymupdf>]

Run it in an environment that has bicheck's dependencies and pyinstaller, and nothing else
large: PyInstaller packs whatever the imports reach. packaging/make_release.py then zips it.
"""
import glob
import os
import sys

import PyInstaller.__main__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
data = [("bicheck/report.html", "bicheck")]
data += [(p.replace(os.sep, "/"), "bicheck/data") for p in sorted(glob.glob("bicheck/data/*"))]
args = ["--onefile", "--noconfirm", "--name", "bicheck", "--workpath", "build", "--specpath", "build", "--paths", ROOT,
        "--hidden-import", "pymupdf", "--hidden-import", "PIL.PngImagePlugin",
        "--hidden-import", "PIL.JpegImagePlugin", "--collect-binaries", "pymupdf"]
for src, dest in data:
    args += ["--add-data", f"{os.path.join(ROOT, src)}{os.pathsep}{dest}"]
PyInstaller.__main__.run(args + sys.argv[1:] + ["packaging/launcher.py"])
print(f"dist/bicheck.exe: {os.path.getsize('dist/bicheck.exe') // 1_000_000} MB")
