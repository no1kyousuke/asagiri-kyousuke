# -*- coding: utf-8 -*-
"""Select a known-good project venv or create an isolated release-build venv.

Only the dedicated .build_venv directory is created/modified. The user's global
Python installation and original project environment are never modified.
"""
from __future__ import annotations
import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = {
    "pywebview": "6.2.1", "pythonnet": None, "faster-whisper": "1.2.1",
    "ctranslate2": "4.9.0", "av": "13.1.0", "yt-dlp": "2026.10.26",
    "requests": "2.34.2", "qrcode": "8.2", "pyinstaller": None,
}

def run(exe: Path, args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([str(exe), *args], cwd=ROOT, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None,
                          check=False)

def candidate_is_ready(exe: Path) -> bool:
    if not exe.is_file():
        return False
    code = (
        "import importlib.metadata as m,sys; "
        "want={'pywebview':'6.2.1','faster-whisper':'1.2.1','ctranslate2':'4.9.0',"
        "'av':'13.1.0','yt-dlp':'2026.10.26','requests':'2.34.2','qrcode':'8.2','PyInstaller':None,'pythonnet':None}; "
        "ok=True; "
        "\nfor k,v in want.items():\n"
        " try: ok = ok and (v is None or m.version(k)==v)\n"
        " except m.PackageNotFoundError: ok=False\n"
        "sys.exit(0 if ok else 1)"
    )
    r = run(exe, ["-c", code])
    if r.returncode:
        return False
    # Require the CUDA 12 wheel distributions so the frozen app can bundle their DLLs.
    cuda_code = (
        "import importlib.metadata as m,sys; "
        "names=('nvidia-cublas-cu12','nvidia-cudnn-cu12'); "
        "sys.exit(0 if all((lambda n: (m.version(n) is not None))(x) for x in names) else 1)"
    )
    return run(exe, ["-c", cuda_code]).returncode == 0

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-python", required=True)
    args = parser.parse_args()
    base = Path(args.base_python).resolve()
    if not base.is_file():
        print(f"[ERROR] Base Python not found: {base}", file=sys.stderr); return 2
    candidates = [
        ROOT / ".build_venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate_is_ready(candidate):
            print(f"[INFO] Reusing validated original project environment: {candidate}", file=sys.stderr)
            print(str(candidate)); return 0

    venv_dir = ROOT / ".build_venv"
    python = venv_dir / "Scripts" / "python.exe"
    if not python.is_file():
        print("[INFO] Creating isolated .build_venv (global Python will not be modified).", file=sys.stderr)
        try:
            # Do not run network upgrades during venv creation. Keep the venv path
            # under the stable staging directory, then install pinned packages below.
            venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(venv_dir)
        except Exception as exc:
            import traceback
            print(f"[ERROR] Could not create build venv at {venv_dir}: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
            return 10
    print("[INFO] Installing pinned Windows runtime, CUDA 12 DLLs and PyInstaller. This may take several minutes and download large packages.", file=sys.stderr)
    cmd = [str(python), "-m", "pip", "install", "--upgrade", "pip"]
    r = subprocess.run(cmd, cwd=ROOT, check=False, stdout=sys.stderr)
    if r.returncode:
        print("[ERROR] pip bootstrap failed.", file=sys.stderr); return r.returncode
    cmd = [str(python), "-m", "pip", "install", "-r", "requirements-windows.txt", "-r", "requirements-gpu-cu12.txt", "-r", "requirements-build.txt"]
    r = subprocess.run(cmd, cwd=ROOT, check=False, stdout=sys.stderr)
    if r.returncode:
        print("[ERROR] Dependency installation failed. See pip output above.", file=sys.stderr); return r.returncode
    print(str(python))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
