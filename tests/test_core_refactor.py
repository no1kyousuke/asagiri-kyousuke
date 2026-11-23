# -*- coding: utf-8 -*-
"""SubFlow Studio 离线回归与单元测试套件。

本套件不下载 Whisper 模型，也不访问外网 DeepSeek / B 站接口；
用于快速验证构建脚本、路径迁移、时间戳切分/对齐、翻译异常处理矩阵、
导出/双人样式与本地 HTTP Range 流媒体服务。
"""
from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


core = _load_module("subflow_core_under_test", ROOT / "core_pipeline.py")
build_release = _load_module("subflow_build_release_under_test", ROOT / "build_release.py")


class EnvironmentBootstrapTests(unittest.TestCase):
    def test_app_configures_startup_log_before_importing_gui_dependencies(self):
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertLess(app_source.index("logging.basicConfig("), app_source.index("import webview"))
        self.assertIn("_show_boot_error(_boot_exc)", app_source)
        self.assertIn("app.log", app_source)
        self.assertIn("_check_webview2_installed", app_source)

    def test_pyinstaller_spec_includes_pythonnet_clr_and_onnxruntime(self):
        spec_source = (ROOT / "SubFlowStudio.spec").read_text(encoding="utf-8")
        self.assertIn('"clr"', spec_source)
        self.assertIn('"pythonnet"', spec_source)
        self.assertIn('"onnxruntime"', spec_source)
        self.assertIn("console=DEBUG_BUILD", spec_source)

    def test_cuda_runtime_paths_are_added_before_acceleration_probe(self):
        source = (ROOT / "core_pipeline.py").read_text(encoding="utf-8")
        init_start = source.index("    def __init__(", source.index("class MediaProcessor:"))
        init_end = source.index("    # ------------------------------------------------------------------ #", init_start)
        init_body = source[init_start:init_end]
        self.assertLess(
            init_body.index("self._add_cuda_runtime_dirs()"),
            init_body.index("self._detect_acceleration()"),
            "CUDA wheel DLL 目录必须在 CTranslate2 探测前注册",
        )
        method_start = source.index("    def _add_cuda_runtime_dirs(", source.index("class MediaProcessor:"))
        method_end = source.index("    def _fill_from_nvidia_smi", method_start)
        bootstrap = source[method_start:method_end]
        self.assertNotIn("import faster_whisper", bootstrap)
        self.assertIn('importlib.util.find_spec("faster_whisper")', bootstrap)


