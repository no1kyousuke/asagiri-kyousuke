# -*- coding: utf-8 -*-
"""SubFlow Studio 翻译质量 20 条黄金测试集量化评测脚本 (P1-5)。

用法：
    .build_venv\\Scripts\\python.exe tests\\eval_translation.py
    .build_venv\\Scripts\\python.exe tests\\eval_translation.py --offline
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import core_pipeline as core  # noqa: E402

BLACKLIST_PATTERNS = [
    "所以，你认为",
    "我将会前进",
    "它变成",
    "足够公平",
    "在一天的结束",
    "制造完全的感觉",
    "走我穿过",
    "为了诚实",
    "握住一秒",
    "双倍检查",
    "感觉自由",
    "中午好",
    "对 是的",
    "使得",
    "对于",
]

ZH_SPACE_RE = re.compile(r"[\u4e00-\u9fff]\s+[\u4e00-\u9fff]")


def load_api_config() -> tuple[str, str, str]:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key, "https://api.deepseek.com", "deepseek-chat"
    local_appdata = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    for cand in (
        local_appdata / "SubFlow Studio" / "settings.json",
        ROOT / "settings.json",
    ):
        if cand.is_file():
            try:
                cfg = json.loads(cand.read_text(encoding="utf-8"))
                k = str(cfg.get("api_key") or "").strip()
                if k:
                    return (
                        k,
                        str(cfg.get("base_url") or "https://api.deepseek.com").strip(),
                        str(cfg.get("model") or "deepseek-chat").strip(),
                    )
            except Exception:
                continue
    return "", "https://api.deepseek.com", "deepseek-chat"


def evaluate_items(golden: list[dict], translations: dict[int, str], speakers: list[int]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    blacklist_hits = 0
    space_hits = 0
    missing_hits = 0
    keep_failures = 0
    ratio_warnings = 0
    passed_count = 0

    print("=== SubFlow Studio 黄金 20 句翻译质量评测 ===", flush=True)
    for item in golden:
        gid = int(item["id"])
        src = item["src"]
        bad = item["bad"]
        zh = str(translations.get(gid) or "").strip()
        must_keep = item.get("must_keep") or []

        issues = []
        if not zh:
            missing_hits += 1
            issues.append("漏翻(空译文)")
        else:
            for bp in BLACKLIST_PATTERNS:
                if bp in zh:
                    blacklist_hits += 1
                    issues.append(f"机翻腔命中({bp})")
            if ZH_SPACE_RE.search(zh):
                space_hits += 1
                issues.append("中文异常空格")
            for token in must_keep:
                if token.lower() not in zh.lower():
                    keep_failures += 1
                    issues.append(f"缺失型号/数字({token})")
            ratio = len(zh) / max(1, len(src))
            if not (0.18 <= ratio <= 1.65):
                ratio_warnings += 1
                issues.append(f"长度比异常({ratio:.2f})")

        ok = len(issues) == 0
        if ok:
            passed_count += 1
        mark = "✅" if ok else ("❌ " + " / ".join(issues))
        print(f"[{gid:02d}] {src}")
        print(f"     旧(机翻参照): {bad}")
        print(f"     新(实测译文): {zh}  {mark}")

    spk_set = set(speakers)
    print("\n--- 汇总指标 ---")
    print(f"1. 机翻腔黑名单命中 : {blacklist_hits} 次 (阈值: 0)")
    print(f"2. 中文异常空格     : {space_hits} 次 (阈值: 0)")
    print(f"3. 漏翻条目数       : {missing_hits} 条 (阈值: 0)")
    print(f"4. 数字/型号保留失败: {keep_failures} 处 (阈值: 0)")
    print(f"5. 说话人区分列表   : {speakers[:15]} (检测到说话人数: {len(spk_set)})")
    print(f"总分: {passed_count}/{len(golden)} 通过 (阈值: >= 18/{len(golden)})")

    if blacklist_hits == 0 and space_hits == 0 and missing_hits == 0 and keep_failures == 0 and passed_count >= 18:
        print("EVAL_TRANSLATION: PASS")
        return 0
    print("EVAL_TRANSLATION: FAIL")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate translation quality on 20 golden subtitle items.")
    parser.add_argument("--offline", action="store_true", help="Use reference translations when no API key is configured.")
    args = parser.parse_args()

    golden_path = ROOT / "tests" / "golden_translation.json"
    golden = json.loads(golden_path.read_text(encoding="utf-8"))

    api_key, base_url, model = load_api_config()
    if args.offline or not api_key:
        print("[INFO] 使用离线参考译文验证评分器 (--offline 或未检测到本地 API Key)")
        translations = {int(x["id"]): x["good"] for x in golden}
        speakers = [i % 2 for i in range(len(golden))]
        return evaluate_items(golden, translations, speakers)

    tr = core.SubtitleTranslator(api_key=api_key, base_url=base_url, model=model)
    en_segs = [{"id": x["id"], "start": float(x["id"]), "end": float(x["id"]) + 1.5, "src_text": x["src"]} for x in golden if x["lang"] == "en"]
    ja_segs = [{"id": x["id"], "start": float(x["id"]), "end": float(x["id"]) + 1.5, "src_text": x["src"]} for x in golden if x["lang"] == "ja"]

    res_en = tr.translate_segments(en_segs, source_lang="en", batch_size=10, context_size=2)
    res_ja = tr.translate_segments(ja_segs, source_lang="ja", batch_size=10, context_size=2)

    translations: dict[int, str] = {}
    speakers: list[int] = []
    for seg in (res_en.get("segments") or []) + (res_ja.get("segments") or []):
        translations[int(seg["id"])] = str(seg.get("zh_text") or "")
        speakers.append(int(seg.get("speaker", 0)))

    return evaluate_items(golden, translations, speakers)


if __name__ == "__main__":
    raise SystemExit(main())
