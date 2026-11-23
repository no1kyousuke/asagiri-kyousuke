/* =============================================================================
 * bridge.js —— 前端桥接层
 * 原生(pywebview) 与 浏览器预览 双模式自动切换。
 *
 * 关键：pywebview 注入 window.pywebview 的时机晚于本脚本加载，
 * 所以用 isNative() 每次调用实时判断，不在加载时固化。
 * ========================================================================== */

(function () {
  'use strict';

  function isNative() {
    return !!(window.pywebview && window.pywebview.api);
  }

  /* ---------- 进度桥：后端 evaluate_js 调 window.__onProgress ---------- */
  var activeProgress = null;
  window.__onProgress = function (percent, text) {
    if (typeof activeProgress === 'function') {
      try { activeProgress(Number(percent) || 0, String(text || '')); } catch (e) { /* noop */ }
    }
  };
  function withProgress(cb) {
    activeProgress = cb;
    return function () { activeProgress = null; };
  }

  /* ---------- 字段互转 ---------- */
  function frontToBack(list) {
    return (list || []).map(function (s) {
      var item = { id: s.id, start: s.start, end: s.end, src_text: s.src, zh_text: s.zh };
      // Keep manual diarization labels while sending a segment to translate/export APIs.
      if (s.speaker === 0 || s.speaker === 1) item.speaker = s.speaker;
      if (s.translate_status) item.translate_status = s.translate_status;
      if (s.source_segment != null) item.source_segment = s.source_segment;
      if (s.raw_start != null) item.raw_start = s.raw_start;
      if (s.raw_end != null) item.raw_end = s.raw_end;
      if (s.window_id != null) item.window_id = s.window_id;
      return item;
    });
  }
  function backToFront(list) {
    return (list || []).map(function (s) {
      return {
        id: s.id, start: s.start, end: s.end,
        src: String(s.src_text == null ? '' : s.src_text),
        zh: String(s.zh_text == null ? '' : s.zh_text),
        speaker: (s.speaker === 0 || s.speaker === 1) ? s.speaker : undefined,
        translate_status: s.translate_status || undefined,
        translate_error: s.translate_error || undefined,
        source_segment: s.source_segment,
        raw_start: s.raw_start,
        raw_end: s.raw_end,
        window_id: s.window_id
      };
    });
  }

  /* ---------- 浏览器预览 mock ---------- */
  function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  function mockIterate(onProgress, text) {
    return (async function () {
      var clear = withProgress(onProgress);
      for (var i = 0; i <= 100; i += 4) {
        if (onProgress) onProgress(i, text + ' ' + i + '%');
        await sleep(25);
      }
      clear();
    })();
  }

  var mock = {
    hwInfo: function () {
      return {
        device: 'cpu', compute_type: 'int8', gpu_name: null, accelerated: false,
        ffmpeg_ok: false, message: '（浏览器预览模式）未连接桌面后端，识别 / 压制功能需在桌面应用中使用。'
      };
    },
    prepare: async function (source, lang, onProgress) {
      await mockIterate(onProgress, '模拟下载 / 提取音轨');
      return { status: 'success', video_path: null, audio_path: null, native_srt_path: null,
        title: '模拟视频', message: '（浏览器预览模式）仅演示流程，未真正处理媒体。' };
    },
    transcribe: async function (path, lang, model, onProgress) {
      await mockIterate(onProgress, '模拟语音识别');
      return { status: 'success', detected_lang: 'en', segments: [], message: '（浏览器预览模式）未连接后端。' };
    },
    translate: async function (segments, glossary, lang, batch, ctx, onProgress) {
      await mockIterate(onProgress, '模拟上下文精翻');
      var out = frontToBack(segments).map(function (s) {
        s.zh_text = s.zh_text || '（模拟译文）';
        return s;
      });
      return { status: 'success', segments: out, message: '（浏览器预览模式）模拟翻译完成。' };
    },
    exportSrt: function () { return { status: 'success', srt_path: null, count: 0, message: '（浏览器预览模式）请直接下载 SRT。' }; },
    exportAss: function () { return { status: 'success', ass_path: null, count: 0, message: '（浏览器预览模式）请直接下载 ASS。' }; },
    burn: function () { return { status: 'error', output_video_path: null, ass_path: null, encoder_used: null, message: '（浏览器预览模式）压制需在桌面应用中进行。' }; }
  };

  var Bridge = {
    native: isNative(),

    hwInfo: async function () {
      if (!isNative()) return mock.hwInfo();
      try { return await window.pywebview.api.get_hardware_info(); }
      catch (e) { return mock.hwInfo(); }
    },

    prepare: async function (source, lang, onProgress) {
      if (!isNative()) return mock.prepare(source, lang, onProgress);
      var clear = withProgress(onProgress);
      try { return await window.pywebview.api.prepare_media(String(source), lang || 'auto'); }
      finally { clear(); }
    },

    transcribe: async function (audioPath, lang, model, onProgress) {
      if (!isNative()) return mock.transcribe(audioPath, lang, model, onProgress);
      var clear = withProgress(onProgress);
      try {
        return await window.pywebview.api.transcribe_audio(String(audioPath), lang || 'auto', model || 'large-v3-turbo');
      } finally { clear(); }
    },

    parseSrt: async function (srtPath) {
      if (!isNative()) return { status: 'error', segments: [], detected_lang: '', message: '（浏览器预览模式）请用上方「导入本地字幕」' };
      return await window.pywebview.api.parse_srt_file(String(srtPath));
    },

    setTranslator: async function (apiKey, baseUrl, model) {
      if (!isNative()) {
        try { localStorage.setItem('sf_api_key', String(apiKey || '')); } catch (e) {}
        return { status: 'success', message: '（浏览器预览模式）未真正保存' };
      }
      return await window.pywebview.api.set_translator(String(apiKey || ''), String(baseUrl || ''), String(model || 'deepseek-chat'));
    },

    setCookieSource: async function (source) {
      if (!isNative()) { return { status: 'success', message: '（浏览器预览模式）未真正保存' }; }
      return await window.pywebview.api.set_cookie_source(String(source || ''));
    },

    setProxy: async function (proxy) {
      if (!isNative()) { return { status: 'success', message: '（浏览器预览模式）未真正保存' }; }
      return await window.pywebview.api.set_proxy(String(proxy || ''));
    },

    /* 本地 Ollama 自动安装 */
    checkOllama: async function () {
      if (!isNative()) { return { status: 'success', installed: false, running: false, models: [], default_model: 'qwen2.5:3b' }; }
      return await window.pywebview.api.check_ollama();
    },

    installOllama: async function (onProgress) {
      if (!isNative()) { return { status: 'error', message: '（浏览器预览模式）' }; }
      var clear = withProgress(onProgress);
      try { return await window.pywebview.api.install_ollama(); }
      finally { clear(); }
    },

    pullOllamaModel: async function (model, onProgress) {
      if (!isNative()) { return { status: 'error', message: '（浏览器预览模式）' }; }
      var clear = withProgress(onProgress);
      try { return await window.pywebview.api.pull_ollama_model(String(model || '')); }
      finally { clear(); }
    },

    importCookies: async function () {
      if (!isNative()) { return { status: 'error', message: '（浏览器预览模式）下载需在桌面应用中进行' }; }
      return await window.pywebview.api.import_cookies();
    },

    detectPlatform: async function (url) {
      if (!isNative()) {
        return { id: 'unknown', name: '其他平台', color: '#9ca3af', hint: '浏览器预览模式', needs_proxy: false };
      }
      return await window.pywebview.api.detect_platform(String(url || ''));
    },

    getModelStatus: async function () {
      if (!isNative()) return { status: 'success', models: {}, recommended: 'small' };
      return await window.pywebview.api.get_model_status();
    },

    setComputeMode: async function (mode) {
      if (!isNative()) return { status: 'success', device: 'cpu', message: '（浏览器预览模式）' };
      return await window.pywebview.api.set_compute_mode(String(mode || 'auto'));
    },

    testTranslator: async function () {
      if (!isNative()) return { status: 'error', message: '（浏览器预览模式）请在桌面应用中测试连通性' };
      return await window.pywebview.api.test_translator();
    },

    translateSingle: async function (srcText, ctxBefore, ctxAfter, glossary, lang) {
      if (!isNative()) return { status: 'error', zh_text: '', message: '（浏览器预览模式）请在桌面应用中使用 AI 重译' };
      return await window.pywebview.api.translate_single_segment(
        String(srcText || ''), ctxBefore || [], ctxAfter || [], glossary || '', lang || 'auto'
      );
    },

    pickExportDir: async function () {
      if (!isNative()) return { status: 'error', message: '（浏览器预览模式）无法选择本地目录' };
      return await window.pywebview.api.pick_export_dir();
    },

    getWorkspaceCacheInfo: async function () {
      if (!isNative()) return { status: 'success', bytes: 0, mb: 0, count: 0 };
      return await window.pywebview.api.get_workspace_cache_info();
    },

    clearWorkspaceCache: async function () {
      if (!isNative()) return { status: 'success', freed_mb: 0, removed: 0, message: '（浏览器预览模式）无本地缓存' };
      return await window.pywebview.api.clear_workspace_cache();
    },

    exportAlignmentAudit: async function (segments, stem) {
      if (!isNative()) return { status: 'error', message: '（浏览器预览模式）' };
      return await window.pywebview.api.export_alignment_audit(frontToBack(segments), String(stem || 'subtitles'));
    },

    /* B站扫码登录 */
    biliQrGenerate: async function () {
      if (!isNative()) { return { status: 'error', message: '（浏览器预览模式）扫码登录需在桌面应用中进行' }; }
      return await window.pywebview.api.bili_qr_generate();
    },

    biliQrPoll: async function (key) {
      if (!isNative()) { return { status: 'error', message: '（浏览器预览模式）' }; }
      return await window.pywebview.api.bili_qr_poll(String(key));
    },

    translate: async function (segments, glossary, lang, batch, ctx, onProgress, onlyMissing, temperature) {
      if (!isNative()) return mock.translate(segments, glossary, lang, batch, ctx, onProgress);
      var clear = withProgress(onProgress);
      try {
        return await window.pywebview.api.translate_segments(
          frontToBack(segments), glossary || '', lang || 'auto',
          Number(batch) || 12, Number(ctx) || 3, !!onlyMissing,
          typeof temperature === 'number' ? temperature : null
        );
      } finally { clear(); }
    },

    exportSrt: async function (segments, outPath, layout) {
      if (!isNative()) return mock.exportSrt();
      return await window.pywebview.api.export_srt(frontToBack(segments), String(outPath), layout || 'zh_top');
    },

    exportAss: async function (segments, outPath, styleConfig, w, h) {
      if (!isNative()) return mock.exportAss();
      return await window.pywebview.api.export_ass(frontToBack(segments), String(outPath), styleConfig, Number(w) || 1920, Number(h) || 1080);
    },

    burn: async function (videoPath, segments, outPath, styleConfig, preferNvenc, onProgress, fps, renderConfig) {
      if (!isNative()) return mock.burn();
      var clear = withProgress(onProgress);
      try {
        return await window.pywebview.api.burn_subtitles(
          String(videoPath), frontToBack(segments), String(outPath),
          styleConfig, preferNvenc !== false, fps || 'source', renderConfig || null
        );
      } finally { clear(); }
    },

    loadSettings: async function () {
      if (!isNative()) {
        try { return JSON.parse(localStorage.getItem('sf_settings') || '{}'); } catch (e) { return {}; }
      }
      return await window.pywebview.api.load_settings();
    },

    saveSettings: async function (settings) {
      if (!isNative()) { try { localStorage.setItem('sf_settings', JSON.stringify(settings)); } catch (e) {} return true; }
      return await window.pywebview.api.save_settings(settings);
    },

    openFolder: async function (path) {
      if (!isNative()) return { status: 'error', message: '（浏览器预览模式）无法打开本地文件夹' };
      try { return await window.pywebview.api.open_path(path || null); }
      catch (e) { return { status: 'error', message: '打开失败' }; }
    },

    /* 把视频暴露给本地 http server（页面是 http:// 加载，file:// 视频会被拦截） */
    exposeVideo: async function (path) {
      if (!isNative()) { return { status: 'error', message: '浏览器预览模式' }; }
      try { return await window.pywebview.api.expose_video(String(path || '')); }
      catch (e) { return { status: 'error', message: '视频暴露失败：' + (e && e.message ? e.message : e) }; }
    },

    prepareDemoSample: async function (onProgress) {
      if (!isNative()) {
        return {
          status: 'success',
          video_path: null,
          title: 'SubFlow_10秒极速上手示例',
          segments: [
            { id: 1, start: 0.25, end: 2.65, src_text: 'Hello and welcome to SubFlow Studio.', zh_text: '你好，欢迎使用 SubFlow Studio 双语字幕工作台。', speaker: 0, translate_status: 'ok' },
            { id: 2, start: 2.85, end: 5.55, src_text: 'How is the new subtitle timeline working today?', zh_text: '今天全新的字幕时间轴对齐效果怎么样？', speaker: 0, translate_status: 'ok' },
            { id: 3, start: 5.75, end: 8.15, src_text: 'It is super fast and accurate.', zh_text: '速度极快，而且卡点非常精准！', speaker: 1, translate_status: 'ok' },
            { id: 4, start: 8.30, end: 10.80, src_text: 'Now you can export bilingual videos in seconds.', zh_text: '现在你只需几秒钟就能导出高质量双语成片。', speaker: 1, translate_status: 'ok' }
          ],
          message: '已载入内置 10 秒双人双语示例字幕！'
        };
      }
      var clear = withProgress(onProgress);
      try { return await window.pywebview.api.prepare_demo_sample(); }
      finally { clear(); }
    },

    pickVideo: async function () {
      if (!isNative()) { var i = document.getElementById('video-upload'); if (i) i.click(); return null; }
      return await window.pywebview.api.pick_video_file();
    },

    pickSrt: async function () {
      if (!isNative()) { var i = document.getElementById('srt-upload'); if (i) i.click(); return null; }
      return await window.pywebview.api.pick_srt_file();
    },

    toggleFullscreen: async function () {
      if (!isNative()) {
        try {
          if (!document.fullscreenElement) {
            await document.documentElement.requestFullscreen();
            return { status: 'success', fullscreen: true };
          } else {
            await document.exitFullscreen();
            return { status: 'success', fullscreen: false };
          }
        } catch (e) {
          return { status: 'error', fullscreen: !!document.fullscreenElement, message: String(e) };
        }
      }
      return await window.pywebview.api.toggle_fullscreen();
    },

    setFullscreen: async function (enabled) {
      if (!isNative()) {
        try {
          if (enabled && !document.fullscreenElement) {
            await document.documentElement.requestFullscreen();
          } else if (!enabled && document.fullscreenElement) {
            await document.exitFullscreen();
          }
          return { status: 'success', fullscreen: !!document.fullscreenElement };
        } catch (e) {
          return { status: 'error', fullscreen: !!document.fullscreenElement, message: String(e) };
        }
      }
      return await window.pywebview.api.set_fullscreen(!!enabled);
    },

    styleConfig: function () {
      var st = (typeof state !== 'undefined' && state.style) ? state.style : {};
      var zhSize = Number(st.zhSize) || 19;
      var srcSize = Number(st.srcSize) || 12;
      var marginPct = (typeof st.marginBottom === 'number') ? st.marginBottom : 11;
      var isMono = st.font === 'mono';
      return {
        layout: st.order || 'zh_top',
        font_size: zhSize,
        en_scale: zhSize > 0 ? srcSize / zhSize : 0.78,
        margin_bottom: (marginPct / 100) * 360,
        bg_opacity: (typeof st.bgOpacity === 'number') ? st.bgOpacity : 68,
        primary_color: st.zhColor || '#fcd34d',
        speaker_b_color: st.speakerBColor || '#7dd3fc',
        secondary_color: st.srcColor || '#d4d4d8',
        font_zh: 'Microsoft YaHei',
        font_en: isMono ? 'Consolas' : 'Arial'
      };
    },

    toFrontSegments: backToFront,
    toBackendSegments: frontToBack
  };

  window.Bridge = Bridge;
})();