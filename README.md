# SubFlow Studio

<p align="center">
  <b>一款面向 Windows 桌面的开源 AI 视频双语字幕制作与硬压工作台</b><br/>
  本地 Whisper (CUDA / CPU) 精准语音识别 · 并发大模型语境精翻 · 实时造型台 · NVENC / x264 一键硬压成片
</p>

<p align="center">
  <img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-emerald.svg" />
  <img alt="Platform: Windows 10/11" src="https://img.shields.io/badge/Platform-Windows%2010%2F11%20(x64)-blue.svg" />
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-3776AB.svg" />
  <img alt="CUDA 12 / CPU" src="https://img.shields.io/badge/Whisper-CUDA%2012%20%7C%20CPU%20INT8-76B900.svg" />
</p>

---

## 核心特性

- **高精度原始时间轴语音识别（faster-whisper + Silero VAD）**
  - 全段原始时间轴连续解码，不破坏音频结构，结合 Silero VAD 只读静音过滤与发声起止边界校准，彻底避免字幕提前闪现、话未说完早关与尾句滞留。
  - 支持 NVIDIA CUDA 12 / cuDNN 9（FP16 极速加速）与纯 CPU（INT8）自适应切换；支持 `small` / `medium` / `large-v3-turbo` 多档模型，首次运行推荐轻量 `small`（约 460MB）开箱即用。
  - 支持通用编辑距离专名实体聚类归一，并可一键导出 CSV / JSON 逐条时间戳核对表。

- **高可靠并发大模型精翻（支持 DeepSeek / OpenAI / 通义千问 / Kimi / 智谱 / 本地 Ollama）**
  - 6 路有界并发 + 全片对白大纲共享（`dialogue_overview`）+ 严格 JSON ID 协议校验 + `src_fix` 语音识别同音错字通用审校 + 零额外请求的智能双人说话人识别（A/B 轨道自动区分）。
  - 局部网络失败绝不用原文冒充译文，支持「补翻失败条目」增量恢复与单句上下文「AI 重译」。
  - 内置 36 个垂直行业专业术语库（可按需锁定或关闭）。

- **丝滑 60fps 交互舞台与真正无边框全屏**
  - 本机 `127.0.0.1` 零拷贝 HTTP 206 Range 流媒体服务 + `requestVideoFrameCallback` 逐帧同步 + 零冗余 DOM 渲染，预览高清视频流畅不卡顿。
  - 支持 **Windows 11 原生无边框全屏**（`F11` 切换应用全屏，双击视频画面或点击全屏图标进入纯黑零边框影院全屏，`Esc` 一键退出）。
  - 二维磁吸触控造型台与调色台，实时所见即所得调整字号、双语顺序、双人配色、描边与毛玻璃背景，并严格同步到 `.ass` 导出与硬压成片。

- **多平台导入与硬件加速压制**
  - 支持本地视频 / SRT 导入，以及基于 `yt-dlp` 的 1000+ 站点解析（内置 B 站扫码登录与代理设置，绝不强杀用户浏览器进程）。
  - 一键导出双语 `.srt`、高级样式 `.ass`，或调用 FFmpeg `h264_nvenc`（自动回退 `libx264`）硬压双语成片。

---

## 快速开始

### 方式一：直接下载 Windows 安装包（推荐）

前往 [GitHub Releases](https://github.com/no1kyousuke/asagiri-kyousuke/releases) 下载适合您的版本：
1. **轻量标准版（`SubFlowStudioSetup_Lite_CPU.exe`，约 125 MB）**：免管理员权限安装，开箱即用（CPU INT8 引擎）。
2. **NVIDIA GPU 加速扩展包（`SubFlowStudio_GPU_Pack_Setup.exe`）**：拥有 NVIDIA 独显的用户安装后可开启 CUDA 12 / FP16 加速（提速 8~10 倍）。
3. **全量 GPU 一体化安装包（`SubFlowStudioSetup.exe`）**：主程序 + CUDA 12 运行库一步到位。

> 详细使用步骤请阅读 [3分钟快速上手与常见问题.md](3分钟快速上手与常见问题.md)，或在软件左上角点击「**新手引导 / 10秒试跑**」。

---

### 方式二：从源码运行与一键构建

#### 环境要求
- Windows 10 / 11 (x64)
- Python 3.12（建议勾选 `Add python.exe to PATH`）
- Microsoft Edge WebView2 Runtime（Windows 11 默认自带）
- 可选：[Inno Setup 6](https://jrsoftware.org/isinfo.php)（如需打包 `.exe` 安装程序）
- 可选：`ffmpeg.exe`（放入 `bin/ffmpeg.exe` 或加入系统 `PATH`，用于音轨提取与硬压成片）

#### 1. 克隆仓库并安装隔离依赖
```powershell
git clone https://github.com/no1kyousuke/asagiri-kyousuke.git
cd asagiri-kyousuke

# 一键初始化隔离虚拟环境 .build_venv 并构建便携版 EXE（不会污染全局 Python）
.\build_windows.bat
```

#### 2. 开发模式直接运行
```powershell
.\.build_venv\Scripts\python.exe app.py
```

#### 3. 运行离线回归测试与质量评测
```powershell
# 全套静态校验 + 40 项 Python 单测 + 前端 JS 逻辑测试
.\run_offline_tests.bat

# （可选）20 条黄金测试集翻译质量评测（需在设置或环境变量配置 API Key）
.\.build_venv\Scripts\python.exe tests\eval_translation.py

# （可选）语音识别时间轴对齐精度评测
.\.build_venv\Scripts\python.exe tests\eval_timing.py
```

---

## 构建产物说明

运行 `build_windows.bat`（或 `python build_release.py`）会自动生成：
- `dist\SubFlow Studio\`：完整 GPU 便携版（含 CUDA 12 / cuDNN 9 DLL）
- `dist_cpu\SubFlow Studio\`：轻量 CPU 便携版（< 400 MB）
- `dist_gpu_pack\`：独立 NVIDIA CUDA 12 GPU 加速扩展包（附一键启用脚本）
- `installer\`（若本机已安装 Inno Setup 6）：自动编译上述 3 个版本的 Windows 安装程序

---

## 数据与隐私安全

- 本仓库及安装包**不包含**任何个人 `settings.json`、API Key、`cookies.txt` 或模型缓存。
- 运行时用户配置、B 站登录 Cookie、工作区缓存、Whisper 模型与日志统一保存在本机 `%LOCALAPPDATA%\SubFlow Studio\`，卸载或覆盖升级程序不会丢失个人配置。

---

## 开源协议

本项目基于 [MIT License](LICENSE) 开源，欢迎 Star、Issue 与 Pull Request！
