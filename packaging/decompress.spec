# PyInstaller spec for Decompress.exe  (build with:  pyinstaller packaging/decompress.spec)
# Produces a one-folder app in dist/Decompress/ — faster to start and more reliable with OpenCV/MediaPipe
# than a single-file exe.
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))

datas = [
    (str(ROOT / "decompress/web/static"), "decompress/web/static"),
    (str(ROOT / "hub/static"), "hub/static"),
    (str(ROOT / "decompress/blender/import_motion.py"), "decompress/blender"),
]
binaries = []
hiddenimports = ["hub", "hub.server", "hub.hub_client"] + collect_submodules("decompress") \
    + collect_submodules("uvicorn") + collect_submodules("python_multipart") \
    + collect_submodules("keyring.backends")

for optional in ("mediapipe", "ultralytics"):          # bundle the vision backends that are installed
    if importlib.util.find_spec(optional):
        d, b, h = collect_all(optional)
        datas += d; binaries += b; hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging/entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["progress_hub", "tkinter", "pytest", "IPython", "notebook"],   # hub is bundled as `hub`
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = ROOT / "packaging/decompress.ico"
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Decompress",
    console=True,                      # the window shows the URLs; closing it stops the app
    icon=str(icon) if icon.exists() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Decompress")