class TimestampAndAsrTests(unittest.TestCase):
    def test_real_word_times_split_on_pause_without_padding(self):
        words = [
            SimpleNamespace(word=" Hello", start=0.10, end=0.35),
            SimpleNamespace(word=" there.", start=0.36, end=0.66),
            SimpleNamespace(word=" How", start=1.20, end=1.36),
            SimpleNamespace(word=" are", start=1.37, end=1.50),
            SimpleNamespace(word=" you?", start=1.51, end=1.82),
        ]
        raw = [SimpleNamespace(
            text=" Hello there. How are you?", words=words,
            start=0.10, end=1.82, no_speech_prob=0.0, avg_logprob=-0.1,
        )]
        out = core.MediaProcessor._segments_from_word_timestamps(raw, 3.0)
        self.assertEqual(len(out), 2, out)
        self.assertAlmostEqual(out[0]["start"], 0.10, places=3)
        self.assertAlmostEqual(out[0]["end"], 0.66, places=3)
        self.assertAlmostEqual(out[1]["start"], 1.20, places=3)
        self.assertAlmostEqual(out[1]["end"], 1.82, places=3)
        self.assertLessEqual(out[0]["end"], out[1]["start"])

    def test_zero_duration_japanese_words_are_preserved(self):
        """faster-whisper 日语经常返回 start == end 的助词/标点，绝不可丢弃。"""
        words = [
            SimpleNamespace(word="こん", start=1.00, end=1.20),
            SimpleNamespace(word="にち", start=1.20, end=1.20),
            SimpleNamespace(word="は", start=1.20, end=1.40),
            SimpleNamespace(word="。", start=1.40, end=1.40),
        ]
        raw = [SimpleNamespace(
            text="こんにちは。", words=words, start=1.00, end=1.40,
            no_speech_prob=0.0, avg_logprob=-0.1,
        )]
        out = core.MediaProcessor._segments_from_word_timestamps(raw, 3.0)
        self.assertEqual(len(out), 1, out)
        self.assertEqual(out[0]["src_text"], "こんにちは。")
        self.assertAlmostEqual(out[0]["start"], 1.00, places=3)
        self.assertAlmostEqual(out[0]["end"], 1.42, places=3)

    def test_mixed_cjk_keeps_latin_model_spacing(self):
        words = [
            SimpleNamespace(word="RTX", start=0.1, end=0.2),
            SimpleNamespace(word=" 3060", start=0.2, end=0.5),
            SimpleNamespace(word="です。", start=0.51, end=0.8),
        ]
        raw = [SimpleNamespace(
            text="RTX 3060です。", words=words, start=0.1, end=0.8,
            no_speech_prob=0.0, avg_logprob=-0.1,
        )]
        out = core.MediaProcessor._segments_from_word_timestamps(raw, 2.0)
        self.assertEqual(len(out), 1, out)
        self.assertIn("RTX 3060", out[0]["src_text"])

    def test_short_cjk_reply_splits_after_pause(self):
        words = [
            SimpleNamespace(word="はい。", start=0.20, end=0.55),
            SimpleNamespace(word="そうです。", start=1.10, end=1.55),
        ]
        raw = [SimpleNamespace(
            text="はい。そうです。", words=words, start=0.20, end=1.55,
            no_speech_prob=0.0, avg_logprob=-0.1,
        )]
        out = core.MediaProcessor._segments_from_word_timestamps(raw, 2.0)
        self.assertEqual(len(out), 2, out)
        self.assertEqual(out[0]["src_text"], "はい。")
        self.assertEqual(out[1]["src_text"], "そうです。")

    def test_hallucination_loop_is_filtered(self):
        self.assertTrue(core.MediaProcessor._is_hallucination_loop("あ〜〜〜〜〜〜〜〜〜〜〜〜"))
        self.assertTrue(core.MediaProcessor._is_hallucination_loop("ええええええええええええ"))
        self.assertFalse(core.MediaProcessor._is_hallucination_loop("おはようございます。"))
        self.assertFalse(core.MediaProcessor._is_hallucination_loop("Hello everyone, welcome back."))

    def test_vad_boundary_alignment_fixes_early_flash_and_premature_cutoff(self):
        """独立 VAD 语音段对齐：修复提前闪现（首字前含静音）与尾音早关（话未说完消失）。"""
        segs = [
            # 模拟 Whisper 提前 0.70s 出现、尾部早关 0.45s（如 ありがとうございます）
            {"id": 1, "start": 10.00, "end": 11.20, "src_text": "ありがとうございます。", "zh_text": ""},
            # 模拟 Whisper 尾部拖尾滞留到静音 1.1s
            {"id": 2, "start": 14.10, "end": 16.50, "src_text": "はい、そうですよ。", "zh_text": ""},
        ]
        vad_spans = [
            (10.70, 11.65),
            (14.12, 15.25),
        ]
        aligned = core.MediaProcessor._align_segments_to_vad_bounds(segs, vad_spans)
        self.assertAlmostEqual(aligned[0]["start"], 10.70, delta=0.05)
        self.assertAlmostEqual(aligned[0]["end"], 11.65, delta=0.05)
        self.assertAlmostEqual(aligned[1]["start"], 14.10, delta=0.05)
        self.assertAlmostEqual(aligned[1]["end"], 15.25, delta=0.05)
        self.assertLessEqual(aligned[0]["end"], aligned[1]["start"])

    def test_segment_level_fallback_uses_original_bounds(self):
        raw = [SimpleNamespace(
            text="fallback transcript", words=None, start=3.125, end=4.875,
            no_speech_prob=0.0, avg_logprob=-0.1,
        )]
        out = core.MediaProcessor._segments_from_word_timestamps(raw, 8.0)
        self.assertEqual(len(out), 1, out)
        self.assertEqual(out[0]["start"], 3.125)
        self.assertEqual(out[0]["end"], 4.875)

    def test_transcription_disables_vad_audio_splicing_in_windows(self):
        """确认传入 Whisper 的始终是 vad_filter=False（禁止内部拼接裁剪），且窗口时间偏移精确叠加。"""
        captured = []

        class FakeModel:
            def transcribe(self, audio_input, **kwargs):
                captured.append(dict(kwargs))
                seg = SimpleNamespace(
                    text=" Hello.", start=0.10, end=0.60,
                    words=[SimpleNamespace(word=" Hello.", start=0.10, end=0.60)],
                    no_speech_prob=0.0, avg_logprob=-0.1,
                )
                return iter([seg]), SimpleNamespace(duration=1.0, language="en")

        mp = object.__new__(core.MediaProcessor)
        mp.device = "cpu"
        mp.accel_degraded = False
        mp._get_model = lambda _size: FakeModel()
        mp._probe_duration = lambda _path: 10.0
        mp._detect_vad_speech_windows = lambda _path, _dur: (None, [], [])
        emit = lambda *args, **kwargs: None
        result = mp._run_transcription("fake.wav", None, "small", emit, None)
        self.assertEqual(len(captured), 1)
        self.assertFalse(captured[0]["vad_filter"])
        self.assertTrue(captured[0]["word_timestamps"])
        self.assertEqual(result[1], "en")
        self.assertEqual(len(result[0]), 1)

    def test_model_cache_integrity_detects_corrupt_snapshot_without_touching_readonly(self):
        with tempfile.TemporaryDirectory(prefix="subflow-models-") as tmp:
            writable_root = Path(tmp) / "writable_models"
            readonly_root = Path(tmp) / "readonly_models"
            # 只读目录放置完整 small（模拟原项目缓存）和残缺 large-v3-turbo（3.6MB 无 model.bin）
            small_snap = readonly_root / "models--Systran--faster-whisper-small" / "snapshots" / "abc123"
            small_snap.mkdir(parents=True)
            (small_snap / "config.json").write_text("{}", encoding="utf-8")
            (small_snap / "model.bin").write_bytes(b"0" * 1024)

            turbo_snap = readonly_root / "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo" / "snapshots" / "def456"
            turbo_snap.mkdir(parents=True)
            (turbo_snap / "tokenizer.json").write_text("{}", encoding="utf-8")

            mp = object.__new__(core.MediaProcessor)
            mp.model_dir = writable_root
            mp.readonly_model_dirs = [readonly_root]
            # 临时缩小最小体积阈值便于单元测试
            orig_min = dict(core.MediaProcessor._MODEL_MIN_BYTES)
            try:
                core.MediaProcessor._MODEL_MIN_BYTES["small"] = 512
                status = mp.get_model_status()
            finally:
                core.MediaProcessor._MODEL_MIN_BYTES = orig_min

            self.assertEqual(status["status"], "success")
            self.assertTrue(status["models"]["small"]["cached"])
            self.assertEqual(status["models"]["small"]["source"], "readonly_cache")
            self.assertFalse(status["models"]["large-v3-turbo"]["cached"])
            self.assertTrue(status["models"]["large-v3-turbo"]["corrupt"])
            self.assertEqual(status["recommended"], "small")
            # 绝不删除只读目录里的残缺快照
            self.assertTrue((turbo_snap / "tokenizer.json").is_file())

    def test_fresh_machine_without_any_cache_recommends_small_model(self):
        """P0-1: 全新客户机器无任何本地缓存时，必须推荐 small（460MB），而非 1.5GB large-v3-turbo。"""
        with tempfile.TemporaryDirectory(prefix="subflow-fresh-machine-") as tmp:
            mp = object.__new__(core.MediaProcessor)
            mp.model_dir = Path(tmp) / "empty_models"
            mp.readonly_model_dirs = []
            status = mp.get_model_status()
            self.assertEqual(status["recommended"], "small")
            self.assertFalse(status["models"]["small"]["cached"])
            self.assertFalse(status["models"]["large-v3-turbo"]["cached"])

    def test_no_browser_killing_logic_exists_in_core_pipeline(self):
        """P0-2: 严禁强杀用户 Chrome / Edge 浏览器进程。"""
        source = (ROOT / "core_pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn("_kill_browser_processes", source)
        self.assertNotIn("msedge.exe", source)
        self.assertNotIn("chrome.exe", source)

    def test_alignment_audit_exports_csv_and_json_with_chinese_path(self):
        with tempfile.TemporaryDirectory(prefix="subflow-audit-中文-") as tmp:
            segs = [
                {"id": 1, "start": 1.0, "end": 2.2, "raw_start": 0.9, "raw_end": 2.0, "src_text": "こんにちは", "zh_text": "你好"},
                {"id": 2, "start": 2.5, "end": 3.8, "raw_start": 2.5, "raw_end": 3.8, "src_text": "はい、そうですよ。", "zh_text": "是的，没错。"},
            ]
            paths = core.MediaProcessor.export_alignment_audit(segs, Path(tmp), stem="测试核对")
            csv_path = Path(paths["csv_path"])
            json_path = Path(paths["json_path"])
            self.assertTrue(csv_path.is_file())
            self.assertTrue(json_path.is_file())
            with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
                rows = list(csv.DictReader(fh))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["src_text"], "こんにちは")
            self.assertEqual(rows[1]["gap_from_prev"], "0.3")


