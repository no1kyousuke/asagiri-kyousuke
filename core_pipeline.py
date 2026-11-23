# -*- coding: utf-8 -*-
"""
core_pipeline.py
================
外网视频双语字幕工作台 —— 后端核心模块

第 1 部分  MediaProcessor     音视频预处理 + Whisper 语音识别 + 原生 SRT 桥接
第 2 部分  SubtitleTranslator DeepSeek 滑窗批量精翻 + 术语锁定
           SubtitleRenderer   双语 SRT/ASS 导出 + NVENC 自动降级硬压

依赖
----
    pip install faster-whisper yt-dlp
    ffmpeg.exe：放到 ./bin 下，或加入系统 PATH
    ffprobe.exe：可选。没有也能跑，时长走 ffmpeg 兜底解析。
    翻译走 OpenAI 兼容 HTTP 接口；优先使用 requests.Session（可选），缺失时回退标准库 urllib；
    不需要 openai / httpx SDK。

对外约定
--------
    * 所有 public 方法都不抛异常，统一返回带 ``status`` 和中文 ``message`` 的 dict；
    * ``progress_callback(percent, text)``：percent 是 0~100 的 float；
    * 前端回调里抛出的任何异常都会被吞掉，绝不影响主流程；
    * 返回字典的 key 固定，前端可以放心解构。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
import threading
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

ProgressCallback = Callable[[float, str], None]

#: 允许作为“视频输入”的扩展名
VIDEO_EXTS = {
    ".mp4", ".mkv", ".mov", ".avi", ".flv", ".webm",
    ".ts", ".m4v", ".wmv", ".mpg", ".mpeg", ".3gp",
}

#: URL 判定（故意写宽一点：用户常把分享文案整段粘进来）
_URL_RE = re.compile(
    r"(?:https?://|www\.)"
    r"(?:[^\s\"'<>]*)" ,
    re.IGNORECASE,
)
_URL_HOST_HINTS = (
    "youtube.com", "youtu.be", "bilibili.com", "b23.tv",
    "x.com", "twitter.com", "vimeo.com", "dailymotion.com",
    "tiktok.com", "nicovideo.jp", "twitch.tv",
)

#: 原生字幕的语言优先级（只在视频自带的人工字幕里挑，不瞎猜）
PREFERRED_SUB_LANGS = [
    "en", "zh-Hans", "zh-CN", "zh", "ja", "ko",
    "de", "fr", "es", "ru", "pt", "it",
]

#: 语言别名归一
_LANG_ALIAS = {
    "zh-cn": "zh", "zh-hans": "zh", "zh-tw": "zh", "zh-hant": "zh",
    "zh-hk": "zh", "cmn": "zh", "chs": "zh", "cht": "zh",
    "jp": "ja", "jpn": "ja", "kr": "ko", "kor": "ko",
    "eng": "en", "deu": "de", "ger": "de",
    "fra": "fr", "fre": "fr", "spa": "es", "rus": "ru",
    "por": "pt", "ita": "it",
}

#: 语言代码 → 中文名（只用于拼提示语）
LANG_NAMES = {
    "zh": "中文", "en": "英语", "ja": "日语", "ko": "韩语",
    "de": "德语", "fr": "法语", "es": "西班牙语", "ru": "俄语",
    "pt": "葡萄牙语", "it": "意大利语", "ar": "阿拉伯语",
    "th": "泰语", "vi": "越南语", "id": "印尼语", "tr": "土耳其语",
    "nl": "荷兰语", "pl": "波兰语", "uk": "乌克兰语",
}

#: faster-whisper 支持的模型规格
WHISPER_MODELS = {
    "tiny", "tiny.en", "base", "base.en", "small", "small.en",
    "medium", "medium.en", "large-v1", "large-v2", "large-v3",
    "large", "large-v3-turbo", "turbo", "distil-large-v2",
    "distil-large-v3", "distil-medium.en", "distil-small.en",
}

#: HuggingFace 国内镜像端点：直连 huggingface.co 超时（WinError 10060）时自动回退。
#: 若用户通过环境变量 HF_ENDPOINT 显式指定了代理/镜像，则优先使用、不覆盖。
HF_MIRROR_ENDPOINT = os.environ.get("HF_MIRROR", "https://hf-mirror.com")

#: 主流视频平台识别表（yt-dlp 本身已适配 1000+ 站点，这里是「识别 + 提示」层）
PLATFORM_HOSTS = {
    "bilibili": ["bilibili.com", "b23.tv"],
    "youtube": ["youtube.com", "youtu.be"],
    "douyin": ["douyin.com", "iesdouyin.com"],
    "kuaishou": ["kuaishou.com", "gifshow.com", "chenzhongtech.com"],
    "xiaohongshu": ["xiaohongshu.com", "xhslink.com"],
    "weibo": ["weibo.com", "weibo.cn"],
    "tiktok": ["tiktok.com"],
    "twitter": ["twitter.com", "x.com", "t.co"],
    "instagram": ["instagram.com"],
    "facebook": ["facebook.com", "fb.watch"],
    "vimeo": ["vimeo.com"],
    "dailymotion": ["dailymotion.com"],
    "twitch": ["twitch.tv"],
    "youku": ["youku.com"],
    "iqiyi": ["iqiyi.com"],
    "tencent": ["v.qq.com"],
    "mgtv": ["mgtv.com", "hunantv.com"],
    "ixigua": ["ixigua.com", "toutiao.com"],
    "pornhub": ["pornhub.com"],  # 仅用于平台识别，不做特殊处理
}

PLATFORM_INFO = {
    "bilibili": {"name": "B站", "color": "#fb7299", "hint": "可扫码登录，国内直连", "needs_proxy": False},
    "youtube": {"name": "YouTube", "color": "#ff0000", "hint": "需代理 + 登录 Cookie", "needs_proxy": True},
    "douyin": {"name": "抖音", "color": "#25f4ee", "hint": "国内直连，可能需登录", "needs_proxy": False},
    "kuaishou": {"name": "快手", "color": "#ff5000", "hint": "国内直连，可能需登录", "needs_proxy": False},
    "xiaohongshu": {"name": "小红书", "color": "#ff2442", "hint": "国内直连", "needs_proxy": False},
    "weibo": {"name": "微博", "color": "#ea7132", "hint": "国内直连", "needs_proxy": False},
    "tiktok": {"name": "TikTok", "color": "#010101", "hint": "需代理 + 登录态", "needs_proxy": True},
    "twitter": {"name": "X / 推特", "color": "#1d9bf0", "hint": "需代理 + 登录态", "needs_proxy": True},
    "instagram": {"name": "Instagram", "color": "#e4405f", "hint": "需代理 + 登录态", "needs_proxy": True},
    "facebook": {"name": "Facebook", "color": "#1877f2", "hint": "需代理 + 登录态", "needs_proxy": True},
    "vimeo": {"name": "Vimeo", "color": "#1ab7ea", "hint": "需代理，部分可直连", "needs_proxy": True},
    "dailymotion": {"name": "Dailymotion", "color": "#0be2ff", "hint": "需代理", "needs_proxy": True},
    "twitch": {"name": "Twitch", "color": "#9146ff", "hint": "需代理", "needs_proxy": True},
    "youku": {"name": "优酷", "color": "#1aa0f3", "hint": "国内直连，可扫码登录", "needs_proxy": False},
    "iqiyi": {"name": "爱奇艺", "color": "#00be06", "hint": "国内直连，可能需登录", "needs_proxy": False},
    "tencent": {"name": "腾讯视频", "color": "#ff6a00", "hint": "国内直连，可能需登录", "needs_proxy": False},
    "mgtv": {"name": "芒果TV", "color": "#ff7f00", "hint": "国内直连", "needs_proxy": False},
    "ixigua": {"name": "西瓜视频", "color": "#ff2e4d", "hint": "国内直连", "needs_proxy": False},
}


def detect_platform(url: str) -> Dict[str, Any]:
    """根据 URL 识别视频平台，返回 {id, name, color, hint, needs_proxy}。"""
    low = (url or "").strip().lower()
    for pid, hosts in PLATFORM_HOSTS.items():
        for h in hosts:
            if h in low:
                info = dict(PLATFORM_INFO.get(pid, {}))
                info["id"] = pid
                return info
    return {"id": "unknown", "name": "其他平台", "color": "#9ca3af",
            "hint": "粘贴视频页链接即可，支持 1000+ 站点", "needs_proxy": False}

#: 字幕单行宽度上限（半角计）：≈ 2 行 × 42 字符
MAX_SEG_WIDTH = 84
#: 单条字幕最长时长（秒）
MAX_SEG_DUR = 8.0
#: 单条字幕最短时长（秒）
MIN_SEG_DUR = 0.30


class PipelineError(Exception):
    """内部异常：message 必须是能直接展示给用户的中文。"""


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _disp_len(text: str) -> int:
    """按“显示宽度”计数：中日韩全角算 2，其余算 1。"""
    n = 0
    for ch in text:
        n += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return n


def _safe_name(name: str, fallback: str = "video", limit: int = 70) -> str:
    """把标题变成安全的文件名（去掉 Windows 非法字符与控制字符）。"""
    if not name:
        return fallback
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', " ", str(name))
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return fallback
    return name[:limit].strip() or fallback


def _no_window_kwargs() -> Dict[str, Any]:
    """Windows 下隐藏子进程黑窗（PyWebView 打包后必须，否则会闪 cmd）。"""
    if os.name == "nt":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


def _which(name: str, bin_dir: Path) -> Optional[str]:
    """先找 bin_dir 下的同名可执行文件，再找系统 PATH。"""
    for cand in (bin_dir / name, bin_dir / f"{name}.exe"):
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    return shutil.which(name) or None


def _run_quiet(cmd: List[str], timeout: Optional[float] = None) -> Tuple[int, str]:
    """不抛异常的短命令执行，只给探测用。返回 (returncode, 合并输出)。"""
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, **_no_window_kwargs(),
        )
        return proc.returncode, (proc.stdout or b"").decode("utf-8", "replace")
    except FileNotFoundError:
        return -1, f"找不到可执行文件：{cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "执行超时"
    except OSError as exc:
        return -1, str(exc)


def _media_duration(ffmpeg: Optional[str], ffprobe: Optional[str], path: str) -> Optional[float]:
    """取媒体时长（秒）。ffprobe 优先，没有就用 ffmpeg -i 的 stderr 兜底。"""
    if ffprobe:
        code, out = _run_quiet(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            timeout=20,
        )
        if code == 0 and out.strip():
            val = out.strip().splitlines()[0].strip()
            if val and val.upper() != "N/A":
                try:
                    return float(val)
                except ValueError:
                    pass
    if ffmpeg:
        # ffmpeg -i 没有输出文件时返回码是 1，属正常
        _, out = _run_quiet([ffmpeg, "-hide_banner", "-i", str(path)], timeout=20)
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
        if m:
            return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    return None


def _media_size(ffmpeg: Optional[str], ffprobe: Optional[str], path: str,
                default: Tuple[int, int] = (1920, 1080)) -> Tuple[int, int]:
    """取视频分辨率，拿不到就返回 default（保证 ASS 永远有合法画布）。"""
    if ffprobe:
        code, out = _run_quiet(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
            timeout=20,
        )
        if code == 0 and out.strip():
            parts = re.split(r"[,\s]+", out.strip().splitlines()[0])
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                w, h = int(parts[0]), int(parts[1])
                if w >= 16 and h >= 16:
                    return w, h
    if ffmpeg:
        _, out = _run_quiet([ffmpeg, "-hide_banner", "-i", str(path)], timeout=20)
        for m in re.finditer(r"(\d{2,5})x(\d{2,5})", out):
            w, h = int(m.group(1)), int(m.group(2))
            if w >= 16 and h >= 16:
                return w, h
    return default


# --------------------------------------------------------------------------- #
# MediaProcessor
# --------------------------------------------------------------------------- #

class MediaProcessor:
    """音视频预处理 + 语音识别。

    典型用法::

        mp = MediaProcessor(bin_dir="./bin", work_dir="./workspace")
        r = mp.prepare_media("https://www.youtube.com/watch?v=xxx",
                             progress_callback=print)
        if r["status"] == "success":
            t = mp.transcribe_audio(r["audio_path"], source_lang="auto")
    """

    def __init__(
        self,
        bin_dir: str = "./bin",
        work_dir: str = "./workspace",
        cookies_from_browser: Optional[str] = None,
        proxy: Optional[str] = None,
        resource_bin_dir: Optional[str] = None,
        readonly_model_dirs: Optional[List[str]] = None,
    ) -> None:
        self.base_dir = Path(__file__).resolve().parent
        self.bin_dir = self._abspath(bin_dir)
        self.resource_bin_dir = self._abspath(resource_bin_dir) if resource_bin_dir else self.bin_dir
        self.work_dir = self._abspath(work_dir)
        self.model_dir = self.bin_dir / "models"
        self.readonly_model_dirs: List[Path] = []
        for extra in (readonly_model_dirs or []):
            try:
                p = self._abspath(extra)
                if p != self.model_dir and p not in self.readonly_model_dirs:
                    self.readonly_model_dirs.append(p)
            except OSError:
                continue

        for d in (self.bin_dir, self.work_dir, self.model_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError as exc:  # 目录建不出来也没关系，后面报错更准确
                logger.warning("无法创建目录 %s: %s", d, exc)

        # ---- 外部工具 ----
        self.ffmpeg: Optional[str] = self._find_binary("ffmpeg")
        self.ffprobe: Optional[str] = self._find_binary("ffprobe")
        self.cookies_file: Optional[str] = self._find_cookies()
        # 优先从本机浏览器直接读 cookie（胜过手动 cookies.txt）；"chrome"/"edge"/"firefox"/"brave"/"opera" 等
        self.cookies_from_browser: Optional[str] = (
            str(cookies_from_browser).strip().lower() or None
        ) if cookies_from_browser else None
        # 代理（对国外平台必需），如 http://127.0.0.1:7890；常驻设置，下载时自动用
        self.proxy: Optional[str] = str(proxy).strip() or None if proxy else None

        # ---- 硬件加速 ----
        # Bootstrap NVIDIA pip-wheel DLL folders BEFORE importing/probing CTranslate2.
        # Otherwise get_cuda_device_count() may return 0 because cublas/cudnn are not
        # on PATH yet; adding them only later during model loading is too late because
        # the instance would already have selected CPU mode.
        self._add_cuda_runtime_dirs()
        self._accel: Dict[str, Any] = self._detect_acceleration()
        self.compute_mode: str = "auto"
        if self._accel.get("cuda_available"):
            self.device: str = "cuda"
            self.compute_type: str = "float16"
        else:
            self.device = "cpu"
            self.compute_type = "int8"

        #: CUDA 初始化失败后自动降级时置 True，前端可提示用户
        self.accel_degraded: bool = False
        self._degrade_reason: str = ""

        # ---- 模型缓存 ----
        self._model = None
        self._model_key: Optional[Tuple[str, str, str]] = None

        #: 是否允许把平台“自动生成”的字幕当原生字幕（默认关：质量通常不如 Whisper）
        self.allow_auto_sub: bool = False

        logger.info(
            "MediaProcessor 就绪 | device=%s compute=%s ffmpeg=%s",
            self.device, self.compute_type, self.ffmpeg or "未找到",
        )

    # ------------------------------------------------------------------ #
    # 基础设施
    # ------------------------------------------------------------------ #

    def _abspath(self, p: str) -> Path:
        path = Path(os.path.expanduser(str(p)))
        if not path.is_absolute():
            path = self.base_dir / path
        return path.resolve()

    def _find_binary(self, name: str) -> Optional[str]:
        """先找随程序携带的 resource_bin_dir，再找用户 bin_dir，最后找系统 PATH。"""
        for folder in (self.resource_bin_dir, self.bin_dir):
            for cand in (folder / name, folder / f"{name}.exe"):
                try:
                    if cand.is_file():
                        return str(cand)
                except OSError:
                    continue
        return shutil.which(name) or None

    def set_compute_mode(self, mode: str) -> Dict[str, Any]:
        """切换语音识别计算设备：auto / cuda / cpu。"""
        want = str(mode or "auto").strip().lower()
        if want not in ("auto", "cuda", "cpu"):
            want = "auto"
        self.compute_mode = want
        self.release_model()
        self.accel_degraded = False
        self._degrade_reason = ""
        if want == "cpu":
            self.device = "cpu"
            self.compute_type = "int8"
            return {"status": "success", "device": self.device, "compute_type": self.compute_type,
                    "message": "已切换为 CPU（int8）识别模式。"}
        if self._accel.get("cuda_available"):
            self.device = "cuda"
            self.compute_type = "float16"
            return {"status": "success", "device": self.device, "compute_type": self.compute_type,
                    "message": "已启用 CUDA（float16）GPU 加速模式。"}
        self.device = "cpu"
        self.compute_type = "int8"
        if want == "cuda":
            return {"status": "error", "device": self.device, "compute_type": self.compute_type,
                    "message": "当前环境未检测到可用 CUDA GPU，已保持 CPU（int8）模式。"}
        return {"status": "success", "device": self.device, "compute_type": self.compute_type,
                "message": "当前未检测到 CUDA，自适应使用 CPU（int8）模式。"}

    def _find_cookies(self) -> Optional[str]:
        """B站/YouTube 会员视频需要 cookie，约定放在 ./bin/cookies.txt。"""
        cand = self.bin_dir / "cookies.txt"
        return str(cand) if cand.is_file() else None

    def _run(self, cmd: List[str], timeout: Optional[float] = None) -> Tuple[int, str]:
        """跑一个短命令，返回 (returncode, 合并输出)。隐藏黑窗、强制 UTF-8 解码。"""
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                **_no_window_kwargs(),
            )
        except FileNotFoundError as exc:
            raise PipelineError(f"找不到可执行文件：{cmd[0]}，请检查安装是否完整。") from exc
        except subprocess.TimeoutExpired as exc:
            raise PipelineError(f"外部程序执行超时（{int(timeout or 0)} 秒）：{Path(cmd[0]).name}") from exc
        except OSError as exc:
            raise PipelineError(f"无法启动外部程序 {Path(cmd[0]).name}：{exc}") from exc
        out = (proc.stdout or b"").decode("utf-8", "replace")
        return proc.returncode, out

    @staticmethod
    def _emit(cb: Optional[ProgressCallback], percent: float, text: str) -> None:
        """安全回调：前端报错也不能打断后端。"""
        if cb is None:
            return
        try:
            cb(round(max(0.0, min(100.0, float(percent))), 1), str(text))
        except Exception:  # noqa: BLE001 —— 故意吞掉
            logger.debug("progress_callback 抛异常，已忽略", exc_info=True)

    def _emitter(self, cb: Optional[ProgressCallback], lo: float = 0.0, hi: float = 100.0):
        """把 0~1 的 ratio 映射到 [lo, hi]，并做节流（≥0.5% 或 ≥0.4s 才发一次）。"""
        state = {"pct": -999.0, "t": 0.0}

        def emit(ratio: float, text: str = "", force: bool = False) -> None:
            ratio = max(0.0, min(1.0, float(ratio)))
            pct = lo + (hi - lo) * ratio
            now = time.monotonic()
            if not force:
                if abs(pct - state["pct"]) < 0.5 and (now - state["t"]) < 0.4:
                    return
            state["pct"], state["t"] = pct, now
            self._emit(cb, pct, text)

        return emit

    # ------------------------------------------------------------------ #
    # 硬件检测
    # ------------------------------------------------------------------ #

    @staticmethod
    def _can_load_cuda_runtime_dlls() -> bool:
        """检查 Windows 进程能否实际加载 CUDA 12 / cuDNN 9 核心运行库（用于识别 CPU 轻量主包）。"""
        if os.name != "nt":
            return True
        try:
            import ctypes
            for dll_name in ("cublas64_12.dll", "cudnn64_9.dll"):
                ctypes.WinDLL(dll_name)
            return True
        except Exception:  # noqa: BLE001
            return False

    def _detect_acceleration(self) -> Dict[str, Any]:
        """优先用 ctranslate2（faster-whisper 的真实后端），退而用 torch。"""
        info: Dict[str, Any] = {
            "cuda_available": False,
            "detector": "none",
            "gpu_count": 0,
            "gpu_name": None,
            "total_vram_gb": None,
            "cuda_version": None,
            "ctranslate2_version": None,
            "torch_available": False,
            "gpu_pack_installed": False,
            "gpu_pack_missing": False,
        }
        dlls_ok = self._can_load_cuda_runtime_dlls()
        info["gpu_pack_installed"] = dlls_ok

        # --- 1) ctranslate2：这是真正决定 faster-whisper 能不能上 GPU 的东西 ---
        try:
            import ctranslate2  # type: ignore

            info["ctranslate2_version"] = getattr(ctranslate2, "__version__", "unknown")
            count = int(ctranslate2.get_cuda_device_count())
            info["gpu_count"] = count
            if count > 0 and dlls_ok:
                info["cuda_available"] = True
                info["detector"] = "ctranslate2"
        except Exception as exc:  # noqa: BLE001
            logger.debug("ctranslate2 CUDA 检测失败：%s", exc)

        # --- 2) torch 兜底（同时能拿到更详细的显存信息）---
        try:
            import torch  # type: ignore

            info["torch_available"] = True
            info["cuda_version"] = getattr(getattr(torch, "version", None), "cuda", None)
            if torch.cuda.is_available() and dlls_ok:
                info["cuda_available"] = True
                info["gpu_count"] = max(int(info["gpu_count"]), int(torch.cuda.device_count()))
                if info["detector"] == "none":
                    info["detector"] = "torch"
                try:
                    info["gpu_name"] = torch.cuda.get_device_name(0)
                    props = torch.cuda.get_device_properties(0)
                    info["total_vram_gb"] = round(props.total_memory / (1024 ** 3), 1)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            logger.debug("torch 检测失败：%s", exc)

        # --- 3) nvidia-smi：没有 torch 也能拿到显卡型号/显存 ---
        # 注意：即使 ctranslate2 没启用 CUDA，也把显卡信息查出来，
        # 这样提示语才能说清「有卡但未装 GPU 加速包」，方便按需启用。
        self._fill_from_nvidia_smi(info)
        if (info.get("gpu_name") or info.get("gpu_count", 0) > 0) and not dlls_ok:
            info["gpu_pack_missing"] = True

        return info

    def _add_cuda_runtime_dirs(self) -> None:
        """把 nvidia-*-cu12 pip 轮子里的 DLL 目录注入进程 DLL 搜索路径。

        Windows 上 ctranslate2 加载 GPU 模型时会找 cublas64_12.dll / cudnn64_9.dll 等；
        这些库由 `pip install nvidia-cublas-cu12 nvidia-cudnn-cu12` 提供并放在
        site-packages/nvidia/<name>/bin 下，但默认不在 DLL 搜索路径里。

        注意：os.add_dll_directory() 返回的句柄必须保持存活，否则目录会被移除。
        """
        if getattr(self, "_cuda_dirs_added", False):
            return
        if os.name != "nt" or not hasattr(os, "add_dll_directory"):
            self._cuda_dirs_added = True
            return

        site_dirs: List[Path] = []
        # PyInstaller onedir can place packages and the nvidia namespace beside
        # the executable (or below _MEIPASS); inspect these roots as well as site-packages.
        for root in (
            Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)),
            Path(sys.executable).resolve().parent,
        ):
            if root.is_dir():
                site_dirs.append(root)
        # Source-mode: locate faster_whisper without importing it. Importing the
        # package can import CTranslate2 internally, which would load CUDA DLLs
        # before PATH has been repaired and defeat this bootstrap.
        try:
            import importlib.util
            package_spec = importlib.util.find_spec("faster_whisper")
            if package_spec and package_spec.origin:
                package_dir = Path(package_spec.origin).resolve().parent
                site_dirs.append(package_dir)
                site_dirs.append(package_dir.parent)
        except Exception:  # noqa: BLE001
            pass
        try:
            import site  # noqa: F401
            for sp in site.getsitepackages():
                site_dirs.append(Path(sp))
            usp = site.getusersitepackages()
            if usp:
                site_dirs.append(Path(usp))
        except Exception:  # noqa: BLE001
            pass

        if not hasattr(self, "_dll_handles"):
            self._dll_handles: List[Any] = []

        seen: set = set()
        found_bins: List[str] = []
        for sp in site_dirs:
            nvidia_dir = sp if sp.name.lower() == "nvidia" else (sp / "nvidia")
            if not nvidia_dir.is_dir():
                continue
            for bindir in nvidia_dir.glob("*/bin"):
                try:
                    key = str(bindir.resolve())
                except OSError:
                    key = str(bindir)
                if key in seen or not bindir.is_dir():
                    continue
                seen.add(key)
                found_bins.append(str(bindir))
                try:
                    self._dll_handles.append(os.add_dll_directory(str(bindir)))
                except FileNotFoundError:
                    pass
                except Exception as exc:  # noqa: BLE001
                    logger.debug("add_dll_directory %s 失败：%s", bindir, exc)

        # 关键：ctranslate2 用标准 LoadLibrary，只吃 PATH，不吃 AddDllDirectory。
        # 把 nvidia bin 目录前置到进程 PATH，LoadLibrary 才会重新扫描到它们。
        if found_bins:
            existing = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
            merged = [d for d in found_bins if d not in existing] + existing
            os.environ["PATH"] = os.pathsep.join(merged)

        self._cuda_dirs_added = True
        if seen:
            logger.info("已注入 %d 个 CUDA 运行库目录到 PATH（nvidia pip 轮子）", len(seen))

    def _fill_from_nvidia_smi(self, info: Dict[str, Any]) -> None:
        smi = shutil.which("nvidia-smi")
        if not smi:
            return
        try:
            code, out = self._run(
                [smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                timeout=5,
            )
            if code != 0 or not out.strip():
                return
            first = out.strip().splitlines()[0]
            parts = [p.strip() for p in first.split(",")]
            if parts and parts[0] and not info.get("gpu_name"):
                info["gpu_name"] = parts[0]
            if len(parts) > 1 and parts[1].isdigit() and not info.get("total_vram_gb"):
                info["total_vram_gb"] = round(int(parts[1]) / 1024, 1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("nvidia-smi 查询失败：%s", exc)

    def get_hardware_info(self) -> Dict[str, Any]:
        """返回当前硬件加速状态，可直接丢给前端展示。"""
        info = dict(self._accel)
        info.update({
            "device": self.device,
            "compute_type": self.compute_type,
            "accelerated": self.device == "cuda",
            "accel_degraded": self.accel_degraded,
            "degrade_reason": self._degrade_reason or None,
            "ffmpeg_path": self.ffmpeg,
            "ffmpeg_ok": bool(self.ffmpeg),
            "ffprobe_ok": bool(self.ffprobe),
            "cookies_file": self.cookies_file,
            "yt_dlp_version": self._ytdlp_version(),
            "work_dir": str(self.work_dir),
            "model_dir": str(self.model_dir),
        })
        info["message"] = self._hardware_message(info)
        return info

    @staticmethod
    def _hardware_message(info: Dict[str, Any]) -> str:
        if info["device"] == "cuda":
            gpu = info.get("gpu_name") or "NVIDIA GPU"
            vram = info.get("total_vram_gb")
            vram_txt = f"，显存 {vram}GB" if vram else ""
            msg = f"已启用 GPU 加速（{gpu}{vram_txt}，float16）。"
        else:
            gpu = info.get("gpu_name")
            if gpu:
                vram = info.get("total_vram_gb")
                vram_txt = f"，显存 {vram}GB" if vram else ""
                if info.get("gpu_pack_missing"):
                    msg = (f"检测到显卡 {gpu}{vram_txt}，当前为轻量 CPU 主包（int8 模式）。"
                           "解压附带的 GPU 加速包（SubFlow_GPU_Pack_CUDA12.zip）可启用显卡加速，识别提速 8~10 倍。")
                else:
                    msg = (f"检测到显卡 {gpu}{vram_txt}，但当前环境未启用 CUDA，"
                           "本次已用 CPU（int8）模式，识别速度较慢。")
            else:
                msg = "当前使用 CPU 模式（int8）。"
        if not info["ffmpeg_ok"]:
            msg += " 警告：未找到 ffmpeg.exe，音视频预处理不可用，请放到 ./bin 目录。"
        if not info["ffprobe_ok"]:
            msg += " 提示：未找到 ffprobe.exe，时长将用 ffmpeg 兜底解析（不影响使用）。"
        if info.get("accel_degraded") and info.get("degrade_reason"):
            msg += f"（本次已从 GPU 回退：{info['degrade_reason']}）"
        return msg

    @staticmethod
    def _ytdlp_version() -> Optional[str]:
        try:
            import yt_dlp  # type: ignore

            return getattr(getattr(yt_dlp, "version", None), "__version__", "unknown")
        except Exception:  # noqa: BLE001
            return None

    def _require_ffmpeg(self) -> str:
        if not self.ffmpeg:
            raise PipelineError(
                "未找到 ffmpeg.exe。请把 ffmpeg.exe 放到程序目录的 bin 文件夹下，"
                "或安装后加入系统 PATH，然后重启程序。"
            )
        return self.ffmpeg

    # ------------------------------------------------------------------ #
    # 通用媒体工具
    # ------------------------------------------------------------------ #

    def _probe_duration(self, path: str) -> Optional[float]:
        """取时长（秒）。ffprobe 优先，没有就用 ffmpeg -i 的 stderr 兜底。"""
        return _media_duration(self.ffmpeg, self.ffprobe, str(path))

    def _extract_wav(
        self,
        video_path: str,
        audio_path: str,
        cb: Optional[ProgressCallback] = None,
        lo: float = 78.0,
        hi: float = 100.0,
    ) -> str:
        """抽出 16kHz 单声道 PCM wav（Whisper 唯一需要的格式）。"""
        ffmpeg = self._require_ffmpeg()
        total = self._probe_duration(video_path)
        emit = self._emitter(cb, lo, hi)
        emit(0.0, "正在提取音轨（16kHz 单声道）…", force=True)

        cmd = [
            ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-progress", "pipe:1", "-nostats", "-loglevel", "error",
            "-i", str(video_path),
            "-vn", "-sn", "-dn",
            "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(audio_path),
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **_no_window_kwargs(),
            )
        except FileNotFoundError as exc:
            raise PipelineError("无法启动 ffmpeg.exe，请确认 bin 目录下文件完整未损坏。") from exc
        except OSError as exc:
            raise PipelineError(f"无法启动 ffmpeg：{exc}") from exc

        tail: List[str] = []
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith("out_time_ms=") or line.startswith("out_time_us="):
                val = line.split("=", 1)[1].strip()
                if val.isdigit() and total and total > 0:
                    sec = int(val) / 1_000_000.0   # 历史命名，单位其实是微秒
                    emit(min(sec / total, 0.99), "正在提取音轨…")
            elif line.startswith(("progress=", "frame=", "fps=", "bitrate=",
                                  "total_size=", "out_time=", "dup_frames=",
                                  "drop_frames=", "speed=")):
                continue
            else:
                tail.append(line)
                if len(tail) > 30:
                    tail.pop(0)

        proc.wait()

        if proc.returncode != 0:
            detail = " | ".join(tail[-4:]) if tail else "未知原因"
            raise PipelineError(f"音频提取失败（ffmpeg 返回 {proc.returncode}）：{detail[:300]}")

        out = Path(audio_path)
        if not out.is_file() or out.stat().st_size < 1024:
            raise PipelineError("音频提取失败：输出文件为空。该视频可能没有音轨，或文件已损坏。")

        emit(1.0, "音轨提取完成", force=True)
        return str(out)

    # ------------------------------------------------------------------ #
    # 1) 预处理
    # ------------------------------------------------------------------ #

    @staticmethod
    def _is_url(text: str) -> bool:
        low = text.lower()
        if _URL_RE.search(low):
            return True
        return any(h in low for h in _URL_HOST_HINTS) and " " not in low.strip()

    @staticmethod
    def _normalize_bv_id(text: str) -> str:
        """用户经常只粘一个 BV 号，这里自动补成完整链接。"""
        m = re.fullmatch(r"(?:BV|bv)([0-9A-Za-z]{10})", text.strip())
        if m:
            return f"https://www.bilibili.com/video/BV{m.group(1)}"
        m = re.fullmatch(r"(?:av|AV)(\d{1,12})", text.strip())
        if m:
            return f"https://www.bilibili.com/video/av{m.group(1)}"
        return text

    @staticmethod
    def _extract_url(text: str) -> str:
        """从用户粘贴的整段文案（标题 + 链接）里提取出真正的 URL。

        从 B站/抖音 App 分享出来的常是「【标题】... https://b23.tv/xxx」这样的混排文本，
        直接丢给 yt-dlp 会报 'is not a valid URL'，所以这里先抠出 http(s) 链接。
        """
        if not text:
            return text
        m = re.search(
            r"https?://[^\s\u4e00-\u9fff，。、；：！？、“”‘’（）【】《》〈〉「」『』]+",
            text,
            re.IGNORECASE,
        )
        if m:
            return m.group(0).rstrip(".,;:!?)]}>'\"")
        return text.strip()

    def prepare_media(
        self,
        source_input: str,
        source_lang: str = "auto",
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """把「本地视频 / 网络链接」统一成 mp4 + wav +（可选）原生外文字幕。

        返回::

            {"status": "success"|"error", "video_path": str|None,
             "audio_path": str|None, "native_srt_path": str|None,
             "title": str, "message": 中文提示}
        """
        result: Dict[str, Any] = {
            "status": "error",
            "video_path": None,
            "audio_path": None,
            "native_srt_path": None,
            "title": "",
            "message": "",
        }

        raw = (source_input or "").strip().strip('"').strip("'")
        if not raw:
            result["message"] = "请先选择一个本地视频文件，或粘贴视频链接。"
            return result
        # 用户常把「标题 + 链接」整段粘进来（B站/抖音 App 分享），先抠出纯 URL
        raw = self._extract_url(raw)
        raw = self._normalize_bv_id(raw)

        try:
            if self._is_url(raw):
                payload = self._prepare_from_url(raw, source_lang, progress_callback)
            else:
                payload = self._prepare_from_local(raw, progress_callback)
        except PipelineError as exc:
            result["message"] = str(exc)
            logger.warning("prepare_media 失败：%s", exc)
            return result
        except Exception as exc:  # noqa: BLE001 —— 兜底，绝不把英文栈丢给前端
            logger.exception("prepare_media 未预期异常")
            result["message"] = (
                f"处理过程中出现未预期的错误（{type(exc).__name__}）。"
                "请换一个文件或链接重试；若持续出现，请把日志发给开发者。"
            )
            return result

        result.update(payload)
        result["status"] = "success"
        return result

    # ---- 本地文件 ----

    def _prepare_from_local(
        self, path_str: str, cb: Optional[ProgressCallback],
    ) -> Dict[str, Any]:
        emit = self._emitter(cb, 0.0, 100.0)
        emit(0.0, "正在检查文件…", force=True)

        src = Path(path_str)
        if not src.is_absolute():
            src = (Path.cwd() / src)
        if not src.exists():
            raise PipelineError(f"找不到文件：{path_str}")
        if not src.is_file():
            raise PipelineError("你选择的似乎是一个文件夹，请选择具体的视频文件。")
        if src.suffix.lower() not in VIDEO_EXTS:
            allow = "、".join(sorted(e.lstrip(".") for e in VIDEO_EXTS))
            raise PipelineError(
                f"不支持的文件格式「{src.suffix or '无扩展名'}」。支持：{allow}。"
                "如果是很少见的封装格式，可以先用格式工厂/ffmpeg 转成 mp4。"
            )

        base = _safe_name(src.stem, "video")
        audio_path = self.work_dir / f"{base}.16k.wav"

        emit(0.05, "文件检查通过，开始提取音轨…", force=True)
        self._extract_wav(str(src), str(audio_path), cb, lo=5.0, hi=100.0)

        title = src.stem or base
        emit(1.0, "预处理完成", force=True)
        return {
            "video_path": str(src),
            "audio_path": str(audio_path),
            "native_srt_path": None,          # 本地文件不猜字幕，交给识别
            "title": title,
            "message": f"已就绪：{title}。音轨已提取，可以开始识别字幕。",
        }

    # ---- 网络视频 ----

    def _require_ytdlp(self):
        try:
            import yt_dlp  # type: ignore
            return yt_dlp
        except ImportError as exc:
            raise PipelineError(
                "未安装 yt-dlp，无法下载网络视频。请在命令行执行："
                "pip install -U yt-dlp"
            ) from exc

    def _ydl_common_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "noprogress": True,
            "retries": 2,
            "fragment_retries": 3,
            "socket_timeout": 15,   # 短超时：国外站连不上时 15s 内给反馈，别让用户干等
            "ignoreerrors": False,
        }
        if self.cookies_from_browser:
            # 直接从本机浏览器读 cookie（比手动 cookies.txt 更省事；浏览器需已登录对应视频站）
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        elif self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        if self.ffmpeg:
            # 关键：yt-dlp 合并分离的音视频流需要 ffmpeg，这里显式告诉它路径（bin/ 目录）
            opts["ffmpeg_location"] = str(Path(self.ffmpeg).parent)
        if self.proxy:
            opts["proxy"] = self.proxy
        return opts

    @staticmethod
    def _is_cookie_error(exc: Exception) -> bool:
        """判断下载失败是不是「缺登录态」类错误（bot 检测 / 412 / 403 / 需登录）。"""
        low = str(exc).lower()
        return any(k in low for k in (
            "not a bot", "sign in", "login", "cookies", "412",
            "precondition failed", "403", "forbidden", "private video",
            "confirms you", "bot",
        ))

    def _probe_cookie(self, yt_dlp, url: str) -> Optional[Tuple[str, str, Any]]:
        """自动探测可用的登录态来源，返回 (来源名, opt_key, opt_val)；全失败返回 None。

        顺序：cookies.txt → firefox → edge → chrome。
        注意：绝不关闭或结束用户的浏览器进程（新版 Chrome/Edge 为 App-Bound 加密，
        强关浏览器也无法解密且会丢失用户工作）。
        """
        candidates: List[Tuple[str, str, Any]] = []
        if self.cookies_file:
            candidates.append(("cookies.txt", "cookiefile", self.cookies_file))
        for browser in ("firefox", "edge", "chrome"):
            candidates.append((browser, "cookiesfrombrowser", (browser,)))

        base = dict(self._ydl_common_opts())
        base.update({"skip_download": True, "socket_timeout": 15, "retries": 1})
        for name, key, val in candidates:
            opts = dict(base)
            opts[key] = val
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=False)
                logger.info("Cookie 来源探测成功：%s", name)
                return name, key, val
            except Exception as exc:  # noqa: BLE001
                logger.info("Cookie 来源 %s 不可用：%s", name, str(exc)[:90])
        return None

    def _apply_cookie(self, key: str, val: Any) -> None:
        """把探测到的 cookie 来源写回实例，让后续 _ydl_common_opts 自动带上。"""
        if key == "cookiefile":
            self.cookies_file = val
            self.cookies_from_browser = None
        else:
            self.cookies_from_browser = val[0] if isinstance(val, (tuple, list)) else str(val)

    @staticmethod
    def _cookie_guidance() -> str:
        """所有授权来源都试过仍失败时，给出**真正可行**的引导。

        注意：新版 Chrome / Edge 的 cookie 是 App-Bound 加密（yt-dlp issue #10927），
        关掉浏览器也读不出来，所以不能再让用户去「关浏览器」——那是死路。
        可行路径只有两条：B站扫码登录（本应用内），或 Firefox / 手动 cookies.txt。
        """
        return (
            "这个视频需要登录才能下载。最快的办法：在输入框贴上链接后点「提取并翻译」，"
            "遇到 B站会自动弹出二维码，用手机 B站 App 扫一下即可，之后永久生效。"
            "（Chrome / Edge 的登录数据被系统加密保护，程序读不出来，请在应用内扫码或改用 Firefox。）"
        )

    def _resolve_sub_lang(
        self, available: Dict[str, Any], source_lang: str,
    ) -> Optional[str]:
        """在视频自带的字幕轨里挑一条。挑不到就返回 None（绝不瞎猜）。"""
        if not available:
            return None
        keys = [k for k in available.keys() if k]
        if not keys:
            return None

        want = self._normalize_lang(source_lang)
        if want:
            for k in keys:
                if k.lower() == want:
                    return k
            for k in keys:
                if k.lower().startswith(want + "-"):
                    return k

        for pref in PREFERRED_SUB_LANGS:
            for k in keys:
                if k.lower() == pref.lower():
                    return k
        for pref in PREFERRED_SUB_LANGS:
            for k in keys:
                if k.lower().startswith(pref.lower() + "-"):
                    return k
        return None

    def _prepare_from_url(
        self, url: str, source_lang: str, cb: Optional[ProgressCallback],
    ) -> Dict[str, Any]:
        yt_dlp = self._require_ytdlp()
        emit = self._emitter(cb, 0.0, 100.0)
        emit(0.01, "正在解析视频信息…", force=True)

        # ---- 1) 先拿元信息（标题、时长、有哪些字幕轨）----
        info = None
        try:
            with yt_dlp.YoutubeDL({**self._ydl_common_opts(), "skip_download": True}) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            # 缺登录态 → B站直接秒返触发前端自动弹扫码框
            if self._is_cookie_error(exc):
                if detect_platform(url).get("id") == "bilibili":
                    raise PipelineError("B站登录态已过期（HTTP 412），请重新扫码登录。") from exc
                probe = self._probe_cookie(yt_dlp, url)
                if probe:
                    name, key, val = probe
                    self._apply_cookie(key, val)
                    emit(0.02, f"已自动读取登录态（{name}）…", force=True)
                    try:
                        with yt_dlp.YoutubeDL({**self._ydl_common_opts(), "skip_download": True}) as ydl2:
                            info = ydl2.extract_info(url, download=False)
                    except Exception as exc2:  # noqa: BLE001
                        raise PipelineError(self._translate_download_error(exc2)) from exc2
                else:
                    raise PipelineError(self._cookie_guidance() or self._translate_download_error(exc)) from exc
            else:
                raise PipelineError(self._translate_download_error(exc)) from exc

        if not info:
            raise PipelineError("解析失败：没有拿到视频信息。请确认链接是否有效、是否需要登录。")
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if not entries:
                raise PipelineError("这是一个空的播放列表，没有可下载的视频。")
            info = entries[0]   # 只处理第一个，避免用户误下单集

        title = info.get("title") or info.get("id") or "video"
        duration = info.get("duration") or 0
        base = _safe_name(f"{title}", "video")
        emit(0.05, f"已解析：{title[:40]}，准备下载…", force=True)

        # ---- 2) 挑选原生字幕轨（只认人工字幕，自动字幕默认不要）----
        manual_subs = info.get("subtitles") or {}
        sub_lang = self._resolve_sub_lang(manual_subs, source_lang)
        auto_used = False
        if sub_lang is None and self.allow_auto_sub:
            auto_subs = info.get("automatic_captions") or {}
            sub_lang = self._resolve_sub_lang(auto_subs, source_lang)
            auto_used = sub_lang is not None

        # ---- 3) 下载视频（mp4 优先，尽量拿最高画质）----
        outtmpl = str(self.work_dir / f"{base}.%(ext)s")
        last_emit = {"t": 0.0, "pct": -1.0}

        def hook(d: Dict[str, Any]) -> None:
            status = d.get("status")
            if status == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                got = d.get("downloaded_bytes") or 0
                ratio = (got / total) if total else 0.0
                pct = 5.0 + ratio * 63.0          # 5% ~ 68%
                now = time.monotonic()
                if pct - last_emit["pct"] >= 0.8 or (now - last_emit["t"]) >= 0.5:
                    last_emit["pct"], last_emit["t"] = pct, now
                    mb = got / (1024 ** 2)
                    if total:
                        text = f"正在下载视频… {ratio * 100:.0f}%（{mb:.1f}MB / {total / 1024 ** 2:.1f}MB）"
                    else:
                        text = f"正在下载视频… 已下载 {mb:.1f}MB"
                    self._emit(cb, pct, text)
            elif status == "finished":
                self._emit(cb, 69.0, "下载完成，正在合并音视频…")

        dl_opts: Dict[str, Any] = {
            **self._ydl_common_opts(),
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "progress_hooks": [hook],
            "overwrites": True,
            "postprocessors": [{"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}],
        }

        try:
            with yt_dlp.YoutubeDL(dl_opts) as ydl:
                ydl.download([url])
        except Exception as exc:  # noqa: BLE001
            raise PipelineError(self._translate_download_error(exc)) from exc

        video_path = self._find_downloaded_video(base)
        if not video_path:
            raise PipelineError(
                "视频下载完成但找不到输出文件，可能是平台限制了画质或合并失败。"
                "请稍后重试，或在 ./bin 放 cookies.txt 后再试一次。"
            )
        emit(0.70, "视频已就绪", force=True)

        # ---- 4) 单独抓字幕（失败不影响视频）----
        native_srt: Optional[str] = None
        sub_note = "未找到视频自带的外文字幕，将使用语音识别生成。"
        if sub_lang:
            emit(0.72, f"正在抓取原生字幕（{sub_lang}）…", force=True)
            native_srt = self._download_subtitles(url, base, sub_lang)
            if native_srt:
                sub_note = f"已抓到视频自带字幕（{sub_lang}），可直接编辑，无需重新识别。"
                if auto_used:
                    sub_note = (
                        f"没有人工字幕，已回退使用平台自动生成的字幕（{sub_lang}），"
                        "质量可能一般，建议核对或改用语音识别。"
                    )
            else:
                sub_note = f"该视频有 {sub_lang} 字幕轨，但抓取失败，将改用语音识别。"
        emit(0.78, "准备提取音轨…", force=True)

        # ---- 5) 抽音轨 ----
        audio_path = self.work_dir / f"{base}.16k.wav"
        self._extract_wav(video_path, str(audio_path), cb, lo=78.0, hi=100.0)

        minutes = f"{duration / 60:.1f} 分钟" if duration else "时长未知"
        emit(1.0, "预处理完成", force=True)
        return {
            "video_path": video_path,
            "audio_path": str(audio_path),
            "native_srt_path": native_srt,
            "title": title,
            "message": f"已就绪：{title}（{minutes}）。{sub_note}",
        }

    def _find_downloaded_video(self, base: str) -> Optional[str]:
        """在 work_dir 里找刚下载的成片（yt-dlp 的合并结果可能是 mp4 也可能是 mkv）。"""
        for ext in (".mp4", ".mkv", ".webm", ".mov"):
            cand = self.work_dir / f"{base}{ext}"
            if cand.is_file() and cand.stat().st_size > 10 * 1024:
                return str(cand)
        # 兜底：按前缀模糊找
        for cand in sorted(self.work_dir.glob(f"{base}.*")):
            if cand.suffix.lower() in VIDEO_EXTS and cand.stat().st_size > 10 * 1024:
                return str(cand)
        return None

    def _download_subtitles(self, url: str, base: str, lang: str) -> Optional[str]:
        """字幕单独下一次，失败就返回 None（绝不让它拖垮视频下载）。"""
        try:
            yt_dlp = self._require_ytdlp()
        except PipelineError:
            return None

        opts = {
            **self._ydl_common_opts(),
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": self.allow_auto_sub,
            "subtitleslangs": [lang],
            "subtitlesformat": "srt/best",
            "outtmpl": str(self.work_dir / f"{base}.%(ext)s"),
            "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": "srt"}],
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as exc:  # noqa: BLE001
            logger.warning("字幕抓取失败：%s", exc)

        # 找 srt；只有 vtt 就自己转
        srt_candidates = sorted(self.work_dir.glob(f"{base}.*.srt"))
        if srt_candidates:
            return str(srt_candidates[0])

        for vtt in sorted(self.work_dir.glob(f"{base}.*.vtt")):
            out = vtt.with_suffix(".srt")
            if self._convert_subtitle_to_srt(str(vtt), str(out)):
                return str(out)
        for ass in sorted(self.work_dir.glob(f"{base}.*.ass")):
            out = ass.with_suffix(".srt")
            if self._convert_subtitle_to_srt(str(ass), str(out)):
                return str(out)
        return None

    def _convert_subtitle_to_srt(self, src: str, dst: str) -> bool:
        if not self.ffmpeg:
            return False
        try:
            code, out = self._run(
                [self.ffmpeg, "-hide_banner", "-y", "-loglevel", "error",
                 "-i", src, str(dst)],
                timeout=60,
            )
            if code == 0 and Path(dst).is_file() and Path(dst).stat().st_size > 0:
                return True
            logger.warning("字幕转 srt 失败：%s", out[-300:])
        except Exception as exc:  # noqa: BLE001
            logger.warning("字幕转 srt 异常：%s", exc)
        return False

    @staticmethod
    def _translate_download_error(exc: Exception) -> str:
        """把 yt-dlp 的英文异常翻译成人话。"""
        msg = str(exc)
        low = msg.lower()

        if "not a bot" in low or "sign in to confirm" in low or "confirm you" in low:
            return ("这个视频站要求登录才能下载（YouTube 反爬：Sign in to confirm you're not a bot）。"
                    "请先用扩展导出 cookies.txt 后在「偏好设置 → 通用」一键导入（Windows 新版 Chrome/Edge 启用了系统加密，无法直读）。")
        if "412" in low or "precondition failed" in low:
            return ("下载被 B站风控拒绝（HTTP 412），登录态缺失或已过期。"
                    "请使用应用内「B站扫码登录」重新扫码即可。")
        if "timed out" in low or "timeout" in low:
            return "下载超时：网络不稳定或被墙。请检查代理/VPN 后重试。"
        if "403" in low or "forbidden" in low:
            return ("平台拒绝访问（403），需要有效登录态。"
                    "B站请点击「B站扫码登录」，其他平台请导入 cookies.txt 后重试。")
        if "404" in low:
            return "视频不存在或已被删除（404）。请确认链接是否还有效。"
        if "private" in low:
            return "该视频是私享内容，当前账号无权访问。请用已关注的账号登录后重试。"
        if "login" in low or "sign in" in low or "requires authentication" in low:
            return "该视频需要登录才能下载。请设置 Cookie 来源（浏览器或 cookies.txt）后重试。"
        if "geo" in low or "not available in your country" in low:
            return "该视频有地区限制，当前网络环境无法访问，需要相应地区的代理。"
        if "unsupported url" in low or "no suitable" in low:
            return "暂不支持这个链接。请确认是单个视频页链接，而不是主页/合集/直播。"
        if "ffmpeg" in low:
            return ("下载需要合并音视频，但 ffmpeg 调用失败。"
                    "请确认 ./bin/ffmpeg.exe 可用（可双击或在命令行执行 ffmpeg -version 验证）。")
        if "unable to download" in low or "urlopen error" in low or "connection" in low:
            return f"网络连接失败，无法下载。原始信息：{msg[:180]}"
        return f"视频下载失败：{msg[:220]}"

    # ------------------------------------------------------------------ #
    # 2) 语音识别
    # ------------------------------------------------------------------ #

    def _normalize_lang(self, source_lang: Optional[str]) -> Optional[str]:
        """'auto'/''/None → None；'zh-CN' → 'zh'。"""
        if source_lang is None:
            return None
        s = str(source_lang).strip().lower().replace("_", "-")
        if s in ("", "auto", "none", "null", "automatic", "detect"):
            return None
        s = _LANG_ALIAS.get(s, s)
        s = s.split("-")[0]
        if not s:
            return None
        if not re.fullmatch(r"[a-z]{2,3}", s):
            raise PipelineError(
                f"不支持的语言代码「{source_lang}」。请用标准代码，例如 "
                "en / ja / ko / de / fr / es / ru，或选「自动识别」。"
            )
        return s

    #: 各型号 Whisper 模型 model.bin 最低合法体积（字节），用于拦截 3.6MB 残包
    _MODEL_MIN_BYTES: Dict[str, int] = {
        "tiny": 30 * 1024 * 1024,
        "tiny.en": 30 * 1024 * 1024,
        "base": 60 * 1024 * 1024,
        "base.en": 60 * 1024 * 1024,
        "small": 180 * 1024 * 1024,
        "small.en": 180 * 1024 * 1024,
        "medium": 600 * 1024 * 1024,
        "medium.en": 600 * 1024 * 1024,
        "large-v3-turbo": 700 * 1024 * 1024,
        "turbo": 700 * 1024 * 1024,
        "large-v3": 1200 * 1024 * 1024,
        "large-v2": 1200 * 1024 * 1024,
        "large": 1200 * 1024 * 1024,
    }

    _MODEL_REPO_HINTS: Dict[str, Tuple[str, ...]] = {
        "tiny": ("models--Systran--faster-whisper-tiny",),
        "tiny.en": ("models--Systran--faster-whisper-tiny.en",),
        "base": ("models--Systran--faster-whisper-base",),
        "base.en": ("models--Systran--faster-whisper-base.en",),
        "small": ("models--Systran--faster-whisper-small",),
        "small.en": ("models--Systran--faster-whisper-small.en",),
        "medium": ("models--Systran--faster-whisper-medium",),
        "medium.en": ("models--Systran--faster-whisper-medium.en",),
        "large-v3-turbo": (
            "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
            "models--Systran--faster-whisper-large-v3-turbo",
        ),
        "turbo": (
            "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo",
            "models--Systran--faster-whisper-large-v3-turbo",
        ),
        "large-v3": ("models--Systran--faster-whisper-large-v3",),
        "large-v2": ("models--Systran--faster-whisper-large-v2",),
    }

    @classmethod
    def _inspect_model_in_root(cls, model_size: str, root: Path) -> Dict[str, Any]:
        """校验指定缓存根目录下的 Whisper 模型是否完整（排除只有 tokenizer/残包的假缓存）。"""
        min_bytes = cls._MODEL_MIN_BYTES.get(model_size, 30 * 1024 * 1024)
        hints = cls._MODEL_REPO_HINTS.get(model_size, ())
        matched_dirs: List[Path] = []
        if root.is_dir():
            for hint in hints:
                cand = root / hint
                if cand.is_dir():
                    matched_dirs.append(cand)
            if not matched_dirs:
                for cand in root.glob(f"models--*--*{model_size}*"):
                    if cand.is_dir():
                        matched_dirs.append(cand)
        for repo_dir in matched_dirs:
            snaps = repo_dir / "snapshots"
            if not snaps.is_dir():
                return {"complete": False, "corrupt": True, "repo_dir": repo_dir, "snapshot_dir": None, "model_bytes": 0}
            for snap in sorted(snaps.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
                if not snap.is_dir():
                    continue
                model_bin = snap / "model.bin"
                cfg = snap / "config.json"
                if model_bin.is_file() and cfg.is_file():
                    try:
                        sz = model_bin.resolve().stat().st_size
                    except OSError:
                        sz = 0
                    if sz >= min_bytes:
                        return {
                            "complete": True, "corrupt": False,
                            "repo_dir": repo_dir, "snapshot_dir": snap, "model_bytes": sz,
                        }
            return {"complete": False, "corrupt": True, "repo_dir": repo_dir, "snapshot_dir": None, "model_bytes": 0}
        return {"complete": False, "corrupt": False, "repo_dir": None, "snapshot_dir": None, "model_bytes": 0}

    def get_model_status(self) -> Dict[str, Any]:
        """返回各主要 Whisper 模型的本地缓存完整状态及推荐默认模型。"""
        tiers = ("large-v3-turbo", "medium", "small", "tiny")
        models: Dict[str, Dict[str, Any]] = {}
        search_roots = [self.model_dir] + list(getattr(self, "readonly_model_dirs", []))
        for tier in tiers:
            found_info = None
            corrupt_seen = False
            for r in search_roots:
                st = self._inspect_model_in_root(tier, r)
                if st["complete"]:
                    found_info = {
                        "cached": True,
                        "corrupt": False,
                        "size_mb": round(st["model_bytes"] / (1024 * 1024), 1),
                        "source": "local" if r == self.model_dir else "readonly_cache",
                    }
                    break
                if st["corrupt"]:
                    corrupt_seen = True
            models[tier] = found_info or {"cached": False, "corrupt": corrupt_seen, "size_mb": 0.0, "source": "remote"}
        # 无任何本地缓存时必须推荐 small（约 460MB）；large-v3-turbo 仅在已完整缓存时自动推荐
        recommended = "large-v3-turbo" if models["large-v3-turbo"]["cached"] else (
            "small" if models["small"]["cached"] else "small"
        )
        return {"status": "success", "models": models, "recommended": recommended}

    def _get_model(self, model_size: str):
        """加载并缓存模型；支持只读复用原项目完整缓存、拦截残包并自动走 hf-mirror 下载。"""
        key = (model_size, self.device, self.compute_type)
        if self._model is not None and self._model_key == key:
            return self._model
        if self._model is not None:
            self.release_model()

        # 用 CUDA 前，先把 nvidia-*-cu12 轮子里的 DLL 目录注入搜索路径，
        # 否则 ctranslate2 会报 "Library cublas64_12.dll is not found"。
        if self.device == "cuda":
            self._add_cuda_runtime_dirs()

        # Windows 下 huggingface_hub 缓存的 symlink 告警无害但刷屏，静音掉
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as exc:
            raise PipelineError(
                "未安装 faster-whisper，无法进行语音识别。请在命令行执行："
                "pip install faster-whisper"
            ) from exc

        try:
            self.model_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        # 1) 检查自有可写 model_dir 中的残包（绝不修改 readonly_model_dirs！）
        own_status = self._inspect_model_in_root(model_size, self.model_dir)
        if own_status["corrupt"] and own_status["repo_dir"] is not None:
            logger.warning("检测到不完整的本地模型残包，自动清理后重新拉取：%s", own_status["repo_dir"])
            try:
                shutil.rmtree(own_status["repo_dir"], ignore_errors=True)
            except OSError:
                pass
            own_status = self._inspect_model_in_root(model_size, self.model_dir)

        # 2) 如果自有目录未缓存，优先只读加载原项目已完整缓存的 snapshot（绝不向原缓存目录写入！）
        if not own_status["complete"]:
            for ro_root in getattr(self, "readonly_model_dirs", []):
                ro_status = self._inspect_model_in_root(model_size, ro_root)
                if ro_status["complete"] and ro_status["snapshot_dir"] is not None:
                    logger.info("只读复用已验证完整的本地模型缓存：%s", ro_status["snapshot_dir"])
                    try:
                        model = WhisperModel(
                            str(ro_status["snapshot_dir"]),
                            device=self.device,
                            compute_type=self.compute_type,
                            local_files_only=True,
                            cpu_threads=0,
                            num_workers=1,
                        )
                        self._model = model
                        self._model_key = key
                        return model
                    except Exception as exc:  # noqa: BLE001
                        raise PipelineError(self._map_model_error(exc, model_size)) from exc
                elif ro_status["corrupt"]:
                    logger.warning("只读缓存目录中的模型 %s 不完整（残包），将改到用户数据目录重新下载", model_size)

        # 3) 选择下载端点。None 表示官方 huggingface.co 直连，默认优先 hf-mirror.com。
        user_endpoint = os.environ.get("HF_ENDPOINT", "").strip()
        if user_endpoint:
            endpoints: List[Optional[str]] = [user_endpoint]   # 用户显式代理/镜像优先，绝不覆盖
        else:
            resolved = getattr(self, "_hf_endpoint_resolved", None)
            if resolved:
                endpoints = [resolved, None]
            else:
                endpoints = [HF_MIRROR_ENDPOINT, None]

        last_exc: Optional[Exception] = None
        for ep in endpoints:
            if ep:
                os.environ["HF_ENDPOINT"] = ep
            else:
                os.environ.pop("HF_ENDPOINT", None)
            try:
                model = WhisperModel(
                    model_size,
                    device=self.device,
                    compute_type=self.compute_type,
                    download_root=str(self.model_dir),
                    cpu_threads=0,
                    num_workers=1,
                )
                self._hf_endpoint_resolved = ep
                self._model = model
                self._model_key = key
                return model
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not self._is_connect_error(exc):
                    break
                logger.warning("模型下载走 %s 失败，尝试下一个端点：%s", ep or "官方", exc)
                continue

        raise PipelineError(self._map_model_error(last_exc, model_size)) from last_exc

    @staticmethod
    def _is_connect_error(exc: Exception) -> bool:
        """判断是不是「连不上模型仓库」类的网络错误（决定要不要换镜像）。"""
        low = str(exc).lower()
        return any(k in low for k in (
            "connecttimeout", "connecterror", "connection error", "timed out",
            "timedout", "winerror 10060", "winerror 10061", "10060", "10061",
            "getaddrinfo", "name or service not known", "nodename nor servname",
            "temporary failure in name resolution", "connection reset",
            "connection refused", "connectionrefused", "proxyerror", "failed to establish",
        ))

    def _map_model_error(self, exc: Optional[Exception], model_size: str) -> str:
        if exc is None:
            return f"语音模型 {model_size} 加载失败（未知原因）。"
        msg = str(exc)
        low = msg.lower()
        if any(k in low for k in ("cudnn", "cublas", "cudart", "nvrtc", "curand", "cuda driver", "no kernel image")):
            return (f"GPU 运行库（CUDA/cuDNN）异常，无法在显卡上加载 {model_size}（详情：{msg[:160]}）。"
                    "请检查 NVIDIA CUDA 12 / cuDNN 9 运行库，或切换为 CPU 模式。")
        if self._looks_like_oom(exc):
            return (f"显存不足，无法在 GPU 上加载 {model_size}（详情：{msg[:140]}）。"
                    "请关闭占用显存的程序，或切到 small / CPU 模式重试。")
        if self._is_connect_error(exc):
            return (f"模型 {model_size} 下载失败：无法访问模型仓库（已尝试 hf-mirror.com 与官方源）。"
                    "请检查网络或代理，或切换到已缓存的 small 模型。")
        if "model.bin" in low or "corrupt" in low or "invalid" in low and "model" in low:
            return f"模型 {model_size} 缓存文件不完整或已损坏（详情：{msg[:140]}），请重试以重新下载。"
        if "not found" in low or "no such file" in low:
            return f"找不到模型 {model_size} 文件（详情：{msg[:140]}），且自动下载失败。请检查网络。"
        return f"语音模型加载失败（{type(exc).__name__}）：{msg[:200]}"

    @staticmethod
    def _looks_like_cuda_error(exc: Exception) -> bool:
        low = str(exc).lower()
        return any(k in low for k in (
            "cudnn", "cublas", "cudart", "nvrtc", "curand",
            "cuda failed", "cuda error", "no kernel image", "device-side", "invalid device",
        ))

    @staticmethod
    def _looks_like_oom(exc: Exception) -> bool:
        """只匹配明确的 CUDA/系统 OOM，绝不把任意含 memory/alloc 的文件解析报错误判成显存不足。"""
        low = str(exc).lower()
        return (
            "out of memory" in low
            or "cudaerroroutofmemory" in low
            or "cublas_status_alloc_failed" in low
            or "std::bad_alloc" in low
            or isinstance(exc, MemoryError)
        )

    def _degrade_to_cpu(self, reason: str) -> None:
        self.accel_degraded = True
        self._degrade_reason = reason
        self.device = "cpu"
        self.compute_type = "int8"
        self._model = None
        self._model_key = None
        try:
            import gc
            gc.collect()
        except Exception:
            pass
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
        logger.warning("GPU 不可用，已自动降级 CPU：%s", reason)

    # ---- 分句规整 ----

    @staticmethod
    def _pick_pieces(text: str) -> List[str]:
        """按标点切成小块（不丢字符）。"""
        pieces: List[str] = []
        buf = ""
        for ch in text:
            buf += ch
            if ch in "。！？!?…；;，,、" or ch == ".":
                if buf.strip():
                    pieces.append(buf.strip())
                buf = ""
        if buf.strip():
            pieces.append(buf.strip())
        return pieces or [text.strip()]

    @staticmethod
    def _smart_join(left: str, right: str) -> str:
        """拼接相邻片段：只有两侧都是拉丁字母/数字时才补空格，中日文直接连。"""
        if not left:
            return right
        if not right:
            return left
        a, b = left[-1], right[0]
        if a.isascii() and b.isascii() and (a.isalnum() or a in ",.;:!?)]}\"'") and (b.isalnum() or b in "([{\"'"):
            return f"{left} {right}"
        return f"{left}{right}"

    @staticmethod
    def _pack_tokens(tokens: List[str], k: int) -> List[str]:
        """把 token 序列切成 k 组：按累计宽度找切点，组内均衡，且保证每组非空。"""
        n = len(tokens)
        if n == 0:
            return []
        k = max(1, min(int(k), n))
        if k == 1:
            return ["".join(tokens).strip()]

        widths = [0 if t.isspace() else max(1, _disp_len(t)) for t in tokens]
        total = sum(widths) or 1
        cum: List[int] = []
        acc = 0
        for w in widths:
            acc += w
            cum.append(acc)

        starts = [0]
        for g in range(1, k):
            want = total * g / k
            upper = n - (k - g)                     # 给后面每组至少留 1 个 token
            cut = upper
            for i in range(g, upper + 1):
                if cum[i - 1] >= want:
                    cut = i
                    break
            cut = max(g, min(cut, upper))
            starts.append(cut)
        starts.append(n)

        groups = ["".join(tokens[a:b]).strip() for a, b in zip(starts, starts[1:], strict=False)]
        return [g for g in groups if g] or ["".join(tokens).strip()]

    @classmethod
    def _hard_chunk(cls, text: str, k: int) -> List[str]:
        """把一段话均分成 k 块：优先在标点处断，其次在词/字边界断。"""
        k = max(1, int(k))
        if k == 1 or not text.strip():
            return [text.strip()]

        # 1) 优先按语义子句断（中文标点 + 拉丁标点的句末）
        clauses = re.findall(r"[^。！？!?…；;，,、：:]+[。！？!?…；;，,、：:]?", text)
        if len(clauses) >= k:
            packed = cls._pack_tokens(clauses, k)
            # 完整性校验：拼回去必须和原文一致（不能丢字），否则走下面的词级切分
            if packed and "".join(packed).replace(" ", "") == text.replace(" ", ""):
                return packed

        # 2) 退化到词/字级别
        tokens = re.findall(r"\s+|[A-Za-z0-9'’]+|[^\sA-Za-z0-9'’]", text)
        if len(tokens) < k * 2:
            # 字符太少，直接按字符数均分
            chars = [ch for ch in text if not ch.isspace()] or [text]
            step = max(1, math.ceil(len(chars) / k))
            return ["".join(chars[i:i + step]) for i in range(0, len(chars), step)][:k] or [text]
        return cls._pack_tokens(tokens, k)

    # 旧版 VAD 裁剪/静音拼接和频谱 K-Means 说话人猜测已从主链路移除。
    # 识别统一使用原始 WAV 的绝对时间戳；说话人没有可靠 diarization 时保持单人标签。

    @classmethod
    def _split_segment(cls, start: float, end: float, text: str,
                       words: Optional[List[Any]] = None) -> List[Tuple[float, float, str]]:
        """把过长的 Whisper 片段切成适合压字幕的多条短句。

        有词级时间戳时，每条的起止来自「真实开口/收声」时刻；
        没有时退回按字符宽度等比分配。
        """
        text = " ".join(text.split())
        if not text:
            return []
        dur = max(end - start, MIN_SEG_DUR)
        width = _disp_len(text)

        if dur <= MAX_SEG_DUR and width <= MAX_SEG_WIDTH:
            return [(round(start, 3), round(end, 3), text)]

        # 1) 先按标点切，再按宽度装箱
        packed: List[str] = []
        for piece in cls._pick_pieces(text):
            if packed and _disp_len(packed[-1]) + 1 + _disp_len(piece) <= MAX_SEG_WIDTH:
                packed[-1] = cls._smart_join(packed[-1], piece)
            else:
                packed.append(piece)

        # 2) 每块都不超宽；超宽的硬切
        final: List[str] = []
        for piece in packed:
            if _disp_len(piece) <= MAX_SEG_WIDTH:
                final.append(piece)
            else:
                n = max(1, round(_disp_len(piece) / MAX_SEG_WIDTH + 0.49))
                final.extend(cls._hard_chunk(piece, n))

        # 3) 时长同样要满足：单条不超过 MAX_SEG_DUR（留 15% 余量），过碎则合并
        need = max(
            1,
            math.ceil(dur / (MAX_SEG_DUR * 0.85)),
            math.ceil(width / MAX_SEG_WIDTH),
        )
        max_parts = max(1, int(dur / 1.0))      # 每条至少 1 秒
        if len(final) > max_parts:
            final = cls._hard_chunk(cls._join_pieces(final), max_parts)
        elif len(final) < need:
            final = cls._hard_chunk(cls._join_pieces(final), need)

        # 4) 分配时间：优先用词级时间戳（真实开口/收声），否则等比
        out = None
        if words:
            out = cls._reflow_words(start, end, final, words, text)
        if not out:
            out = cls._reflow(start, end, final)

        # 5) 兜底：仍然超时的再对半切（词级时间不参与，避免二次错位）
        if not words:
            for _ in range(4):
                if not any((b - a) > MAX_SEG_DUR + 1e-6 and _disp_len(txt) > 4
                           for a, b, txt in out):
                    break
                refined: List[Tuple[float, float, str]] = []
                for a, b, txt in out:
                    if (b - a) > MAX_SEG_DUR + 1e-6 and _disp_len(txt) > 4:
                        halves = cls._hard_chunk(txt, 2)
                        if len(halves) >= 2:
                            refined.extend(cls._reflow(a, b, halves))
                            continue
                    refined.append((a, b, txt))
                out = refined
        return out

    @staticmethod
    def _reflow_words(
        start: float, end: float, pieces: List[str],
        words: List[Any], text: str,
    ) -> Optional[List[Tuple[float, float, str]]]:
        """把词级时间戳映射到每个文本片段上，返回真实起止。

        这是字幕时间轴准不准的关键：不再按字符数等比切时间，
        而是找每个片段实际覆盖到的那几个词，用它们真实的开口/收声时刻。
        """
        # 1) 词 -> 字符区间 + 真实时间
        spans: List[Tuple[int, int, float, float]] = []
        cursor = 0
        for w in words:
            raw = getattr(w, "word", None)
            wt = " ".join(str(raw or "").split())
            if not wt:
                continue
            idx = text.find(wt, cursor)
            if idx < 0:
                idx = text.find(wt)
            if idx < 0:
                continue
            ws = getattr(w, "start", None)
            we = getattr(w, "end", None)
            if ws is None or we is None:
                continue
            spans.append((idx, idx + len(wt), float(ws), float(we)))
            cursor = idx + len(wt)
        if not spans:
            return None

        # 2) 每个片段覆盖的词 → 真实起止
        got: List[Optional[Tuple[float, float, str]]] = []
        search_from = 0
        for piece in pieces:
            p = piece.strip()
            if not p:
                continue
            pos = text.find(p, search_from)
            if pos < 0:
                pos = text.find(p)
            if pos < 0:
                got.append(None)
                continue
            p_end = pos + len(p)
            search_from = p_end
            hit = [s for s in spans if s[1] > pos and s[0] < p_end]
            if not hit:
                got.append(None)
                continue
            got.append((min(s[2] for s in hit), max(s[3] for s in hit), piece))

        if not any(g for g in got):
            return None

        # 3) 补齐没匹配上的片段：夹在前后已知边界之间
        last_end = start
        for i, g in enumerate(got):
            if g is None:
                continue
            a = max(last_end, g[0])
            b = max(a + 0.20, g[1])
            got[i] = (a, b, g[2])
            last_end = b

        out: List[Tuple[float, float, str]] = []
        prev_end = start
        for i, g in enumerate(got):
            if g is None:
                # 找下一个已知起点
                nxt = None
                for j in range(i + 1, len(got)):
                    if got[j] is not None:
                        nxt = got[j][0]
                        break
                a = prev_end
                b = (nxt - 0.05) if nxt is not None else end
                piece = pieces[i] if i < len(pieces) else ""
                if not piece.strip():
                    continue
                b = max(a + 0.20, b)
                out.append((round(a, 3), round(b, 3), piece))
                prev_end = b
                continue
            a = max(prev_end, g[0])
            b = max(a + 0.20, g[1])
            out.append((round(a, 3), round(b, 3), g[2]))
            prev_end = b
        if not out:
            return None

        # 4) 超长条（>MAX_SEG_DUR）按自身词边界再切一次，避免一条挂太久
        final: List[Tuple[float, float, str]] = []
        for a, b, piece in out:
            # 用显示宽度判断（中日韩没有空格，不能靠 word count）
            if (b - a) <= MAX_SEG_DUR + 1e-6 or _disp_len(piece) < 8:
                final.append((a, b, piece))
                continue
            n = max(2, int(math.ceil((b - a) / MAX_SEG_DUR)))
            cap = max(2, _disp_len(piece) // 6)   # 每段至少 6 个显示宽度
            n = min(n, cap)
            halves = [h for h in MediaProcessor._hard_chunk(piece, n) if h.strip()]
            if len(halves) < 2:
                final.append((a, b, piece))
                continue
            sub = MediaProcessor._reflow_words(a, b, halves, words, piece)
            if sub:
                final.extend(sub)
            else:
                final.extend(MediaProcessor._reflow(a, b, halves))
        return final or None

    @staticmethod
    def _reflow(start: float, end: float, pieces: List[str]) -> List[Tuple[float, float, str]]:
        """把一串文本片段铺满 [start, end]，时间按可见宽度等比分配。"""
        pieces = [p for p in pieces if p and p.strip()]
        if not pieces:
            return []
        if end <= start:
            end = start + MIN_SEG_DUR
        span = max(end - start, MIN_SEG_DUR)
        total_w = sum(_disp_len(p) for p in pieces) or 1

        out: List[Tuple[float, float, str]] = []
        t = start
        for idx, piece in enumerate(pieces):
            share = span * _disp_len(piece) / total_w
            seg_start = t
            seg_end = (start + span) if idx == len(pieces) - 1 else t + share
            if _disp_len(piece) >= 2:
                out.append((
                    round(seg_start, 3),
                    round(max(seg_end, seg_start + 0.2), 3),
                    piece,
                ))
            t = seg_end
        return out

    @staticmethod
    def _join_pieces(pieces: List[str]) -> str:
        """重新拼接片段：中日韩不加空格，拉丁语系补一个空格。"""
        if not pieces:
            return ""
        if len(pieces) == 1:
            return pieces[0]
        if any(" " in p for p in pieces):
            return " ".join(pieces)
        sample = pieces[0]
        cjk = sum(1 for ch in sample if unicodedata.east_asian_width(ch) in ("W", "F"))
        return "".join(pieces) if cjk * 2 > len(sample) else " ".join(pieces)

    def transcribe_audio(
        self,
        audio_path: str,
        source_lang: str = "auto",
        model_size: str = "large-v3-turbo",
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        """Whisper 识别 → 规整成可直接压字幕的 segments。

        返回::

            {"status": "success"|"error", "detected_lang": "en",
             "segments": [{"id": 1, "start": 0.0, "end": 2.5, "src_text": "..."}],
             "message": 中文提示}
        """
        result: Dict[str, Any] = {
            "status": "error",
            "detected_lang": "",
            "segments": [],
            "message": "",
        }

        try:
            lang = self._normalize_lang(source_lang)
            model_size = (model_size or "large-v3-turbo").strip()
            if model_size not in WHISPER_MODELS:
                raise PipelineError(
                    f"不支持的模型规格「{model_size}」。可选：large-v3-turbo（推荐）、"
                    "large-v3、medium、small、base、tiny。"
                )

            audio = Path(audio_path)
            if not audio.is_absolute():
                audio = Path.cwd() / audio
            if not audio.is_file():
                raise PipelineError(f"找不到音频文件：{audio_path}，请先执行音视频预处理。")
            if audio.stat().st_size < 1024:
                raise PipelineError("音频文件为空或已损坏，请重新提取音轨。")

            emit = self._emitter(progress_callback, 0.0, 100.0)
            emit(0.0, "正在加载语音模型（首次使用需下载，约 1.5GB，请耐心等待）…", force=True)

            segments, detected, note = self._run_transcription(
                str(audio), lang, model_size, emit, progress_callback,
            )
        except PipelineError as exc:
            result["message"] = str(exc)
            logger.warning("transcribe_audio 失败：%s", exc)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.exception("transcribe_audio 未预期异常")
            result["message"] = (
                f"语音识别失败（{type(exc).__name__}）。"
                "请先确认音频能正常播放，或换一个小一号的模型再试。"
            )
            return result

        if not segments:
            result["status"] = "success"
            result["detected_lang"] = detected
            result["message"] = "识别完成，但没有检测到有效语音（可能是纯音乐/静音）。"
            return result

        lang_name = LANG_NAMES.get(detected, detected or "未知")
        result.update({
            "status": "success",
            "detected_lang": detected,
            "segments": segments,
            "message": f"识别完成，共 {len(segments)} 条字幕（语言：{lang_name}）。{note}",
        })
        return result

    _HANGING_EN_WORDS = {
        "a", "an", "the", "to", "of", "in", "on", "at", "for", "with",
        "and", "or", "but", "as", "by", "from", "into", "about", "that",
    }

    @staticmethod
    def _is_hallucination_loop(text: str) -> bool:
        """过滤 Whisper 在长静音/BGM 中常见的单字或短节拍无限重复幻觉（如 あ〜〜〜〜〜）。"""
        compact = re.sub(r"\s+", "", str(text or ""))
        if len(compact) < 12:
            return False
        if re.search(r"(.)\1{9,}", compact):
            return True
        if re.search(r"(.{1,3})\1{6,}", compact):
            return True
        return False

    @staticmethod
    def _load_pcm16_wav(audio_path: str) -> Optional[Tuple[Any, int]]:
        """读取 16kHz 单声道 PCM WAV 为 float32 numpy 数组，供只读 Silero VAD 使用。"""
        p = Path(audio_path)
        if not p.is_file() or p.suffix.lower() != ".wav":
            return None
        try:
            import wave
            import numpy as np
            with wave.open(str(p), "rb") as wf:
                if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                    return None
                sr = int(wf.getframerate())
                if sr <= 0:
                    return None
                raw = wf.readframes(wf.getnframes())
            if not raw:
                return None
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            return audio, sr
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取 PCM16 WAV 失败，回退直接传路径给 Whisper：%s", exc)
            return None

    @staticmethod
    def _build_independent_vad_windows(
        speech_spans: List[Dict[str, int]],
        sr: int,
        total_samples: int,
        merge_gap_s: float = 12.0,
        pad_s: float = 0.35,
        max_window_s: float = 180.0,
    ) -> List[Tuple[int, int]]:
        """把 Silero VAD 区间合并为互不重叠的连续原音频窗口（绝不跨静音拼接音频）。

        仅在超长静音（>12s，如无声片头/片尾曲/长转场）处切分窗口，保留完整连续对话场景的
        30 秒级声学上下文，彻底避免 1~3 秒碎窗导致的日语/多语种同音误识与幻觉。
        """
        if not speech_spans:
            return [(0, total_samples)]
        groups: List[List[Dict[str, int]]] = []
        for sp in speech_spans:
            if groups:
                gap_s = (sp["start"] - groups[-1][-1]["end"]) / sr
                span_s = (sp["end"] - groups[-1][0]["start"]) / sr
                if gap_s <= merge_gap_s and span_s <= max_window_s:
                    groups[-1].append(sp)
                    continue
            groups.append([sp])

        pad = int(pad_s * sr)
        windows: List[Tuple[int, int]] = []
        for i, grp in enumerate(groups):
            ws = max(0, grp[0]["start"] - pad)
            we = min(total_samples, grp[-1]["end"] + pad)
            if i > 0:
                prev_vad_e = groups[i - 1][-1]["end"]
                mid = (prev_vad_e + grp[0]["start"]) // 2
                ws = max(ws, mid)
            if i + 1 < len(groups):
                next_vad_s = groups[i + 1][0]["start"]
                mid = (grp[-1]["end"] + next_vad_s) // 2
                we = min(we, mid)
            if we > ws + int(0.1 * sr):
                windows.append((ws, we))
        return windows or [(0, total_samples)]

    @staticmethod
    def _align_segments_to_vad_bounds(
        segments: List[Dict[str, Any]],
        vad_intervals: List[Tuple[float, float]],
    ) -> List[Dict[str, Any]]:
        """仅针对位于真实语音边界的字幕，依据原始 WAV 的 Silero VAD 区间收紧静音漂移与过早截断。

        - 不加固定 HOLD_AFTER / MIN_DISPLAY 人为滞留；
        - 仅匹配与该条字幕真实重叠（>0.04s）的 VAD 区间，绝不跨句借用邻近说话人区间；
        - 若 Whisper 把窗口首词拉进前导静音，向前收紧到真实开口；
        - 若 Whisper 在说话人尾音未落时提前截断（如日语 ございます/ましょう），且该 VAD 语音段后无下一句抢占，
          则贴合到真实语音收声处（最多修正 1.0s，绝不越过下一句开口）。
        """
        if not segments or not vad_intervals:
            return segments

        n = len(segments)
        for idx, seg in enumerate(segments):
            a = float(seg["start"])
            b = float(seg["end"])
            prev_end = float(segments[idx - 1]["end"]) if idx > 0 else -10.0
            next_start = float(segments[idx + 1]["start"]) if idx + 1 < n else 1e12

            overlapping = [
                iv for iv in vad_intervals
                if (min(iv[1], b) - max(iv[0], a)) > 0.04
                or (iv[0] <= (a + b) * 0.5 <= iv[1])
            ]
            if not overlapping:
                continue

            first_vs, first_ve = overlapping[0]
            last_vs, last_ve = overlapping[-1]

            # 本条是该 VAD 语音段内的首条（上一条在 first_vs 之前已结束）
            is_first_in_vad = prev_end <= first_vs + 0.04
            # 本条是该 VAD 语音段内的尾条（下一条在 last_ve 之后才开始）
            is_last_in_vad = next_start >= last_ve - 0.04

            if is_first_in_vad:
                # 消除 Whisper 将窗口首词拉入前导静音导致的“提前闪现”
                if a < first_vs - 0.04 and first_vs < b - 0.06:
                    a = first_vs
                # 若开口稍晚于真实发声起音（<=0.12s），轻微前靠至真实开口
                elif 0.03 < (a - first_vs) <= 0.12:
                    a = max(prev_end if idx > 0 else 0.0, first_vs)

            if is_last_in_vad:
                # 消除 Whisper 尾音提前关闭（话未说完字幕先灭）
                if b < last_ve - 0.04 and (last_ve - b) <= 1.0:
                    b = min(last_ve, next_start - 0.03) if idx + 1 < n else last_ve
                # 消除 Whisper 尾词拉入尾随静音导致的“说完仍滞留”
                elif b > last_ve + 0.08 and last_ve > a + 0.08:
                    b = last_ve + 0.03

            if b > a + 0.04:
                seg["start"] = round(max(0.0, a), 3)
                seg["end"] = round(b, 3)

        # 最终保证严格单调无重叠
        for idx in range(1, len(segments)):
            if segments[idx]["start"] < segments[idx - 1]["end"]:
                segments[idx]["start"] = segments[idx - 1]["end"]
                if segments[idx]["end"] <= segments[idx]["start"] + 0.04:
                    segments[idx]["end"] = round(segments[idx]["start"] + 0.05, 3)
        return segments

    @classmethod
    def _segments_from_word_timestamps(
        cls,
        raw_segments: Any,
        duration: float = 0.0,
        offset_s: float = 0.0,
        window_id: int = 0,
        source_seg_offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """只使用 Whisper 原始时间戳分组（+精确窗口 offset）；保留零时长助词/标点，不按字符比例伪造时间。"""
        # 每个 word 元组：(start, end, token, source_segment_id)
        words: List[Tuple[float, float, str, int]] = []
        fallback: List[Tuple[float, float, str, int]] = []
        for seg_idx, seg in enumerate(raw_segments, start=1):
            src_seg_id = source_seg_offset + seg_idx
            seg_text = re.sub(r"\s+", " ", str(getattr(seg, "text", "") or "")).strip()
            if not seg_text or cls._is_hallucination_loop(seg_text):
                continue
            nsp = float(getattr(seg, "no_speech_prob", 0.0) or 0.0)
            alp = float(getattr(seg, "avg_logprob", 0.0) or 0.0)
            if nsp > 0.94 and alp < -1.3:
                continue

            seg_a = float(getattr(seg, "start", 0.0) or 0.0) + offset_s
            seg_b = float(getattr(seg, "end", seg_a - offset_s) or 0.0) + offset_s
            if duration > 0:
                seg_a = max(0.0, min(duration, seg_a))
                seg_b = max(seg_a, min(duration, seg_b))

            raw_w = list(getattr(seg, "words", None) or [])
            seg_words: List[List[Any]] = []
            for w in raw_w:
                token = str(getattr(w, "word", "") or "")
                ws, we = getattr(w, "start", None), getattr(w, "end", None)
                if not token.strip() or ws is None or we is None:
                    continue
                a, b = float(ws) + offset_s, float(we) + offset_s
                if not math.isfinite(a) or not math.isfinite(b):
                    continue
                if duration > 0:
                    a = max(0.0, min(duration, a))
                    b = max(0.0, min(duration, b))
                if b < a:
                    b = a
                seg_words.append([a, b, token, src_seg_id])

            # 关键修复：绝不丢弃 start==end 的零时长词（日语助词/标点经常为 0ms）
            for wi, sw in enumerate(seg_words):
                if sw[1] <= sw[0]:
                    nxt_a = seg_words[wi + 1][0] if wi + 1 < len(seg_words) else seg_b
                    if nxt_a > sw[0]:
                        sw[1] = min(nxt_a, sw[0] + 0.04)
                    else:
                        sw[1] = sw[0] + 0.02

            if seg_words:
                for sw in seg_words:
                    words.append((sw[0], sw[1], sw[2], sw[3]))
            elif seg_b > seg_a:
                fallback.append((seg_a, seg_b, seg_text, src_seg_id))

        # Word timestamps are absolute offsets in the original full WAV.
        # Keep stable order by (start, original index) so zero-duration tokens stay ordered.
        words = [item for _, item in sorted(enumerate(words), key=lambda pair: (pair[1][0], pair[0]))]
        out: List[Dict[str, Any]] = []
        current: List[Tuple[float, float, str, int]] = []

        def join_tokens(items: List[Tuple[float, float, str, int]]) -> str:
            raw = "".join(x[2] for x in items)
            raw = re.sub(r"[ \t]+", " ", raw).strip()
            # Remove only spacing between adjacent CJK characters/punctuation.
            # Keep meaningful Latin spacing in mixed text such as "RTX 3060です".
            cjk = r"[\u3040-\u30ff\u3400-\u9fff]"
            raw = re.sub(rf"(?<={cjk})\s+(?={cjk})", "", raw)
            raw = re.sub(rf"(?<={cjk})\s+(?=[，。！？；：、])", "", raw)
            raw = re.sub(rf"(?<=[，。！？；：、])\s+(?={cjk})", "", raw)
            return raw.strip()

        def flush() -> None:
            if not current:
                return
            txt = join_tokens(current)
            if txt and not cls._is_hallucination_loop(txt):
                a, b = current[0][0], max(x[1] for x in current)
                out.append({
                    "start": a,
                    "end": b,
                    "raw_start": round(a, 3),
                    "raw_end": round(b, 3),
                    "src_text": txt,
                    "source_segment": current[0][3],
                    "window_id": window_id,
                })
            current.clear()

        sentence_end = re.compile(r'[.!?。！？…]+["\')\]」』】]*$')
        clause_end = re.compile(r'[,;:，；：、]+["\')\]」』】]*$')
        cjk_particle_end = re.compile(r'(?:は|が|を|に|で|と|も|から|けど|ので|ね|よ)$')
        for token in words:
            if not current:
                current.append(token)
                continue
            prev = current[-1]
            candidate_text = join_tokens(current)
            candidate_dur = max(x[1] for x in current) - current[0][0]
            gap = max(0.0, token[0] - prev[1])
            width = _disp_len(candidate_text)
            prev_word_clean = prev[2].strip().strip("\"'.,;:!?").lower()
            hanging = prev_word_clean in cls._HANGING_EN_WORDS
            new_whisper_seg = (token[3] != prev[3]) and not (hanging and gap < 0.20)
            cjk_no_space = (" " not in candidate_text) and bool(re.search(r"[\u3040-\u30ff\u3400-\u9fff]", candidate_text))

            if cjk_no_space:
                is_sent_end = bool(sentence_end.search(prev[2].strip()))
                is_clause_end = bool(clause_end.search(prev[2].strip()))
                split_here = (
                    (new_whisper_seg and width >= 2)
                    or (is_sent_end and width >= 2)
                    or (gap >= 0.42 and width >= 8 and not is_clause_end)
                    or (gap >= 1.10 and width >= 2)
                    or (is_clause_end and (width >= 26 or candidate_dur >= 2.8))
                    or (bool(cjk_particle_end.search(prev[2].strip())) and (width >= 40 or candidate_dur >= 4.6))
                    or candidate_dur >= 7.5
                    or width >= 68
                )
            else:
                split_here = (
                    (new_whisper_seg and width >= 2)
                    or (gap >= 0.42 and width >= 2)
                    or (sentence_end.search(prev[2].strip()) and width >= 2)
                    or (clause_end.search(prev[2].strip()) and (width >= 26 or candidate_dur >= 2.8))
                    or (candidate_dur >= 5.2 and not (hanging and candidate_dur < 6.0))
                    or (width >= 46 and not (hanging and width < 56))
                )
            if split_here:
                flush()
            current.append(token)
        flush()

        # Only use segment-level timestamps if this segment had no word timestamps.
        if fallback:
            for a, b, txt, src_seg_id in fallback:
                out.append({
                    "start": a,
                    "end": b,
                    "raw_start": round(a, 3),
                    "raw_end": round(b, 3),
                    "src_text": txt,
                    "source_segment": src_seg_id,
                    "window_id": window_id,
                })

        out.sort(key=lambda x: (x["start"], x["end"]))
        cleaned: List[Dict[str, Any]] = []
        for item in out:
            a, b = float(item["start"]), float(item["end"])
            txt = str(item["src_text"]).strip()
            if not txt or b <= a:
                continue
            if cleaned and a < cleaned[-1]["end"]:
                a = max(a, cleaned[-1]["end"])
            if b <= a:
                continue
            cleaned.append({
                "id": len(cleaned) + 1,
                "start": round(max(0.0, a), 3),
                "end": round(b, 3),
                "raw_start": item.get("raw_start", round(max(0.0, a), 3)),
                "raw_end": item.get("raw_end", round(b, 3)),
                "src_text": txt,
                "source_segment": item.get("source_segment", len(cleaned) + 1),
                "window_id": item.get("window_id", window_id),
            })
        return cleaned

    @staticmethod
    def export_alignment_audit(
        segments: List[Dict[str, Any]],
        output_dir: Path | str,
        stem: str = "alignment_audit",
    ) -> Dict[str, str]:
        """导出逐条时间戳核对表（CSV + JSON），供人工审查开口/收声精度。"""
        import csv
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe = _safe_name(stem, "alignment_audit")
        csv_path = out_dir / f"{safe}_alignment.csv"
        json_path = out_dir / f"{safe}_alignment.json"
        rows = []
        prev_end: Optional[float] = None
        for seg in segments:
            s = float(seg.get("start") or 0.0)
            e = float(seg.get("end") or 0.0)
            gap = "" if prev_end is None else round(s - prev_end, 3)
            prev_end = e
            rows.append({
                "id": seg.get("id"),
                "start": round(s, 3),
                "end": round(e, 3),
                "duration": round(max(0.0, e - s), 3),
                "gap_from_prev": gap,
                "raw_start": seg.get("raw_start", round(s, 3)),
                "raw_end": seg.get("raw_end", round(e, 3)),
                "source_segment": seg.get("source_segment", seg.get("id")),
                "window_id": seg.get("window_id", 0),
                "src_text": str(seg.get("src_text") or ""),
                "zh_text": str(seg.get("zh_text") or ""),
            })
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(
                fh,
                fieldnames=["id", "start", "end", "duration", "gap_from_prev", "raw_start", "raw_end", "source_segment", "window_id", "src_text", "zh_text"],
            )
            writer.writeheader()
            writer.writerows(rows)
        json_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"csv_path": str(csv_path), "json_path": str(json_path)}

    @staticmethod
    def _edit_distance(a: str, b: str) -> int:
        if a == b:
            return 0
        if not a:
            return len(b)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a, start=1):
            cur = [i] + [0] * len(b)
            for j, cb in enumerate(b, start=1):
                cost = 0 if ca == cb else 1
                cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            prev = cur
        return prev[-1]

    @classmethod
    def _repair_asr_contextual_homophones(
        cls,
        segments: List[Dict[str, Any]],
        detected_lang: str,
    ) -> List[Dict[str, Any]]:
        """通用片假名实体编辑距离聚类归一（零硬编码词表）：

        扫描全片带称谓后缀（さん/くん/君/ちゃん/様/先生）或自我介绍句式中的片假名专名，
        若同一视频中出现长度相同（>=3）且编辑距离为 1 的低频近音变体，自动向高频主实体对齐。
        """
        if not segments or (detected_lang or "").lower() not in ("ja", "jp", "auto", ""):
            return segments
        full_text = "\n".join(str(s.get("src_text") or "") for s in segments)
        if not re.search(r"[\u30a0-\u30ff]{3,}", full_text):
            return segments

        entity_re = re.compile(
            r"([\u30a1-\u30fc]{3,12})(?=さん|くん|君|ちゃん|様|先生)"
            r"|([\u30a1-\u30fc]{3,12})(?=です)"
        )
        scores: Dict[str, int] = {}
        for m in entity_re.finditer(full_text):
            if m.group(1):
                name = m.group(1)
                scores[name] = scores.get(name, 0) + 3
            elif m.group(2):
                name = m.group(2)
                scores[name] = scores.get(name, 0) + 1

        if len(scores) < 2:
            return segments

        canonical_map: Dict[str, str] = {}
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
        for i, (cand, sc) in enumerate(ranked):
            for canon, canon_sc in ranked[:i]:
                if canon_sc > sc and len(canon) >= 3 and abs(len(canon) - len(cand)) <= 1:
                    if cls._edit_distance(canon, cand) == 1:
                        canonical_map[cand] = canon
                        break

        if not canonical_map:
            return segments

        for seg in segments:
            txt = str(seg.get("src_text") or "")
            if not txt:
                continue
            for wrong, right in canonical_map.items():
                txt = re.sub(
                    rf"{re.escape(wrong)}(?=です|くん|君|さん|ちゃん|様|先生|[?？!！。、]|$)",
                    right,
                    txt,
                )
            seg["src_text"] = txt
        return segments

    def _run_transcription(
        self,
        audio_path: str,
        lang: Optional[str],
        model_size: str,
        emit,
        cb: Optional[ProgressCallback],
    ) -> Tuple[List[Dict[str, Any]], str, str]:
        """非破坏性独立 VAD 窗口 + 精确原始时间轴偏移识别。

        绝不把不连续语音段拼接解码（根除跨句串台与提前闪现）；
        对每个独立连续语音窗口加回精确 offset，并用 Silero VAD 真实语音边界收紧首尾漂移。
        """
        last_error: Optional[Exception] = None
        for attempt in (0, 1):
            try:
                emit(0.05, f"正在加载语音模型（{model_size}）…", force=True)
                model = self._get_model(model_size)
                emit(0.15, "模型加载完成，正在分析语音区间…", force=True)

                pcm = self._load_pcm16_wav(str(audio_path))
                vad_intervals: List[Tuple[float, float]] = []
                windows: List[Tuple[int, int]] = []
                audio_arr = None
                sr = 16000
                total = 0.0

                if pcm is not None:
                    audio_arr, sr = pcm
                    total = len(audio_arr) / float(sr)
                    try:
                        from faster_whisper.vad import VadOptions, get_speech_timestamps
                        # 高精度参考 VAD（30ms padding）用于开口/收声边界核验
                        ref_spans = get_speech_timestamps(
                            audio_arr,
                            VadOptions(
                                threshold=0.35,
                                neg_threshold=0.22,
                                min_speech_duration_ms=100,
                                min_silence_duration_ms=250,
                                speech_pad_ms=30,
                            ),
                            sampling_rate=sr,
                        )
                        vad_intervals = [(sp["start"] / sr, sp["end"] / sr) for sp in ref_spans]
                        # 独立连续解码窗口（绝不跨静音拼接）
                        win_spans = get_speech_timestamps(
                            audio_arr,
                            VadOptions(
                                threshold=0.35,
                                neg_threshold=0.22,
                                min_speech_duration_ms=140,
                                min_silence_duration_ms=450,
                                speech_pad_ms=150,
                            ),
                            sampling_rate=sr,
                        )
                        if win_spans:
                            windows = self._build_independent_vad_windows(win_spans, sr, len(audio_arr))
                        else:
                            windows = [(0, len(audio_arr))]
                    except Exception as vad_exc:  # noqa: BLE001
                        logger.warning("Silero VAD 区间分析不可用，回退整段识别：%s", vad_exc)
                        windows = [(0, len(audio_arr))]

                decode_kwargs: Dict[str, Any] = {
                    "task": "transcribe",
                    "beam_size": 5,
                    "best_of": 5,
                    "vad_filter": False,
                    "condition_on_previous_text": False,
                    "temperature": (0.0, 0.2, 0.4),
                    "compression_ratio_threshold": 2.4,
                    "no_speech_threshold": 0.6,
                    "log_prob_threshold": -1.0,
                    "word_timestamps": True,
                }

                detected = (lang or "").lower()
                all_segments: List[Dict[str, Any]] = []

                if audio_arr is not None and windows:
                    # 自动语言检测：优先用最长语音窗口检测主语种，避免片头音乐或 1 秒短窗误判语种
                    eff_lang = lang
                    if not eff_lang:
                        longest = max(windows, key=lambda w: w[1] - w[0])
                        sample_slice = audio_arr[longest[0]:min(longest[1], longest[0] + sr * 30)]
                        probe_gen, probe_info = model.transcribe(
                            sample_slice, language=None, beam_size=1, vad_filter=False,
                        )
                        try:
                            probe_gen.close()
                        except Exception:  # noqa: BLE001
                            pass
                        detected = (getattr(probe_info, "language", "") or "").lower()
                        eff_lang = detected or None

                    emit(0.18, "开始按原始时间轴识别…", force=True)
                    c_segs_iter, c_info = model.transcribe(audio_arr, language=eff_lang, **decode_kwargs)
                    if not detected:
                        detected = (getattr(c_info, "language", "") or "").lower()
                    collected_full = []
                    last_progress = 0.0
                    for seg in c_segs_iter:
                        collected_full.append(seg)
                        seg_end = float(getattr(seg, "end", 0.0) or 0.0)
                        if total > 0 and seg_end - last_progress >= 2.0:
                            last_progress = seg_end
                            emit(
                                0.18 + min(seg_end / total, 0.99) * 0.78,
                                f"识别中… {seg_end:.0f}/{total:.0f} 秒",
                            )
                    all_segments = self._segments_from_word_timestamps(
                        collected_full,
                        duration=total,
                        offset_s=0.0,
                        window_id=0,
                        source_seg_offset=0,
                    )
                else:
                    # 兼容非 PCM WAV 或单元测试桩（直接传路径，保持 vad_filter=False）
                    raw_segments, info = model.transcribe(str(audio_path), language=lang, **decode_kwargs)
                    duration = float(getattr(info, "duration", 0.0) or 0.0)
                    if duration <= 0:
                        duration = float(self._probe_duration(str(audio_path)) or 0.0)
                    total = max(0.0, duration)
                    detected = (getattr(info, "language", "") or lang or "").lower()
                    collected = []
                    last_progress = 0.0
                    for seg in raw_segments:
                        collected.append(seg)
                        seg_end = float(getattr(seg, "end", 0.0) or 0.0)
                        if total > 0 and seg_end - last_progress >= 2.0:
                            last_progress = seg_end
                            emit(
                                0.18 + min(seg_end / total, 0.99) * 0.78,
                                f"识别中… {seg_end:.0f}/{total:.0f} 秒",
                            )
                    all_segments = self._segments_from_word_timestamps(collected, total)

                # 按绝对时间重排、过滤无语音静音幻觉、合并同句回声重复并用只读 VAD 收紧边界
                all_segments.sort(key=lambda x: (x["start"], x["end"]))
                cleaned: List[Dict[str, Any]] = []
                for item in all_segments:
                    a, b = float(item["start"]), float(item["end"])
                    if vad_intervals:
                        has_speech = any(
                            (min(iv[1], b) - max(iv[0], a)) > 0.02
                            or (iv[0] - 0.15 <= (a + b) * 0.5 <= iv[1] + 0.15)
                            for iv in vad_intervals
                        )
                        if not has_speech:
                            continue
                    if cleaned:
                        prev_item = cleaned[-1]
                        norm_cur = re.sub(r"[\s.,!?。，！？、〜ー~]+", "", str(item.get("src_text") or "")).lower()
                        norm_prev = re.sub(r"[\s.,!?。，！？、〜ー~]+", "", str(prev_item.get("src_text") or "")).lower()
                        if (
                            norm_cur
                            and norm_cur == norm_prev
                            and (a - float(prev_item["end"])) <= 0.18
                            and (b - float(prev_item["start"])) <= 2.6
                        ):
                            prev_item["end"] = round(max(float(prev_item["end"]), b), 3)
                            prev_item["raw_end"] = round(max(float(prev_item.get("raw_end", b)), b), 3)
                            continue
                        if a < prev_item["end"]:
                            a = prev_item["end"]
                    if b <= a + 0.04:
                        continue
                    item["start"] = round(max(0.0, a), 3)
                    item["end"] = round(b, 3)
                    item["id"] = len(cleaned) + 1
                    item["speaker"] = 0
                    cleaned.append(item)

                if vad_intervals and cleaned:
                    cleaned = self._align_segments_to_vad_bounds(cleaned, vad_intervals)
                if cleaned:
                    cleaned = self._repair_asr_contextual_homophones(cleaned, detected)

                if not cleaned:
                    emit(1.0, "识别完成，未检测到有效语音", force=True)
                    return [], detected, ""

                emit(1.0, "识别完成", force=True)
                note = ""
                if self.accel_degraded:
                    note = "（GPU 初始化失败，本次已自动改用 CPU，速度较慢）"
                logger.info(
                    "非破坏性独立窗口识别完成：%d 条字幕（语种：%s，模型：%s，窗口数：%d）",
                    len(cleaned), detected, model_size, len(windows) or 1,
                )
                return cleaned, detected, note

            except PipelineError as pe:
                cause = pe.__cause__
                if (self.device == "cuda" and attempt == 0 and cause is not None
                        and (self._looks_like_cuda_error(cause) or self._looks_like_oom(cause))):
                    reason = "显存不足" if self._looks_like_oom(cause) else "CUDA/cuDNN 运行库异常"
                    self._degrade_to_cpu(reason)
                    self._emit(cb, 5.0, f"GPU 不可用（{reason}），已切换到 CPU 模式重试…")
                    continue
                raise
            except MemoryError as exc:
                if self.device == "cuda" and attempt == 0:
                    self._degrade_to_cpu("显存不足")
                    self._emit(cb, 5.0, "显存不足，已切换到 CPU 模式重试…")
                    continue
                raise PipelineError("内存/显存不足。请换用 medium 或 small 模型，或关闭其他占用资源的程序后重试。") from exc
            except Exception as exc:
                last_error = exc
                if self.device == "cuda" and attempt == 0 and (
                    self._looks_like_cuda_error(exc) or self._looks_like_oom(exc)
                ):
                    reason = "显存不足" if self._looks_like_oom(exc) else "CUDA/cuDNN 运行库异常"
                    self._degrade_to_cpu(reason)
                    self._emit(cb, 5.0, f"GPU 不可用（{reason}），已切换到 CPU 模式重试…")
                    continue
                raise PipelineError(self._map_infer_error(exc)) from exc
        raise PipelineError(self._map_infer_error(last_error) if last_error else "语音识别失败。")

    @staticmethod
    def _map_infer_error(exc: Exception) -> str:
        msg = str(exc)
        low = msg.lower()
        if "out of memory" in low:
            return ("显存/内存不足。建议换小一号模型（medium / small），"
                    "或关闭游戏、OBS、浏览器硬件加速后重试。")
        if any(k in low for k in ("cudnn", "cublas", "cuda", "cudart")):
            return ("GPU 运行库异常（CUDA/cuDNN）。请把程序切换为 CPU 模式，"
                    "或安装与 ctranslate2 版本匹配的 CUDA 12 + cuDNN 9。")
        if "audio" in low and ("decode" in low or "invalid" in low):
            return "音频解码失败：文件可能损坏，或不是标准 PCM wav。请重新执行预处理。"
        if "ffmpeg" in low:
            return ("识别前置的音频解析依赖 ffmpeg，但 ffmpeg 调用失败。"
                    "请确认 ./bin/ffmpeg.exe 可用后重试。")
        return f"语音识别失败（{type(exc).__name__}）：{msg[:200]}"

    # ------------------------------------------------------------------ #
    # 资源管理
    # ------------------------------------------------------------------ #

    def release_model(self) -> None:
        """释放模型占用的显存/内存（切换模型或闲置时调用）。"""
        self._model = None
        self._model_key = None
        try:
            import gc
            gc.collect()
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ #
    # 原生字幕桥接（跳过 Whisper 快速通道）
    # ------------------------------------------------------------------ #

    def parse_srt_file(self, srt_path: str) -> Dict[str, Any]:
        """把 prepare_media 拿到的 native_srt_path 解析成与 transcribe_audio 一致的 segments。

        走同一套 MAX_SEG_WIDTH / MAX_SEG_DUR 分段器，所以前端不需要写两套渲染逻辑。
        """
        return parse_srt_file(srt_path)


# --------------------------------------------------------------------------- #
# 第 2 部分 · SRT 解析（原生字幕桥接，跳过 Whisper）
# --------------------------------------------------------------------------- #

_SRT_TS_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})"
)
#: 清掉 <i> / <font> / {\an8} 这类样式标签，只留纯文本
_SUB_TAG_RE = re.compile(r"<[^<>]{0,80}>|\{\\[^{}]{0,80}\}")


def _read_text_any(path: Path) -> str:
    """按 BOM / 常见编码依次尝试读取，绝不因为编码问题让用户懵。"""
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def _srt_ts_to_sec(h: str, m: str, s: str, frac: str) -> float:
    frac = (frac + "000")[:3]
    return int(h) * 3600 + int(m) * 60 + int(s) + int(frac) / 1000.0


def _guess_lang_from_name(name: str) -> str:
    """从 xxx.en.srt / xxx.zh-Hans.srt 这种命名里猜语言。"""
    m = re.search(r"\.([a-zA-Z]{2,3})(?:[-_][A-Za-z]{2,4})?$", name)
    if not m:
        return ""
    code = m.group(1).lower()
    return _LANG_ALIAS.get(code, code)


def _parse_srt_impl(srt_path: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "status": "error", "detected_lang": "", "segments": [], "message": "",
    }

    p = Path(os.path.expanduser(str(srt_path)))
    if not p.is_absolute():
        p = Path.cwd() / p
    if not p.is_file():
        result["message"] = f"找不到字幕文件：{srt_path}"
        return result
    if p.stat().st_size == 0:
        result["message"] = "字幕文件是空的。"
        return result

    try:
        text = _read_text_any(p)
    except OSError as exc:
        result["message"] = f"读取字幕文件失败：{exc}"
        return result

    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    raw_items: List[Tuple[float, float, str]] = []

    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        m = None
        ts_idx = -1
        for i in (0, 1):                       # 序号行可有可无，时间轴一定在前两行
            if i < len(lines):
                m = _SRT_TS_RE.search(lines[i])
                if m:
                    ts_idx = i
                    break
        if not m or ts_idx < 0:
            continue
        start = _srt_ts_to_sec(m.group(1), m.group(2), m.group(3), m.group(4))
        end = _srt_ts_to_sec(m.group(5), m.group(6), m.group(7), m.group(8))
        content = " ".join(lines[ts_idx + 1:])
        content = _SUB_TAG_RE.sub("", content)
        content = re.sub(r"\s+", " ", content).strip()
        if content and end > start:
            raw_items.append((start, end, content))

    if not raw_items:
        result["message"] = "这个文件里没有解析到有效的 SRT 字幕块，可能不是标准 srt 格式。"
        return result

    # 复用第 1 部分的分段器：原生字幕常常一行 20 秒 / 上百字符，直接压会出界
    segments: List[Dict[str, Any]] = []
    next_id = 1
    for start, end, content in raw_items:
        for a, b, piece in MediaProcessor._split_segment(start, end, content):
            segments.append({"id": next_id, "start": a, "end": b, "src_text": piece})
            next_id += 1

    if not segments:
        result["message"] = "字幕解析完成但内容为空，请检查文件。"
        return result

    result.update({
        "status": "success",
        "detected_lang": _guess_lang_from_name(p.stem),
        "segments": segments,
        "message": (f"已解析原生字幕：{len(raw_items)} 条原始行 → 清洗为 "
                    f"{len(segments)} 条字幕，可直接进入翻译。"),
    })
    return result


def parse_srt_file(srt_path: str) -> Dict[str, Any]:
    """独立工具函数：解析 .srt → 与 transcribe_audio 完全一致的 segments 结构。"""
    try:
        return _parse_srt_impl(srt_path)
    except Exception as exc:  # noqa: BLE001
        logger.exception("parse_srt_file 未预期异常")
        return {
            "status": "error", "detected_lang": "", "segments": [],
            "message": f"字幕解析失败（{type(exc).__name__}），请确认文件是标准 SRT。",
        }


# --------------------------------------------------------------------------- #
# 第 2 部分 · DeepSeek 滑窗批量精翻
# --------------------------------------------------------------------------- #

class _FatalAPIError(PipelineError):
    '''Key 无效 / 余额不足：重试没有意义，必须立刻中止整批。'''


def _extract_id_map_with_meta(content: str) -> Tuple[Dict[int, str], Dict[str, Any]]:
    '''从模型返回里抽出 {id: 译文} 并记录 schema 诊断（重复 ID、空值、解析模式、说话人 spk、ASR 原文纠错 src_fix）。'''
    meta: Dict[str, Any] = {"mode": "empty", "duplicate_ids": [], "empty_ids": [], "raw_count": 0, "speakers": {}, "src_fixes": {}}
    if not content:
        return {}, meta
    text = content.strip()
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    obj: Any = None
    try:
        obj = json.loads(text)
        meta["mode"] = "json"
    except (ValueError, TypeError):
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                meta["mode"] = "json_extracted"
            except (ValueError, TypeError):
                obj = None
        if obj is None:
            m_arr = re.search(r"\[.*\]", text, re.S)
            if m_arr:
                try:
                    obj = json.loads(m_arr.group(0))
                    meta["mode"] = "json_array"
                except (ValueError, TypeError):
                    obj = None

    mapping: Dict[int, str] = {}
    seen_ids: set = set()

    if isinstance(obj, dict):
        if "id" in obj and any(k in obj for k in ("zh", "zh_text", "text", "translation")):
            obj = [obj]
        else:
            items = obj.get("items") or obj.get("data") or obj.get("segments") or obj.get("result")
            if isinstance(items, list):
                obj = items
            else:
                for k, v in obj.items():
                    if isinstance(k, str) and k.strip().lstrip("-").isdigit() and isinstance(v, str):
                        rid = int(k.strip())
                        meta["raw_count"] += 1
                        val = v.strip()
                        if rid in seen_ids:
                            meta["duplicate_ids"].append(rid)
                        seen_ids.add(rid)
                        if val:
                            mapping[rid] = val
                        else:
                            meta["empty_ids"].append(rid)
                if mapping or meta["empty_ids"]:
                    return mapping, meta
                obj = None

    if isinstance(obj, list):
        for it in obj:
            if not isinstance(it, dict):
                continue
            rid = it.get("id", it.get("index", it.get("no")))
            txt = it.get("zh", it.get("zh_text", it.get("text", it.get("translation"))))
            if rid is None:
                continue
            try:
                rid = int(str(rid).strip().strip("[]#"))
            except (ValueError, TypeError):
                continue
            meta["raw_count"] += 1
            if rid in seen_ids:
                meta["duplicate_ids"].append(rid)
            seen_ids.add(rid)
            val = str(txt).strip() if txt is not None else ""
            raw_spk = it.get("spk", it.get("speaker"))
            if raw_spk is not None and rid not in meta["speakers"]:
                s_str = str(raw_spk).strip().upper()
                if s_str in ("1", "B", "SPEAKER_B", "SPK_B"):
                    meta["speakers"][rid] = 1
                elif s_str in ("0", "A", "SPEAKER_A", "SPK_A"):
                    meta["speakers"][rid] = 0
            raw_fix = it.get("src_fix")
            if isinstance(raw_fix, str) and raw_fix.strip() and rid not in meta["src_fixes"]:
                meta["src_fixes"][rid] = re.sub(r"\s+", " ", raw_fix).strip()
            if val:
                if rid not in mapping:
                    mapping[rid] = val
            else:
                meta["empty_ids"].append(rid)
    if mapping or meta["empty_ids"]:
        return mapping, meta

    if "-->" not in text:
        meta["mode"] = "anchor_text"
        for m in re.finditer(r"^\s*\[(\d{1,5})\]\s*[:：\-]?\s*(.+)$", text, re.M):
            rid, val = int(m.group(1)), m.group(2).strip()
            if val and not val.startswith("{"):
                if rid in mapping:
                    meta["duplicate_ids"].append(rid)
                mapping.setdefault(rid, val)
        if not mapping:
            for m in re.finditer(r"^\s*(\d{1,5})[.)、]\s+(.+)$", text, re.M):
                rid, val = int(m.group(1)), m.group(2).strip()
                if val and not val.startswith("{"):
                    if rid in mapping:
                        meta["duplicate_ids"].append(rid)
                    mapping.setdefault(rid, val)
    return mapping, meta


def _extract_id_map(content: str) -> Dict[int, str]:
    '''从模型返回里抽出 {id: 译文}。兼容 JSON 对象 / JSON 数组 / [id] 锚点三种格式。'''
    mapping, _ = _extract_id_map_with_meta(content)
    return mapping


class SubtitleTranslator:
    '''高可靠并发字幕翻译器：有界并发 + 连接复用 + 句子语境感知 + 严格 Schema 校验 + 逐条状态与可恢复重试。'''

    DEFAULT_BASE_URL = "https://api.deepseek.com"
    _MAX_SRC_CHARS = 600
    _TERM_RETRY_BUDGET = 5
    _MAX_SPLIT_DEPTH = 4
    _CLOUD_WORKERS = 6

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = "deepseek-chat",
        timeout: float = 25.0,
        max_retries: int = 3,
        temperature: float = 0.3,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or self.DEFAULT_BASE_URL).strip().rstrip("/")
        self.model = (model or "deepseek-chat").strip() or "deepseek-chat"
        base_low = self.base_url.lower()
        self._is_local = ("localhost" in base_low) or ("127.0.0.1" in base_low)
        raw_to = float(timeout or 25.0)
        self.timeout = max(raw_to, 90.0) if self._is_local else min(raw_to, 30.0)
        self.max_retries = max(1, int(max_retries or 3))
        self.temperature = max(0.0, min(1.0, float(temperature if temperature is not None else 0.3)))
        self._json_mode = True
        self._last_error = ""
        self._session_local = threading.local()
        self._token_lock = threading.Lock()
        self._usage_prompt_tokens = 0
        self._usage_completion_tokens = 0
        self._last_schema_warnings: Dict[str, int] = {"extra_ids": 0, "duplicate_ids": 0, "residue_rejected": 0}
        self._last_speakers: Dict[int, int] = {}
        self._last_src_fixes: Dict[int, str] = {}
        self._current_dialogue_overview: str = ""
        logger.info(
            "SubtitleTranslator 就绪 | model=%s | workers=%d | timeout=%.0fs | base_url=%s",
            self.model, 1 if self._is_local else self._CLOUD_WORKERS, self.timeout, self.base_url,
        )

    @staticmethod
    def _build_session():
        try:
            import requests
            from requests.adapters import HTTPAdapter
            sess = requests.Session()
            adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            return sess
        except Exception:
            return None

    def parse_glossary(self, glossary_text: str) -> Dict[str, str]:
        table: Dict[str, str] = {}
        if not glossary_text:
            return table
        for raw in str(glossary_text).splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith("//"):
                continue
            parts = re.split(r"\s*(?:=>|=|:|：|＝|\t)\s*", line, maxsplit=1)
            if len(parts) != 2:
                continue
            src, dst = parts[0].strip(), parts[1].strip()
            if src and dst and src.lower() != dst.lower():
                table[src] = dst
        return table

    @staticmethod
    def _term_hits(text: str, glossary: Dict[str, str]) -> List[Tuple[str, str, int]]:
        if not text or not glossary:
            return []
        hits: List[Tuple[str, str, int]] = []
        low = text.lower()
        for src, dst in glossary.items():
            if not src:
                continue
            if src.isascii():
                m = re.search(
                    r"(?<![A-Za-z0-9])" + re.escape(src.lower()) + r"(?![A-Za-z0-9])",
                    low,
                )
                pos = m.start() if m else -1
            else:
                pos = text.find(src)
            if pos >= 0:
                hits.append((src, dst, pos))
        hits.sort(key=lambda x: x[2])
        return hits

    def _endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _system_prompt(
        self,
        glossary: Dict[str, str],
        lang_name: str,
        extra_rule: Optional[str] = None,
    ) -> str:
        rules = [
            f"你是资深影视字幕译者，负责把{lang_name}对白字幕翻译成自然地道的简体中文。",
            "严格遵守以下规则：",
            '1. 只输出一个 JSON 对象，形如 {"items":[{"id":1,"src_fix":"仅当原文有语音识别同音错字时填写修正后的原语，无误则省略","zh":"译文","spk":0}]}，禁止输出任何解释、注释或 Markdown 代码块。',
            "2. items 必须与输入逐条一一对应：id 必须完全一致（不增、不减、不改、不重复），条数必须相同。",
            '2.1 每条额外输出 "spk": 0 或 1（用于区分说话人 A=0 / B=1：单人独白统一输出 0；当根据问答语义、称呼或回应判断发生换人对谈时在 0 与 1 之间切换；无法判断时与前一条相同）。',
            "3. 若连续几条字幕同属一个完整长句，请先通读完整句意再把中文自然切分回对应 id，保持衔接流畅，严禁逐词生硬直译。",
            "3.1 语音识别（ASR）同音/近音错字通用审校：输入文本通常来自语音识别（ASR），常会出现「读音相同或相近、但字面词义与动作/场景逻辑冲突」的同音异义词（例如：在问路、带路「一起走吧」、到达某处「这里就是…」的连续场景中，把目的地大门/建筑误识为发音相近的片假名或抽象学科词汇；或把口语惯用语误识为无意义假名拼写；或同一专名在前后句被识为近音词）。翻译每条字幕前，请通读 dialogue_overview（全片对白大纲）的完整因果链审校 src：若某词在上下文中指代具体地点方位、动作或人物，但字面却是无关同音词或无意义假名，必须先在 \"src_fix\" 字段将其还原为读音相近且符合全片剧情逻辑的正确原语词句，再依据还原后的正确语义翻译 \"zh\"，并在全片保持同一地点/人物译法统一。",
            "4. 译文要像中文母语者自然说出口的话：口语化、简洁、无换行；严禁出现「所以，你认为」「它变成」「我将会前进并且」「使得」「对于…而言」「关于那件事」等西化机翻腔；语境明确时省略多余的「你/我/它/那个」。",
            "4.1 中文词间严禁出现异常空格，术语自然融入句子（正确：「电控单元失去同步」；错误：「电控单元 失去同步」）。",
            "4.2 不得省略否定、条件、因果、转折、数量或关键动作；输出长度简洁自然。",
            "4.3 英文高频口语地道译法示例：「So, what do you think about that?」译为「那你觉得怎么样？」；「I'm gonna go ahead and get started」译为「那我先开始了」；「It turns out we were wrong」译为「结果发现是我们想错了」。",
            "5. 数字、型号、URL、代码、专有缩写保持原样（如 1080P、USB-C、RTX 3060），保留英文单词与型号内部正常空格。",
            "6. context_before / context_after / passage_hint / dialogue_overview 仅用于理解完整语境与全片剧情，严禁翻译它们，也严禁让它们的 id 出现在输出里。",
            "7. 短应答和语气词必须按语境自然翻译，绝不留空。",
            "7.1 日语高频口语必须按语境地道翻译：「こんにちは」译为「你好/您好」（除非明确指正午，绝不机械译「中午好」）；"
            "「はい、そうですよ」译为「是的/没错」（禁止重复堆成「对 是的」）；「すみません」按场景译「不好意思/请问/打扰一下」；"
            "「じゃあ一緒に行きましょう」译为「那我们一起走吧」；"
            "在「…はあっち/こっち」「ここが…よ」等指路与到达地点的句式中，若宾语被语音识别记为片假名或抽象同音词（如把校门/大门记为同音片假名或学科词），必须先在 src_fix 中还原为读音相同的建筑/大门汉字词再翻译，绝不可把地点名词音译成人名。",
        ]
        if glossary:
            pairs = "；".join([f"{k} → {v}" for k, v in list(glossary.items())[:120]])
            rules.append(f"8. 术语必须使用指定译法，直接嵌进中文句子、不加空格：{pairs}")
        if extra_rule:
            rules.append(f"9. {extra_rule}")
        return "\n".join(rules)

    def _post_http(self, payload: Dict[str, Any]) -> Tuple[int, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }
        session = getattr(self._session_local, "session", None)
        if session is None:
            session = self._build_session()
            self._session_local.session = session
        if session is not None:
            resp = session.post(
                self._endpoint(),
                json=payload,
                headers=headers,
                timeout=(8.0, self.timeout),
            )
            return resp.status_code, resp.text
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._endpoint(), data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return int(getattr(resp, "status", 200)), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")
            except Exception:
                pass
            return int(exc.code), detail

    def _chat(self, messages: List[Dict[str, str]], max_tokens: int = 2048) -> str:
        if not self.api_key:
            raise _FatalAPIError("还没有填写 DeepSeek API Key，请先在设置里填入后再翻译。")

        import random
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": getattr(self, "temperature", 0.3),
            "max_tokens": int(max_tokens),
            "stream": False,
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_detail = ""
        for attempt in range(self.max_retries):
            try:
                code, raw = self._post_http(payload)
                if code == 200:
                    obj = json.loads(raw)
                    usage = obj.get("usage")
                    if isinstance(usage, dict):
                        with self._token_lock:
                            self._usage_prompt_tokens += int(usage.get("prompt_tokens") or 0)
                            self._usage_completion_tokens += int(usage.get("completion_tokens") or 0)
                    choices = obj.get("choices") or []
                    if not choices:
                        raise PipelineError("翻译接口返回了空结果，请稍后重试。")
                    content = (choices[0].get("message") or {}).get("content") or ""
                    if not str(content).strip():
                        raise PipelineError("翻译接口返回了空文本，请稍后重试。")
                    return str(content)

                detail = (raw or "")[:300]
                low = detail.lower()
                if code == 400 and "response_format" in low:
                    self._json_mode = False
                    payload.pop("response_format", None)
                    continue
                if code in (401, 403):
                    raise _FatalAPIError(
                        f"API Key 无效或已失效（HTTP {code}）。请检查设置里的 Key 是否复制完整。"
                    )
                if code == 402:
                    raise _FatalAPIError("账户余额不足（HTTP 402），充值后再试。")
                if code == 429:
                    last_detail = "请求过于频繁（HTTP 429）"
                    backoff = min((1.2 * (2 ** attempt)) + random.uniform(0.1, 0.5), 6.0)
                    time.sleep(backoff)
                    continue
                if 500 <= code < 600:
                    last_detail = f"服务端错误（HTTP {code}）"
                    backoff = min((0.8 * (2 ** attempt)) + random.uniform(0.05, 0.35), 5.0)
                    time.sleep(backoff)
                    continue
                raise PipelineError(f"翻译接口返回错误（HTTP {code}）：{detail or '无详细信息'}")

            except (_FatalAPIError, PipelineError):
                raise
            except json.JSONDecodeError as exc:
                raise PipelineError("翻译接口返回的不是合法 JSON，可能是代理/网络异常。") from exc
            except Exception as exc:
                last_detail = f"网络超时/异常（{type(exc).__name__}）"
                backoff = min((0.8 * (2 ** attempt)) + random.uniform(0.05, 0.35), 4.0)
                time.sleep(backoff)
                continue

        raise PipelineError(f"翻译接口连续 {self.max_retries} 次失败（{last_detail or '原因未知'}），请检查网络后重试。")

    def test_connection(self) -> Dict[str, Any]:
        """发送一次极轻量请求验证 API Key、接口地址和模型是否连通。"""
        if not self.api_key:
            return {"status": "error", "message": "请先填写 API Key。"}
        t0 = time.monotonic()
        try:
            payload = {
                "model": self.model,
                "messages": [{"role": "user", "content": "Reply with OK"}],
                "temperature": 0.0,
                "max_tokens": 8,
                "stream": False,
            }
            code, raw = self._post_http(payload)
            ms = int((time.monotonic() - t0) * 1000)
            if code == 200:
                return {"status": "success", "latency_ms": ms,
                        "message": f"连通正常（{self.model}，延迟 {ms}ms）"}
            if code in (401, 403):
                return {"status": "error", "message": f"鉴权失败（HTTP {code}），请检查 API Key。"}
            if code == 402:
                return {"status": "error", "message": "账户余额不足（HTTP 402）。"}
            return {"status": "error", "message": f"接口返回 HTTP {code}：{(raw or '')[:140]}"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "message": f"无法连接翻译接口：{type(exc).__name__}: {str(exc)[:140]}"}

    @staticmethod
    def _effective_source_language(src: str, lang_name: str) -> str:
        """补足 auto/未知语种场景的脚本识别，避免日/韩原文残留漏检。"""
        name = str(lang_name or "").strip()
        if name and name not in ("外语", "未知", "auto", "none"):
            return name
        text = str(src or "")
        kana = len(re.findall(r"[\u3040-\u30ff]", text))
        hangul = len(re.findall(r"[\uac00-\ud7af]", text))
        latin = len(re.findall(r"[A-Za-zÀ-ÿ]", text))
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        if kana >= 2:
            return "日语"
        if hangul >= 2:
            return "韩语"
        if latin >= 8 and latin / max(1, len(text)) > 0.45 and cjk == 0:
            return "英语"
        return name or "外语"

    def _translate_batch(
        self,
        todo: List[Dict[str, Any]],
        context: List[Dict[str, Any]],
        glossary: Dict[str, str],
        lang_name: str,
        extra_rule: Optional[str] = None,
    ) -> Dict[int, str]:
        target_indexes = [int(t.get("idx", int(t["id"]) - 1)) for t in todo]
        first_idx, last_idx = min(target_indexes), max(target_indexes)
        before = sorted(
            (c for c in context if int(c.get("idx", -1)) < first_idx),
            key=lambda c: int(c.get("idx", -1)),
        )[-3:]
        after = sorted(
            (c for c in context if int(c.get("idx", -1)) > last_idx),
            key=lambda c: int(c.get("idx", -1)),
        )[:3]
        # 把本批文本拼成通顺语境提示，帮助模型理解跨条拆分出的半句，同时保持严格 ID 映射
        passage_parts = [str(t.get("src") or "").strip() for t in todo if str(t.get("src") or "").strip()]
        user_payload: Dict[str, Any] = {
            "context_before": [{"src": (c.get("src") or "")[:180]} for c in before],
            "context_after": [{"src": (c.get("src") or "")[:180]} for c in after],
            "items": [{"id": t["id"], "src": (t.get("src") or "")[:self._MAX_SRC_CHARS]} for t in todo],
        }
        if len(passage_parts) > 1:
            user_payload["passage_hint"] = " ".join(passage_parts)[:900]
        overview = getattr(self, "_current_dialogue_overview", "") or ""
        if overview and len(overview) > len(user_payload.get("passage_hint", "")):
            user_payload["dialogue_overview"] = overview
        messages = [
            {"role": "system", "content": self._system_prompt(glossary, lang_name, extra_rule)},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        content = self._chat(messages, max_tokens=max(512, 140 * len(todo) + 260))
        mapping, meta = _extract_id_map_with_meta(content)
        expected = {int(t["id"]): t for t in todo}
        clean: Dict[int, str] = {}
        extra_count = 0
        residue_count = 0
        for key, value in mapping.items():
            if key not in expected:
                extra_count += 1
                continue
            src = str(expected[key].get("src") or "").strip()
            zh = self._normalize_zh_translation(value)
            check_lang = self._effective_source_language(src, lang_name)
            if zh and not self._translation_has_source_residue(src, zh, check_lang):
                clean[key] = zh
            else:
                residue_count += 1
        if extra_count or meta.get("duplicate_ids") or residue_count or meta.get("speakers") or meta.get("src_fixes"):
            with self._token_lock:
                self._last_schema_warnings["extra_ids"] += extra_count
                self._last_schema_warnings["duplicate_ids"] += len(meta.get("duplicate_ids") or [])
                self._last_schema_warnings["residue_rejected"] += residue_count
                for spk_id, spk_val in (meta.get("speakers") or {}).items():
                    if spk_id in expected:
                        self._last_speakers[spk_id] = spk_val
                for fix_id, fix_val in (meta.get("src_fixes") or {}).items():
                    if fix_id in expected and fix_val:
                        orig_src = str(expected[fix_id].get("src") or "").strip()
                        zh_val = clean.get(fix_id, "")
                        if (
                            orig_src
                            and fix_val != orig_src
                            and fix_val != zh_val
                            and "修正后" not in fix_val
                            and "无误" not in fix_val
                            and 0.4 <= (len(fix_val) / max(1, len(orig_src))) <= 1.9
                        ):
                            self._last_src_fixes[fix_id] = fix_val
        return clean

    def _diagnose_translate_failure(self) -> str:
        err = (self._last_error or "").lower()
        base = (self.base_url or "").lower()
        is_local = ("localhost" in base) or ("127.0.0.1" in base)

        if is_local:
            net_like = any(k in err for k in ("refused", "urlopen error", "network", "timed out", "超时", "网络", "connection"))
            if net_like:
                return f"连不上本地模型服务（{self.base_url}），请确认 Ollama 已启动并拉取模型 {self.model or 'qwen2.5:7b'}。"
            if "404" in err or "not found" in err or "model" in err:
                return f"本地服务已连上，但没找到模型「{self.model}」。"

        if any(k in err for k in ("401", "403", "key", "无效", "unauthorized")):
            return "API Key 无效或已失效。请到「偏好设置 → 算力与翻译引擎」重新粘贴完整 Key 并保存。"
        if any(k in err for k in ("402", "余额", "quota", "insufficient")):
            return f"账户余额/额度不足，请到 {self.base_url} 对应的服务商后台充值。"
        if any(k in err for k in ("429", "频繁", "rate")):
            return "请求太频繁被限流，请稍等片刻后点击补翻失败条目。"
        if "404" in err:
            return f"接口路径或模型名不对（HTTP 404）：{self.base_url} / {self.model}"

        base_msg = "所有字幕条目都翻译失败了。"
        base_msg += ("最后一次错误：" + self._last_error[:200]) if self._last_error else "请检查 API Key、余额和网络后重试。"
        return base_msg

    @staticmethod
    def _normalize_zh_translation(text: str) -> str:
        """只清理中文异常空格，绝不破坏正常英文单词、型号（RTX 3060）和数字间距。"""
        value = re.sub(r"[\r\n\t]+", " ", str(text or "")).strip()
        value = re.sub(r"[ \u00a0\u3000]+", " ", value)
        if re.search(r"[\u3400-\u9fff]", value):
            # 中文汉字之间、汉字与全角标点/括号之间的异常空格
            value = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", value)
            value = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[，。！？；：、）》」』】])", "", value)
            value = re.sub(r"(?<=[，。！？；：、（《「『【])\s+(?=[\u3400-\u9fff])", "", value)
            # 清理「中文 英文」以外多余的标点后空格（保留纯英文内空格）
            value = re.sub(r"(?<=[，。！？；：、])\s+(?=[A-Za-z0-9])", "", value)
        # 仅折叠重复同类句末标点（保留 …… / ... 省略号和 ？！）
        value = re.sub(r"([。！？!?])\1+$", r"\1", value)
        return value.strip()

    @staticmethod
    def _is_passthrough_literal(text: str) -> bool:
        """判断原文本身是否属于无需强行改写的数字、URL、代码标识或纯型号（如 RTX 3060 / 1080P）。"""
        s = str(text or "").strip()
        if not s:
            return True
        # 纯数字/时间/标点
        if re.fullmatch(r"[\d\s.,:%+\-/#()]+", s):
            return True
        # URL 或邮箱
        if re.fullmatch(r"(?:https?://\S+|\S+@\S+\.\S+)", s, re.I):
            return True
        # 常见通用缩写/型号/短标识（<=3个词，且不含普通英文动词/句型）
        if re.fullmatch(r"(?:OK|OKAY|YES|NO|USB-C|HDMI|1080P|4K|8K|AI|API|CPU|GPU|RAM|VRAM|CUDA|JSON|HTML|CSS|SDK|CLI)", s, re.I):
            return True
        if re.fullmatch(r"[A-Z]{2,8}[\s\-_]?\d{2,5}[A-Za-z0-9\-]*", s):
            return True
        return False

    @staticmethod
    def _translation_has_source_residue(src: str, zh: str, lang_name: str) -> bool:
        """拦截明显照抄或大段原文残留，同时放行专有名词、URL、数字、代码和型号。"""
        s_raw = str(src or "").strip()
        t_raw = str(zh or "").strip()
        s = re.sub(r"\s+", "", s_raw).lower()
        t = re.sub(r"\s+", "", t_raw).lower()
        if not t:
            return True
        if SubtitleTranslator._is_passthrough_literal(s_raw):
            return False
        if s and s == t:
            # 若原文已是中文（如中英混排中本就纯汉字的短句），允许保持原样
            cjk = len(re.findall(r"[\u3400-\u9fff]", s_raw))
            kana = len(re.findall(r"[\u3040-\u30ff]", s_raw))
            if cjk >= 2 and kana == 0 and cjk / max(1, len(s)) >= 0.6:
                return False
            return True
        # 剔除 URL / 型号代码后再检测外文残留
        cleaned_t = re.sub(r"https?://\S+", "", t_raw)
        cleaned_t = re.sub(r"\b[A-Z]{2,8}[\s\-_]?\d{2,5}[A-Za-z0-9\-]*\b", "", cleaned_t)
        compact = re.sub(r"\s+", "", cleaned_t)
        if not compact:
            return False
        if "日语" in lang_name:
            kana = len(re.findall(r"[\u3040-\u30ff]", compact))
            return kana >= 3 and kana / max(1, len(compact)) > 0.12
        if "韩语" in lang_name:
            hangul = len(re.findall(r"[\uac00-\ud7af]", compact))
            return hangul >= 3 and hangul / max(1, len(compact)) > 0.12
        if any(x in lang_name for x in ("英语", "德语", "法语", "西班牙语", "葡萄牙语", "意大利语")):
            latin = len(re.findall(r"[A-Za-zÀ-ÿ]", compact))
            cjk = len(re.findall(r"[\u3400-\u9fff]", compact))
            if latin >= 12 and latin / max(1, len(compact)) > 0.62 and cjk == 0:
                return True
        return False

    def _translate_single_plain(
        self, item: Dict[str, Any], context: List[Dict[str, Any]],
        glossary: Dict[str, str], lang_name: str,
    ) -> str:
        """最后兜底：单条纯文本请求，不依赖 JSON 包装/ID 解析。"""
        src = str(item.get("src") or "").strip()
        if not src:
            return ""
        if self._is_passthrough_literal(src):
            return src
        ctx = "\n".join(str(x.get("src") or "")[:160] for x in context[-4:])
        terms = "；".join(f"{k}→{v}" for k, v in list(glossary.items())[:80])
        prompt = (
            f"把下面这句{lang_name}口语字幕翻译成自然、简洁的简体中文。"
            "只输出译文，不要解释、不要复述原文、不要在中文词语之间加空格。"
            "即使是短应答、语气词也必须翻译。"
            "日语「こんにちは」通常译为「你好」，「はい、そうですよ」译为「是的/没错」。"
            + (f"\n上下文语境（只供理解）：{ctx}" if ctx else "")
            + (f"\n术语要求：{terms}" if terms else "")
            + f"\n原文：{src}"
        )
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": 160,
            "stream": False,
        }
        last = ""
        for attempt in range(min(2, self.max_retries)):
            try:
                code, raw = self._post_http(payload)
                if code == 200:
                    obj = json.loads(raw)
                    usage = obj.get("usage")
                    if isinstance(usage, dict):
                        with self._token_lock:
                            self._usage_prompt_tokens += int(usage.get("prompt_tokens") or 0)
                            self._usage_completion_tokens += int(usage.get("completion_tokens") or 0)
                    choices = obj.get("choices") or []
                    value = str(((choices[0].get("message") or {}).get("content")) or "").strip() if choices else ""
                    value = re.sub(r"^```(?:text|json)?\s*|\s*```$", "", value, flags=re.I).strip()
                    try:
                        parsed = json.loads(value)
                        if isinstance(parsed, dict):
                            value = str(parsed.get("zh") or parsed.get("translation") or value)
                    except Exception:
                        pass
                    value = self._normalize_zh_translation(value.strip(" \t\r\n\"'「」『』"))
                    check_lang = self._effective_source_language(src, lang_name)
                    if value and not self._translation_has_source_residue(src, value, check_lang):
                        return value
                    last = "单句兜底仍检测到原文残留"
                elif code in (401, 403, 402):
                    raise _FatalAPIError(f"翻译服务鉴权/额度错误（HTTP {code}）。请检查 API Key 和账户余额。")
                else:
                    last = f"HTTP {code}"
            except _FatalAPIError:
                raise
            except Exception as exc:
                last = str(exc)[:120]
            if attempt + 1 < min(2, self.max_retries):
                time.sleep(0.3 * (attempt + 1))
        self._last_error = last or self._last_error
        logger.warning("单句纯文本兜底失败 id=%s: %s", item.get("id"), last)
        return ""

    def translate_single(
        self,
        src_text: str,
        context_before: Optional[List[str]] = None,
        context_after: Optional[List[str]] = None,
        glossary_text: str = "",
        source_lang: str = "auto",
    ) -> Dict[str, Any]:
        """单条字幕上下文重译（供前端卡片「AI 重译」按钮直接调用）。"""
        src = str(src_text or "").strip()
        if not src:
            return {"status": "error", "zh_text": "", "message": "原文为空，无法重译。"}
        if not self.api_key:
            return {"status": "error", "zh_text": "", "message": "尚未填写 API Key。"}
        glossary = self.parse_glossary(glossary_text)
        lang_code = (source_lang or "auto").strip().lower()
        lang_name = "外语" if lang_code in ("", "auto", "none") else LANG_NAMES.get(lang_code.split("-")[0], lang_code)
        ctx = [{"src": s, "idx": i} for i, s in enumerate(context_before or [])]
        item = {"id": 1, "src": src, "idx": len(ctx)}
        ctx.extend({"src": s, "idx": len(ctx) + 1 + i} for i, s in enumerate(context_after or []))
        try:
            got = self._translate_batch([item], ctx, glossary, lang_name)
            zh = got.get(1, "")
            if not zh:
                zh = self._translate_single_plain(item, ctx, glossary, lang_name)
            if zh:
                return {"status": "success", "zh_text": zh, "message": "单句上下文重译完成。"}
            return {"status": "error", "zh_text": "", "message": self._last_error or "单句重译未获得有效译文。"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "zh_text": "", "message": str(exc)}

    def _fill(
        self,
        todo: List[Dict[str, Any]],
        context: List[Dict[str, Any]],
        out: Dict[int, str],
        glossary: Dict[str, str],
        lang_name: str,
        depth: int = 0,
        single_tried: Optional[set] = None,
        stop_event: Any = None,
        item_meta: Optional[Dict[int, Dict[str, Any]]] = None,
        transport_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not todo or (stop_event is not None and stop_event.is_set()):
            return
        if single_tried is None:
            single_tried = set()
        if item_meta is None:
            item_meta = {}

        t_req = time.monotonic()
        try:
            got = self._translate_batch(todo, context, glossary, lang_name)
            req_dur = time.monotonic() - t_req
            if transport_state is not None:
                with transport_state["lock"]:
                    transport_state["consecutive_failures"] = 0
                    transport_state["batch_latencies"].append(req_dur)
        except _FatalAPIError:
            if stop_event is not None:
                stop_event.set()
            raise
        except PipelineError as exc:
            self._last_error = str(exc)
            got = {}
            detail = str(exc).lower()
            transient = any(token in detail for token in (
                "连续", "网络超时", "timed out", "timeout", "connection",
                "network", "http 429", "http 5", "不是合法 json",
            ))
            if transient:
                for t in todo:
                    item_meta[int(t["id"])] = {"status": "failed", "error": str(exc)[:160]}
                if transport_state is not None:
                    with transport_state["lock"]:
                        transport_state["consecutive_failures"] += 1
                        if transport_state["consecutive_failures"] >= transport_state.get("trip_threshold", 2):
                            if stop_event is not None:
                                stop_event.set()
                elif stop_event is not None:
                    stop_event.set()
                return

        for tid, zh in got.items():
            out[tid] = zh
            item_meta[tid] = {"status": "ok", "error": ""}

        missing = [t for t in todo if not out.get(int(t["id"]))]
        if not missing:
            return

        if len(missing) > 1 and depth < self._MAX_SPLIT_DEPTH:
            mid = max(1, len(missing) // 2)
            self._fill(missing[:mid], context, out, glossary, lang_name,
                       depth + 1, single_tried, stop_event, item_meta, transport_state)
            self._fill(missing[mid:], context, out, glossary, lang_name,
                       depth + 1, single_tried, stop_event, item_meta, transport_state)
            missing = [t for t in missing if not out.get(int(t["id"]))]

        for item in missing:
            tid = int(item["id"])
            if tid in single_tried or (stop_event is not None and stop_event.is_set()):
                out.setdefault(tid, "")
                item_meta.setdefault(tid, {"status": "failed", "error": self._last_error or "未返回译文"})
                continue
            single_tried.add(tid)
            try:
                try:
                    single = self._translate_batch([item], context[-4:], glossary, lang_name)
                except PipelineError as exc:
                    self._last_error = str(exc)
                    single = {}
                value = single.get(tid, "")
                used_fallback = False
                if not value:
                    value = self._translate_single_plain(item, context[-4:], glossary, lang_name)
                    used_fallback = bool(value)
                out[tid] = value or ""
                if value:
                    item_meta[tid] = {"status": "fallback" if used_fallback else "ok", "error": ""}
                else:
                    item_meta[tid] = {"status": "failed", "error": self._last_error or "单句纯文本兜底仍失败"}
            except _FatalAPIError:
                if stop_event is not None:
                    stop_event.set()
                raise

    @staticmethod
    def _emit(cb: Optional[ProgressCallback], percent: float, text: str) -> None:
        if cb is None:
            return
        try:
            cb(round(max(0.0, min(100.0, float(percent))), 1), str(text))
        except Exception:
            logger.debug("progress_callback 抛异常，已忽略", exc_info=True)

    def translate_segments(
        self,
        segments: List[Dict[str, Any]],
        glossary_text: str = "",
        source_lang: str = "auto",
        batch_size: int = 12,
        context_size: int = 3,
        progress_callback: Optional[ProgressCallback] = None,
        only_missing: bool = False,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        '''并发滑窗批量精翻，返回带 zh_text / translate_status / stats 的结构，支持失败条目可恢复补翻。'''
        from concurrent.futures import ThreadPoolExecutor, as_completed

        result: Dict[str, Any] = {"status": "error", "segments": [], "message": "", "stats": {}}

        if not segments:
            result["message"] = "没有可翻译的字幕，请先识别或导入字幕。"
            return result
        if not self.api_key:
            result["message"] = "还没有填写 DeepSeek API Key，请先在设置里填入后再翻译。"
            return result

        if temperature is not None:
            try:
                self.temperature = max(0.0, min(1.0, float(temperature)))
            except (TypeError, ValueError):
                pass

        try:
            t0 = time.monotonic()
            self._last_error = ""
            with self._token_lock:
                self._usage_prompt_tokens = 0
                self._usage_completion_tokens = 0
                self._last_schema_warnings = {"extra_ids": 0, "duplicate_ids": 0, "residue_rejected": 0}
                if not only_missing:
                    self._last_speakers = {}
                    self._last_src_fixes = {}

            glossary = self.parse_glossary(glossary_text)
            lang_code = (source_lang or "auto").strip().lower()
            if lang_code in ("", "auto", "none"):
                lang_name = "外语"
            else:
                base = lang_code.replace("_", "-").split("-")[0]
                lang_name = LANG_NAMES.get(base, lang_code)

            batch_size = max(1, min(20, int(batch_size or 12)))
            context_size = max(0, min(4, int(context_size if context_size is not None else 3)))

            all_work: List[Dict[str, Any]] = []
            todo_work: List[Dict[str, Any]] = []
            preserved: Dict[int, str] = {}
            for i, seg in enumerate(segments):
                src = str(seg.get("src_text") or "").strip()
                existing_zh = str(seg.get("zh_text") or "").strip()
                if src:
                    entry = {"id": i + 1, "src": src, "idx": i}
                    all_work.append(entry)
                    if only_missing and existing_zh and seg.get("translate_status") != "failed":
                        preserved[i + 1] = existing_zh
                    else:
                        todo_work.append(entry)

            if not all_work:
                result["message"] = "所有字幕的原文都是空的，没有需要翻译的内容。"
                return result
            if only_missing and not todo_work:
                result.update({
                    "status": "success",
                    "segments": [dict(s) for s in segments],
                    "message": "所有字幕均已有译文，无需补翻。",
                    "stats": {"total": len(all_work), "succeeded": len(all_work), "failed": 0, "fallback_count": 0, "elapsed_s": 0.0},
                })
                return result

            # 构建全片对话大纲（前 64 条、最多 1600 字符），供并发批次跨批共享剧情场景与人名上下文
            overview_lines = [str(w["src"])[:90] for w in all_work[:64] if str(w.get("src") or "").strip()]
            self._current_dialogue_overview = " / ".join(overview_lines)[:1600]

            # 索引映射用于从完整序列中精确取上下文
            pos_by_idx = {w["idx"]: pos for pos, w in enumerate(all_work)}
            total_todo = len(todo_work)
            tasks: List[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = []
            for start_i in range(0, total_todo, batch_size):
                batch = todo_work[start_i:start_i + batch_size]
                if context_size:
                    first_pos = pos_by_idx[batch[0]["idx"]]
                    last_pos = pos_by_idx[batch[-1]["idx"]]
                    before = all_work[max(0, first_pos - context_size):first_pos]
                    after = all_work[last_pos + 1:min(len(all_work), last_pos + 1 + context_size)]
                    ctx = before + after
                else:
                    ctx = []
                tasks.append((batch, ctx))

            workers = 1 if self._is_local else min(self._CLOUD_WORKERS, len(tasks))
            translated: Dict[int, str] = dict(preserved)
            item_meta: Dict[int, Dict[str, Any]] = {
                tid: {"status": "ok", "error": ""} for tid in preserved
            }
            lock = threading.Lock()
            stop_event = threading.Event()
            transport_state = {
                "lock": threading.Lock(),
                "consecutive_failures": 0,
                "trip_threshold": 2 if len(tasks) <= 4 else 3,
                "batch_latencies": [],
            }
            done_items = 0

            def _run_one(batch_pair):
                b, c = batch_pair
                local_out: Dict[int, str] = {}
                local_meta: Dict[int, Dict[str, Any]] = {}
                self._fill(
                    b, c, local_out, glossary, lang_name,
                    stop_event=stop_event, item_meta=local_meta, transport_state=transport_state,
                )
                return b, local_out, local_meta

            self._emit(progress_callback, 3.0, f"并发翻译中（{workers} 路并行，待翻 {total_todo} 条）…")

            if workers <= 1:
                for pair in tasks:
                    b, local_out, local_meta = _run_one(pair)
                    translated.update(local_out)
                    item_meta.update(local_meta)
                    done_items += len(b)
                    self._emit(progress_callback, (done_items / total_todo) * 96.0, f"翻译中 {done_items}/{total_todo} 条")
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futs = [pool.submit(_run_one, pair) for pair in tasks]
                    for fut in as_completed(futs):
                        b, local_out, local_meta = fut.result()
                        with lock:
                            translated.update(local_out)
                            item_meta.update(local_meta)
                            done_items += len(b)
                            pct = (done_items / total_todo) * 96.0
                            self._emit(progress_callback, pct, f"并发翻译中 {done_items}/{total_todo} 条")

            # 术语锁定：仅开启术语表时限量补译
            budget = self._TERM_RETRY_BUDGET
            if glossary and not stop_event.is_set():
                for item in todo_work:
                    zh = translated.get(item["id"], "")
                    if not zh or budget <= 0:
                        continue
                    hits = self._term_hits(item["src"], glossary)
                    missing_terms = [d for _, d, _ in hits if d and d not in zh]
                    if not missing_terms:
                        continue
                    budget -= 1
                    try:
                        fixed = self._translate_batch(
                            [item], [], glossary, lang_name,
                            extra_rule="本次译文必须原样出现这些术语：" + "、".join(sorted(set(missing_terms))),
                        )
                        if fixed.get(item["id"]):
                            translated[item["id"]] = fixed[item["id"]]
                    except _FatalAPIError:
                        raise
                    except PipelineError:
                        continue

            out_segments: List[Dict[str, Any]] = []
            failed_ids: List[int] = []
            fallback_count = 0
            for i, seg in enumerate(segments):
                item = dict(seg)
                tid = i + 1
                src = str(seg.get("src_text") or "").strip()
                if tid in self._last_src_fixes and self._last_src_fixes[tid]:
                    item.setdefault("raw_src_text", src)
                    src = self._last_src_fixes[tid]
                    item["src_text"] = src
                zh = translated.get(tid, "")
                meta = item_meta.get(tid, {})
                hits = self._term_hits(src, glossary) if glossary else []
                item["zh_text"] = zh
                item["matched_terms"] = list(dict.fromkeys([d for _, d, _ in hits if d]))
                # 若用户未手工指定 speaker=1，则采用大模型根据对话语义识别的 spk (0/1)
                if item.get("speaker") not in (0, 1) or (not only_missing and item.get("speaker", 0) == 0):
                    if tid in self._last_speakers:
                        item["speaker"] = int(self._last_speakers[tid])
                    else:
                        item["speaker"] = 1 if item.get("speaker") == 1 else 0
                if not src:
                    item["translate_status"] = "skipped"
                    item["translate_error"] = ""
                elif zh:
                    st = meta.get("status") or "ok"
                    if st == "fallback":
                        fallback_count += 1
                    item["translate_status"] = st
                    item["translate_error"] = ""
                else:
                    failed_ids.append(tid)
                    item["translate_status"] = "failed"
                    item["translate_error"] = meta.get("error") or self._last_error or "未获得有效译文"
                out_segments.append(item)

            elapsed = round(time.monotonic() - t0, 2)
            lats = sorted(transport_state["batch_latencies"])
            avg_batch = round(sum(lats) / len(lats), 3) if lats else 0.0
            p95_batch = round(lats[min(len(lats) - 1, int(math.ceil(len(lats) * 0.95)) - 1)], 3) if lats else 0.0
            total = len(all_work)
            failed = len(failed_ids)
            stats = {
                "total": total,
                "succeeded": total - failed,
                "failed": failed,
                "failed_ids": failed_ids,
                "fallback_count": fallback_count,
                "elapsed_s": elapsed,
                "avg_batch_s": avg_batch,
                "p95_batch_s": p95_batch,
                "batch_count": len(tasks),
                "workers": workers,
                "model": self.model,
                "prompt_tokens": self._usage_prompt_tokens,
                "completion_tokens": self._usage_completion_tokens,
                "schema_warnings": dict(self._last_schema_warnings),
            }

            if total - failed <= 0:
                result["segments"] = out_segments
                result["stats"] = stats
                result["message"] = self._diagnose_translate_failure()
                return result

            logger.info(
                "并发翻译完成：%d/%d 条成功（兜底 %d，失败 %d），耗时 %.2fs（%d 路，avg=%.2fs p95=%.2fs）",
                total - failed, total, fallback_count, failed, elapsed, workers, avg_batch, p95_batch,
            )
            self._emit(progress_callback, 100.0, f"翻译完成（耗时 {elapsed:.1f}s）")
            if failed > 0:
                msg = (
                    f"部分翻译完成：{total - failed}/{total} 条成功，{failed} 条失败"
                    f"（ID: {', '.join(map(str, failed_ids[:8]))}{'…' if failed > 8 else ''}，"
                    f"耗时 {elapsed:.1f} 秒）。可点击「补翻失败条目」仅重试失败项。"
                )
                result.update({"status": "partial", "segments": out_segments, "message": msg, "stats": stats})
                return result

            msg = f"翻译完成：{total}/{total} 条全部成功（耗时 {elapsed:.1f} 秒"
            if fallback_count:
                msg += f"，单句兜底 {fallback_count} 条"
            msg += "）。"
            if glossary:
                msg += f"术语表已生效（{len(glossary)} 条）。"
            result.update({"status": "success", "segments": out_segments, "message": msg, "stats": stats})
            return result

        except _FatalAPIError as exc:
            result["message"] = str(exc)
            logger.warning("翻译中止（致命错误）：%s", exc)
            return result
        except PipelineError as exc:
            result["message"] = str(exc)
            logger.warning("translate_segments 失败：%s", exc)
            return result
        except Exception as exc:
            logger.exception("translate_segments 未预期异常")
            result["message"] = f"翻译过程中出现未预期的错误（{type(exc).__name__}）。请重试。"
            return result


# --------------------------------------------------------------------------- #
# 第 2 部分 · 双语导出与硬压
# --------------------------------------------------------------------------- #

#: 前端样式面板的预览画布基准：字号 17 / 底边距 18 都是按这个尺寸给的值。
#: 真正换算时只看短边，所以竖屏视频也不会被放大成巨型字幕。
PREVIEW_CANVAS = (640, 360)

DEFAULT_STYLE: Dict[str, Any] = {
    "layout": "zh_top",          # zh_top | en_top | zh_only
    "font_size": 17,             # 预览画布下的字号
    "margin_bottom": 18,         # 预览画布下的底边距
    "bg_opacity": 68,            # 字幕底色不透明度 0~100（0 = 不要底色）
    "primary_color": "#fcd34d",  # 中文字色
    "secondary_color": "#e5e7eb",  # 原文色（略暗一档，形成层级）
    "font_zh": "Microsoft YaHei",
    "font_en": "Arial",
    "zh_scale": 1.0,
    "en_scale": 0.78,            # 原文比中文小一档
    "reference_width": PREVIEW_CANVAS[0],
    "reference_height": PREVIEW_CANVAS[1],
}


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


class SubtitleRenderer:
    """双语 SRT/ASS 导出 + ffmpeg 硬压（NVENC 优先，自动降级 libx264）。"""

    def __init__(
        self,
        bin_dir: str = "./bin",
        work_dir: str = "./workspace",
        resource_bin_dir: Optional[str] = None,
    ) -> None:
        self.base_dir = Path(__file__).resolve().parent
        self.bin_dir = Path(os.path.expanduser(str(bin_dir)))
        if not self.bin_dir.is_absolute():
            self.bin_dir = (self.base_dir / self.bin_dir).resolve()
        self.resource_bin_dir = Path(os.path.expanduser(str(resource_bin_dir))) if resource_bin_dir else self.bin_dir
        if not self.resource_bin_dir.is_absolute():
            self.resource_bin_dir = (self.base_dir / self.resource_bin_dir).resolve()
        self.work_dir = Path(os.path.expanduser(str(work_dir)))
        if not self.work_dir.is_absolute():
            self.work_dir = (self.base_dir / self.work_dir).resolve()
        for d in (self.bin_dir, self.work_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        self.ffmpeg = _which("ffmpeg", self.resource_bin_dir) or _which("ffmpeg", self.bin_dir)
        self.ffprobe = _which("ffprobe", self.resource_bin_dir) or _which("ffprobe", self.bin_dir)

    # ------------------------------------------------------------------ #
    # 小工具
    # ------------------------------------------------------------------ #

    @staticmethod
    def _emit(cb: Optional[ProgressCallback], percent: float, text: str) -> None:
        if cb is None:
            return
        try:
            cb(round(max(0.0, min(100.0, float(percent))), 1), str(text))
        except Exception:  # noqa: BLE001
            logger.debug("progress_callback 抛异常，已忽略", exc_info=True)

    def _abs(self, path: str, *, for_output: bool = False) -> Path:
        p = Path(os.path.expanduser(str(path)))
        if p.is_absolute():
            return p
        if for_output:
            parts = p.parts
            if parts and parts[0].lower() == "workspace":
                p = Path(*parts[1:]) if len(parts) > 1 else Path("output")
            return (self.work_dir / p).resolve()
        return (Path.cwd() / p).resolve()

    @staticmethod
    def _srt_time(sec: float) -> str:
        ms = max(0, int(round(float(sec) * 1000)))
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def _ass_time(sec: float) -> str:
        cs = max(0, int(round(float(sec) * 100)))
        h, rem = divmod(cs, 360000)
        m, rem = divmod(rem, 6000)
        s, cs = divmod(rem, 100)
        return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"

    @staticmethod
    def _esc(text: str) -> str:
        """ASS 里 { } 是样式块起始符，直接换成全角，视觉几乎无差别但不会吃字幕。"""
        t = (text or "").replace("\ufeff", "")
        t = t.replace("\r", " ").replace("\n", " ")
        t = t.replace("{", "｛").replace("}", "｝")
        return re.sub(r"\s+", " ", t).strip()

    @staticmethod
    def _hex_to_ass(color: str, opacity_percent: Optional[float] = None) -> str:
        """#RRGGBB (+ 不透明度) → &HAABBGGRR（ASS 是 ABGR，且 AA=00 为不透明）。"""
        c = str(color or "#ffffff").strip().lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        if len(c) < 6 or not re.fullmatch(r"[0-9A-Fa-f]{6}", c[:6]):
            c = "ffffff"
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        aa = 0
        if opacity_percent is not None:
            op = _clamp(float(opacity_percent), 0.0, 100.0) / 100.0
            aa = int(round(255 * (1.0 - op)))
        return f"&H{aa:02X}{b:02X}{g:02X}{r:02X}"

    def _merged_style(self, style_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        style = dict(DEFAULT_STYLE)
        if isinstance(style_config, dict):
            for k, v in style_config.items():
                if v is not None:
                    style[k] = v
        if style.get("layout") not in ("zh_top", "en_top", "zh_only"):
            style["layout"] = "zh_top"
        return style

    @staticmethod
    def _layout_lines(seg: Dict[str, Any], layout: str) -> List[str]:
        zh = str(seg.get("zh_text") or "").strip()
        en = str(seg.get("src_text") or "").strip()
        if layout == "zh_only":
            return [zh or en] if (zh or en) else []
        if layout == "en_top":
            return [x for x in (en, zh) if x]
        return [x for x in (zh, en) if x]   # zh_top

    def _scale(self, video_width: int, video_height: int, style: Dict[str, Any]) -> float:
        """按「短边」等比缩放：640×360 预览 → 1080P 是 3 倍，竖屏 1080×1920 也是 3 倍。"""
        ref_short = max(1.0, min(float(style.get("reference_width", 640)),
                                 float(style.get("reference_height", 360))))
        actual_short = max(1.0, float(min(video_width, video_height)))
        return _clamp(actual_short / ref_short, 0.5, 8.0)

    # ------------------------------------------------------------------ #
    # 1) 导出 SRT
    # ------------------------------------------------------------------ #

    def export_srt(
        self,
        segments: List[Dict[str, Any]],
        output_path: str,
        layout: str = "zh_top",
    ) -> Dict[str, Any]:
        res: Dict[str, Any] = {"status": "error", "srt_path": None, "count": 0, "message": ""}
        try:
            if not segments:
                res["message"] = "没有字幕内容可导出。"
                return res
            if layout not in ("zh_top", "en_top", "zh_only"):
                layout = "zh_top"

            out = self._abs(output_path, for_output=True)
            out.parent.mkdir(parents=True, exist_ok=True)

            lines: List[str] = []
            idx = 0
            for seg in segments:
                parts = self._layout_lines(seg, layout)
                if not parts:
                    continue
                idx += 1
                start = float(seg.get("start") or 0.0)
                end = max(float(seg.get("end") or 0.0), start + 0.2)
                lines.append(str(idx))
                lines.append(f"{self._srt_time(start)} --> {self._srt_time(end)}")
                lines.extend(parts)
                lines.append("")

            if idx == 0:
                res["message"] = "所有字幕都是空的，没有生成任何字幕行。"
                return res

            with open(out, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write("\r\n".join(lines))

            res.update({
                "status": "success",
                "srt_path": str(out),
                "count": idx,
                "message": f"已导出双语 SRT（{layout}）：{idx} 条 → {out.name}",
            })
            return res
        except OSError as exc:
            res["message"] = f"写入 SRT 失败：{exc}（文件可能正被播放器占用）"
            return res
        except Exception as exc:  # noqa: BLE001
            logger.exception("export_srt 未预期异常")
            res["message"] = f"导出 SRT 失败（{type(exc).__name__}），请换一个输出目录试试。"
            return res

    # ------------------------------------------------------------------ #
    # 2) 导出 ASS
    # ------------------------------------------------------------------ #

    def export_ass(
        self,
        segments: List[Dict[str, Any]],
        output_path: str,
        style_config: Optional[Dict[str, Any]] = None,
        video_width: int = 1920,
        video_height: int = 1080,
    ) -> Dict[str, Any]:
        res: Dict[str, Any] = {"status": "error", "ass_path": None, "count": 0, "message": ""}
        try:
            if not segments:
                res["message"] = "没有字幕内容可导出。"
                return res

            style = self._merged_style(style_config)
            layout = style["layout"]
            w = int(video_width) if int(video_width) > 0 else 1920
            h = int(video_height) if int(video_height) > 0 else 1080
            scale = self._scale(w, h, style)

            fs = _clamp(float(style.get("font_size", 17)) * scale, 8.0, 400.0)
            mb = _clamp(float(style.get("margin_bottom", 18)) * scale, 0.0, h * 0.5)
            zh_size = _clamp(fs * float(style.get("zh_scale", 1.0)), 8.0, 400.0)
            en_size = _clamp(fs * float(style.get("en_scale", 0.78)), 8.0, 400.0)

            opacity = _clamp(float(style.get("bg_opacity", 68)), 0.0, 100.0)
            boxed = opacity > 0.5
            border_style = 3 if boxed else 1
            zh_outline = max(1.0, round(zh_size * (0.16 if boxed else 0.055), 1))
            en_outline = max(1.0, round(en_size * (0.16 if boxed else 0.055), 1))
            shadow = 0 if boxed else 1

            zh_col = self._hex_to_ass(style.get("primary_color", "#fcd34d"))
            zh_col_b = self._hex_to_ass(style.get("speaker_b_color", "#7dd3fc"))
            en_col = self._hex_to_ass(style.get("secondary_color", "#e5e7eb"))
            outline_col = "&H00000000"
            back_col = self._hex_to_ass("#000000", opacity) if boxed else "&HFF000000"

            # 两条字幕的垂直位置：中文在上时，英文贴底、中文压在英文上方
            en_mv = mb
            zh_mv = mb + en_size * 1.15
            if layout == "en_top":
                zh_mv = mb
                en_mv = mb + zh_size * 1.15
            elif layout == "zh_only":
                zh_mv = mb

            # 双人重叠对话时的第二行高度偏移（避免两位说话人在成片中文字重影）
            row_step = (zh_size + en_size) * 1.25 if layout != "zh_only" else zh_size * 1.35

            header = [
                "[Script Info]",
                "; Generated by core_pipeline.SubtitleRenderer",
                "ScriptType: v4.00+",
                f"PlayResX: {w}",
                f"PlayResY: {h}",
                "WrapStyle: 2",
                "ScaledBorderAndShadow: yes",
                "YCbCr Matrix: TV.709",
                "",
                "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
                "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
                "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                "MarginL, MarginR, MarginV, Encoding",
                f"Style: ZH,{style.get('font_zh', 'Microsoft YaHei')},{zh_size:.1f},"
                f"{zh_col},&H000000FF,{outline_col},{back_col},0,0,0,0,100,100,0,0,"
                f"{border_style},{zh_outline},{shadow},2,40,40,{int(round(zh_mv))},1",
                f"Style: ZH_B,{style.get('font_zh', 'Microsoft YaHei')},{zh_size:.1f},"
                f"{zh_col_b},&H000000FF,{outline_col},{back_col},0,0,0,0,100,100,0,0,"
                f"{border_style},{zh_outline},{shadow},2,40,40,{int(round(zh_mv))},1",
                f"Style: EN,{style.get('font_en', 'Arial')},{en_size:.1f},"
                f"{en_col},&H000000FF,{outline_col},{back_col},0,0,0,0,100,100,0,0,"
                f"{border_style},{en_outline},{shadow},2,40,40,{int(round(en_mv))},1",
                "",
                "[Events]",
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            ]

            events: List[str] = []
            count = 0
            # 对称双轨避让：无论 A 先说 B 插话还是 B 先说 A 插话，只要与另一说话人真实重叠即自动错行
            lane_end: List[float] = [-1.0, -1.0]
            lane_spk: List[int] = [-1, -1]
            for seg in segments:
                start = float(seg.get("start") or 0.0)
                end = max(float(seg.get("end") or 0.0), start + 0.08)
                a, b = self._ass_time(start), self._ass_time(end)
                zh = self._esc(seg.get("zh_text") or "")
                en = self._esc(seg.get("src_text") or "")
                spk = 1 if str(seg.get("speaker", 0)) == "1" or seg.get("speaker") == 1 else 0
                zh_style = "ZH_B" if spk == 1 else "ZH"

                # 若底轨（lane 0）正被另一位说话人占用，则本条抬升到上轨（lane 1）
                if start < lane_end[0] - 0.02 and lane_spk[0] != spk:
                    lane = 1
                else:
                    lane = 0
                lane_end[lane] = max(lane_end[lane], end)
                lane_spk[lane] = spk
                lane_shift = row_step if lane == 1 else 0.0

                if layout == "zh_only":
                    text = zh or en
                    if text:
                        events.append(
                            f"Dialogue: 0,{a},{b},{zh_style},,0,0,{int(round(zh_mv + lane_shift))},,{text}")
                        count += 1
                    continue

                top, bottom = (zh, en) if layout == "zh_top" else (en, zh)
                style_top, style_bottom = (zh_style, "EN") if layout == "zh_top" else ("EN", zh_style)
                mv_top = int(round((zh_mv if layout == "zh_top" else en_mv) + lane_shift))
                mv_bottom = int(round((en_mv if layout == "zh_top" else zh_mv) + lane_shift))

                if top:
                    events.append(f"Dialogue: 0,{a},{b},{style_top},,0,0,{mv_top},,{top}")
                    count += 1
                if bottom:
                    events.append(f"Dialogue: 0,{a},{b},{style_bottom},,0,0,{mv_bottom},,{bottom}")
                    count += 1

            if not events:
                res["message"] = "所有字幕都是空的，没有生成任何 ASS 事件。"
                return res

            out = self._abs(output_path, for_output=True)
            out.parent.mkdir(parents=True, exist_ok=True)
            with open(out, "w", encoding="utf-8-sig", newline="\r\n") as fh:
                fh.write("\n".join(header) + "\n")
                fh.write("\n".join(events) + "\n")

            res.update({
                "status": "success",
                "ass_path": str(out),
                "count": count,
                "message": (f"已导出 ASS：{count} 行事件，画布 {w}×{h}，"
                            f"字号 中 {zh_size:.0f} / 原文 {en_size:.0f}。"),
            })
            return res
        except OSError as exc:
            res["message"] = f"写入 ASS 失败：{exc}"
            return res
        except Exception as exc:  # noqa: BLE001
            logger.exception("export_ass 未预期异常")
            res["message"] = f"导出 ASS 失败（{type(exc).__name__}）。"
            return res

    # ------------------------------------------------------------------ #
    # 3) 硬压
    # ------------------------------------------------------------------ #

    def _encoder_ok(self, encoder: str) -> bool:
        if not self.ffmpeg:
            return False
        code, _ = _run_quiet(
            [self.ffmpeg, "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "color=c=black:s=256x144:d=0.1",
             "-c:v", encoder, "-f", "null", "-"],
            timeout=25,
        )
        return code == 0

    def _encode_once(
        self,
        encoder: str,
        video: Path,
        ass_file: Path,
        out: Path,
        duration: Optional[float],
        cb: Optional[ProgressCallback],
        lo: float = 0.0,
        hi: float = 100.0,
        fps: Optional[str] = None,
        crf: int = 23,
        audio_bitrate: str = "192k",
        scale_size: Optional[Tuple[int, int]] = None,
    ) -> Tuple[int, List[str]]:
        """跑一次压制。返回 (returncode, stderr 尾部若干行)。"""
        assert self.ffmpeg
        crf_val = str(int(_clamp(float(crf or 23), 14, 34)))
        abr = str(audio_bitrate or "192k").strip()
        if not re.fullmatch(r"\d{2,4}k", abr, re.I):
            abr = "192k"

        vf_parts: List[str] = []
        if scale_size and scale_size[0] >= 16 and scale_size[1] >= 16:
            tw = scale_size[0] - (scale_size[0] % 2)
            th = scale_size[1] - (scale_size[1] % 2)
            vf_parts.append(f"scale={tw}:{th}:force_original_aspect_ratio=decrease,pad={tw}:{th}:(ow-iw)/2:(oh-ih)/2")
        # 关键：ass 只用纯文件名，cwd 切到字幕目录，彻底绕开 C: 和反斜杠解析问题
        vf_parts.append(f"ass={ass_file.name}")

        cmd = [
            self.ffmpeg, "-hide_banner", "-nostdin", "-y",
            "-progress", "pipe:1", "-nostats", "-loglevel", "error",
            "-i", str(video),
            "-vf", ",".join(vf_parts),
        ]
        if fps and fps.isdigit() and 1 <= int(fps) <= 240:
            cmd += ["-r", fps]
        cmd += [
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", encoder,
        ]
        if encoder == "h264_nvenc":
            cmd += ["-preset", "p4", "-rc", "vbr", "-cq", crf_val, "-b:v", "0"]
        else:
            cmd += ["-preset", "veryfast", "-crf", crf_val]
        cmd += [
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", abr,
            "-movflags", "+faststart",
            str(out),
        ]

        try:
            proc = subprocess.Popen(
                cmd, cwd=str(ass_file.parent),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                **_no_window_kwargs(),
            )
        except FileNotFoundError:
            return -1, ["无法启动 ffmpeg.exe，请确认 bin 目录下文件完整。"]
        except OSError as exc:
            return -1, [f"无法启动 ffmpeg：{exc}"]

        tail: List[str] = []
        last_pct = -1.0
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            sec: Optional[float] = None
            if line.startswith(("out_time_ms=", "out_time_us=")):
                val = line.split("=", 1)[1].strip()
                if val.isdigit():
                    sec = int(val) / 1_000_000.0
            elif line.startswith("time="):
                m = re.match(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", line)
                if m:
                    sec = (int(m.group(1)) * 3600 + int(m.group(2)) * 60
                           + float(m.group(3)))
            if sec is not None and duration and duration > 0:
                pct = lo + (hi - lo) * min(sec / duration, 0.999)
                if pct - last_pct >= 0.5:
                    last_pct = pct
                    self._emit(cb, pct, f"压制中… {min(sec / duration, 1) * 100:.0f}%")
                continue
            if line.startswith(("progress=", "frame=", "fps=", "bitrate=",
                                "total_size=", "out_time=", "dup_frames=",
                                "drop_frames=", "speed=", "stream_0_0_q=")):
                continue
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)

        proc.wait()
        return proc.returncode, tail

    def burn_subtitles(
        self,
        video_path: str,
        segments: List[Dict[str, Any]],
        output_path: str,
        style_config: Optional[Dict[str, Any]] = None,
        prefer_nvenc: bool = True,
        progress_callback: Optional[ProgressCallback] = None,
        fps: str = "source",
        render_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """生成临时 .ass → ffmpeg 硬压。NVENC 不可用或失败时自动降级 libx264。

        fps: "source"/空 = 跟随源帧率；数字字符串（如 "60"/"120"）= 强制输出帧率。
        render_config: 可选 {"crf": 23, "audio_bitrate": "192k", "target_width": 0, "target_height": 0}。
        """
        res: Dict[str, Any] = {
            "status": "error",
            "output_video_path": None,
            "ass_path": None,
            "encoder_used": None,
            "message": "",
        }
        try:
            if not self.ffmpeg:
                res["message"] = ("未找到 ffmpeg.exe，无法压制。"
                                  "请把 ffmpeg.exe 放到程序目录的 bin 文件夹下后重启。")
                return res
            if not segments:
                res["message"] = "没有字幕内容可压制。"
                return res

            video = self._abs(video_path)
            if not video.is_file():
                res["message"] = f"找不到视频文件：{video_path}"
                return res
            out = self._abs(output_path, for_output=True)
            try:
                out.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                res["message"] = f"输出目录不可写：{exc}"
                return res
            if out.resolve() == video.resolve():
                res["message"] = "输出文件不能和源视频同名，请换一个文件名。"
                return res

            src_w, src_h = _media_size(self.ffmpeg, self.ffprobe, str(video))
            rcfg = render_config if isinstance(render_config, dict) else {}
            tw = int(rcfg.get("target_width") or 0)
            th = int(rcfg.get("target_height") or 0)
            scale_size: Optional[Tuple[int, int]] = None
            if tw >= 320 and th >= 240 and (tw != src_w or th != src_h):
                width, height = tw - (tw % 2), th - (th % 2)
                scale_size = (width, height)
            else:
                width, height = src_w, src_h
            crf = int(rcfg.get("crf") or 23)
            audio_bitrate = str(rcfg.get("audio_bitrate") or "192k")
            duration = _media_duration(self.ffmpeg, self.ffprobe, str(video))

            # 临时 ass 用纯 ASCII 文件名放在输出目录，压完再改成可读的名字
            tag = hashlib.md5(str(out).encode("utf-8")).hexdigest()[:8]
            tmp_ass = out.parent / f"_subs_{tag}.ass"
            ass_res = self.export_ass(segments, str(tmp_ass), style_config, width, height)
            if ass_res.get("status") != "success":
                res["message"] = ass_res.get("message") or "生成 ASS 字幕失败。"
                return res

            self._emit(progress_callback, 1.0, f"字幕已生成，开始压制（{width}×{height}）…")

            encoders: List[str] = []
            fallback_reason = ""
            if prefer_nvenc:
                if self._encoder_ok("h264_nvenc"):
                    encoders = ["h264_nvenc", "libx264"]
                else:
                    logger.info("h264_nvenc 不可用，直接使用 libx264")
                    fallback_reason = "未检测到可用 NVIDIA NVENC，已自动切换 CPU (libx264)"
                    self._emit(progress_callback, 2.0, "未检测到可用的 NVENC，改用 CPU 编码…")
                    encoders = ["libx264"]
            else:
                encoders = ["libx264"]

            used = encoders[0]
            code, tail = -1, []
            fps_val = None
            if fps and str(fps).strip().lower() not in ("", "source", "auto", "none"):
                fps_val = str(fps).strip()
            for i, enc in enumerate(encoders):
                used = enc
                code, tail = self._encode_once(
                    enc, video, tmp_ass, out, duration, progress_callback,
                    lo=2.0 + i * 0.0, hi=100.0, fps=fps_val,
                    crf=crf, audio_bitrate=audio_bitrate, scale_size=scale_size,
                )
                if code == 0:
                    break
                if i + 1 < len(encoders):
                    reason_tail = " | ".join(tail[-2:]) if tail else "编码器返回非零"
                    fallback_reason = f"{enc} 失败（{reason_tail[:100]}），已自动回退 {encoders[i + 1]}"
                    logger.warning("%s 压制失败，准备降级：%s", enc, " | ".join(tail[-3:]))
                    try:
                        if out.exists() and out.stat().st_size > 0:
                            out.unlink()
                    except OSError:
                        pass
                    self._emit(progress_callback, 2.0, "显卡编码失败，已自动切换 CPU 编码重试…")

            if code != 0 or not out.is_file() or out.stat().st_size < 1024:
                try:
                    tmp_ass.unlink(missing_ok=True)
                except OSError:
                    pass
                detail = " | ".join(tail[-4:]) if tail else "未知原因"
                low = detail.lower()
                if any(k in low for k in ("nvenc", "cuda", "no capable", "cannot load")):
                    hint = "显卡编码器不可用且 CPU 回退也失败。"
                elif "no such file" in low or "cannot find" in low:
                    hint = "输入文件或字幕文件找不到。"
                elif "invalid data" in low or "moov atom" in low:
                    hint = "视频文件损坏或未下载完整。"
                else:
                    hint = "压制失败。"
                res["message"] = f"{hint}（ffmpeg 返回 {code}）：{detail[:260]}"
                return res

            # 临时名 → 可读名（这一步不涉及 -vf，中文名不会出问题）
            final_ass = out.parent / f"{_safe_name(out.stem, 'subtitles')}.ass"
            ass_path = tmp_ass
            try:
                if final_ass.exists():
                    final_ass.unlink()
                tmp_ass.replace(final_ass)
                ass_path = final_ass
            except OSError:
                ass_path = tmp_ass

            self._emit(progress_callback, 100.0, "压制完成")
            enc_label = "NVIDIA 显卡（NVENC）" if used == "h264_nvenc" else "CPU（libx264）"
            res.update({
                "status": "success",
                "output_video_path": str(out),
                "ass_path": str(ass_path),
                "encoder_used": used,
                "fallback_reason": fallback_reason,
                "message": f"压制完成：{out.name}（编码器：{enc_label}）。",
            })
            return res

        except PipelineError as exc:
            res["message"] = str(exc)
            return res
        except Exception as exc:  # noqa: BLE001
            logger.exception("burn_subtitles 未预期异常")
            res["message"] = (
                f"压制过程中出现未预期的错误（{type(exc).__name__}）。"
                "请确认视频没有损坏、输出目录有写入权限。"
            )
            return res


# --------------------------------------------------------------------------- #
# 本地命令行测试
# --------------------------------------------------------------------------- #

def _cli_progress(percent: float, text: str = "") -> None:
    bar = "#" * int(percent / 4)
    sys.stdout.write(f"\r[{bar:<25}] {percent:5.1f}%  {text[:46]:<46}")
    sys.stdout.flush()
    if percent >= 100:
        sys.stdout.write("\n")


def _main() -> int:
    import argparse
    import json

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(
        description="core_pipeline 本地测试（预处理 / 识别 / 解析SRT / 翻译 / 压制）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="查看硬件加速与依赖状态")

    p_prep = sub.add_parser("prepare", help="下载/提取音频与原生字幕")
    p_prep.add_argument("source", help="本地视频路径或视频 URL")
    p_prep.add_argument("--lang", default="auto", help="源语言，如 en/ja/auto")
    p_prep.add_argument("--bin", default="./bin")
    p_prep.add_argument("--work", default="./workspace")

    p_tr = sub.add_parser("transcribe", help="对 wav 做语音识别")
    p_tr.add_argument("audio", help="16k wav 路径")
    p_tr.add_argument("--lang", default="auto")
    p_tr.add_argument("--model", default="large-v3-turbo")
    p_tr.add_argument("--bin", default="./bin")
    p_tr.add_argument("--work", default="./workspace")

    p_srt = sub.add_parser("srt", help="解析原生 .srt（走同一套分段器）")
    p_srt.add_argument("path", help=".srt 文件路径")

    p_tl = sub.add_parser("translate", help="翻译 segments.json（需环境变量 DEEPSEEK_API_KEY）")
    p_tl.add_argument("json", help="含 segments 数组的 json 文件")
    p_tl.add_argument("--lang", default="auto")
    p_tl.add_argument("--glossary", default="", help="术语表文件路径或直接传多行文本")
    p_tl.add_argument("--batch", type=int, default=10)
    p_tl.add_argument("--context", type=int, default=2)
    p_tl.add_argument("--out", default="", help="结果写到指定 json（默认只打印）")

    p_burn = sub.add_parser("burn", help="烧录字幕（NVENC 自动降级）")
    p_burn.add_argument("video", help="源视频")
    p_burn.add_argument("json", help="含 segments（必须带 zh_text）的 json 文件")
    p_burn.add_argument("output", help="输出视频")
    p_burn.add_argument("--no-nvenc", action="store_true", help="强制用 libx264")
    p_burn.add_argument("--bin", default="./bin")
    p_burn.add_argument("--work", default="./workspace")

    args = parser.parse_args()

    if args.cmd == "info":
        mp = MediaProcessor()
        print(json.dumps(mp.get_hardware_info(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "prepare":
        mp = MediaProcessor(bin_dir=args.bin, work_dir=args.work)
        res = mp.prepare_media(args.source, source_lang=args.lang, progress_callback=_cli_progress)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["status"] == "success" else 1

    if args.cmd == "transcribe":
        mp = MediaProcessor(bin_dir=args.bin, work_dir=args.work)
        res = mp.transcribe_audio(
            args.audio, source_lang=args.lang,
            model_size=args.model, progress_callback=_cli_progress,
        )
        for seg in res.get("segments", [])[:10]:
            print(f"  #{seg['id']:<3} {seg['start']:>7.2f} --> {seg['end']:<7.2f}  {seg['src_text']}")
        if len(res.get("segments", [])) > 10:
            print(f"  ... 共 {len(res['segments'])} 条")
        res_show = {k: v for k, v in res.items() if k != "segments"}
        res_show["segment_count"] = len(res.get("segments", []))
        print(json.dumps(res_show, ensure_ascii=False, indent=2))
        return 0 if res["status"] == "success" else 1

    if args.cmd == "srt":
        res = parse_srt_file(args.path)
        for seg in res.get("segments", [])[:10]:
            print(f"  #{seg['id']:<3} {seg['start']:>7.2f} --> {seg['end']:<7.2f}  {seg['src_text']}")
        if len(res.get("segments", [])) > 10:
            print(f"  ... 共 {len(res['segments'])} 条")
        show = {k: v for k, v in res.items() if k != "segments"}
        show["segment_count"] = len(res.get("segments", []))
        print(json.dumps(show, ensure_ascii=False, indent=2))
        return 0 if res["status"] == "success" else 1

    if args.cmd == "translate":
        with open(args.json, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        segs = data.get("segments", data) if isinstance(data, dict) else data
        glossary = args.glossary
        if glossary and Path(glossary).is_file():
            glossary = Path(glossary).read_text(encoding="utf-8")
        tr = SubtitleTranslator(api_key=os.environ.get("DEEPSEEK_API_KEY", ""))
        res = tr.translate_segments(
            segs, glossary_text=glossary, source_lang=args.lang,
            batch_size=args.batch, context_size=args.context,
            progress_callback=_cli_progress,
        )
        for seg in res.get("segments", [])[:10]:
            print(f"  #{seg['id']:<3} {seg.get('src_text', '')[:40]}")
            print(f"        → {seg.get('zh_text', '')}  {seg.get('matched_terms') or ''}")
        show = {k: v for k, v in res.items() if k != "segments"}
        show["segment_count"] = len(res.get("segments", []))
        print(json.dumps(show, ensure_ascii=False, indent=2))
        if args.out and res["status"] == "success":
            Path(args.out).write_text(
                json.dumps(res["segments"], ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"已写出：{args.out}")
        return 0 if res["status"] == "success" else 1

    if args.cmd == "burn":
        with open(args.json, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        segs = data.get("segments", data) if isinstance(data, dict) else data
        rd = SubtitleRenderer(bin_dir=args.bin, work_dir=args.work)
        res = rd.burn_subtitles(
            args.video, segs, args.output,
            prefer_nvenc=not args.no_nvenc, progress_callback=_cli_progress,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["status"] == "success" else 1

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(_main())