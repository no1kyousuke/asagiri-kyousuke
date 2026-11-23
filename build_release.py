# -*- coding: utf-8 -*-
"""SubFlow Studio Windows release build controller.

Replaces fragile CMD path parsing with pathlib + subprocess argv arrays.
Safe to invoke from normal directories, ZIP preview Temp directories, or paths
containing Chinese characters, spaces, parentheses, or percent signs.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

REQUIRED = [
    "app.py",
    "core_pipeline.py",
    "app.js",
    "bridge.js",
    "data.js",
    "index.html",
    "tailwind.css",
    "SubFlowStudio.spec",
    "requirements-build.txt",
    "requirements-gpu-cu12.txt",
    "requirements-windows.txt",
    "verify_release.py",
    "runtime_preflight.py",
    "prepare_build.py",
    "diagnose_startup.py",
    "build_release.py",
]

EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    ".build_venv",
    "build",
    "dist",
    "dist_cpu",
    "dist_gpu_pack",
    "installer",
    "workspace",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
}

EXCLUDE_FILES = {
    "build_windows.log",
    "startup_diagnostic.log",
    "app.log",
    "selftest.log",
    "settings.json",
    "cookies.txt",
}

PINNED_RUNTIME = {
    "pywebview": "6.2.1",
    "faster-whisper": "1.2.1",
    "ctranslate2": "4.9.0",
    "av": "13.1.0",
    "yt-dlp": "2026.10.26",
    "requests": "2.34.2",
    "qrcode": "8.2",
}


def is_zip_or_temp_path(path: Path) -> bool:
    """Return True when launched from Windows Explorer ZIP preview or Temp."""
    normalized = str(path).replace("/", "\\").casefold()
    parts = [p.casefold() for p in path.parts]
    if "\\temp\\" in normalized or "\\appdata\\local\\temp\\" in normalized:
        return True
    for part in parts:
        if ".zip." in part or part.endswith(".zip"):
            return True
        if part in {"zip", "zip.blf", "zip.bf6"}:
            return True
        if part.startswith(("7zo", "rar$", "wz")):
            return True
    return False


def has_venv_unsafe_chars(path: Path) -> bool:
    """Detect characters that break pip/PyInstaller/venv activation shims on Windows."""
    s = str(path)
    if any(ch in s for ch in (" ", "%", "(", ")", "&", "^", "!", ";")):
        return True
    try:
        s.encode("ascii")
    except UnicodeEncodeError:
        return True
    return False


def get_stable_dir() -> Path:
    custom = os.environ.get("SUBFLOW_STABLE_BUILD_DIR", "").strip()
    if custom:
        return Path(custom).resolve()
    drive = os.environ.get("SystemDrive", "C:").rstrip("\\/")
    return Path(f"{drive}\\SubFlowStudioBuild\\SubFlowStudio_Windows_Release")


def copy_to_stable_dir(source: Path, stable: Path | None = None) -> Path:
    """Stage source tree into a fixed ASCII path without secrets or caches."""
    target = stable or get_stable_dir()
    if source.resolve() == target.resolve():
        return source

    target.parent.mkdir(parents=True, exist_ok=True)

    def ignore(directory: str, names: list[str]) -> set[str]:
        ignored: set[str] = set()
        for name in names:
            low = name.casefold()
            if name in EXCLUDE_DIRS or low in {d.casefold() for d in EXCLUDE_DIRS}:
                ignored.add(name)
            elif name in EXCLUDE_FILES or low in {f.casefold() for f in EXCLUDE_FILES}:
                ignored.add(name)
            elif low.startswith("_preview_") or Path(name).suffix.lower() in {".pyc", ".pyo", ".log"}:
                ignored.add(name)
        return ignored

    shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignore)
    # Never leave stale secrets in staged build directory.
    for secret in (target / "settings.json", target / "bin" / "cookies.txt"):
        if secret.exists():
            secret.unlink()
    for name in REQUIRED:
        if not (target / name).is_file():
            raise RuntimeError(f"Staging copy incomplete; missing {name} in {target}")
    return target


def log_line(log, message: str) -> None:
    print(message, flush=True)
    log.write(message + "\n")
    log.flush()


def run(
    cmd: list[str],
    cwd: Path,
    log,
    env: dict[str, str] | None = None,
    label: str | None = None,
) -> None:
    if label:
        log_line(log, f"\n===== {label} =====")
    log_line(log, "$ " + subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        shell=False,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        print(line, end="", flush=True)
        log.write(line)
        log.flush()
    code = proc.wait()
    if code != 0:
        raise subprocess.CalledProcessError(code, cmd)


def venv_dependencies_ready(build_python: Path, cwd: Path) -> bool:
    """Return True if .build_venv already has all pinned packages, PyInstaller, and CUDA DLLs."""
    if not build_python.is_file():
        return False
    check_code = (
        "import importlib.metadata as m, site, sys; "
        "from pathlib import Path; "
        f"want = {PINNED_RUNTIME!r}; "
        "ok = True\n"
        "for k, v in want.items():\n"
        "    try:\n"
        "        ok = ok and (m.version(k) == v)\n"
        "    except m.PackageNotFoundError:\n"
        "        ok = False\n"
        "for extra in ('PyInstaller', 'pythonnet', 'nvidia-cublas-cu12', 'nvidia-cudnn-cu12'):\n"
        "    try:\n"
        "        m.version(extra)\n"
        "    except m.PackageNotFoundError:\n"
        "        ok = False\n"
        "roots = [Path(x) for x in site.getsitepackages()]\n"
        "dlls = [p for r in roots if r.is_dir() for p in r.glob('nvidia/*/bin/*.dll')]\n"
        "ok = ok and len(dlls) > 0\n"
        "raise SystemExit(0 if ok else 1)\n"
    )
    proc = subprocess.run(
        [str(build_python), "-c", check_code],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return proc.returncode == 0


def find_inno_setup() -> Path | None:
    hit = shutil.which("ISCC.exe") or shutil.which("ISCC")
    if hit:
        return Path(hit)
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        cand = Path(local_app) / "Programs" / "Inno Setup 6" / "ISCC.exe"
        if cand.is_file():
            return cand
    for env_key in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env_key, "")
        if not base:
            continue
        cand = Path(base) / "Inno Setup 6" / "ISCC.exe"
        if cand.is_file():
            return cand
    return None


def verify_dist_artifacts(dist_dir: Path, log) -> None:
    """Inspect PyInstaller output for required files, DLLs, and absence of secrets."""
    log_line(log, "\n===== Verify PyInstaller output artifacts =====")
    exe = dist_dir / "SubFlow Studio.exe"
    if not exe.is_file():
        raise RuntimeError(f"Missing packaged EXE: {exe}")

    for asset in ("index.html", "tailwind.css", "app.js", "bridge.js", "data.js"):
        if not (dist_dir / asset).is_file():
            raise RuntimeError(f"Missing packaged UI asset: {dist_dir / asset}")

    for secret in (dist_dir / "settings.json", dist_dir / "bin" / "cookies.txt"):
        if secret.exists():
            raise RuntimeError(f"Secret file leaked into dist directory: {secret}")

    ffmpeg = dist_dir / "bin" / "ffmpeg.exe"
    if ffmpeg.is_file():
        log_line(log, f"[OK] Bundled FFmpeg: {ffmpeg} ({ffmpeg.stat().st_size:,} bytes)")
    else:
        log_line(log, "[WARN] dist\\bin\\ffmpeg.exe missing; media features will be limited.")

    py_runtime = list(dist_dir.rglob("Python.Runtime.dll"))
    log_line(log, f"[INFO] Python.Runtime.dll matches: {len(py_runtime)}")
    if not py_runtime:
        raise RuntimeError("Missing Python.Runtime.dll in dist; PyWebView EdgeChromium/WinForms backend will fail.")

    cuda_dlls = sorted({p.name.lower() for p in dist_dir.rglob("*.dll") if "cublas" in p.name.lower() or "cudnn" in p.name.lower()})
    log_line(log, f"[INFO] Bundled CUDA DLLs ({len(cuda_dlls)}): {', '.join(cuda_dlls[:12])}")
    if not cuda_dlls:
        log_line(log, "[WARN] No cublas/cudnn DLLs found in dist; GPU ASR may fall back to CPU.")

    silero = list(dist_dir.rglob("silero_vad*.onnx"))
    log_line(log, f"[INFO] Bundled Silero VAD ONNX models: {len(silero)}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SubFlow Studio Windows Release Build Controller")
    parser.add_argument("--staged", action="store_true", help="Internal flag indicating relocation already happened.")
    parser.add_argument("--debug", action="store_true", help="Build diagnostic console EXE (SUBFLOW_DEBUG_BUILD=1).")
    parser.add_argument("--reinstall", action="store_true", help="Force reinstall of dependencies inside .build_venv.")
    parser.add_argument("--skip-pyinstaller", action="store_true", help="Run env setup and tests without PyInstaller.")
    parser.add_argument("--skip-installer", action="store_true", help="Skip Inno Setup compilation.")
    parser.add_argument("--require-ffmpeg", action="store_true", help="Fail build if bin/ffmpeg.exe is missing.")
    parser.add_argument("--no-pause", action="store_true", help="Accepted for launcher compatibility.")
    parser.add_argument("--pip-index-url", default="", help="Optional custom PyPI index URL for .build_venv pip.")
    return parser.parse_args(argv)


def _fast_link_or_copy_tree(src: Path, dst: Path, ignore_top_names: set[str] | None = None) -> None:
    """Fast directory clone using hardlinks where possible, falling back to copy2."""
    ignore_low = {n.casefold() for n in (ignore_top_names or set())}
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.rglob("*"):
        rel = item.relative_to(src)
        if rel.parts and rel.parts[0].casefold() in ignore_low:
            continue
        target = dst / rel
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(item, target)
            except OSError:
                shutil.copy2(item, target)


def split_cpu_and_gpu_packages(source: Path, dist_dir: Path, log) -> None:
    """Create <400MB CPU lightweight main package (dist_cpu) + standalone GPU pack (dist_gpu_pack)."""
    log_line(log, "\n===== Split CPU main package (<400MB) and GPU acceleration pack =====")
    quickstart_doc = source / "3分钟快速上手与常见问题.md"
    if quickstart_doc.is_file():
        shutil.copy2(quickstart_doc, dist_dir / quickstart_doc.name)

    cpu_dir = source / "dist_cpu" / "SubFlow Studio"
    _fast_link_or_copy_tree(dist_dir, cpu_dir, ignore_top_names={"nvidia"})
    cpu_bytes = sum(p.stat().st_size for p in cpu_dir.rglob("*") if p.is_file())
    cpu_mb = round(cpu_bytes / (1024 * 1024), 1)
    log_line(log, f"[OK] Lightweight CPU main package: {cpu_dir} ({cpu_mb} MB)")
    if cpu_mb >= 400.0:
        raise RuntimeError(f"CPU main package exceeded 400 MB limit: {cpu_mb} MB")

    gpu_pack_dir = source / "dist_gpu_pack"
    nvidia_src = dist_dir / "nvidia"
    if nvidia_src.is_dir():
        _fast_link_or_copy_tree(nvidia_src, gpu_pack_dir / "nvidia")
        ps1_content = (
            "$ErrorActionPreference = 'Stop'\n"
            "$here = Split-Path -Parent $MyInvocation.MyCommand.Path\n"
            "$src = Join-Path $here 'nvidia'\n"
            "$candidates = @(\n"
            "  (Join-Path $here 'SubFlow Studio'),\n"
            "  (Join-Path (Split-Path -Parent $here) 'SubFlow Studio'),\n"
            "  (Join-Path (Split-Path -Parent $here) 'dist_cpu\\SubFlow Studio')\n"
            ")\n"
            "$targetApp = $null\n"
            "foreach ($c in $candidates) {\n"
            "  if (Test-Path (Join-Path $c 'SubFlow Studio.exe')) { $targetApp = $c; break }\n"
            "}\n"
            "if (-not $targetApp) {\n"
            "  Write-Host '[提示] 请将本 GPU 加速包解压到 SubFlow Studio 主程序同级目录后再运行。' -ForegroundColor Yellow\n"
            "  exit 1\n"
            "}\n"
            "$dst = Join-Path $targetApp 'nvidia'\n"
            "Write-Host \"正在安装 CUDA 12 / cuDNN 9 显卡加速包到: $dst ...\"\n"
            "Copy-Item -Path $src -Destination $dst -Recurse -Force\n"
            "Write-Host '[成功] GPU 加速包已启用！重启 SubFlow Studio 即可开启 8~10 倍显卡加速。' -ForegroundColor Green\n"
        )
        bat_content = (
            "@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "powershell -NoProfile -ExecutionPolicy Bypass -File \"%~dp0enable_gpu.ps1\"\r\n"
            "pause\r\n"
        )
        (gpu_pack_dir / "enable_gpu.ps1").write_text(ps1_content, encoding="utf-8")
        (gpu_pack_dir / "一键启用GPU加速.bat").write_text(bat_content, encoding="utf-8")
        gpu_bytes = sum(p.stat().st_size for p in (gpu_pack_dir / "nvidia").rglob("*") if p.is_file())
        gpu_mb = round(gpu_bytes / (1024 * 1024), 1)
        log_line(log, f"[OK] Standalone GPU acceleration pack: {gpu_pack_dir} ({gpu_mb} MB)")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(__file__).resolve().parent

    if not (source / "app.py").is_file():
        print(f"[ERROR] app.py not found in {source}", flush=True)
        return 10

    # Relocate ZIP-preview / Temp launches, or paths whose characters break Windows
    # venv/PyInstaller tooling (Chinese, spaces, parentheses, '%'), before creating
    # any virtual environment or build artifacts.
    need_relocate = (not args.staged) and (is_zip_or_temp_path(source) or has_venv_unsafe_chars(source))
    if need_relocate:
        reason = "ZIP/Temp directory" if is_zip_or_temp_path(source) else "path contains spaces/parentheses/%/non-ASCII"
        print(f"[INFO] Relocation triggered ({reason}): {source}", flush=True)
        try:
            stable = copy_to_stable_dir(source)
        except Exception:
            traceback.print_exc()
            return 11
        print(f"[INFO] Relocated source to stable build directory: {stable}", flush=True)
        forward = [sys.executable, str(stable / "build_release.py"), "--staged"]
        if args.debug:
            forward.append("--debug")
        if args.reinstall:
            forward.append("--reinstall")
        if args.skip_pyinstaller:
            forward.append("--skip-pyinstaller")
        if args.skip_installer:
            forward.append("--skip-installer")
        if args.require_ffmpeg:
            forward.append("--require-ffmpeg")
        if args.no_pause:
            forward.append("--no-pause")
        if args.pip_index_url:
            forward.extend(["--pip-index-url", args.pip_index_url])
        return subprocess.call(forward, cwd=str(stable), shell=False)

    missing = [name for name in REQUIRED if not (source / name).is_file()]
    if missing:
        print(f"[ERROR] Required files missing: {', '.join(missing)}", flush=True)
        return 12

    if os.name != "nt":
        print("[ERROR] This build controller must run on Windows.", flush=True)
        return 13

    if sys.version_info[:2] != (3, 12) or sys.maxsize <= 2**32:
        print(f"[ERROR] Need Python 3.12 x64; got {sys.version} / {sys.executable}", flush=True)
        return 14

    debug_build = args.debug or os.environ.get("SUBFLOW_DEBUG_BUILD", "0") == "1"
    log_path = source / "build_windows.log"
    build_env = os.environ.copy()
    build_env["PYTHONUTF8"] = "1"
    build_env["PYTHONIOENCODING"] = "utf-8"
    if debug_build:
        build_env["SUBFLOW_DEBUG_BUILD"] = "1"
    if args.require_ffmpeg:
        build_env["SUBFLOW_REQUIRE_FFMPEG"] = "1"

    try:
        with log_path.open("a", encoding="utf-8") as log:
            log_line(log, "\n\n===== SubFlow Studio build started =====")
            log_line(log, f"Project: {source}")
            log_line(log, f"Base Python: {sys.executable}")
            log_line(log, f"Python version: {sys.version}")
            log_line(log, f"Debug console build: {debug_build}")

            venv_dir = source / ".build_venv"
            build_python = venv_dir / "Scripts" / "python.exe"

            if not build_python.is_file():
                run(
                    [sys.executable, "-m", "venv", str(venv_dir)],
                    source,
                    log,
                    env=build_env,
                    label="Create isolated .build_venv (system Python untouched)",
                )

            if not build_python.is_file():
                raise RuntimeError(f"Isolated venv python.exe was not created: {build_python}")

            if args.reinstall or not venv_dependencies_ready(build_python, source):
                run(
                    [str(build_python), "-m", "pip", "--version"],
                    source,
                    log,
                    env=build_env,
                    label="Verify pip in .build_venv",
                )
                pip_cmd = [
                    str(build_python),
                    "-m",
                    "pip",
                    "install",
                    "--prefer-binary",
                    "-r",
                    "requirements-build.txt",
                    "-r",
                    "requirements-gpu-cu12.txt",
                ]
                if args.pip_index_url:
                    pip_cmd.extend(["-i", args.pip_index_url])
                run(
                    pip_cmd,
                    source,
                    log,
                    env=build_env,
                    label="Install pinned runtime, CUDA 12 DLLs and PyInstaller in .build_venv",
                )
            else:
                log_line(log, "[INFO] Isolated .build_venv already has all pinned packages and CUDA DLLs.")

            run(
                [str(build_python), "prepare_build.py"],
                source,
                log,
                env=build_env,
                label="Find and validate FFmpeg",
            )

            verify_cmd = [str(build_python), "verify_release.py", "--for-build"]
            if args.require_ffmpeg:
                verify_cmd.append("--require-ffmpeg")
            run(verify_cmd, source, log, env=build_env, label="Verify release structure")

            run(
                [str(build_python), "runtime_preflight.py"],
                source,
                log,
                env=build_env,
                label="Check Python, WebView2, ASR and CUDA runtime",
            )

            run(
                [
                    str(build_python),
                    "-m",
                    "py_compile",
                    "app.py",
                    "core_pipeline.py",
                    "runtime_preflight.py",
                    "verify_release.py",
                    "prepare_build.py",
                    "diagnose_startup.py",
                    "build_release.py",
                ],
                source,
                log,
                env=build_env,
                label="Compile Python sources",
            )

            run(
                [str(build_python), "-m", "unittest", "discover", "-s", "tests", "-v"],
                source,
                log,
                env=build_env,
                label="Run Python regression tests",
            )

            node = shutil.which("node")
            if node:
                for js in ("app.js", "bridge.js", "data.js"):
                    run([node, "--check", js], source, log, env=build_env, label=f"Check {js}")
                run(
                    [node, "tests/test_frontend_logic.js"],
                    source,
                    log,
                    env=build_env,
                    label="Run frontend timing and bridge tests",
                )
            else:
                log_line(log, "[WARN] Node.js missing; frontend tests skipped.")

            if args.skip_pyinstaller:
                log_line(log, "[INFO] --skip-pyinstaller specified; stopping before packaging.")
                return 0

            run(
                [str(build_python), "-m", "PyInstaller", "--noconfirm", "--clean", "SubFlowStudio.spec"],
                source,
                log,
                env=build_env,
                label="Build PyInstaller application",
            )

            dist_dir = source / "dist" / "SubFlow Studio"
            verify_dist_artifacts(dist_dir, log)
            split_cpu_and_gpu_packages(source, dist_dir, log)

            if not args.skip_installer:
                iscc = find_inno_setup()
                if iscc and (source / "installer.iss").is_file():
                    (source / "installer").mkdir(exist_ok=True)
                    try:
                        run(
                            [
                                str(iscc),
                                r"/DSourceDir=dist_cpu\SubFlow Studio",
                                "/DOutputName=SubFlowStudioSetup_Lite_CPU",
                                "installer.iss",
                            ],
                            source,
                            log,
                            env=build_env,
                            label="Build Inno Setup lightweight CPU installer (<150MB)",
                        )
                        if (source / "installer_gpu_pack.iss").is_file():
                            run(
                                [str(iscc), "installer_gpu_pack.iss"],
                                source,
                                log,
                                env=build_env,
                                label="Build Inno Setup GPU acceleration pack installer",
                            )
                        run(
                            [str(iscc), "installer.iss"],
                            source,
                            log,
                            env=build_env,
                            label="Build Inno Setup full GPU installer",
                        )
                    except subprocess.CalledProcessError as exc:
                        log_line(
                            log,
                            f"[WARN] Inno Setup failed (exit {exc.returncode}), "
                            "but portable EXE build succeeded.",
                        )
                else:
                    log_line(log, "[INFO] Inno Setup 6 (ISCC.exe) not found; portable EXE built, installer skipped.")

            exe = dist_dir / "SubFlow Studio.exe"
            log_line(log, f"[SUCCESS] Portable application: {exe}")
            log_line(log, "NOTE: Build success alone is not runtime proof; launch and verify on Windows 11.")
            print("\n[SUCCESS] EXE created:", flush=True)
            print(exe, flush=True)
            return 0

    except subprocess.CalledProcessError as exc:
        print(f"\n[FAILED] Command returned {exc.returncode}: {exc.cmd}", flush=True)
        print(f"[FAILED] Full log: {log_path}", flush=True)
        return int(exc.returncode or 1)
    except Exception as exc:
        print(f"\n[FAILED] {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        print(f"[FAILED] Full log: {log_path}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