class MockTranslator(core.SubtitleTranslator):
    translations = {
        "こんにちは": "你好",
        "はい、そうですよ。": "是的，没错。",
        "Good morning, everyone.": "大家早上好。",
    }

    def __init__(self):
        super().__init__(api_key="offline-test-key", timeout=1, max_retries=1)
        self.chat_calls = 0
        self.plain_calls = 0
        self.omit_src: set[str] = set()
        self.empty_src: set[str] = set()
        self.extra_id = False
        self.duplicate_id = False
        self.plain_text_override = None

    def _chat(self, messages, max_tokens=2048):
        self.chat_calls += 1
        payload = json.loads(messages[-1]["content"])
        items = payload["items"]
        response = []
        for item in items:
            if item["src"] in self.omit_src:
                continue
            if item["src"] in self.empty_src:
                response.append({"id": item["id"], "zh": ""})
                continue
            value = self.translations.get(item["src"], "已翻译")
            response.append({"id": item["id"], "zh": value})
            if self.duplicate_id:
                response.append({"id": item["id"], "zh": value + "重复"})
        if self.extra_id:
            response.append({"id": 999999, "zh": "多余条目"})
        return json.dumps({"items": response}, ensure_ascii=False)

    def _post_http(self, payload):
        self.plain_calls += 1
        value = self.plain_text_override if self.plain_text_override is not None else "补翻成功"
        raw = json.dumps({"choices": [{"message": {"content": value}}]}, ensure_ascii=False)
        return 200, raw


