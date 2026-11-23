# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules
import os

DEBUG_BUILD = os.environ.get("SUBFLOW_DEBUG_BUILD", "0") == "1"

ROOT = Path(SPECPATH).resolve()
datas = []
binaries = []
hiddenimports = []

# Collect runtime packages with dynamic imports/data files.
for package in (
    "webview", "requests", "yt_dlp", "faster_whisper", "ctranslate2", "av",
    "onnxruntime", "huggingface_hub", "tokenizers", "qrcode", "PIL", "certifi",
    "clr_loader", "pythonnet",
):
    try:
        d, b, h = collect_all(package)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:
        print(f"[WARN] optional collection skipped for {package}: {exc}")

for package in ("webview.platforms.edgechromium", "webview.platforms.winforms"):
    try:
        hiddenimports += collect_submodules(package)
    except Exception:
        hiddenimports.append(package)

# Include NVIDIA pip-wheel DLLs. They are deliberately bundled from the build
# interpreter actually used on the customer's RTX 3060 machine.
seen_dlls = set()
for entry in list(sys.path):
    root = Path(entry)
    nvidia = root / "nvidia"
    if not nvidia.is_dir():
        continue
    for dll in nvidia.rglob("*.dll"):
        try:
            rel = dll.relative_to(root)
        except ValueError:
            continue
        key = (str(dll.resolve()).lower(), str(rel.parent).replace('\\', '/').lower())
        if key in seen_dlls:
            continue
        seen_dlls.add(key)
        binaries.append((str(dll), str(rel.parent)))

# All front-end resources stay adjacent to the EXE; no web assets are fetched at runtime.
for name in ("index.html", "tailwind.css", "app.js", "bridge.js", "data.js"):
    source = ROOT / name
    if not source.is_file():
        raise SystemExit(f"Missing application resource: {source}")
    datas.append((str(source), "."))

icon = ROOT / "assets" / "subflow.ico"
if not icon.is_file():
    icon_arg = None
else:
    icon_arg = str(icon)

ffmpeg = ROOT / "bin" / "ffmpeg.exe"
if ffmpeg.is_file():
    binaries.append((str(ffmpeg), "bin"))
elif os.environ.get("SUBFLOW_REQUIRE_FFMPEG", "0") == "1":
    raise SystemExit("Missing bin\\ffmpeg.exe and SUBFLOW_REQUIRE_FFMPEG=1 was requested.")
else:
    print("[WARN] ffmpeg.exe missing: app will open, but media processing features will be unavailable until supplied.")

# ffprobe is optional but included when the original project has it.
ffprobe = ROOT / "bin" / "ffprobe.exe"
if ffprobe.is_file():
    binaries.append((str(ffprobe), "bin"))

# Never include settings.json, cookies.txt, workspace, cached models or logs.
a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=sorted(set(hiddenimports + ["core_pipeline", "clr", "pythonnet", "clr_loader"])),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "tensorflow", "matplotlib", "pytest", "notebook", "IPython"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SubFlow Studio",
    debug=DEBUG_BUILD,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=DEBUG_BUILD,
    disable_windowed_traceback=not DEBUG_BUILD,
    icon=icon_arg,
    contents_directory=".",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SubFlow Studio",
)
