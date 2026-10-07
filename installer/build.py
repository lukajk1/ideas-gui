"""Builds the Ideas installer: bundles the app with PyInstaller, then compiles ideas.iss with Inno Setup.

    python installer/build.py            # -> installer/output/IdeasSetup-<version>.exe
    python installer/build.py --no-setup # bundle only (installer/publish/Ideas.exe)

The version comes from __version__ in ideas_gui/__init__.py. Inno Setup 6 is found in Program Files, on PATH,
or at the ISCC environment variable.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PUBLISH = os.path.join(HERE, "publish")
WORK = os.path.join(HERE, "build")
ISS = os.path.join(HERE, "ideas.iss")


def version():
    with open(os.path.join(ROOT, "ideas_gui", "__init__.py"), encoding="utf-8") as f:
        match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", f.read())
    if not match:
        sys.exit("No __version__ in ideas_gui/__init__.py.")
    return match.group(1)


def find_iscc():
    candidates = [
        os.environ.get("ISCC"),
        shutil.which("ISCC"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Inno Setup 6", "ISCC.exe"),
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "Inno Setup 6", "ISCC.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    sys.exit("Inno Setup 6 (ISCC.exe) not found. Install it from https://jrsoftware.org/isdl.php or set ISCC.")


def run(args):
    print(">", " ".join(f'"{a}"' if " " in a else a for a in args), flush=True)
    if subprocess.run(args, cwd=ROOT).returncode != 0:
        sys.exit(f"Failed: {args[0]}")


def main():
    v = version()
    print(f"Ideas {v}")
    for module in ("PyInstaller", "webview", "pystray", "PIL"):
        try:
            __import__(module)
        except ImportError:
            sys.exit(f"Missing {module}. Run: pip install -r requirements.txt pyinstaller")

    # A clean publish folder, so files from older builds don't end up in the installer.
    shutil.rmtree(PUBLISH, ignore_errors=True)
    run([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", "Ideas",
        "--icon", os.path.join(ROOT, "assets", "icon.ico"),
        "--add-data", f"{os.path.join(ROOT, 'ideas_gui', 'ui')}{os.pathsep}ideas_gui/ui",
        "--hidden-import", "pystray._win32",
        "--exclude-module", "tkinter",  # only used by --browser mode when run from source
        # Optional imports of pywebview/Pillow the app never uses (~40 MB).
        "--exclude-module", "numpy",
        "--exclude-module", "cryptography",
        "--exclude-module", "yaml",
        "--distpath", HERE,
        "--workpath", WORK,
        "--specpath", WORK,
        os.path.join(ROOT, "main.pyw"),
    ])
    os.replace(os.path.join(HERE, "Ideas"), PUBLISH)
    if not os.path.isfile(os.path.join(PUBLISH, "Ideas.exe")):
        sys.exit("PyInstaller produced no Ideas.exe.")
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, files in os.walk(PUBLISH) for f in files)
    print(f"Bundled to {PUBLISH} ({size / 1e6:.0f} MB)")

    if "--no-setup" in sys.argv:
        return
    run([find_iscc(), f"/DAppVersion={v}", f"/DPublishDir={PUBLISH}", ISS])
    print(f"\nInstaller: {os.path.join(HERE, 'output', f'IdeasSetup-{v}.exe')}")


if __name__ == "__main__":
    main()
