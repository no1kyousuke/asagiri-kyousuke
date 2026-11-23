# -*- coding: utf-8 -*-
"""
app.py —— SubFlow Studio 桌面入口（PyWebView）

用法：
    python app.py                 # 打开桌面窗口
    python app.py --selftest      # 无窗口冒烟测试（跑通 准备→识别→导出→压制 主流程）

依赖：
    pip install pywebview    (窗口)
    pip install faster-whisper yt-dlp
    ./bin/ffmpeg.exe         (已随项目携带)
    ./bin/ffprobe.exe        (可选)

加载 `index.html` 并把 `Api` 暴露给前端（window.pywebview.api）。
前端的进度通过 window.__onProgress(percent, text) 接收，由后端 evaluate_js 推送。
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

# Source mode uses the project directory. Frozen builds keep immutable resources
# beside the EXE and write settings/cache/work files under the current user's profile.
IS_FROZEN = bool(getattr(sys, "frozen", False))
BASE = Path(sys.executable).resolve().parent if IS_FROZEN else Path(__file__).resolve().parent
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", BASE)).resolve() if IS_FROZEN else BASE
_LEGACY_ENV = os.environ.get("SUBFLOW_LEGACY_DIR", "").strip()
LEGACY_PROJECT_DIR: Optional[Path] = Path(_LEGACY_ENV) if _LEGACY_ENV else None
if IS_FROZEN:
    _local_appdata = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    APP_DATA_DIR = _local_appdata / "SubFlow Studio"
else:
    APP_DATA_DIR = BASE
try:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Do not fail before the startup diagnostic logger has a chance to report it.
    pass

# Configure logging BEFORE importing optional GUI / media packages. In a
# PyInstaller windowed build, an import-time exception otherwise disappears
# without a console and looks like a silent no-op to the user.
try:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(APP_DATA_DIR / "app.log"),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        encoding="utf-8",
        force=True,
    )
except Exception:
    pass

def _show_boot_error(exc: BaseException) -> None:
    """Persist import/bootstrap errors and show a useful Windows dialog."""
    detail = f"{type(exc).__name__}: {exc}"
    try:
        logging.exception("启动阶段依赖导入失败")
    except Exception:
        pass
    try:
        log_path = APP_DATA_DIR / "app.log"
        message = (
            "SubFlow Studio 在加载运行依赖时失败，窗口尚未创建。\n\n"
            f"错误：{detail}\n\n"
            "请把以下日志文件发给开发者：\n" + str(log_path)
        )
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "SubFlow Studio 启动失败", 0x10)
        else:
            print(message, file=sys.stderr)
    except Exception:
        pass

# FFmpeg ships as a resource under the application folder. Models and cookies stay
# in the writable per-user data directory and are never embedded in the release.
_RESOURCE_BIN = BASE / "bin"
if _RESOURCE_BIN.is_dir():
    os.environ["PATH"] = str(_RESOURCE_BIN) + os.pathsep + os.environ.get("PATH", "")
for _root in (BASE, BUNDLE_ROOT):
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

try:
    import requests  # noqa: E402
    import webview  # noqa: E402

    from core_pipeline import (  # noqa: E402
        MediaProcessor,
        SubtitleRenderer,
        SubtitleTranslator,
        detect_platform,
        parse_srt_file,
    )
except Exception as _boot_exc:  # noqa: BLE001
    _show_boot_error(_boot_exc)
    raise

# 前端传入的 segments 使用 src_text / zh_text 字段；前端内部用 src / zh，桥接层负责互转。
# Api 层保持与 core_pipeline 的契约完全一致，不做字段改写。


def _safe_source_log_label(source: str) -> str:
    """Keep logs useful without writing full share URLs, query tokens or local directories."""
    value = str(source or "").strip()
    if re.match(r"^https?://", value, re.I):
        try:
            parsed = urlsplit(value)
            host = parsed.hostname or "unknown-host"
            return f"{parsed.scheme}://{host}{parsed.path[:48]}"  # query/userinfo intentionally omitted
        except Exception:
            return "网络视频链接（已隐藏参数）"
    try:
        return Path(value).name or "本地视频"
    except Exception:
        return "本地视频"


class _LocalMediaServer:
    """Loopback-only zero-copy HTTP Range media server for WebView2 <video> playback.

    Serves ONLY explicitly registered video tokens directly from disk (no copying or
    hard-linking into BASE, works across drives and inside read-only Program Files).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: Dict[str, Path] = {}
        self._server: Optional[ThreadingHTTPServer] = None
        self._port: int = 0

    def _ensure_started(self) -> int:
        with self._lock:
            if self._server is not None and self._port > 0:
                return self._port
            files_map = self._files
            lock = self._lock

            class Handler(BaseHTTPRequestHandler):
                protocol_version = "HTTP/1.1"

                def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                    return

                def _resolve_target(self) -> Optional[Path]:
                    req_path = urlsplit(self.path).path.lstrip("/")
                    if not req_path.startswith("media/"):
                        return None
                    token = req_path[len("media/"):].split("/", 1)[0]
                    with lock:
                        return files_map.get(token)

                def _serve(self, head_only: bool = False) -> None:
                    target = self._resolve_target()
                    if target is None or not target.is_file():
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    try:
                        size = target.stat().st_size
                    except OSError:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return

                    ctype = mimetypes.guess_type(str(target))[0] or "video/mp4"
                    range_hdr = self.headers.get("Range")
                    start, end = 0, max(0, size - 1)
                    status = 200
                    if range_hdr and range_hdr.startswith("bytes=") and size > 0:
                        spec = range_hdr[6:].split(",", 1)[0].strip()
                        if "-" in spec:
                            s_part, e_part = spec.split("-", 1)
                            try:
                                if s_part:
                                    start = int(s_part)
                                    end = int(e_part) if e_part else size - 1
                                elif e_part:
                                    suffix = int(e_part)
                                    start = max(0, size - suffix)
                                    end = size - 1
                            except ValueError:
                                start, end = 0, size - 1
                            if start >= size or end < start:
                                self.send_response(416)
                                self.send_header("Content-Range", f"bytes */{size}")
                                self.send_header("Content-Length", "0")
                                self.end_headers()
                                return
                            end = min(end, size - 1)
                            status = 206

                    length = max(0, end - start + 1) if size > 0 else 0
                    self.send_response(status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Accept-Ranges", "bytes")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "private, max-age=3600")
                    self.send_header("Content-Length", str(length))
                    if status == 206:
                        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                    self.end_headers()
                    if head_only or length == 0:
                        return
                    try:
                        with target.open("rb") as fh:
                            fh.seek(start)
                            remaining = length
                            while remaining > 0:
                                chunk = fh.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                remaining -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                        pass

                def do_HEAD(self) -> None:  # noqa: N802
                    self._serve(head_only=True)

                def do_GET(self) -> None:  # noqa: N802
                    self._serve(head_only=False)

            class QuietMediaServer(ThreadingHTTPServer):
                daemon_threads = True

                def handle_error(self, request: Any, client_address: Any) -> None:
                    exc = sys.exc_info()[1]
                    if isinstance(exc, (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)):
                        return
                    super().handle_error(request, client_address)

            srv = QuietMediaServer(("127.0.0.1", 0), Handler)
            self._server = srv
            self._port = int(srv.server_address[1])
            t = threading.Thread(target=srv.serve_forever, daemon=True, name="subflow-media-http")
            t.start()
            return self._port

    def register(self, path: Path) -> str:
        port = self._ensure_started()
        ext = path.suffix.lower() or ".mp4"
        digest = hashlib.md5(f"{path}:{path.stat().st_mtime_ns}".encode("utf-8")).hexdigest()[:12]
        token = f"{digest}{ext}"
        with self._lock:
            if token in self._files:
                self._files.pop(token, None)
            self._files[token] = path
            # 保留最近 3 个视频 token，避免切换视频瞬间旧播放请求出现 404
            while len(self._files) > 3:
                oldest = next(iter(self._files))
                self._files.pop(oldest, None)
        return f"http://127.0.0.1:{port}/media/{token}"

    def close(self) -> None:
        with self._lock:
            srv = self._server
            self._server = None
            self._port = 0
            self._files.clear()
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:  # noqa: BLE001
                pass


def _check_webview2_installed() -> bool:
    """Return True if Microsoft Edge WebView2 Evergreen Runtime is registered on Windows."""
    if os.name != "nt":
        return True
    try:
        import winreg
        guid = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
        for hive, sub in (
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{guid}"),
            (winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\EdgeUpdate\Clients\{guid}"),
        ):
            try:
                with winreg.OpenKey(hive, sub) as k:
                    pv, _ = winreg.QueryValueEx(k, "pv")
                    if pv and str(pv) != "0.0.0.0":
                        return True
            except OSError:
                continue
        return False
    except Exception:
        return True


class Api:
    """暴露给 window.pywebview.api 的方法集合。全部幂等、不抛未捕获异常。"""

    def __init__(self) -> None:
        self.work_dir = APP_DATA_DIR / "workspace"
        self.bin_dir = APP_DATA_DIR / "bin"
        self._media_server = _LocalMediaServer()
        self._push_lock = threading.Lock()
        self._last_push_time = 0.0
        self._fullscreen_lock = threading.Lock()
        self._is_fullscreen = False
        self._saved_win_geom: Dict[str, Any] = {}
        self._last_push_pct = -1.0
        for d in (self.work_dir, self.bin_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        self.settings_path = APP_DATA_DIR / "settings.json"
        # One-time migration for users who previously kept settings/cookies beside
        # the script or EXE. The build itself never packages either secret file.
        if IS_FROZEN and not self.settings_path.exists():
            legacy_candidates = [BASE / "settings.json"]
            if LEGACY_PROJECT_DIR is not None:
                legacy_candidates.append(LEGACY_PROJECT_DIR / "settings.json")
            for legacy_settings in legacy_candidates:
                if legacy_settings.is_file():
                    try:
                        shutil.copy2(legacy_settings, self.settings_path)
                        logging.info("已从旧位置迁移用户设置")
                        break
                    except OSError:
                        logging.warning("旧设置迁移失败：%s", legacy_settings)
        if IS_FROZEN:
            legacy_cookie_candidates = [BASE / "bin" / "cookies.txt"]
            if LEGACY_PROJECT_DIR is not None:
                legacy_cookie_candidates.append(LEGACY_PROJECT_DIR / "bin" / "cookies.txt")
            new_cookie = self.bin_dir / "cookies.txt"
            if not new_cookie.exists():
                for legacy_cookie in legacy_cookie_candidates:
                    if legacy_cookie.is_file():
                        try:
                            shutil.copy2(legacy_cookie, new_cookie)
                            logging.info("已从旧位置迁移 Cookie 文件")
                            break
                        except OSError:
                            logging.warning("旧 Cookie 迁移失败：%s", legacy_cookie)
        self.settings: Dict[str, Any] = self._load_settings()

        # 只读复用已存在的完整模型缓存，绝不硬编码特定开发机路径
        readonly_models: List[str] = []
        if LEGACY_PROJECT_DIR is not None:
            legacy_models = LEGACY_PROJECT_DIR / "bin" / "models"
            if legacy_models.is_dir() and legacy_models.resolve() != (self.bin_dir / "models").resolve():
                readonly_models.append(str(legacy_models))

        self.mp = MediaProcessor(
            bin_dir=str(self.bin_dir),
            work_dir=str(self.work_dir),
            cookies_from_browser=self.settings.get("cookies_from_browser") or None,
            proxy=self.settings.get("proxy") or None,
            resource_bin_dir=str(BASE / "bin"),
            readonly_model_dirs=readonly_models,
        )
        saved_compute = str(self.settings.get("compute_mode") or "auto").strip().lower()
        if saved_compute in ("auto", "cuda", "cpu") and saved_compute != "auto":
            self.mp.set_compute_mode(saved_compute)

        self.renderer = SubtitleRenderer(
            bin_dir=str(self.bin_dir),
            work_dir=str(self.work_dir),
            resource_bin_dir=str(BASE / "bin"),
        )

        self.translator: Optional[SubtitleTranslator] = None
        self._make_translator()

    # ------------------------------------------------------------------ #
    # 设置持久化
    # ------------------------------------------------------------------ #

    def _load_settings(self) -> Dict[str, Any]:
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_settings(self) -> bool:
        """Atomically replace settings; return False when persistence did not succeed."""
        temp_path = self.settings_path.with_name(self.settings_path.name + ".tmp")
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.settings, ensure_ascii=False, indent=2)
            with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temp_path), str(self.settings_path))
            return True
        except Exception:  # noqa: BLE001
            logging.exception("保存设置失败；为避免泄露敏感值，不记录设置内容")
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _make_translator(self) -> None:
        key = str(self.settings.get("api_key") or "").strip()
        if key:
            temp = self.settings.get("temperature")
            self.translator = SubtitleTranslator(
                api_key=key,
                base_url=str(self.settings.get("base_url") or "https://api.deepseek.com"),
                model=str(self.settings.get("model") or "deepseek-chat"),
                temperature=float(temp) if temp is not None else 0.3,
            )
        else:
            self.translator = None

    # ------------------------------------------------------------------ #
    # 进度推送：window.__onProgress(percent, text)
    # ------------------------------------------------------------------ #

    def _push(self, percent: float, text: str) -> None:
        try:
            p = round(max(0.0, min(100.0, float(percent))), 1)
            now = time.monotonic()
            with self._push_lock:
                if p <= 5.0 and self._last_push_pct > 15.0:
                    self._last_push_pct = -1.0
                if p < self._last_push_pct and p < 100.0:
                    p = self._last_push_pct
                if 5.0 < p < 100.0 and (now - self._last_push_time) < 0.05 and (p - self._last_push_pct) < 1.0:
                    return
                self._last_push_pct = p
                self._last_push_time = now
            pct = json.dumps(p)
            txt = json.dumps(str(text), ensure_ascii=False)
            webview.windows[0].evaluate_js(f"window.__onProgress({pct}, {txt})")
        except Exception:  # noqa: BLE001 —— 窗口未就绪/已关闭时静默
            pass

    # ------------------------------------------------------------------ #
    # 对外 API
    # ------------------------------------------------------------------ #

    def get_hardware_info(self) -> Dict[str, Any]:
        info = self.mp.get_hardware_info()
        info["compute_mode"] = getattr(self.mp, "compute_mode", "auto")
        info["export_dir"] = str(self.settings.get("export_dir") or self.work_dir)
        return info

    def get_model_status(self) -> Dict[str, Any]:
        return self.mp.get_model_status()

    def set_compute_mode(self, mode: str = "auto") -> Dict[str, Any]:
        res = self.mp.set_compute_mode(str(mode or "auto"))
        self.settings["compute_mode"] = self.mp.compute_mode
        self._save_settings()
        return res

    def test_translator(self) -> Dict[str, Any]:
        if not self.translator:
            return {"status": "error", "message": "请先填写并保存 API Key。"}
        return self.translator.test_connection()

    def translate_single_segment(
        self,
        src_text: str,
        context_before: Optional[list] = None,
        context_after: Optional[list] = None,
        glossary_text: str = "",
        source_lang: str = "auto",
    ) -> Dict[str, Any]:
        if not self.translator:
            return {"status": "error", "zh_text": "", "message": "还没有填写 API Key，请先在偏好设置中配置。"}
        return self.translator.translate_single(
            str(src_text or ""),
            context_before=[str(x) for x in (context_before or [])],
            context_after=[str(x) for x in (context_after or [])],
            glossary_text=glossary_text or "",
            source_lang=source_lang or "auto",
        )

    def export_alignment_audit(self, segments: list, stem: str = "subtitles") -> Dict[str, Any]:
        try:
            export_root = Path(str(self.settings.get("export_dir") or self.work_dir))
            paths = self.mp.export_alignment_audit(list(segments or []), export_root, stem=stem or "subtitles")
            return {"status": "success", **paths, "message": f"已导出时间戳对照表（CSV/JSON）：{paths['csv_path']}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"导出时间戳对照表失败：{exc}"}

    def prepare_media(self, source: str, source_lang: str = "auto") -> Dict[str, Any]:
        t0 = time.time()
        logging.info("prepare_media 开始: %s", _safe_source_log_label(source))
        try:
            res = self.mp.prepare_media(
                str(source), source_lang=source_lang or "auto", progress_callback=self._push
            )
            logging.info("prepare_media 结果: status=%s message=%s (%.1fs)",
                         res.get("status"), (res.get("message") or "")[:160], time.time() - t0)
            return res
        except Exception as exc:  # noqa: BLE001
            logging.exception("prepare_media 异常")
            return {"status": "error", "video_path": None, "audio_path": None,
                    "native_srt_path": None, "title": "", "message": f"内部错误：{exc}"}

    def transcribe_audio(
        self, audio_path: str, source_lang: str = "auto", model_size: str = "large-v3-turbo"
    ) -> Dict[str, Any]:
        t0 = time.time()
        logging.info("transcribe_audio 开始: %s model=%s", audio_path, model_size)
        try:
            res = self.mp.transcribe_audio(
                str(audio_path),
                source_lang=source_lang or "auto",
                model_size=model_size or "large-v3-turbo",
                progress_callback=self._push,
            )
            logging.info("transcribe_audio 结果: status=%s segs=%d (%.1fs)",
                         res.get("status"), len(res.get("segments", [])), time.time() - t0)
            return res
        except Exception as exc:  # noqa: BLE001
            logging.exception("transcribe_audio 异常")
            return {"status": "error", "detected_lang": "", "segments": [],
                    "message": f"识别内部错误：{exc}"}

    def parse_srt_file(self, srt_path: str) -> Dict[str, Any]:
        return parse_srt_file(str(srt_path))

    def set_translator(
        self, api_key: str = "", base_url: str = "", model: str = "deepseek-chat"
    ) -> Dict[str, Any]:
        previous = dict(self.settings)
        self.settings["api_key"] = str(api_key or "").strip()
        self.settings["base_url"] = str(base_url or "https://api.deepseek.com").strip() or "https://api.deepseek.com"
        self.settings["model"] = str(model or "deepseek-chat").strip() or "deepseek-chat"
        if not self._save_settings():
            self.settings = previous
            self._make_translator()
            return {"status": "error", "message": "API 设置保存失败，请检查用户数据目录的写入权限或磁盘空间。"}
        self._make_translator()
        if self.translator:
            return {"status": "success", "message": "API Key 已保存并生效，可以开始精翻。"}
        return {"status": "success", "message": "已清空 API Key（翻译功能需填写后才能用）。"}

    def translate_segments(
        self,
        segments: list,
        glossary_text: str = "",
        source_lang: str = "auto",
        batch_size: int = 12,
        context_size: int = 3,
        only_missing: bool = False,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not self.translator:
            return {
                "status": "error",
                "segments": [],
                "message": "还没有填写 DeepSeek API Key，请在「偏好设置 → 算力与翻译引擎」里填写后再翻译。",
            }
        if not segments:
            return {"status": "error", "segments": [], "message": "没有可翻译的字幕，请先识别或导入字幕。"}
        return self.translator.translate_segments(
            list(segments),
            glossary_text=glossary_text or "",
            source_lang=source_lang or "auto",
            batch_size=int(batch_size or 12),
            context_size=int(context_size if context_size is not None else 3),
            progress_callback=self._push,
            only_missing=bool(only_missing),
            temperature=float(temperature) if temperature is not None else None,
        )

    def _resolve_export_path(self, output_path: str) -> str:
        p = Path(os.path.expanduser(str(output_path or "")))
        if p.is_absolute():
            return str(p)
        export_root = Path(str(self.settings.get("export_dir") or self.work_dir))
        parts = p.parts
        if parts and parts[0].lower() == "workspace":
            p = Path(*parts[1:]) if len(parts) > 1 else Path("output")
        return str((export_root / p).resolve())

    def export_srt(self, segments: list, output_path: str, layout: str = "zh_top") -> Dict[str, Any]:
        return self.renderer.export_srt(list(segments), self._resolve_export_path(output_path), layout=layout or "zh_top")

    def export_ass(
        self,
        segments: list,
        output_path: str,
        style_config: Optional[dict] = None,
        video_width: int = 1920,
        video_height: int = 1080,
    ) -> Dict[str, Any]:
        return self.renderer.export_ass(
            list(segments),
            self._resolve_export_path(output_path),
            style_config=style_config if isinstance(style_config, dict) else None,
            video_width=int(video_width or 1920),
            video_height=int(video_height or 1080),
        )

    def burn_subtitles(
        self,
        video_path: str,
        segments: list,
        output_path: str,
        style_config: Optional[dict] = None,
        prefer_nvenc: bool = True,
        fps: str = "source",
        render_config: Optional[dict] = None,
    ) -> Dict[str, Any]:
        return self.renderer.burn_subtitles(
            str(video_path),
            list(segments),
            self._resolve_export_path(output_path),
            style_config=style_config if isinstance(style_config, dict) else None,
            prefer_nvenc=bool(prefer_nvenc),
            progress_callback=self._push,
            fps=fps,
            render_config=render_config if isinstance(render_config, dict) else None,
        )

    def save_settings(self, settings: dict) -> Dict[str, Any]:
        if isinstance(settings, dict):
            previous = dict(self.settings)
            for k, v in settings.items():
                if k == "api_key":
                    self.settings["api_key"] = str(v or "").strip()
                else:
                    self.settings[k] = v
            if not self._save_settings():
                self.settings = previous
                self._make_translator()
                return {"status": "error", "message": "设置保存失败，请检查用户数据目录的写入权限或磁盘空间。"}
            self._make_translator()
        return {"status": "success", "message": "设置已保存。"}

    def load_settings(self) -> Dict[str, Any]:
        out = dict(self.settings)
        cookies_path = self.bin_dir / "cookies.txt"
        out["cookies_file_exists"] = cookies_path.is_file() and cookies_path.stat().st_size > 20
        return out

    def detect_platform(self, url: str = "") -> Dict[str, Any]:
        """识别视频平台，返回 name/color/hint/needs_proxy 等给前端做徽章。"""
        return detect_platform(url)

    def set_cookie_source(self, source: str = "") -> Dict[str, Any]:
        """设置 Cookie 来源：空 / cookies.txt / chrome / edge / firefox / brave / opera ..."""
        src = str(source or "").strip()
        previous = dict(self.settings)
        self.settings["cookies_from_browser"] = src
        if not self._save_settings():
            self.settings = previous
            self.mp.cookies_from_browser = str(previous.get("cookies_from_browser") or "").lower() or None
            return {"status": "error", "message": "Cookie 来源设置保存失败，请检查用户数据目录的写入权限。"}
        # 即时生效：browser 优先，其次 cookies.txt（MediaProcessor 内部自动回退）
        self.mp.cookies_from_browser = src.lower() or None
        if src.lower():
            return {"status": "success", "message": f"已设置 Cookie 来源：{src}。重新「提取并翻译」即可生效。"}
        return {"status": "success", "message": "已清空 Cookie 来源，改用手动 cookies.txt（如已放置）。"}

    def set_proxy(self, proxy: str = "") -> Dict[str, Any]:
        """设置网络代理（对国外平台必需）。常驻设置，下载时自动使用。"""
        p = str(proxy or "").strip()
        previous = dict(self.settings)
        self.settings["proxy"] = p
        if not self._save_settings():
            self.settings = previous
            self.mp.proxy = str(previous.get("proxy") or "").strip() or None
            return {"status": "error", "message": "代理设置保存失败，请检查用户数据目录的写入权限。"}
        self.mp.proxy = p or None
        if p:
            return {"status": "success", "message": f"代理已保存：{p}，下载国外平台时会自动走它。"}
        return {"status": "success", "message": "已清空代理（国内平台不受影响）。"}

    def import_cookies(self) -> Dict[str, Any]:
        """扫描下载目录 / 桌面，找最近的 cookies*.txt，复制到 bin/cookies.txt。"""
        roots = []
        for d in (Path.home() / "Downloads", Path.home() / "Desktop", Path.home() / "Documents"):
            roots.append(d)
        # 也扫当前工作目录下的 workspace，避免漏
        roots.append(self.work_dir)

        found = []
        for d in roots:
            if not d.is_dir():
                continue
            try:
                for p in d.glob("cookies*.txt"):
                    if p.is_file():
                        found.append(p)
            except OSError:
                continue

        if not found:
            return {
                "status": "error",
                "message": "没在「下载」「桌面」等目录找到 cookies.txt。"
                           "请先用浏览器扩展（如「Get cookies.txt LOCALLY」）导出，再点一次导入。",
            }
        found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        src = found[0]
        dst = self.bin_dir / "cookies.txt"
        temp_dst = dst.with_name(dst.name + ".tmp")
        previous = dict(self.settings)
        try:
            shutil.copy2(str(src), str(temp_dst))
            os.replace(str(temp_dst), str(dst))
        except OSError as exc:
            try:
                temp_dst.unlink(missing_ok=True)
            except OSError:
                pass
            return {"status": "error", "message": f"复制失败：{exc}"}

        self.settings.pop("cookies_from_browser", None)
        if not self._save_settings():
            self.settings = previous
            return {"status": "error", "message": "Cookie 文件已复制，但设置保存失败；请检查用户数据目录权限后重试。"}
        self.mp.cookies_file = str(dst)
        self.mp.cookies_from_browser = None
        return {
            "status": "success",
            "message": f"已导入 {src.name} → bin/cookies.txt。现在可以直接「提取并翻译」了。",
        }

    # ------------------------------------------------------------------ #
    # B站扫码登录（免导 cookie，手机扫一下即登录）
    # ------------------------------------------------------------------ #

    _BILI_GEN = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    _BILI_POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
    _UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

    # ------------------------------------------------------------------ #
    # 本地 Ollama（免费离线翻译引擎）：检测 / 自动安装 / 拉模型
    # ------------------------------------------------------------------ #

    _OLLAMA_URL = "https://ollama.com/download/OllamaSetup.exe"
    _OLLAMA_DEFAULT_MODEL = "qwen2.5:3b"

    def _no_window(self) -> Dict[str, Any]:
        if os.name != "nt":
            return {}
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW

    def _ollama_exe(self) -> Optional[Path]:
        """定位 ollama.exe：优先 PATH，其次默认安装目录。"""
        exe = shutil.which("ollama")
        if exe:
            return Path(exe)
        cand = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
        return cand if cand.is_file() else None

    def _ollama_running(self) -> bool:
        try:
            return requests.get("http://localhost:11434/api/tags", timeout=2).status_code == 200
        except Exception:  # noqa: BLE001
            return False

    def check_ollama(self) -> Dict[str, Any]:
        """检测本地 Ollama：装没装、服务跑没跑、有哪些模型。"""
        exe = self._ollama_exe()
        models = []
        if exe:
            try:
                cp = subprocess.run(
                    [str(exe), "list"], capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=20, **self._no_window(),
                )
                for ln in (cp.stdout or "").strip().splitlines()[1:]:
                    if ln.strip():
                        models.append(ln.split()[0])
            except Exception:  # noqa: BLE001
                pass
        return {
            "status": "success",
            "installed": exe is not None,
            "running": self._ollama_running(),
            "models": models,
            "path": str(exe) if exe else "",
            "default_model": self._OLLAMA_DEFAULT_MODEL,
        }

    def install_ollama(self) -> Dict[str, Any]:
        """下载并静默安装 Ollama（约 150MB），装完自动拉起服务。"""
        exe = self._ollama_exe()
        if exe and self._ollama_running():
            return {"status": "success", "message": "Ollama 已安装并在运行中。"}
        try:
            dst = self.work_dir / "OllamaSetup.exe"
            self._push(3, "正在下载 Ollama 安装包（约 150MB）…")
            deadline = time.time() + 900   # 最多 15 分钟，避免网络卡死
            last_move = time.time()
            last_size = 0
            with requests.get(self._OLLAMA_URL, stream=True, timeout=45) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                got = 0
                with open(dst, "wb") as fh:
                    for chunk in resp.iter_content(1024 * 256):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        got += len(chunk)
                        if total:
                            pct = got * 60.0 / total
                            self._push(3 + pct, f"下载安装包 {got * 100 // total}%")
                        # 卡死检测：90 秒内没增长就放弃
                        if got > last_size:
                            last_size, last_move = got, time.time()
                        if time.time() - last_move > 90 or time.time() > deadline:
                            self._push(0, "下载中断")
                            return {"status": "error", "message": "Ollama 安装包下载太慢/被网络阻断。\n"
                                    "建议改用国内直连的免费额度：点「智谱 GLM」或「硅基流动」，去官网注册拿 Key（2 分钟），"
                                    "不用下载任何大文件。"}

            self._push(66, "正在静默安装 Ollama…")
            subprocess.run(
                [str(dst), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES", "/SP-"],
                capture_output=True, timeout=600, **self._no_window(),
            )
            for _ in range(10):
                time.sleep(2)
                if self._ollama_exe():
                    break

            exe = self._ollama_exe()
            if not exe:
                return {"status": "error", "message": "安装包已运行，但没找到 ollama.exe。请手动到 ollama.com 安装一次。"}

            if not self._ollama_running():
                self._push(76, "启动 Ollama 服务…")
                subprocess.Popen(
                    [str(exe), "serve"], stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, **self._no_window(),
                )
                for _ in range(8):
                    time.sleep(1.5)
                    if self._ollama_running():
                        break
            self._push(82, "Ollama 已就绪")
            return {"status": "success", "message": "Ollama 安装完成，服务已启动。"}
        except Exception as exc:  # noqa: BLE001
            logging.exception("install_ollama 失败")
            return {"status": "error", "message": f"自动安装失败：{exc}\n可到 ollama.com 手动安装，或改用「智谱 GLM / 硅基流动」免费额度。"}

    def pull_ollama_model(self, model: str = "") -> Dict[str, Any]:
        """下载指定 Ollama 模型（默认 qwen2.5:3b，约 1.9GB），带实时进度。"""
        m = (model or self._OLLAMA_DEFAULT_MODEL).strip() or self._OLLAMA_DEFAULT_MODEL
        exe = self._ollama_exe()
        if not exe:
            return {"status": "error", "message": "还没安装 Ollama，请先点「自动安装」。"}
        try:
            self._push(2, f"开始下载模型 {m}（约 1.9GB）…")
            proc = subprocess.Popen(
                [str(exe), "pull", m], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", **self._no_window(),
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                s = line.strip()
                if "%" not in s:
                    continue
                for tok in s.replace("\r", "\n").split("\n"):
                    if "%" not in tok:
                        continue
                    try:
                        pct = float(tok.split("%")[0].split()[-1])
                        self._push(min(97.0, 2 + pct * 0.95), f"下载 {m} … {pct:.0f}%")
                    except (ValueError, IndexError):
                        continue
            proc.wait(timeout=3600)
            if proc.returncode != 0:
                return {"status": "error", "message": f"模型下载失败（退出码 {proc.returncode}）。可换更小的模型或检查网络。"}
            self._push(100.0, "模型已就绪")
            return {"status": "success", "message": f"模型 {m} 下载完成，现在可以翻译了。"}
        except Exception as exc:  # noqa: BLE001
            logging.exception("pull_ollama_model 失败")
            return {"status": "error", "message": f"模型下载失败：{exc}"}

    def bili_qr_generate(self) -> Dict[str, Any]:
        """生成 B站登录二维码，返回 base64 图片 + 轮询 key。"""
        try:
            resp = requests.get(self._BILI_GEN, headers={"User-Agent": self._UA}, timeout=15)
            data = resp.json()
            if data.get("code") != 0 or not data.get("data"):
                return {"status": "error", "message": "B站登录接口异常：" + str(data.get("message", ""))}
            key = data["data"]["qrcode_key"]
            url = data["data"]["url"]

            import base64
            import io as _io

            import qrcode

            img = qrcode.make(url)
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            return {"status": "success", "qrcode_key": key, "qr_base64": b64}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"无法连接 B站登录服务：{exc}"}

    def bili_qr_poll(self, qrcode_key: str) -> Dict[str, Any]:
        """轮询扫码状态：86101 未扫 / 86090 已扫未确认 / 0 成功 / 86038 过期。"""
        try:
            session = requests.Session()
            resp = session.get(
                self._BILI_POLL,
                params={"qrcode_key": qrcode_key},
                headers={"User-Agent": self._UA},
                timeout=15,
            )
            data = resp.json()
            biz = data.get("data") or {}
            code = biz.get("code") if isinstance(biz.get("code"), int) else data.get("code")
            if code == 0:
                saved = self._save_bili_cookies(session.cookies)
                msg = "B站登录成功，已保存登录态" + ("" if saved else "（但 cookie 保存失败）")
                return {"status": "success", "done": True, "code": 0, "message": msg}
            if code == 86101:
                return {"status": "success", "done": False, "code": 86101, "message": "等待扫码…"}
            if code == 86090:
                return {"status": "success", "done": False, "code": 86090, "message": "已扫码，请在手机上点确认…"}
            if code == 86038:
                return {"status": "success", "done": False, "code": 86038, "message": "二维码已过期，请重新打开"}
            return {"status": "error", "message": f"登录失败（code {code}）：{data.get('message', '')}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"轮询失败：{exc}"}

    def _save_bili_cookies(self, cookies) -> bool:
        """把 requests 的 cookie 写成 Netscape 格式 cookies.txt，供 yt-dlp 用。"""
        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            domain = getattr(c, "domain", "") or ""
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            secure = "TRUE" if getattr(c, "secure", False) else "FALSE"
            try:
                exp = int(getattr(c, "expires", 0) or 0)
            except (ValueError, TypeError):
                exp = 0
            cpath = getattr(c, "path", "/") or "/"
            if not cpath.startswith("/"):
                cpath = "/" + cpath
            lines.append(f"{domain}\t{flag}\t{cpath}\t{secure}\t{exp}\t{c.name}\t{c.value}")
        try:
            dst = self.bin_dir / "cookies.txt"
            tmp = dst.with_name(dst.name + ".tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write("\n".join(lines) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(tmp), str(dst))
            self.mp.cookies_file = str(dst)
            self.mp.cookies_from_browser = None
            self.settings.pop("cookies_from_browser", None)
            if not self._save_settings():
                return False
            return True
        except OSError:
            return False

    def open_path(self, path: Optional[str] = None) -> Dict[str, Any]:
        target = Path(str(path or self.work_dir))
        try:
            if os.name == "nt":
                if target.is_file():
                    subprocess.Popen(["explorer.exe", f"/select,{target.resolve()}"], shell=False)
                else:
                    folder = target if target.is_dir() else target.parent
                    folder.mkdir(parents=True, exist_ok=True)
                    os.startfile(str(folder.resolve()))
            else:
                os.startfile(str(target))  # type: ignore[attr-defined]
            return {"status": "success"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"无法打开文件夹：{exc}"}

    def pick_export_dir(self) -> Dict[str, Any]:
        """弹出原生目录选择框并持久化导出目录。"""
        try:
            cur = str(self.settings.get("export_dir") or self.work_dir)
            res = webview.windows[0].create_file_dialog(
                webview.FOLDER_DIALOG,
                directory=cur if Path(cur).is_dir() else str(self.work_dir),
            )
            chosen = str(res[0]) if isinstance(res, (list, tuple)) and res else (res if isinstance(res, str) else "")
            if not chosen:
                return {"status": "cancelled", "path": cur}
            self.settings["export_dir"] = chosen
            self._save_settings()
            return {"status": "success", "path": chosen, "message": f"导出目录已更新：{chosen}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"选择目录失败：{exc}"}

    def get_workspace_cache_info(self) -> Dict[str, Any]:
        """统计 workspace 中的临时 16k WAV 与预览文件占用（绝不触碰 settings/cookies/models）。"""
        total = 0
        files = 0
        try:
            if self.work_dir.is_dir():
                for item in self.work_dir.glob("*.16k.wav"):
                    if item.is_file():
                        total += item.stat().st_size
                        files += 1
                for item in self.work_dir.glob("_subs_*.ass"):
                    if item.is_file():
                        total += item.stat().st_size
                        files += 1
            for item in BASE.glob("_preview_*"):
                if item.is_file() or item.is_symlink():
                    try:
                        total += item.stat().st_size
                        files += 1
                    except OSError:
                        pass
        except OSError:
            pass
        return {"status": "success", "bytes": total, "mb": round(total / (1024 * 1024), 1), "count": files}

    def clear_workspace_cache(self) -> Dict[str, Any]:
        """安全清理临时 16k WAV / 临时 ASS / 遗留预览文件，绝不删除设置、Cookie、模型或导出成片。"""
        freed = 0
        removed = 0
        targets: List[Path] = []
        if self.work_dir.is_dir():
            targets.extend(self.work_dir.glob("*.16k.wav"))
            targets.extend(self.work_dir.glob("_subs_*.ass"))
        targets.extend(BASE.glob("_preview_*"))
        for item in targets:
            try:
                if item.is_file() or item.is_symlink():
                    freed += item.stat().st_size
                    item.unlink()
                    removed += 1
            except OSError:
                continue
        mb = round(freed / (1024 * 1024), 1)
        return {"status": "success", "freed_mb": mb, "removed": removed,
                "message": f"已清理 {removed} 个临时缓存文件（释放 {mb:.1f} MB）。"}

    def expose_video(self, path: str = "") -> Dict[str, Any]:
        """通过本机 127.0.0.1 HTTP Range 媒体服务零拷贝暴露视频给 WebView2 播放器。

        彻底避免在安装目录 BASE 创建 _preview_*.mp4（跨盘无需拷贝、只读安装目录也可用）。
        """
        src = Path(str(path or ""))
        if not src.is_file():
            return {"status": "error", "message": "视频文件不存在"}
        try:
            url = self._media_server.register(src.resolve())
            return {"status": "success", "url": url, "name": src.name}
        except OSError as exc:
            return {"status": "error", "message": f"无法启动本地视频预览服务：{exc}"}

    def prepare_demo_sample(self) -> Dict[str, Any]:
        """一键生成/加载 12 秒内置双语示例视频与字幕，供新用户 30 秒内体验完整工作流。"""
        demo_mp4 = self.work_dir / "_demo_quickstart.mp4"
        demo_wav = self.work_dir / "_demo_quickstart.wav"
        ffmpeg = self.mp.ffmpeg or str(BASE / "bin" / "ffmpeg.exe")
        if not demo_mp4.is_file() or demo_mp4.stat().st_size < 4096:
            self._push(10.0, "正在合成 12 秒内置语音示例视频…")
            try:
                if os.name == "nt":
                    ps = (
                        "Add-Type -AssemblyName System.Speech; "
                        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                        "$s.Rate = 0; "
                        f"$s.SetOutputToWaveFile('{demo_wav}', [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,16,1)); "
                        "$s.Speak('Hello and welcome to SubFlow Studio. How is the new subtitle timeline working today? It is super fast and accurate. Now you can export bilingual videos in seconds.'); "
                        "$s.Dispose(); "
                    )
                    subprocess.run(
                        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=35,
                    )
                if Path(ffmpeg).is_file():
                    if demo_wav.is_file() and demo_wav.stat().st_size > 4000:
                        subprocess.run(
                            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                             "-f", "lavfi", "-i", "color=c=0x161824:s=1280x720:d=11:r=25",
                             "-i", str(demo_wav), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                             "-c:a", "aac", "-shortest", str(demo_mp4)],
                            timeout=45,
                        )
                    else:
                        subprocess.run(
                            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                             "-f", "lavfi", "-i", "color=c=0x161824:s=1280x720:d=10:r=25",
                             "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                             "-t", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                             "-c:a", "aac", str(demo_mp4)],
                            timeout=45,
                        )
            except Exception as exc:  # noqa: BLE001
                return {"status": "error", "message": f"生成示例视频失败：{exc}"}

        if not demo_mp4.is_file():
            return {"status": "error", "message": "未找到 ffmpeg.exe，无法生成内置示例视频。"}

        sample_segments = [
            {
                "id": 1, "start": 0.25, "end": 2.65,
                "src_text": "Hello and welcome to SubFlow Studio.",
                "zh_text": "你好，欢迎使用 SubFlow Studio 双语字幕工作台。",
                "speaker": 0, "translate_status": "ok",
            },
            {
                "id": 2, "start": 2.85, "end": 5.55,
                "src_text": "How is the new subtitle timeline working today?",
                "zh_text": "今天全新的字幕时间轴对齐效果怎么样？",
                "speaker": 0, "translate_status": "ok",
            },
            {
                "id": 3, "start": 5.75, "end": 8.15,
                "src_text": "It is super fast and accurate.",
                "zh_text": "速度极快，而且卡点非常精准！",
                "speaker": 1, "translate_status": "ok",
            },
            {
                "id": 4, "start": 8.30, "end": 10.80,
                "src_text": "Now you can export bilingual videos in seconds.",
                "zh_text": "现在你只需几秒钟就能导出高质量双语成片。",
                "speaker": 1, "translate_status": "ok",
            },
        ]
        self._push(100.0, "示例视频已就绪")
        return {
            "status": "success",
            "video_path": str(demo_mp4),
            "title": "SubFlow_10秒极速上手示例",
            "segments": sample_segments,
            "message": "已载入内置 10 秒双人双语示例视频！可直接点击播放、调节造型台或测试「导出成片」。",
        }

    # ------------------------------------------------------------------ #
    # 原生文件选择框
    # ------------------------------------------------------------------ #

    def pick_video_file(self) -> Optional[str]:
        return self._pick_file("视频文件 (*.mp4;*.mkv;*.mov;*.avi;*.webm;*.flv;*.ts;*.m4v)")

    def pick_srt_file(self) -> Optional[str]:
        return self._pick_file("字幕文件 (*.srt;*.ass;*.vtt)")

    def _pick_file(self, file_types: str) -> Optional[str]:
        try:
            res = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG,
                directory=str(self.work_dir),
                allow_multiple=False,
                file_types=(file_types, "所有文件 (*.*)"),
            )
        except Exception as exc:  # noqa: BLE001
            print("[warn] file dialog failed:", exc, file=sys.stderr)
            return None
        if isinstance(res, (list, tuple)):
            return str(res[0]) if res else None
        if isinstance(res, str):
            return res
        return None

    # ------------------------------------------------------------------ #
    # 原生无边框全屏控制（彻底修复 Windows WinForms / DWM 全屏残余边框）
    # ------------------------------------------------------------------ #

    @staticmethod
    def _dwm_set_attr(hwnd: int, attr: int, val: int) -> None:
        if os.name != "nt" or not hwnd:
            return
        try:
            import ctypes
            c_val = ctypes.c_uint32(val)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint32(attr),
                ctypes.byref(c_val),
                ctypes.sizeof(c_val),
            )
        except Exception:  # noqa: BLE001
            pass

    def _get_winforms_form(self):
        if os.name != "nt" or not webview.windows:
            return None
        try:
            from webview.platforms.winforms import BrowserView
            win = webview.windows[0]
            return BrowserView.instances.get(win.uid)
        except Exception:  # noqa: BLE001
            return None

    def apply_window_appearance(self) -> None:
        """Windows 11 深色标题栏与深色边框设置，并监听 WebView2 原生全屏元素事件。"""
        form = self._get_winforms_form()
        if form is None:
            return
        try:
            from System import Func, Type

            def _apply():
                try:
                    hwnd = int(form.Handle.ToInt64())
                    # DWMWA_USE_IMMERSIVE_DARK_MODE (20) = 1，消除刺眼白色标题栏
                    self._dwm_set_attr(hwnd, 20, 1)
                    # DWMWA_BORDER_COLOR (34) = #050508 (COLORREF BGR: 0x00080505)
                    self._dwm_set_attr(hwnd, 34, 0x00080505)
                except Exception:  # noqa: BLE001
                    pass
                # 绑定 WebView2 HTML5 全屏元素变化事件（如 <video> 或 DOM requestFullscreen）
                try:
                    browser = getattr(form, "browser", None)
                    wv = getattr(browser, "webview", None) if browser is not None else None
                    core_wv = getattr(wv, "CoreWebView2", None) if wv is not None else None
                    if core_wv is not None and not getattr(form, "_subflow_fs_hooked", False):
                        form._subflow_fs_hooked = True

                        def _on_contains_fs(sender, _args):
                            try:
                                want = bool(sender.ContainsFullScreenElement)
                                self.set_fullscreen(want)
                            except Exception:  # noqa: BLE001
                                pass

                        core_wv.ContainsFullScreenElementChanged += _on_contains_fs
                except Exception:  # noqa: BLE001
                    pass

            if getattr(form, "InvokeRequired", False):
                form.Invoke(Func[Type](_apply))
            else:
                _apply()
        except Exception:  # noqa: BLE001
            pass

    def set_fullscreen(self, enabled: Optional[bool] = None) -> Dict[str, Any]:
        """进入或退出真正的 Windows 无边框全屏（先复位 Normal 再置 None，杜绝最大化切全屏残留边框）。"""
        with self._fullscreen_lock:
            target = (not self._is_fullscreen) if enabled is None else bool(enabled)
            if target == self._is_fullscreen:
                return {"status": "success", "fullscreen": self._is_fullscreen}

            if not webview.windows:
                self._is_fullscreen = target
                return {"status": "success", "fullscreen": self._is_fullscreen}

            form = self._get_winforms_form()
            if os.name == "nt" and form is not None:
                try:
                    import ctypes
                    import System.Windows.Forms as WinForms
                    from System import Func, Type

                    def _apply_fs():
                        hwnd = int(form.Handle.ToInt64())
                        if target:
                            self._saved_win_geom = {
                                "state": form.WindowState,
                                "style": form.FormBorderStyle,
                                "bounds": form.Bounds,
                                "screen": WinForms.Screen.FromControl(form),
                            }
                            screen = WinForms.Screen.FromControl(form)
                            # 关键修复：若窗口此前处于 Maximized，必须先切回 Normal 再去掉边框，
                            # 否则 WinForms 不会重算非客户区，导致四周残留 8px 边框或任务栏缝隙。
                            if form.WindowState != WinForms.FormWindowState.Normal:
                                form.WindowState = WinForms.FormWindowState.Normal
                            form.FormBorderStyle = getattr(WinForms.FormBorderStyle, "None")
                            form.Bounds = screen.Bounds
                            # 禁用 Windows 11 圆角与 1px DWM 窗口边框
                            self._dwm_set_attr(hwnd, 33, 1)           # DWMWCP_DONOTROUND
                            self._dwm_set_attr(hwnd, 34, 0xFFFFFFFE)  # DWMWA_COLOR_NONE
                            # SWP_FRAMECHANGED (0x0020) | SWP_SHOWWINDOW (0x0040) = 0x0060
                            ctypes.windll.user32.SetWindowPos(
                                ctypes.c_void_p(hwnd),
                                ctypes.c_void_p(0),
                                int(screen.Bounds.X),
                                int(screen.Bounds.Y),
                                int(screen.Bounds.Width),
                                int(screen.Bounds.Height),
                                0x0060,
                            )
                            form.is_fullscreen = True
                        else:
                            saved = self._saved_win_geom or {}
                            old_style = saved.get("style", WinForms.FormBorderStyle.Sizable)
                            old_state = saved.get("state", WinForms.FormWindowState.Normal)
                            old_bounds = saved.get("bounds")
                            form.WindowState = WinForms.FormWindowState.Normal
                            form.FormBorderStyle = old_style
                            if old_bounds is not None:
                                form.Bounds = old_bounds
                            if old_state == WinForms.FormWindowState.Maximized:
                                form.WindowState = WinForms.FormWindowState.Maximized
                            # 恢复深色标题栏与默认圆角/深色边框
                            self._dwm_set_attr(hwnd, 20, 1)
                            self._dwm_set_attr(hwnd, 33, 0)
                            self._dwm_set_attr(hwnd, 34, 0x00080505)
                            # SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                            ctypes.windll.user32.SetWindowPos(
                                ctypes.c_void_p(hwnd),
                                ctypes.c_void_p(0),
                                0, 0, 0, 0,
                                0x0027,
                            )
                            form.is_fullscreen = False

                    if getattr(form, "InvokeRequired", False):
                        form.Invoke(Func[Type](_apply_fs))
                    else:
                        _apply_fs()
                    self._is_fullscreen = target
                    return {"status": "success", "fullscreen": self._is_fullscreen}
                except Exception as exc:  # noqa: BLE001
                    logging.warning("WinForms 无边框全屏切换失败，回退 pywebview 默认切换：%s", exc)

            # 非 Windows 或回退分支
            try:
                webview.windows[0].toggle_fullscreen()
                self._is_fullscreen = target
                return {"status": "success", "fullscreen": self._is_fullscreen}
            except Exception as exc:  # noqa: BLE001
                return {"status": "error", "fullscreen": self._is_fullscreen, "message": str(exc)}

    def toggle_fullscreen(self) -> Dict[str, Any]:
        """切换窗口无边框全屏状态。"""
        return self.set_fullscreen(None)


# --------------------------------------------------------------------------- #
# 无窗口冒烟测试
# --------------------------------------------------------------------------- #

def _selftest() -> int:
    # PyInstaller windowed builds set stdout/stderr to None. Keep the same
    # --selftest entry usable from the packaged EXE by writing its report to
    # the per-user data directory instead of crashing before tests start.
    if sys.stdout is None:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        sys.stdout = (APP_DATA_DIR / "selftest.log").open("w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    ffmpeg = str(BASE / "bin" / "ffmpeg.exe")
    demo = APP_DATA_DIR / "workspace" / "_demo.mp4"
    results: list = []

    def ok(name: str, cond: bool, extra: str = "") -> None:
        results.append(f"{'PASS ' if cond else 'FAIL '}{name}" + (f" | {extra}" if extra else ""))

    api = Api()

    # 0) 自检绝不能污染用户真实配置：把 settings 指向临时文件，结束时还原
    _real_settings_path = api.settings_path
    _real_settings = dict(api.settings)
    api.settings_path = APP_DATA_DIR / "workspace" / "_selftest_settings.json"
    api.settings = {"api_key": "", "base_url": "", "model": ""}
    api._make_translator()

    # 1) 硬件
    hw = api.get_hardware_info()
    ok("hardware dict", "device" in hw and "ffmpeg_ok" in hw,
       f"device={hw.get('device')} gpu={hw.get('gpu_name')} ffmpeg={hw.get('ffmpeg_ok')}")

    # 2) 生成一段带英语语音的测试视频（用系统 TTS + ffmpeg 合流）
    wav = APP_DATA_DIR / "workspace" / "_demo.wav"
    try:
        # 用 PowerShell 的系统语音合成一英语短句
        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$s.Rate = 0; "
            f"$s.SetOutputToWaveFile('{wav}', [System.Speech.AudioFormat.SpeechAudioFormatInfo]::new(16000,16,1)); "
            "$s.Speak('Hello everyone and welcome back to the channel. Today we test three things. First download. Second recognition. Third translation.'); "
            "$s.Dispose(); "
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60,
        )
        wav_ok = wav.exists() and wav.stat().st_size > 4000
        ok("tts generated", wav_ok, f"wav={wav.stat().st_size if wav.exists() else 0}B rc={r.returncode}")
    except Exception as exc:  # noqa: BLE001
        wav_ok = False
        ok("tts generated", False, str(exc))

    # 合流成 mp4（无音轨也能继续，只是识别不到词）
    if wav.exists() and wav.stat().st_size > 4000:
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "color=c=0x2b2b32:s=640x360:d=4:r=25",
             "-i", str(wav), "-c:v", "libx264", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(demo)],
            timeout=60,
        )

    if not demo.exists():
        # 退路：无声测试片
        subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=s=640x360:d=4:r=25",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(demo)],
            timeout=60,
        )
    ok("demo video", demo.exists() and demo.stat().st_size > 10000,
       f"size={demo.stat().st_size if demo.exists() else 0}")

    # 3) 准备（本地视频）
    prep = api.prepare_media(str(demo), source_lang="auto")
    ok("prepare", prep.get("status") == "success" and prep.get("audio_path"),
       prep.get("message", "").strip()[:60])
    audio_path = prep.get("audio_path")

    # 4) 识别（用 tiny 模型，下载小、跑得快）
    trans = None
    if audio_path:
        trans = api.transcribe_audio(audio_path, source_lang="auto", model_size="tiny")
        ok("transcribe status", trans.get("status") == "success",
           f"lang={trans.get('detected_lang')} segs={len(trans.get('segments', []))}")
        ok("transcribe segments shape", all(
            set(s) >= {"id", "start", "end", "src_text"} for s in trans.get("segments", [])
        ) if trans.get("segments") else True)
        if trans.get("segments"):
            first = trans["segments"][0]
            ok("transcribed real words", bool(first["src_text"].strip()),
               first["src_text"][:50])
        else:
            ok("transcribed real words", False, trans.get("message", "").strip()[:50])

    # 5) 翻译（无 key 时应返回中文提示，不崩）
    #    注意：这里强制清空 key，保证自检结果不受用户已配置的真实 Key 影响
    segments = trans.get("segments", []) if trans else [
        {"id": 1, "start": 0.0, "end": 2.0, "src_text": "Hello world.", "zh_text": ""}
    ]
    _saved_key = api.settings.get("api_key", "")
    api.settings["api_key"] = ""
    api.mp  # noqa: B018 —— 保持引用，避免被优化掉
    api.translator = None
    api._make_translator()
    try:
        tres = api.translate_segments(segments, glossary_text="", source_lang="auto")
    finally:
        api.settings["api_key"] = _saved_key
        api._make_translator()
    ok("translate graceful", tres.get("status") == "error" and "API Key" in tres.get("message", ""),
       tres.get("message", "").strip()[:40])

    # 6) 导出 SRT / ASS
    srt_out = APP_DATA_DIR / "workspace" / "_selftest.srt"
    ass_out = APP_DATA_DIR / "workspace" / "_selftest.ass"
    r_srt = api.export_srt(segments, str(srt_out), layout="zh_top")
    ok("export srt", r_srt.get("status") == "success" and srt_out.exists(), r_srt.get("message", "")[:40])
    r_ass = api.export_ass(segments, str(ass_out), {
        "layout": "zh_top", "font_size": 19, "bg_opacity": 68, "primary_color": "#fcd34d"
    }, 1920, 1080)
    ok("export ass", r_ass.get("status") == "success" and ass_out.exists(), r_ass.get("message", "")[:40])

    # 7) 压制（NVENC 优先，自动降级 libx264）
    burn_out = APP_DATA_DIR / "workspace" / "_selftest_burn.mp4"
    r_burn = api.burn_subtitles(str(demo), segments, str(burn_out), {
        "layout": "zh_top", "font_size": 19, "bg_opacity": 68, "primary_color": "#fcd34d"
    }, prefer_nvenc=True)
    ok("burn", r_burn.get("status") == "success" and burn_out.exists(),
       f"enc={r_burn.get('encoder_used')} size={burn_out.stat().st_size if burn_out.exists() else 0}")

    # 8) 设置持久化往返（写的是临时 settings，不碰用户真实配置）
    api.save_settings({"api_key": "sk-test-123", "whisper": "tiny"})
    s2 = api.load_settings()
    ok("settings roundtrip", s2.get("api_key") == "sk-test-123" and s2.get("whisper") == "tiny", str(s2))
    api.set_translator("")  # 清掉，避免后续误用
    ok("clear api key", api.load_settings().get("api_key") == "")

    # 还原用户真实配置（自检全程只写临时文件）
    try:
        api.settings_path = _real_settings_path
        api.settings = _real_settings
        api._make_translator()
    except Exception:  # noqa: BLE001
        pass
    ok("user settings untouched", "sk-test-123" not in json.dumps(
        json.loads(_real_settings_path.read_text(encoding="utf-8")) if _real_settings_path.is_file() else {},
        ensure_ascii=False))

    print("\n".join(results))
    fails = [r for r in results if r.startswith("FAIL")]
    print(f"\n=== {len(results) - len(fails)}/{len(results)} PASS ===")
    return 1 if fails else 0


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #

