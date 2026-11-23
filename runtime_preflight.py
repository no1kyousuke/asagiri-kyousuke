# -*- coding: utf-8 -*-
"""Windows release preflight. Does not read/write settings.json or cookies.txt."""
from __future__ import annotations
import importlib.metadata as md
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = {
    "pywebview": "6.2.1",
    "faster-whisper": "1.2.1",
    "ctranslate2": "4.9.0",
    "av": "13.1.0",
    "yt-dlp": "2026.10.26",
    "requests": "2.34.2",
    "qrcode": "8.2",
}

def add_nvidia_runtime_paths() -> tuple[list[str], list[object]]:
    """Expose pip-installed CUDA DLL directories before importing CTranslate2.

    CTranslate2's Windows loader uses PATH; add_dll_directory handles are also
    retained for libraries loaded through the standard Windows DLL search API.
    """
    if os.name != "nt":
        return [], []
    import site
    roots = [Path(sys.executable).resolve().parent]
    try:
        roots.extend(Path(x) for x in site.getsitepackages())
    except Exception:
        pass
    try:
        user_site = site.getusersitepackages()
        if user_site:
            roots.append(Path(user_site))
    except Exception:
        pass
    bins: list[str] = []
    handles: list[object] = []
    seen: set[str] = set()
    for root in roots:
        nvidia_root = root if root.name.lower() == "nvidia" else root / "nvidia"
        if not nvidia_root.is_dir():
            continue
        for bindir in nvidia_root.glob("*/bin"):
            if not bindir.is_dir():
                continue
            value = str(bindir.resolve())
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            bins.append(value)
            try:
                handles.append(os.add_dll_directory(value))
            except (AttributeError, OSError):
                pass
    if bins:
        existing = os.environ.get("PATH", "").split(os.pathsep)
        existing_keys = {x.casefold() for x in existing if x}
        os.environ["PATH"] = os.pathsep.join(
            [x for x in bins if x.casefold() not in existing_keys] + [x for x in existing if x]
        )
    return bins, handles


def check_webview2_runtime() -> tuple[bool, str]:
    """Detect Microsoft Edge WebView2 Runtime on Windows via official registry keys."""
    if os.name != "nt":
        return True, "non-windows"
    try:
        import winreg
        guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
        ]
        for hive, subkey in keys:
            try:
                with winreg.OpenKey(hive, subkey) as k:
                    pv, _ = winreg.QueryValueEx(k, "pv")
                    if pv and str(pv) != "0.0.0.0":
                        return True, str(pv)
            except OSError:
                continue
    except Exception as exc:
        return False, f"registry probe failed: {exc}"
    return False, "not installed"


def main() -> int:
    failures = []
    print("SubFlow Studio Windows build preflight")
    print("Python:", sys.version.split()[0], sys.executable)
    if sys.version_info[:2] != (3, 12):
        failures.append("必须使用 Python 3.12 x64。")
    if sys.maxsize <= 2**32:
        failures.append("必须使用 64 位 Python。")
    for package, expected in EXPECTED.items():
        try:
            actual = md.version(package)
            print(f"{package}: {actual}")
            if actual != expected:
                failures.append(f"{package} 版本应为 {expected}，当前为 {actual}。")
        except md.PackageNotFoundError:
            failures.append(f"缺少运行依赖 {package}=={expected}。")
    ffmpeg = ROOT / "bin" / "ffmpeg.exe"
    if not ffmpeg.is_file():
        print("WARNING: bin\\ffmpeg.exe missing; UI can still be packaged, media processing will be unavailable.")
    else:
        print("FFmpeg:", ffmpeg.stat().st_size, "bytes")
    cuda_bins, _dll_handles = add_nvidia_runtime_paths()
    if cuda_bins:
        print("NVIDIA CUDA DLL directories added to PATH:", len(cuda_bins))
    try:
        import ctranslate2
        devices = int(ctranslate2.get_cuda_device_count())
        compute = sorted(ctranslate2.get_supported_compute_types("cuda")) if devices else []
        print("CTranslate2 CUDA devices:", devices)
        print("CUDA compute types:", ", ".join(compute) if compute else "unavailable")
        if devices < 1:
            print("WARNING: CTranslate2 did not detect CUDA; the app will fall back to CPU until CUDA/cuDNN is configured.")
        if devices > 0 and "float16" not in compute:
            print("WARNING: CUDA is visible but float16 is unavailable; model loading may need a different compute type.")
    except Exception as exc:
        print(f"WARNING: CUDA runtime probe failed ({type(exc).__name__}: {exc}); app can still start in CPU fallback mode.")
    try:
        import faster_whisper  # noqa: F401
        print("faster-whisper import: OK")
    except Exception as exc:
        failures.append(f"faster-whisper 导入失败：{type(exc).__name__}: {exc}")
    try:
        import webview  # noqa: F401
        print("PyWebView import: OK")
    except Exception as exc:
        failures.append(f"PyWebView 导入失败：{type(exc).__name__}: {exc}")
    if os.name == "nt":
        try:
            import clr_loader  # noqa: F401
            import pythonnet  # noqa: F401
            print("pythonnet/clr_loader import: OK")
        except Exception as exc:
            failures.append(f"pythonnet/clr_loader 导入失败：{type(exc).__name__}: {exc}")
        wv2_ok, wv2_ver = check_webview2_runtime()
        print("WebView2 Runtime:", wv2_ver if wv2_ok else "MISSING")
        if not wv2_ok:
            print("WARNING: Edge WebView2 Runtime not found; install Evergreen WebView2 Runtime before launching the EXE.")
    if os.name == "nt":
        # CTranslate2's CUDA DLL packages must be collected into the frozen app.
        site_roots = []
        try:
            import site
            site_roots = [Path(x) for x in site.getsitepackages()]
            site_roots.append(Path(site.getusersitepackages()))
        except Exception:
            pass
        dlls = [p for root in site_roots if root.is_dir() for p in root.glob("nvidia/*/bin/*.dll")]
        names = {p.name.lower() for p in dlls}
        print("NVIDIA pip-wheel DLL files:", len(dlls))
        print("CUDA DLL sample:", ", ".join(sorted(names)[:12]))
        if not dlls:
            print("WARNING: no NVIDIA pip-wheel DLLs found; GPU inference may require CUDA/cuDNN installed separately.")
        if not shutil.which("nvidia-smi"):
            print("WARNING: nvidia-smi not found in PATH (not fatal if CTranslate2 CUDA check succeeded).")
    else:
        print("NOTE: CUDA DLL collection check is Windows-only.")
    if failures:
        print("\nPREFLIGHT FAILED:")
        for item in failures:
            print(" -", item)
        return 1
    print("\nPREFLIGHT PASSED")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