class TranslationTests(unittest.TestCase):
    def test_auto_source_language_detects_english_for_residue_guard(self):
        lang = core.SubtitleTranslator._effective_source_language(
            "This is a complete sentence without Chinese characters.", "外语"
        )
        self.assertEqual(lang, "英语")

    def test_batch_translation_returns_clean_chinese(self):
        tr = MockTranslator()
        src = [
            {"id": 1, "start": 0.0, "end": 1.0, "src_text": "こんにちは"},
            {"id": 2, "start": 1.1, "end": 2.0, "src_text": "はい、そうですよ。"},
        ]
        result = tr.translate_segments(src, source_lang="ja", batch_size=2, context_size=1)
        self.assertEqual(result["status"], "success", result)
        self.assertEqual([x["zh_text"] for x in result["segments"]], ["你好", "是的，没错。"])
        self.assertTrue(all(x.get("translate_status") == "ok" for x in result["segments"]))

    def test_missing_empty_extra_duplicate_ids_are_handled_cleanly(self):
        tr = MockTranslator()
        tr.omit_src = {"漏翻句"}
        tr.empty_src = {"空值句"}
        tr.extra_id = True
        tr.duplicate_id = True
        src = [
            {"id": 1, "start": 0.0, "end": 1.0, "src_text": "こんにちは"},
            {"id": 2, "start": 1.1, "end": 2.0, "src_text": "漏翻句"},
            {"id": 3, "start": 2.1, "end": 3.0, "src_text": "空值句"},
        ]
        result = tr.translate_segments(src, source_lang="ja", batch_size=3, context_size=1)
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["segments"][0]["zh_text"], "你好")
        self.assertEqual(result["segments"][1]["zh_text"], "补翻成功")
        self.assertEqual(result["segments"][2]["zh_text"], "补翻成功")
        self.assertGreaterEqual(tr.plain_calls, 2)

    def test_plain_text_non_json_response_recovers_via_fallback(self):
        tr = MockTranslator()
        tr._chat = lambda messages, max_tokens=2048: "抱歉，这是一段非 JSON 的纯文本回复"
        src = [{"id": 1, "start": 0.0, "end": 1.0, "src_text": "こんにちは"}]
        result = tr.translate_segments(src, source_lang="ja", batch_size=2, context_size=1)
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["segments"][0]["zh_text"], "补翻成功")

    def test_partial_failure_never_fakes_success_and_recovers_with_only_missing(self):
        tr = MockTranslator()
        tr.omit_src = {"需要补翻"}
        tr.plain_text_override = ""  # 第一次让兜底也失败
        src = [
            {"id": 1, "start": 0.0, "end": 1.0, "src_text": "こんにちは"},
            {"id": 2, "start": 1.1, "end": 2.0, "src_text": "需要补翻"},
        ]
        first = tr.translate_segments(src, source_lang="ja", batch_size=2, context_size=1)
        self.assertEqual(first["status"], "partial", first)
        self.assertEqual(first["segments"][0]["zh_text"], "你好")
        self.assertEqual(first["segments"][0]["translate_status"], "ok")
        self.assertEqual(first["segments"][1]["zh_text"], "")  # 绝不用原文冒充译文
        self.assertEqual(first["segments"][1]["translate_status"], "failed")

        # 第二次仅补翻缺失项，已成功的第 1 条不重新发请求
        tr.plain_text_override = "恢复成功"
        tr.chat_calls = 0
        second = tr.translate_segments(first["segments"], source_lang="ja", batch_size=2, context_size=1, only_missing=True)
        self.assertEqual(second["status"], "success", second)
        self.assertEqual(second["segments"][0]["zh_text"], "你好")
        self.assertEqual(second["segments"][1]["zh_text"], "恢复成功")

    def test_passthrough_literals_are_not_false_positive_residue(self):
        self.assertTrue(core.SubtitleTranslator._is_passthrough_literal("RTX 3060"))
        self.assertTrue(core.SubtitleTranslator._is_passthrough_literal("1080P"))
        self.assertTrue(core.SubtitleTranslator._is_passthrough_literal("https://example.com/a"))
        self.assertFalse(core.SubtitleTranslator._translation_has_source_residue("RTX 3060", "RTX 3060", "英语"))
        self.assertFalse(core.SubtitleTranslator._translation_has_source_residue("1080P", "1080P", "英语"))

    def test_chinese_spacing_normalization_keeps_english_model_spacing(self):
        self.assertEqual(
            core.SubtitleTranslator._normalize_zh_translation("中午好 这里，对 是的"),
            "中午好这里，对是的",
        )
        self.assertEqual(
            core.SubtitleTranslator._normalize_zh_translation("这是 RTX 3060 显卡，支持 OpenAI API 接口。"),
            "这是 RTX 3060 显卡，支持 OpenAI API 接口。",
        )

    def test_auto_source_language_detects_japanese_residue(self):
        lang = core.SubtitleTranslator._effective_source_language("こんにちは", "外语")
        self.assertEqual(lang, "日语")
        self.assertTrue(core.SubtitleTranslator._translation_has_source_residue("こんにちは", "こんにちは", lang))

    def test_rate_limit_429_retries_and_succeeds(self):
        tr = core.SubtitleTranslator(api_key="offline-key", timeout=1, max_retries=2)
        attempts = {"n": 0}

        def fake_post(payload):
            attempts["n"] += 1
            if attempts["n"] == 1:
                return 429, '{"error": "rate limit"}'
            return 200, json.dumps({"choices": [{"message": {"content": '{"items":[{"id":1,"zh":"限流恢复"}]}'}}]}, ensure_ascii=False)

        tr._post_http = fake_post
        orig_sleep = core.time.sleep
        try:
            core.time.sleep = lambda _s: None
            res = tr.translate_segments(
                [{"id": 1, "start": 0.0, "end": 1.0, "src_text": "Hello"}],
                source_lang="en", batch_size=2, context_size=0,
            )
        finally:
            core.time.sleep = orig_sleep
        self.assertEqual(res["status"], "success", res)
        self.assertEqual(res["segments"][0]["zh_text"], "限流恢复")
        self.assertGreaterEqual(attempts["n"], 2)

    def test_translation_batches_receive_previous_and_following_context(self):
        tr = MockTranslator()
        captured = []
        original = tr._translate_batch

        def capture(todo, context, glossary, lang_name, extra_rule=None):
            captured.append((list(x["src"] for x in todo), list(x["src"] for x in context)))
            return original(todo, context, glossary, lang_name, extra_rule)

        tr._translate_batch = capture
        src = [
            {"id": i, "start": float(i), "end": float(i) + 0.8, "src_text": f"line-{i}"}
            for i in range(1, 7)
        ]
        result = tr.translate_segments(src, source_lang="en", batch_size=2, context_size=1)
        self.assertEqual(result["status"], "success", result)
        middle = next(item for item in captured if item[0] == ["line-3", "line-4"])
        self.assertEqual(middle[1], ["line-2", "line-5"])

    def test_transport_failure_stops_retry_fanout(self):
        tr = MockTranslator()
        calls = {"n": 0}

        def fail(messages, max_tokens=2048):
            calls["n"] += 1
            raise core.PipelineError("翻译接口连续 1 次失败（网络超时/异常）")

        tr._chat = fail
        src = [
            {"id": i + 1, "start": float(i), "end": float(i) + 0.8, "src_text": f"line {i}"}
            for i in range(24)
        ]
        result = tr.translate_segments(src, source_lang="en", batch_size=4, context_size=2)
        self.assertEqual(result["status"], "error")
        self.assertLessEqual(calls["n"], 10, calls)

    def test_translate_single_and_test_connection(self):
        tr = MockTranslator()
        tr.translations = dict(tr.translations, **{"Hello world": "单独重译结果"})
        r1 = tr.translate_single("Hello world", context_before=["Prev"], context_after=["Next"], source_lang="en")
        self.assertEqual(r1["status"], "success", r1)
        self.assertEqual(r1["zh_text"], "单独重译结果")

        tr.plain_text_override = "OK"
        r2 = tr.test_connection()
        self.assertEqual(r2["status"], "success", r2)


