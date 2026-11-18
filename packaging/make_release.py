"""Zip the Windows build (dist/bicheck.exe, made with PyInstaller) and the source into release/."""
import glob
import os
import zipfile

from bicheck import __version__ as V

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.makedirs("release", exist_ok=True)
with zipfile.ZipFile(f"release/bicheck-{V}-windows.zip", "w", zipfile.ZIP_DEFLATED) as z:
    z.write("dist/bicheck.exe", "bicheck/bicheck.exe")
    z.write("packaging/HOW-TO-USE.txt", "bicheck/HOW-TO-USE.txt")
files = ["README.md", "pyproject.toml", "packaging/HOW-TO-USE.txt", "packaging/launcher.py", "packaging/make_release.py"]
files += [p for p in glob.glob("bicheck/**/*", recursive=True) if os.path.isfile(p) and "__pycache__" not in p]
files += glob.glob("tests/*.py")
with zipfile.ZipFile(f"release/bicheck-{V}-source.zip", "w", zipfile.ZIP_DEFLATED) as z:
    for f in files:
        z.write(f, f"bicheck-{V}/" + f.replace(os.sep, "/"))
for f in sorted(os.listdir("release")):
    print(f, os.path.getsize(os.path.join("release", f)) // 1024, "KB")
