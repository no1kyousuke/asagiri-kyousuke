# -*- coding: utf-8 -*-
from __future__ import annotations
import ast, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent
required = [
    "app.py", "core_pipeline.py", "app.js", "index.html", "bridge.js", "data.js",
    "tailwind.css", "runtime_preflight.py", "diagnose_startup.py", "start_debug.bat",
    "build_windows_debug.bat", "prepare_build.py", "build_release.py",
    "run_self_tests.py", "requirements-gpu-cu12.txt",
]
missing = [n for n in required if not (ROOT / n).is_file()]
if missing:
    print("FAIL missing:", ", ".join(missing)); raise SystemExit(1)
# Parse Python without importing heavyweight GPU/GUI packages.
for name in ("app.py", "core_pipeline.py"):
    ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)
app = (ROOT / "app.py").read_text(encoding="utf-8")
bridge = (ROOT / "bridge.js").read_text(encoding="utf-8")
html = (ROOT / "index.html").read_text(encoding="utf-8")
# Every native API used by bridge.js must be provided by app.py.
used = set(re.findall(r"api\.([A-Za-z_][A-Za-z0-9_]*)", bridge))
defined = set(re.findall(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", app, re.M))
missing_api = sorted(used - defined)
if missing_api:
    print("FAIL API methods missing:", ", ".join(missing_api)); raise SystemExit(1)
# Ensure all local script/style references are present and no obsolete tailwind runtime remains.
refs = re.findall(r'<(?:script|link)[^>]+(?:src|href)=["\']([^"\']+)["\']', html, re.I)
missing_refs = [r for r in refs if not r.startswith(("http://", "https://", "data:")) and not (ROOT / r).is_file()]
if missing_refs:
    print("FAIL local HTML assets missing:", ", ".join(missing_refs)); raise SystemExit(1)
if 'src="tailwind.js"' in html:
    print("FAIL obsolete tailwind.js runtime reference remains"); raise SystemExit(1)
# Prevent credentials/model caches from accidentally entering the release source tree.
for secret in (ROOT / "settings.json", ROOT / "bin" / "cookies.txt"):
    if secret.exists():
        print("FAIL secret file must not be in release tree:", secret.relative_to(ROOT)); raise SystemExit(1)

# Protect against regressions in known desktop integration failure points.
bridge_text = bridge
front_to_back_start = bridge_text.find("function frontToBack(list)")
front_to_back_end = bridge_text.find("function backToFront(list)", front_to_back_start)
if front_to_back_start < 0 or front_to_back_end < 0 or "item.speaker = s.speaker" not in bridge_text[front_to_back_start:front_to_back_end]:
    print("FAIL Bridge frontToBack must preserve speaker A/B labels"); raise SystemExit(1)
app_js = (ROOT / "app.js").read_text(encoding="utf-8")
set_video_start = app_js.find("function setVideoByPath(path)")
set_video_end = app_js.find("async function previewVideo(path)", set_video_start)
if set_video_start < 0 or set_video_end < 0 or "previewVideo(" not in app_js[set_video_start:set_video_end]:
    print("FAIL local video picker must use the expose_video preview path"); raise SystemExit(1)
core_text = (ROOT / "core_pipeline.py").read_text(encoding="utf-8")
mp_start = core_text.find("class MediaProcessor:")
init_start = core_text.find("    def __init__(", mp_start)
init_end = core_text.find("    # ------------------------------------------------------------------ #", init_start)
init_body = core_text[init_start:init_end]
if ("self._add_cuda_runtime_dirs()" not in init_body or
        init_body.find("self._add_cuda_runtime_dirs()") > init_body.find("self._detect_acceleration()")):
    print("FAIL CUDA runtime paths must be injected before the CTranslate2 device probe"); raise SystemExit(1)
bootstrap_start = core_text.find("    def _add_cuda_runtime_dirs(", mp_start)
bootstrap_end = core_text.find("    def _fill_from_nvidia_smi", bootstrap_start)
if "import faster_whisper" in core_text[bootstrap_start:bootstrap_end]:
    print("FAIL CUDA bootstrap must not import faster_whisper before adding DLL directories"); raise SystemExit(1)
if "if sys.stdout is None:" not in app:
    print("FAIL packaged --selftest must handle PyInstaller windowed stdout=None"); raise SystemExit(1)
if app.find("logging.basicConfig(") > app.find("import webview") or "_show_boot_error(_boot_exc)" not in app:
    print("FAIL startup logging / import-error reporting must be configured before GUI imports"); raise SystemExit(1)
spec_text = (ROOT / "SubFlowStudio.spec").read_text(encoding="utf-8")
if '"clr"' not in spec_text or "console=DEBUG_BUILD" not in spec_text:
    print("FAIL PyInstaller spec must explicitly include pythonnet CLR shim for WebView backend"); raise SystemExit(1)

# Guard the Windows launcher against previously observed build failures.
build_bat = (ROOT / "build_windows.bat").read_text(encoding="utf-8")
if "%PY%" in build_bat:
    print("FAIL build script contains an undefined %PY% variable"); raise SystemExit(1)
if "build_release.py" not in build_bat:
    print("FAIL build_windows.bat must delegate build orchestration to build_release.py"); raise SystemExit(1)
for bad_cmd in ("findstr", "robocopy", "for /f"):
    if bad_cmd in build_bat.lower():
        print(f"FAIL build_windows.bat must not contain fragile CMD parsing ({bad_cmd})"); raise SystemExit(1)
build_py = (ROOT / "build_release.py").read_text(encoding="utf-8")
if "is_zip_or_temp_path" not in build_py or "SubFlowStudioBuild" not in build_py:
    print("FAIL build_release.py must self-relocate when launched from ZIP preview / Temp"); raise SystemExit(1)
if "prepare_build.py" not in build_py or ".build_venv" not in build_py:
    print("FAIL build_release.py must use isolated .build_venv and prepare_build.py"); raise SystemExit(1)
offline_bat = (ROOT / "run_offline_tests.bat").read_text(encoding="utf-8")
if "if errorlevel 1 pause & exit /b 1" in offline_bat:
    print("FAIL offline test batch has unconditional EXIT caused by '&' precedence"); raise SystemExit(1)
if "run_self_tests.py" not in offline_bat:
    print("FAIL offline test batch must delegate to run_self_tests.py"); raise SystemExit(1)
app_js_text = (ROOT / "app.js").read_text(encoding="utf-8")
index_text = (ROOT / "index.html").read_text(encoding="utf-8")
if any(k in index_text for k in ("VIP888", "pro-modal", "pro-badge-text", "PRO 专业版")):
    print("FAIL paid/PRO modal or badge must be completely removed for open-source release"); raise SystemExit(1)
if any(k in app_js_text for k in ("activateVip", "pro-modal", "永久专业买断版")):
    print("FAIL paid/PRO activation logic must be completely removed for open-source release"); raise SystemExit(1)
if not (ROOT / "LICENSE").is_file():
    print("FAIL missing open-source LICENSE file"); raise SystemExit(1)
print(f"PASS release structure; {len(used)} bridge API methods match; {len(refs)} local resource references resolve; CUDA bootstrap, PyWebView CLR packaging and build launchers verified.")
if "--for-build" in sys.argv:
    ff = ROOT / "bin" / "ffmpeg.exe"
    if not ff.is_file():
        if "--require-ffmpeg" in sys.argv:
            print("FAIL bin\\ffmpeg.exe is missing and --require-ffmpeg was requested."); raise SystemExit(2)
        print("WARN ffmpeg.exe missing: desktop UI can be built, but media processing features will be unavailable.")
    else:
        print("PASS ffmpeg.exe present.")
