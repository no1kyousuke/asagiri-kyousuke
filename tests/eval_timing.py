# -*- coding: utf-8 -*-
"""SubFlow Studio 时间轴对齐独立可复现评测脚本 (P1-6)。

同时量化并验证三项核心指标：
1) 独立句开口/收声误差 <= 0.15s 的比例（中位数、均值、P90）；
2) 重叠字幕条数（必须 = 0）；
3) 相邻字幕空隙（gap）分布（确认说话间隙字幕确实消失，未被 hold 撑满）。

用法：
    .build_venv\\Scripts\\python.exe tests\\eval_timing.py
    .build_venv\\Scripts\\python.exe tests\\eval_timing.py --audio <path_to_16k.wav> --lang ja
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core_pipeline as core  # noqa: E402


def eval_audio_file(mp: core.MediaProcessor, wav_path: Path, lang: str, model_size: str = "small") -> dict:
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    res = mp.transcribe_audio(str(wav_path), source_lang=lang, model_size=model_size)
    if res.get("status") != "success":
        raise RuntimeError(f"识别失败: {res.get('message')}")
    segs = res.get("segments") or []

    pcm = mp._load_pcm16_wav(str(wav_path))
    assert pcm is not None, f"无法读取 16kHz PCM WAV: {wav_path}"
    audio, sr = pcm
    vad_raw = get_speech_timestamps(
        audio,
        VadOptions(threshold=0.35, min_speech_duration_ms=120, min_silence_duration_ms=260, speech_pad_ms=30),
        sampling_rate=sr,
    )
    raw_spans = [(sp["start"] / sr, sp["end"] / sr) for sp in vad_raw]

    onset_errs: list[float] = []
    offset_errs: list[float] = []
    for idx, seg in enumerate(segs):
        s, e = float(seg["start"]), float(seg["end"])
        overlapping = [sp for sp in raw_spans if min(e, sp[1]) > max(s, sp[0])]
        if not overlapping:
            continue
        first_sp, last_sp = overlapping[0], overlapping[-1]
        prev_e = float(segs[idx - 1]["end"]) if idx > 0 else -1.0
        next_s = float(segs[idx + 1]["start"]) if idx + 1 < len(segs) else 1e9
        if prev_e < first_sp[0] - 0.05:
            onset_errs.append(abs(s - first_sp[0]))
        if next_s > last_sp[1] + 0.05:
            offset_errs.append(abs(e - last_sp[1]))

    overlaps = sum(1 for i in range(1, len(segs)) if float(segs[i]["start"]) < float(segs[i - 1]["end"]) - 1e-6)
    gaps = [round(float(segs[i]["start"]) - float(segs[i - 1]["end"]), 3) for i in range(1, len(segs))]
    gaps_sorted = sorted(gaps)

    def summarize(arr: list[float]) -> dict:
        if not arr:
            return {"count": 0, "median": 0.0, "mean": 0.0, "p90": 0.0, "within_015": 0, "ratio": "0/0"}
        a = sorted(arr)
        w15 = sum(1 for x in a if x <= 0.151)
        return {
            "count": len(a),
            "median": round(a[len(a) // 2], 3),
            "mean": round(sum(a) / len(a), 3),
            "p90": round(a[min(len(a) - 1, int(len(a) * 0.9))], 3),
            "within_015": w15,
            "ratio": f"{w15}/{len(a)} ({w15 / len(a) * 100:.1f}%)",
        }

    return {
        "file": wav_path.name,
        "lang": lang,
        "segments": len(segs),
        "overlaps": overlaps,
        "onset": summarize(onset_errs),
        "offset": summarize(offset_errs),
        "gaps": {
            "count": len(gaps),
            "min": gaps_sorted[0] if gaps_sorted else 0.0,
            "median": gaps_sorted[len(gaps_sorted) // 2] if gaps_sorted else 0.0,
            "max": gaps_sorted[-1] if gaps_sorted else 0.0,
            "clear_pause_ge_03s": sum(1 for g in gaps if g >= 0.3),
        },
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Reproducible subtitle timing accuracy evaluator.")
    parser.add_argument("--audio", type=str, default="", help="Optional 16k WAV path to evaluate.")
    parser.add_argument("--lang", type=str, default="auto", help="Language code (ja/en/auto).")
    parser.add_argument("--model", type=str, default="small", help="Whisper model size.")
    args = parser.parse_args()

    local_appdata = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    app_data = local_appdata / "SubFlow Studio"
    readonly_dirs = []
    if (app_data / "bin" / "models").is_dir():
        readonly_dirs.append(str(app_data / "bin" / "models"))
    legacy_env = os.environ.get("SUBFLOW_LEGACY_DIR", "").strip()
    if legacy_env and (Path(legacy_env) / "bin" / "models").is_dir():
        readonly_dirs.append(str(Path(legacy_env) / "bin" / "models"))

    mp = core.MediaProcessor(
        bin_dir=str(app_data / "bin"),
        work_dir=str(app_data / "workspace"),
        resource_bin_dir=str(ROOT / "bin"),
        readonly_model_dirs=readonly_dirs,
    )

    samples: list[tuple[Path, str]] = []
    if args.audio:
        samples.append((Path(args.audio), args.lang))
    else:
        # 自动查找本机已有的日语/英语测试样本或内置 _demo.16k.wav
        search_dirs = [app_data / "workspace"]
        if legacy_env:
            search_dirs.insert(0, Path(legacy_env) / "workspace")
        for sdir in search_dirs:
            if not sdir.is_dir():
                continue
            for wav in sorted(sdir.glob("*.16k.wav")):
                low = wav.name.lower()
                if "日本語" in wav.name or "日语" in wav.name:
                    samples.append((wav, "ja"))
                elif "英语" in wav.name or "english" in low:
                    samples.append((wav, "en"))
            if samples:
                break
        if not samples and (app_data / "workspace" / "_demo.16k.wav").is_file():
            samples.append((app_data / "workspace" / "_demo.16k.wav", "en"))

    if not samples:
        print("[WARN] 未找到本地 16k.wav 测试音频，请通过 --audio 指定路径。")
        return 0

    print("=== SubFlow Studio 时间轴独立复验报告 (P1-6) ===", flush=True)
    all_ok = True
    for wav_path, lang in samples:
        r = eval_audio_file(mp, wav_path, lang, model_size=args.model)
        print(
            f"样本 [{r['lang'].upper()}] {r['file'][:38]}:\n"
            f"  · 字幕条数: {r['segments']} | 重叠条数: {r['overlaps']} (要求 = 0)\n"
            f"  · 开口误差: 中位 {r['onset']['median']:.3f}s | 均值 {r['onset']['mean']:.3f}s | P90 {r['onset']['p90']:.3f}s | <=0.15s {r['onset']['ratio']}\n"
            f"  · 收声误差: 中位 {r['offset']['median']:.3f}s | 均值 {r['offset']['mean']:.3f}s | P90 {r['offset']['p90']:.3f}s | <=0.15s {r['offset']['ratio']}\n"
            f"  · 句间空隙: 最小 {r['gaps']['min']:.3f}s | 中位 {r['gaps']['median']:.3f}s | 最大 {r['gaps']['max']:.3f}s | >=0.3s 留白 {r['gaps']['clear_pause_ge_03s']} 处",
            flush=True,
        )
        if r["overlaps"] != 0:
            all_ok = False

    print("EVAL_TIMING:", "PASS" if all_ok else "FAIL", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
