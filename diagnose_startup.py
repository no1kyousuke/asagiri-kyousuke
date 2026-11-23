# -*- coding: utf-8 -*-
"""Read-only startup diagnostics for SubFlow Studio. Does not read secrets."""
from __future__ import annotations
import importlib.metadata as md
import importlib.util
import os
import platform
import shutil
import struct
import sys
import traceback
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

def out(label: str, value: object) -> None:
    print(f"[{label}] {value}", flush=True)

def main() -> int:
    problems = []
    warnings = []
    out("SYSTEM", platform.platform())
    out("PYTHON", sys.version.replace("\n", " "))
    out("PYTHON_EXE", sys.executable)
    out("BITS", struct.calcsize("P") * 8)
    out("PROJECT", ROOT)
    out("FROZEN", bool(getattr(sys, "frozen", False)))

    if os.name != "nt":
        out("WARN", "当前不是 Windows；只能进行静态依赖检查")
    if sys.version_info[:2] != (3, 12):
        problems.append("需要 Python 3.12")
    if struct.calcsize("P") * 8 != 64:
        problems.append("需要 64 位 Python")

    for package, expected in EXPECTED.items():
        try:
            actual = md.version(package)
            out("PACKAGE", f"{package} {actual} (expected {expected})")
            if actual != expected:
                warnings.append(f"{package} 建议版本为 {expected}，当前为 {actual}")
        except md.PackageNotFoundError:
            message = f"缺少 Python 依赖：{package}=={expected}"
            (problems if package in ("pywebview", "requests") else warnings).append(message)
            out("MISSING", package)

    for rel in ("app.py", "core_pipeline.py", "index.html", "app.js", "bridge.js", "data.js", "tailwind.css"):
        out("FILE", f"{rel}: {'OK' if (ROOT / rel).is_file() else 'MISSING'}")
        if not (ROOT / rel).is_file():
            problems.append(f"缺少文件：{rel}")

    ffmpeg_candidates = [ROOT / "bin" / "ffmpeg.exe"]
    path_ff = shutil.which("ffmpeg.exe") or shutil.which("ffmpeg")
    if path_ff:
        ffmpeg_candidates.append(Path(path_ff))
    ffmpeg = next((p for p in ffmpeg_candidates if p.is_file()), None)
    out("FFMPEG", ffmpeg if ffmpeg else "MISSING")
    if not ffmpeg:
        warnings.append("找不到 ffmpeg.exe；主窗口可能仍能打开，但媒体提取、下载和硬压功能不可用")

    for mod in ("requests", "webview", "clr_loader", "pythonnet", "ctranslate2", "faster_whisper", "av"):
        try:
            spec = importlib.util.find_spec(mod)
            out("MODULE", f"{mod}: {'FOUND' if spec else 'MISSING'}")
            if not spec:
                message = f"模块不可导入：{mod}"
                (problems if mod in ("requests", "webview") else warnings).append(message)
        except Exception as exc:
            out("MODULE_ERROR", f"{mod}: {type(exc).__name__}: {exc}")
            problems.append(f"检查模块 {mod} 失败：{exc}")

    if os.name == "nt":
        try:
            from runtime_preflight import add_nvidia_runtime_paths, check_webview2_runtime
            dirs, _handles = add_nvidia_runtime_paths()
            out("CUDA_DLL_DIRS", len(dirs))
            for d in dirs:
                out("CUDA_DLL", d)
            if not dirs:
                warnings.append("未发现 NVIDIA pip-wheel DLL 目录；GPU 识别可能不可用，但不一定阻止窗口启动")
            wv2_ok, wv2_ver = check_webview2_runtime()
            out("WEBVIEW2", wv2_ver if wv2_ok else "MISSING")
            if not wv2_ok:
                problems.append("未检测到 Microsoft Edge WebView2 Runtime，请安装 Evergreen WebView2 Runtime")
        except Exception:
            out("CUDA_PATH_ERROR", traceback.format_exc())
            problems.append("CUDA DLL 路径初始化失败")

    # Import checks intentionally happen after CUDA path setup and mirror app.py order.
    for name in ("requests", "webview", "core_pipeline"):
        try:
            __import__(name)
            out("IMPORT", f"{name}: OK")
        except Exception:
            detail = traceback.format_exc()
            out("IMPORT_FAILED", f"{name}\n{detail}")
            problems.append(f"导入 {name} 失败；请查看输出中的 traceback")
            break

    for item in warnings:
        out("WARNING", item)
    if problems:
        out("RESULT", "BLOCKED")
        for item in problems:
            out("ACTION", item)
        return 1
    out("RESULT", "BASIC STARTUP CHECKS PASSED; actual WebView2 window / GPU / FFmpeg execution still needs Windows runtime testing")
    return 0

def _run_launch() -> int:
    import subprocess
    from build_release import copy_to_stable_dir, is_zip_or_temp_path

    if is_zip_or_temp_path(ROOT):
        stable = copy_to_stable_dir(ROOT)
        print(f"[INFO] ZIP/Temp launch detected; relocated to {stable}", flush=True)
        return subprocess.call([sys.executable, str(stable / "diagnose_startup.py"), "--launch"], cwd=str(stable), shell=False)

    log_path = ROOT / "startup_diagnostic.log"
    rc = main()
    print(f"\n[INFO] Launching app.py (diagnostic log: {log_path})", flush=True)
    if rc != 0:
        return rc
    with log_path.open("w", encoding="utf-8") as fh:
        fh.write("===== SubFlow Studio Startup Diagnostic Launch =====\n")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "app.py")],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            fh.write(line)
            fh.flush()
        return proc.wait()


if __name__ == "__main__":
    if "--launch" in sys.argv:
        raise SystemExit(_run_launch())
    raise SystemExit(main())