class ExportAndMediaServerTests(unittest.TestCase):
    def test_srt_export_keeps_real_timestamp_format_and_chinese_path(self):
        with tempfile.TemporaryDirectory(prefix="subflow-srt-中文-") as tmp:
            renderer = core.SubtitleRenderer(
                bin_dir=str(Path(tmp) / "bin"),
                work_dir=str(Path(tmp) / "work"),
            )
            output = Path(tmp) / "中文子目录" / "字幕 测试.srt"
            result = renderer.export_srt([
                {"start": 1.25, "end": 2.5, "src_text": "Hello", "zh_text": "你好"}
            ], str(output), layout="zh_only")
            self.assertEqual(result["status"], "success", result)
            content = output.read_text(encoding="utf-8-sig")
            self.assertIn("00:00:01,250 --> 00:00:02,500", content)
            self.assertIn("你好", content)

    def test_ass_export_supports_dual_speaker_and_relative_work_dir(self):
        with tempfile.TemporaryDirectory(prefix="subflow-ass-") as tmp:
            work_dir = Path(tmp) / "work"
            renderer = core.SubtitleRenderer(bin_dir=str(Path(tmp) / "bin"), work_dir=str(work_dir))
            # 故意测试 B 先开口 (1.0->4.0)、A 后插话 (2.0->5.0) 的反向重叠场景，确认对称避让与整数 MarginV
            result = renderer.export_ass(
                [
                    {"start": 1.0, "end": 4.0, "src_text": "Hi B", "zh_text": "乙先说", "speaker": 1},
                    {"start": 2.0, "end": 5.0, "src_text": "Hi A", "zh_text": "甲插话", "speaker": 0},
                ],
                "workspace/双人测试.ass",
            )
            self.assertEqual(result["status"], "success", result)
            ass_path = Path(result["ass_path"])
            self.assertTrue(ass_path.is_file())
            self.assertEqual(ass_path.parent.resolve(), work_dir.resolve())
            text = ass_path.read_text(encoding="utf-8-sig")
            self.assertIn("Style: ZH_B,", text)
            self.assertIn(",ZH_B,", text)
            dialogues = [l for l in text.splitlines() if l.startswith("Dialogue:")]
            self.assertEqual(len(dialogues), 4)
            # 校验所有 MarginV 为整数且插话一方（ZH）被抬高到高于先说一方（ZH_B）的轨道
            mvs = {}
            for line in dialogues:
                parts = line.split(",")
                style_name = parts[3]
                mv_str = parts[7]
                self.assertTrue(mv_str.isdigit(), f"ASS MarginV 必须为整数，实际: {mv_str}")
                if style_name in ("ZH", "ZH_B"):
                    mvs[style_name] = int(mv_str)
            self.assertGreater(mvs["ZH"], mvs["ZH_B"])

    def test_temperature_override_applies_to_translate_segments(self):
        tr = MockTranslator()
        tr.translate_segments(
            [{"id": 1, "start": 0.0, "end": 1.0, "src_text": "こんにちは"}],
            source_lang="ja", temperature=0.75,
        )
        self.assertAlmostEqual(tr.temperature, 0.75, places=3)

    def test_llm_speaker_diarization_tags_are_extracted_zero_extra_calls(self):
        """P1-4: 翻译时顺带从 LLM 返回的 spk 字段提取双人说话人 0/1 标签（零额外请求）。"""
        tr = MockTranslator()
        tr._chat = lambda messages, max_tokens=2048: json.dumps({
            "items": [
                {"id": 1, "zh": "你好，请问这里是机器人大学吗？", "spk": 0},
                {"id": 2, "zh": "是的，没错。", "spk": 1},
            ]
        }, ensure_ascii=False)
        res = tr.translate_segments([
            {"id": 1, "start": 0.0, "end": 2.0, "src_text": "ここ、ロボット大学？"},
            {"id": 2, "start": 2.2, "end": 3.5, "src_text": "はい、そうですよ。"},
        ], source_lang="ja")
        self.assertEqual(res["status"], "success")
        self.assertEqual([s["speaker"] for s in res["segments"]], [0, 1])

    def test_local_media_server_supports_http_206_range_zero_copy(self):
        app_mod = _load_module("subflow_app_helper_check", ROOT / "app.py")
        with tempfile.TemporaryDirectory(prefix="subflow-media-中文-") as tmp:
            sample = Path(tmp) / "演示视频 (1).mp4"
            payload = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 100
            sample.write_bytes(payload)
            srv = app_mod._LocalMediaServer()
            try:
                url1 = srv.register(sample)
                # P2-9: 注册第 2 个视频后，第 1 个视频的 token 仍保留在最近 3 个缓存内，不会立刻 404
                sample2 = Path(tmp) / "演示视频 (2).mp4"
                sample2.write_bytes(payload)
                _ = srv.register(sample2)
                req = urllib.request.Request(url1, headers={"Range": "bytes=10-25"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    self.assertEqual(resp.status, 206)
                    self.assertEqual(resp.read(), payload[10:26])
                    self.assertEqual(resp.headers.get("Accept-Ranges"), "bytes")
            finally:
                srv.close()

    def test_no_hardcoded_developer_path_in_app_py(self):
        """P2-8: app.py 不得硬编码开发机路径 D:\\deepseek\\subflow-studio。"""
        app_source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("D:\\deepseek\\subflow-studio", app_source)
        self.assertNotIn("d:\\deepseek\\subflow-studio", app_source.lower())


class ReleasePackagingTests(unittest.TestCase):
    def test_build_windows_bat_is_thin_python_launcher(self):
        bat = (ROOT / "build_windows.bat").read_text(encoding="utf-8")
        self.assertIn("build_release.py", bat)
        self.assertNotIn("findstr", bat.lower())
        self.assertNotIn("robocopy", bat.lower())

    def test_build_release_relocates_zip_temp_and_special_char_paths(self):
        self.assertTrue(build_release.is_zip_or_temp_path(Path(r"C:\Users\demo\AppData\Local\Temp\SubFlow (4).zip.bf6\SubFlowStudio")))
        self.assertTrue(build_release.is_zip_or_temp_path(Path(r"C:\Users\demo\AppData\Local\Temp\7zO1234\SubFlowStudio")))
        self.assertFalse(build_release.is_zip_or_temp_path(Path(r"C:\SubFlowStudio\SubFlowStudio_Windows_Release")))

        self.assertTrue(build_release.has_venv_unsafe_chars(Path(r"C:\测试目录\SubFlow")))
        self.assertTrue(build_release.has_venv_unsafe_chars(Path(r"C:\SubFlow Studio\Release")))
        self.assertTrue(build_release.has_venv_unsafe_chars(Path(r"C:\SubFlow(1)\Release")))
        self.assertTrue(build_release.has_venv_unsafe_chars(Path(r"C:\SubFlow%20\Release")))
        self.assertFalse(build_release.has_venv_unsafe_chars(Path(r"C:\SubFlowStudioBuild\SubFlowStudio_Windows_Release")))

    def test_ffmpeg_absence_does_not_block_desktop_bundle(self):
        spec = (ROOT / "SubFlowStudio.spec").read_text(encoding="utf-8")
        verify = (ROOT / "verify_release.py").read_text(encoding="utf-8")
        self.assertIn("if ffmpeg.is_file():", spec)
        self.assertIn("--require-ffmpeg", verify)

    def test_offline_test_batch_delegates_to_run_self_tests(self):
        bat = (ROOT / "run_offline_tests.bat").read_text(encoding="utf-8")
        self.assertNotIn("if errorlevel 1 pause & exit /b 1", bat)
        self.assertIn("run_self_tests.py", bat)

    def test_build_environment_is_isolated(self):
        source = (ROOT / "build_release.py").read_text(encoding="utf-8")
        self.assertIn(".build_venv", source)
        self.assertIn("requirements-gpu-cu12.txt", source)

    def test_paid_pro_mode_is_completely_removed_for_open_source(self):
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        data_js = (ROOT / "data.js").read_text(encoding="utf-8")
        self.assertNotIn("VIP888", html)
        self.assertNotIn("pro-modal", html)
        self.assertNotIn("pro-badge-text", html)
        self.assertNotIn("PRO 专业版", html)
        self.assertNotIn("activateVip", app_js)
        self.assertNotIn("pro-modal", app_js)
        self.assertNotIn("detectBuyerHardware", app_js)
        self.assertNotIn("proTrial", data_js)
        self.assertTrue((ROOT / "LICENSE").is_file())

    def test_borderless_fullscreen_support_wired_end_to_end(self):
        app_py = (ROOT / "app.py").read_text(encoding="utf-8")
        bridge_js = (ROOT / "bridge.js").read_text(encoding="utf-8")
        app_js = (ROOT / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn("def set_fullscreen(", app_py)
        self.assertIn("def toggle_fullscreen(", app_py)
        self.assertIn("DwmSetWindowAttribute", app_py)
        self.assertIn("ContainsFullScreenElementChanged", app_py)
        self.assertIn("toggle_fullscreen", bridge_js)
        self.assertIn("set_fullscreen", bridge_js)
        self.assertIn("toggleAppFullscreen", app_js)
        self.assertIn("toggleStageFullscreen", app_js)
        self.assertIn("body.is-fullscreen", html)
        self.assertIn("body.is-stage-fullscreen", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
