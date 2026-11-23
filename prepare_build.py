# -*- coding: utf-8 -*-
"""Prepare optional media binaries for a Windows build without touching the source project."""
from __future__ import annotations
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def candidates() -> list[Path]:
    values = [
        ROOT / "bin" / "ffmpeg.exe",
        Path.home() / "subflow-studio" / "bin" / "ffmpeg.exe",
    ]
    custom_dir = os.environ.get("SUBFLOW_FFMPEG_DIR", "").strip()
    if custom_dir:
        values.insert(1, Path(custom_dir) / "ffmpeg.exe")
    path_hit = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if path_hit:
        values.append(Path(path_hit))
    # Common package-manager locations; PATH lookup above remains preferred.
    values.extend([
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ffmpeg" / "bin" / "ffmpeg.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
    ])
    seen, out = set(), []
    for p in values:
        key = str(p).casefold()
        if key not in seen:
            seen.add(key); out.append(p)
    return out

def valid_exe(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 100_000:
        return False
    try:
        r = subprocess.run([str(path), "-version"], capture_output=True, text=True, timeout=8,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and "ffmpeg version" in ((r.stdout or "") + (r.stderr or "")).lower()
    except Exception:
        return False

def main() -> int:
    target_dir = ROOT / "bin"
    target_dir.mkdir(exist_ok=True)
    target = target_dir / "ffmpeg.exe"
    if valid_exe(target):
        print(f"[OK] FFmpeg ready: {target}")
    else:
        found = next((p for p in candidates() if p.resolve() != target.resolve() and valid_exe(p)), None)
        if found:
            shutil.copy2(found, target)
            print(f"[OK] FFmpeg copied from: {found}")
        else:
            print("[WARN] FFmpeg not found. The desktop UI can still be built and opened, but download/audio extraction/video hard-subtitle features will be unavailable until ffmpeg.exe is supplied.")
    ffprobe_target = target_dir / "ffprobe.exe"
    if not ffprobe_target.is_file():
        ffprobe_candidates = [
            target_dir / "ffprobe.exe",
        ]
        custom_dir = os.environ.get("SUBFLOW_FFMPEG_DIR", "").strip()
        if custom_dir:
            ffprobe_candidates.append(Path(custom_dir) / "ffprobe.exe")
        path_hit = shutil.which("ffprobe.exe") or shutil.which("ffprobe")
        if path_hit:
            ffprobe_candidates.append(Path(path_hit))
        found = next((p for p in ffprobe_candidates if p.is_file() and p.resolve() != ffprobe_target.resolve()), None)
        if found:
            shutil.copy2(found, ffprobe_target)
            print(f"[OK] FFprobe copied from: {found}")
        else:
            print("[INFO] FFprobe not found; duration parsing will use FFmpeg fallback.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