def _cleanup_preview_files() -> None:
    """Remove stale preview links/copies left by an interrupted previous run."""
    try:
        for item in BASE.glob("_preview_*"):
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
            except OSError:
                logging.debug("无法清理预览临时文件：%s", item, exc_info=True)
    except OSError:
        logging.debug("扫描预览临时文件失败", exc_info=True)


def main() -> None:
    try:
        _cleanup_preview_files()
        if not _check_webview2_installed():
            raise RuntimeError(
                "未检测到 Microsoft Edge WebView2 Runtime。\n"
                "请下载安装 Microsoft Edge WebView2 Evergreen Runtime 后再启动：\n"
                "https://developer.microsoft.com/microsoft-edge/webview2/"
            )
        api = Api()
        html_path = str(BASE / "index.html")
        if not Path(html_path).is_file():
            raise FileNotFoundError(f"应用界面资源缺失：{html_path}")

        win = webview.create_window(
            "SubFlow Studio",
            html_path,
            js_api=api,
            width=1440,
            height=900,
            min_size=(1024, 680),
            background_color="#050508",
        )
        def _on_shown_or_loaded() -> None:
            api.apply_window_appearance()

        def _push_win_state(maximized: bool) -> None:
            try:
                win.evaluate_js(
                    f"window.__onWindowStateChange && window.__onWindowStateChange({json.dumps({'maximized': maximized})})"
                )
            except Exception:  # noqa: BLE001
                pass

        def _on_maximized() -> None:
            _push_win_state(True)

        def _on_restored() -> None:
            _push_win_state(False)

        def _on_closed() -> None:
            try:
                api._media_server.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                api.mp.release_model()
            except Exception:  # noqa: BLE001
                pass
        try:
            win.events.shown += _on_shown_or_loaded
            win.events.loaded += _on_shown_or_loaded
            win.events.maximized += _on_maximized
            win.events.restored += _on_restored
            win.events.closed += _on_closed
        except Exception:  # noqa: BLE001
            pass
        # 默认 http_server=True：pywebview 起本地服务加载 index.html 及相对资源。
        webview.start(debug=False)
    except Exception as exc:  # noqa: BLE001
        logging.exception("SubFlow Studio 启动失败")
        message = (
            "SubFlow Studio 启动失败。\n\n"
            f"错误：{type(exc).__name__}: {exc}\n\n"
            "请确认已安装 Microsoft Edge WebView2 Runtime，且没有被安全软件拦截。"
            "必要时重新安装 WebView2 Evergreen Runtime 后再启动。\n"
            f"日志位置：{APP_DATA_DIR / 'app.log'}"
        )
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, message, "SubFlow Studio 启动失败", 0x10)
                return
            except Exception:
                pass
        raise


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    main()