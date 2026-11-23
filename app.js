/* =============================================================================
 * app.js —— SubFlow Studio V9.2
 * 重点改动：
 *   1) 分屏引擎重写：rAF 合帧 + 只写不读 + 拖拽期间关掉 backdrop-filter
 *   2) 折叠不再用 display:none，改成宽度/内边距/透明度的弹簧过场
 *   3) 偏好设置 6 页（全面接通真实持久化、算力/模型缓存与渲染导出）
 *   4) 界面语言 12 种（含 RTL）
 *   5) 字幕高光滑块缓存测量 + requestVideoFrameCallback 逐帧精确同步
 *   6) 显式单/双人模式切换 + 失败条目增量补翻 + 调色台实时联动
 * ========================================================================== */

/* ============================ 0. 全局状态 ============================ */

const state = {
  currentTime: 0.0,
  duration: 24.00,
  isPlaying: false,
  usingRealVideo: false,
  videoFailed: false,
  termLock: false,          // 术语强制锁定开关（关闭时不向翻译注入术语表）
  dualSpeaker: true,        // 默认开启智能双人模式（翻译时 LLM 自动区分对话 A/B；单人独白时自动隐藏色条）
  islandOpen: false,
  sourceLang: 'auto',
  targetLang: 'zh-CN',
  uiLang: 'zh-CN',
  visMode: 'particles',
  activeId: -1,
  magneticStrength: 0.20,
  computeMode: 'auto',
  model: 'small',           // 默认推荐 small（460MB）；若本地已缓存 large-v3-turbo 启动时自动切换
  modelExplicit: false,
  modelStatus: null,
  native: false,               // 是否运行在 pywebview 桌面环境
  videoPath: null,             // 本地视频的绝对路径（用于压制）
  videoTitle: '',              // 当前视频标题（用于导出命名模板 {title}）
  prefs: {
    autoOpenExport: true,
    completionSound: true,
    exportDir: ''
  },
  style: {
    zhColor: '#fcd34d',
    speakerBColor: '#7dd3fc',
    srcColor: '#d4d4d8',
    zhSize: 19,
    srcSize: 12,
    blur: 12,
    stroke: 75,
    order: 'zh_top',
    font: 'sans',
    bgOpacity: 72,
    marginBottom: 11
  },
  render: {
    res: '1080',
    customW: 1920,
    customH: 1080,
    fps: 'source',
    crf: 23,
    encoder: 'auto',
    audioBitrate: '192k',
    burnSub: true,
    nameTpl: '{title}_{lang}_{res}'
  },
  engine: { temp: 0.3, batch: 12, context: 3 },
  subs: []   // 启动时空列表，不显示任何示例字幕；导入视频或识别后自动填充
};

const $ = (id) => document.getElementById(id);

/* ============================ 1. 声效 ============================ */

const SFX = {
  masterVol: 0.70, microVol: 0.18, profile: 'mech', ctx: null, _lastTick: 0,
  btnEnabled: true, waveEnabled: true, tickEnabled: true,
  init() {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) this.ctx = new AC();
    }
    if (this.ctx && this.ctx.state === 'suspended') this.ctx.resume();
  },
  microTick(freq = 950) {
    if (!this.tickEnabled || this.microVol <= 0.005) return;
    const now = performance.now();               // 拖拽时节流，避免海量音频节点
    if (now - this._lastTick < 55) return;
    this._lastTick = now;
    this._synth(freq, 0.022, this.microVol * 0.18);
  },
  actionPop(freq = 520) {
    if (!this.btnEnabled || this.masterVol <= 0.01) return;
    this._synth(freq, 0.055, this.masterVol * 0.15);
  },
  _synth(freq, dur, v) {
    this.init();
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    const osc = this.ctx.createOscillator(), gain = this.ctx.createGain();
    if (this.profile === 'mech') {
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(240, now);
      osc.frequency.exponentialRampToValueAtTime(75, now + dur);
    } else if (this.profile === 'taptic') {
      osc.type = 'sine';
      osc.frequency.setValueAtTime(165, now);
      osc.frequency.exponentialRampToValueAtTime(52, now + 0.02);
    } else if (this.profile === 'synth') {
      osc.type = 'sawtooth';
      osc.frequency.setValueAtTime(freq * 0.75, now);
      osc.frequency.exponentialRampToValueAtTime(freq * 1.25, now + dur);
    } else if (this.profile === 'harp') {
      osc.type = 'sine';
      osc.frequency.setValueAtTime(freq * 1.3, now);
      osc.frequency.exponentialRampToValueAtTime(freq * 1.6, now + dur * 1.2);
    } else {
      osc.type = 'sine';
      osc.frequency.setValueAtTime(380, now);
      osc.frequency.exponentialRampToValueAtTime(860, now + dur);
    }
    gain.gain.setValueAtTime(this.profile === 'synth' ? v * 0.45 : v, now);
    gain.gain.exponentialRampToValueAtTime(0.0008, now + dur);
    osc.connect(gain); gain.connect(this.ctx.destination);
    osc.start(now); osc.stop(now + dur + 0.005);
  },
  wavePluck(idx) {
    if (!this.waveEnabled || this.microVol <= 0.005) return;
    this.init();
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    const scale = [261.63, 293.66, 329.63, 392.00, 440.00, 523.25, 587.33, 659.25, 783.99, 880.00];
    const osc = this.ctx.createOscillator(), gain = this.ctx.createGain();
    const v = this.microVol * 0.15;
    osc.type = 'sine';
    osc.frequency.setValueAtTime(scale[idx % scale.length], now);
    gain.gain.setValueAtTime(v, now);
    gain.gain.exponentialRampToValueAtTime(0.0005, now + 0.18);
    osc.connect(gain); gain.connect(this.ctx.destination);
    osc.start(now); osc.stop(now + 0.19);
  },
  stringPluck(idx) {
    if (!this.waveEnabled || this.masterVol <= 0.01) return;
    this.init();
    if (!this.ctx) return;
    const now = this.ctx.currentTime;
    const scale = [261.63, 293.66, 329.63, 392.00, 440.00, 523.25, 587.33, 659.25, 783.99, 880.00];
    const osc = this.ctx.createOscillator(), gain = this.ctx.createGain();
    const v = this.masterVol * 0.11;
    osc.type = this.profile === 'synth' ? 'sawtooth' : (this.profile === 'mech' ? 'triangle' : 'sine');
    osc.frequency.setValueAtTime(scale[idx % scale.length], now);
    gain.gain.setValueAtTime(this.profile === 'synth' ? v * 0.4 : v, now);
    gain.gain.exponentialRampToValueAtTime(0.0008, now + 0.25);
    osc.connect(gain); gain.connect(this.ctx.destination);
    osc.start(now); osc.stop(now + 0.26);
  },
  chord() {
    if (this.masterVol <= 0.01) return;
    [523.25, 659.25, 783.99].forEach((f, i) => setTimeout(() => this.actionPop(f), i * 55));
  }
};

/* ============================ 2. 分屏引擎（性能核心） ============================ */
/*
 * 旧实现的三个致命点：
 *   a) mousemove 里直接读 offsetTop / offsetHeight → 每帧强制同步 layout
 *   b) 每次 move 挂两个 setTimeout(60/350) → 定时器堆积，越拖越卡
 *   c) 两个 28px backdrop-filter 面板每帧重算模糊 → 掉帧主因
 * 现在的做法：
 *   a) move 里只记录百分比，宽度写入合并到 rAF
 *   b) 拖拽期间 body.is-dragging 关掉面板 blur 与过渡
 *   c) 松手后才量一次字幕高光滑块
 */
const Split = {
  leftPct: 58,
  collapsed: null,
  _els: null,
  _raf: 0,
  _pending: null,
  _drag: null,
  _settle: 0,
  SNAP: 58,

  els() {
    if (!this._els) {
      this._els = {
        root: $('main-workspace'),
        left: $('left-pane'),
        right: $('right-pane'),
        resizer: $('split-resizer'),
        lrail: $('left-collapsed-bar'),
        rrail: $('right-collapsed-bar'),
        hud: $('split-ratio-hud')
      };
    }
    return this._els;
  },

  paint(pct) {
    const e = this.els();
    e.left.style.width = pct + '%';
    const n = Math.round(pct);
    const txt = n + '% : ' + (100 - n) + '%';
    if (e.hud.textContent !== txt) e.hud.textContent = txt;
  },

  schedule(pct) {
    if (this.collapsed) return;
    this._pending = pct;
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = 0;
      const p = this._pending;
      if (p == null) return;
      this._pending = null;
      this.paint(p);
    });
  },

  startDrag(ev) {
    if (ev.button !== 0) return;
    ev.preventDefault();
    if (this.collapsed) return;

    const rect = this.els().root.getBoundingClientRect();   // 整个拖拽只读这一次
    this._drag = { left: rect.left, width: rect.width, lastTick: this.leftPct };
    document.body.classList.add('is-dragging');
    this.els().hud.style.opacity = '1';
    SFX.microTick(880);

    window.addEventListener('mousemove', this._onMove);
    window.addEventListener('mouseup', this._onUp, { once: true });
  },

  _onMove: (ev) => {
    const d = Split._drag;
    if (!d) return;
    const raw = ((ev.clientX - d.left) / d.width) * 100;

    if (raw < 17) { Split._finishDrag(); Split.collapse('left'); SFX.actionPop(460); return; }
    if (raw > 83) { Split._finishDrag(); Split.collapse('right'); SFX.actionPop(460); return; }

    let pct = Math.abs(raw - Split.SNAP) < 1.8 ? Split.SNAP : raw;
    pct = Math.max(24, Math.min(76, pct));
    Split.leftPct = pct;
    Split.schedule(pct);

    if (Math.abs(pct - d.lastTick) >= 4) {
      d.lastTick = pct;
      // 拖拽中的刻度音大幅降频（每 12% 一次），避免海量 AudioContext 节点抢主线程
      if (Math.abs(pct - (d.lastSnd || -99)) >= 12) {
        d.lastSnd = pct;
        SFX.microTick(650 + pct * 6);
      }
    }
  },

  _onUp: () => Split._finishDrag(),

  _finishDrag() {
    if (!Split._drag) return;
    Split._drag = null;
    document.body.classList.remove('is-dragging');
    Split.els().hud.style.opacity = '';
    window.removeEventListener('mousemove', Split._onMove);
    // 宽度稳定后再量一次卡片高度（宽度变了文字会重排）
    clearTimeout(Split._settle);
    Split._settle = setTimeout(() => Glider.refresh(), 280);
  },

  collapse(mode) {
    const e = this.els();
    if (this.collapsed === mode) return;
    this.collapsed = mode;
    if (this._raf) { cancelAnimationFrame(this._raf); this._raf = 0; }
    this._pending = null;

    e.left.classList.remove('pane-collapsed', 'pane-max');
    e.right.classList.remove('pane-collapsed');
    e.lrail.classList.add('rail-off');
    e.rrail.classList.add('rail-off');
    e.resizer.classList.remove('resizer-off');

    if (mode === 'left') {
      e.left.classList.add('pane-collapsed');
      e.resizer.classList.add('resizer-off');
      e.lrail.classList.remove('rail-off');
      e.hud.textContent = '左舱已收起';
    } else if (mode === 'right') {
      e.right.classList.add('pane-collapsed');
      e.left.classList.add('pane-max');
      e.resizer.classList.add('resizer-off');
      e.rrail.classList.remove('rail-off');
      e.hud.textContent = '右舱已收起';
    } else {
      e.left.style.width = this.leftPct + '%';
      e.hud.textContent = Math.round(this.leftPct) + '% : ' + (100 - Math.round(this.leftPct)) + '%';
    }

    clearTimeout(this._settle);
    this._settle = setTimeout(() => Glider.refresh(), 580);
  },

  restore() {
    SFX.actionPop(660);
    if (this.collapsed) { this.collapse(null); toast('已展开分屏'); return; }
    this.leftPct = 58;
    this.paint(58);
    toast('已弹簧复位至 58% : 42% 黄金分屏');
    setTimeout(() => Glider.refresh(), 600);
  },

  nudge(dir) {
    SFX.actionPop(620);
    if (dir === 'left') {
      if (this.collapsed === 'right') return this.restore();
      if (this.collapsed === 'left') return this.restore();
      if (this.leftPct > 34) { this.leftPct = 30; this.paint(30); toast('已让出宽屏空间 (30 : 70)，再点一次收起左舱'); }
      else { this.collapse('left'); toast('左舱已收起 · 右侧工作台全屏'); }
    } else {
      if (this.collapsed) return this.restore();
      if (this.leftPct < 66) { this.leftPct = 72; this.paint(72); toast('已放大舞台 (72 : 28)，再点一次收起右舱'); }
      else { this.collapse('right'); toast('右舱已收起 · 舞台全屏'); }
    }
    setTimeout(() => Glider.refresh(), 600);
  }
};

/* ============================ 3. 字幕高光滑块（缓存版） ============================ */

const Glider = {
  _raf: 0,
  _dirty: true,
  _cache: null,
  _lastStamp: '',

  invalidate() { this._dirty = true; },

  refresh() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.draw(); });
  },

  draw() {
    const wrapper = $('sub-cards-wrapper');
    const glider = $('sub-active-glider');
    if (!wrapper || !glider) return;

    if (this._dirty || !this._cache) {
      const map = new Map();
      wrapper.querySelectorAll('.sub-squircle-card').forEach(card => {
        map.set(card.dataset.subId, {
          top: card.offsetTop,
          h: card.offsetHeight,
          badge: card.querySelector('.sub-idx-badge')
        });
      });
      this._cache = map;
      this._dirty = false;
      this._lastStamp = '';
    }

    const stamp = String(state.activeId);
    if (this._lastStamp && this._lastStamp !== stamp) {
      const prevHit = this._cache.get(this._lastStamp);
      if (prevHit && prevHit.badge) {
        prevHit.badge.className = 'sub-idx-badge px-2 py-0.5 rounded-full font-mono-num text-[10px] font-semibold transition-colors duration-300 bg-white/[0.06] text-zinc-400';
      }
    }

    const hit = this._cache.get(stamp);
    if (!hit) {
      glider.style.opacity = '0';
      this._lastStamp = '';
      return;
    }
    glider.style.height = hit.h + 'px';
    glider.style.transform = 'translateY(' + hit.top + 'px)';
    glider.style.opacity = '1';
    if (hit.badge && this._lastStamp !== stamp) {
      hit.badge.className = 'sub-idx-badge px-2 py-0.5 rounded-full font-mono-num text-[10px] font-semibold transition-colors duration-300 bg-indigo-500 text-white';
    }
    this._lastStamp = stamp;
  }
};

/* ============================ 4. 通用小工具 ============================ */

function formatTime(sec) {
  const m = String(Math.floor(sec / 60)).padStart(2, '0');
  const s = (sec % 60).toFixed(2).padStart(5, '0');
  return m + ':' + s;
}

/* 真正的 SRT 时间码：HH:MM:SS,mmm —— 旧版把 "00:05.20" 直接拼成 "00:05,20 0" 是错的 */
function toSrtTime(sec) {
  const ms = Math.max(0, Math.round(sec * 1000));
  const h = Math.floor(ms / 3600000);
  const m = Math.floor((ms % 3600000) / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  const mm = ms % 1000;
  return String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0') + ':' +
    String(s).padStart(2, '0') + ',' + String(mm).padStart(3, '0');
}

function toAssTime(sec) {
  const cs = Math.max(0, Math.round(sec * 100));
  const h = Math.floor(cs / 360000);
  const m = Math.floor((cs % 360000) / 6000);
  const s = Math.floor((cs % 6000) / 100);
  const c = cs % 100;
  return h + ':' + String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0') + '.' + String(c).padStart(2, '0');
}

function hexToAss(hex, opacityPct) {
  let c = String(hex || '#ffffff').replace('#', '');
  if (c.length === 3) c = c.split('').map(x => x + x).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(c)) c = 'ffffff';
  const r = parseInt(c.slice(0, 2), 16), g = parseInt(c.slice(2, 4), 16), b = parseInt(c.slice(4, 6), 16);
  const aa = opacityPct == null ? 0 : Math.round(255 * (1 - Math.max(0, Math.min(100, opacityPct)) / 100));
  const h2 = (v) => v.toString(16).padStart(2, '0').toUpperCase();
  return '&H' + h2(aa) + h2(b) + h2(g) + h2(r);
}

function toast(msg) {
  const box = $('toast-box');
  const el = document.createElement('div');
  el.className = 'glass-pill px-4 py-2 rounded-full text-xs text-zinc-100 transition-all duration-300';
  el.innerText = msg;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, 2200);
}

/* 居中红色错误横幅：关键失败用这个，比右下角 toast 醒目 */
let _errTimer = null;
function showError(msg) {
  const el = $('error-banner'), txt = $('error-banner-text');
  if (!el) { toast(msg); return; }
  if (txt) txt.innerText = msg;
  el.classList.remove('hidden');
  el.style.opacity = '0';
  el.style.transform = 'translate(-50%, -50%) translateY(14px)';
  requestAnimationFrame(() => {
    el.style.opacity = '1';
    el.style.transform = 'translate(-50%, -50%) translateY(0)';
  });
  SFX.actionPop(240);
  if (_errTimer) clearTimeout(_errTimer);
  _errTimer = setTimeout(() => { el.classList.add('hidden'); }, 5000);
}
function hideError() {
  if (_errTimer) { clearTimeout(_errTimer); _errTimer = null; }
  const el = $('error-banner');
  if (el) el.classList.add('hidden');
}

function openModal(id) { SFX.microTick(580); $(id).classList.remove('hidden'); }
function closeModal(id) { SFX.microTick(420); $(id).classList.add('hidden'); }

/* 通用开关按钮 */
function toggleSwitch(btn) {
  const on = btn.dataset.on !== '0';
  btn.dataset.on = on ? '0' : '1';
  btn.textContent = on ? '关闭' : '开启';
  btn.className = on
    ? 'px-3 py-1 rounded-full bg-white/[0.08] text-zinc-400 text-[11px] font-medium'
    : 'px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-300 text-[11px] font-medium';
  SFX.microTick(on ? 480 : 760);
}

/* 渲染清晰度：目标分辨率 */
const RES_MAP = { '2160': [3840, 2160], '1080': [1920, 1080], '720': [1280, 720], '480': [854, 480] };

function sourceSize() {
  const v = $('real-video');
  if (state.usingRealVideo && v && v.videoWidth) return { w: v.videoWidth, h: v.videoHeight };
  return { w: 1920, h: 1080 };
}

function renderTarget() {
  const r = state.render;
  if (r.res === 'original') return sourceSize();
  if (r.res === 'custom') return { w: r.customW, h: r.customH };
  const p = RES_MAP[r.res] || RES_MAP['1080'];
  return { w: p[0], h: p[1] };
}

/* ============================ 5. 灵动岛 / 语种 / 界面语言 ============================ */

let pickingMode = null;

function toggleIsland(open) {
  if (state.islandOpen === open) return;
  state.islandOpen = open;
  SFX.microTick(600);
  const island = $('dynamic-island'), col = $('island-collapsed'), exp = $('island-expanded');

  if (open) {
    island.style.width = '540px';
    island.style.height = '290px';   // 加高：容纳平台徽章 + B站扫码按钮，避免底部被裁
    island.style.borderRadius = '28px';
    col.classList.add('hidden');
    exp.classList.remove('hidden');
    requestAnimationFrame(() => { exp.style.opacity = '1'; });
  } else {
    closeLangPicker();
    exp.style.opacity = '0';
    island.style.width = '410px';
    island.style.height = '42px';
    island.style.borderRadius = '999px';
    setTimeout(() => {
      if (!state.islandOpen) { exp.classList.add('hidden'); col.classList.remove('hidden'); }
    }, 200);
  }
}

function openLangPicker(mode) {
  SFX.actionPop(560);
  pickingMode = mode;
  $('island-main-controls').classList.add('hidden');
  $('lang-grid-drawer').classList.remove('hidden');
  $('dynamic-island').style.height = '300px';
  $('btn-auto-detect').classList.toggle('hidden', mode === 'target');
  $('lang-drawer-title').innerText = mode === 'source'
    ? '选择原视频语言 (12 国语言 4×3 对称矩阵)'
    : '选择翻译目标语言 (支持任意语种互译)';

  const box = $('lang-grid-box');
  box.innerHTML = '';
  LANGUAGES_12.forEach(lang => {
    const cur = (mode === 'source' && state.sourceLang === lang.code) ||
                (mode === 'target' && state.targetLang === lang.code);
    const btn = document.createElement('button');
    btn.onclick = () => confirmLanguageChoice(lang);
    btn.className = 'py-2 px-2.5 rounded-full text-[11px] truncate transition flex items-center justify-center space-x-1.5 ' +
      (cur ? 'bg-indigo-500 text-white font-semibold shadow-[0_0_14px_rgba(99,102,241,0.55)] scale-[1.03]'
           : 'bg-white/[0.05] text-zinc-300 hover:bg-white/[0.12] hover:text-white');
    btn.innerHTML = '<span class="font-mono-num text-[10px] opacity-75">' + lang.short + '</span><span>' +
      lang.name.split(' ')[0] + '</span>';
    box.appendChild(btn);
  });
}

function closeLangPicker() {
  pickingMode = null;
  $('lang-grid-drawer').classList.add('hidden');
  $('island-main-controls').classList.remove('hidden');
  if (state.islandOpen) $('dynamic-island').style.height = '290px';
}

function confirmLanguageChoice(lang) {
  SFX.actionPop(660);
  if (pickingMode === 'source') {
    state.sourceLang = lang.code;
    $('label-source-lang').innerText = lang.name;
    $('pill-src-code').innerText = lang.short;
  } else {
    state.targetLang = lang.code;
    $('label-target-lang').innerText = lang.name;
    $('pill-tgt-code').innerText = lang.short;
    if (lang.sample && state.subs[1]) {
      state.subs[1].zh = lang.sample;
      refreshUI(true);
    }
  }
  closeLangPicker();
}

let swapDeg = 0;
function swapLanguages() {
  if (state.sourceLang === 'auto') {
    toast('源语言为「自动检测」时无需对调，请先指定源语言');
    return;
  }
  SFX.actionPop(680);
  swapDeg += 180;
  $('btn-swap-lang').style.transform = 'rotate(' + swapDeg + 'deg)';

  const oldSrc = LANGUAGES_12.find(l => l.code === state.sourceLang);
  const oldTgt = LANGUAGES_12.find(l => l.code === state.targetLang);
  if (!oldSrc || !oldTgt) return;

  state.sourceLang = oldTgt.code;
  state.targetLang = oldSrc.code;
  $('label-source-lang').innerText = oldTgt.name;
  $('label-target-lang').innerText = oldSrc.name;
  $('pill-src-code').innerText = oldTgt.short;
  $('pill-tgt-code').innerText = oldSrc.short;

  state.subs.forEach(s => { const t = s.zh; s.zh = s.src; s.src = t; });
  refreshUI(true);
  toast('已切换为：' + oldTgt.name + ' ⇄ ' + oldSrc.name);
}

/* ---------- 12 种界面语言 ---------- */

function renderUiLangGrid() {
  const box = $('ui-lang-grid');
  if (!box) return;
  box.innerHTML = '';
  UI_LANGS.forEach(lang => {
    const cur = state.uiLang === lang.code;
    const btn = document.createElement('button');
    btn.onclick = () => setUiLanguage(lang.code);
    btn.className = 'py-1.5 px-2 rounded-full text-[11px] truncate transition flex items-center justify-center space-x-1 ' +
      (cur ? 'bg-indigo-500 text-white font-semibold shadow-[0_0_12px_rgba(99,102,241,0.5)]'
           : 'bg-white/[0.05] text-zinc-300 hover:bg-white/[0.12] hover:text-white');
    btn.innerHTML = '<span class="font-mono-num text-[10px] opacity-70">' + lang.short + '</span><span>' + lang.name + '</span>';
    box.appendChild(btn);
  });
  const cur = UI_LANGS.find(l => l.code === state.uiLang);
  const lbl = $('ui-lang-label');
  if (lbl && cur) lbl.innerText = cur.name;
}

function setUiLanguage(code) {
  const lang = UI_LANGS.find(l => l.code === code) || UI_LANGS[0];
  state.uiLang = lang.code;
  SFX.actionPop(600);

  const dict = I18N[lang.code] || I18N['zh-CN'];
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (dict[key]) el.innerText = dict[key];
  });

  document.body.dir = lang.dir === 'rtl' ? 'rtl' : 'ltr';
  document.documentElement.lang = lang.code;
  renderUiLangGrid();
  setTimeout(() => Glider.refresh(), 120);
  toast('界面语言：' + lang.name);
}

/* ============================ 6. 36 行业术语库 ============================ */

let currentSectorIdx = 0;
let currentIndustryId = 0;

function switchMacroSector(secIdx) {
  SFX.microTick(900);
  currentSectorIdx = secIdx;
  $('sector-glider').style.transform = 'translateX(' + (secIdx * 100) + '%)';
  for (let i = 0; i < 6; i++) {
    $('sec-' + i).className = i === secIdx
      ? 'relative z-10 py-1 rounded-full text-white font-semibold transition truncate'
      : 'relative z-10 py-1 rounded-full text-zinc-400 hover:text-white transition truncate';
  }
  const first = GLOSSARY_36.find(g => g.sector === secIdx);
  if (first) selectIndustry(first.id);
}

function renderSubIndustryBar() {
  const bar = $('sub-industry-bar');
  if (!bar) return;
  bar.innerHTML = '';
  GLOSSARY_36.filter(g => g.sector === currentSectorIdx).forEach(ind => {
    const sel = ind.id === currentIndustryId;
    const btn = document.createElement('button');
    btn.onclick = () => selectIndustry(ind.id);
    btn.className = 'py-1 px-2 rounded-full truncate transition ' +
      (sel ? 'bg-indigo-500/30 border border-indigo-400/50 text-white font-semibold shadow-sm'
           : 'text-zinc-400 hover:text-zinc-200 hover:bg-white/[0.04]');
    btn.innerText = String(ind.id + 1).padStart(2, '0') + '. ' + ind.name;
    bar.appendChild(btn);
  });
}

function selectIndustry(indId) {
  SFX.microTick(1050);
  currentIndustryId = indId;
  const obj = GLOSSARY_36[indId];
  if (!obj) return;
  currentSectorIdx = obj.sector;
  $('sector-glider').style.transform = 'translateX(' + (currentSectorIdx * 100) + '%)';
  $('current-ind-badge').innerText = String(indId + 1).padStart(2, '0') + '. ' + obj.name;
  renderSubIndustryBar();
  renderTermTiles(true);
  if (!$('industry-36-drawer').classList.contains('hidden')) renderAll36IndustriesGrid();
}

function toggleIndustryMatrixDrawer() {
  SFX.actionPop(580);
  const drawer = $('industry-36-drawer');
  const opening = drawer.classList.contains('hidden');
  if (opening) {
    drawer.classList.remove('hidden');
    $('term-tiles-box').classList.add('hidden');
    $('term-input-row').classList.add('hidden');
    renderAll36IndustriesGrid();
  } else {
    drawer.classList.add('hidden');
    $('term-tiles-box').classList.remove('hidden');
    $('term-input-row').classList.remove('hidden');
  }
}

function renderAll36IndustriesGrid() {
  const grid = $('industry-36-grid');
  grid.innerHTML = '';
  GLOSSARY_36.forEach((ind, idx) => {
    const sel = ind.id === currentIndustryId;
    const btn = document.createElement('button');
    btn.onclick = () => selectIndustry(ind.id);
    btn.style.animationDelay = (idx * 8) + 'ms';
    btn.className = 'term-tile-anim p-2.5 rounded-[16px] text-left truncate transition flex items-center justify-between ' +
      (sel ? 'bg-indigo-500 text-white font-semibold shadow-[0_0_15px_rgba(99,102,241,0.5)]'
           : 'bg-white/[0.04] hover:bg-white/[0.1] text-zinc-300 border border-white/[0.05]');
    btn.innerHTML = '<span class="truncate">' + String(ind.id + 1).padStart(2, '0') + '. ' + ind.name +
      '</span><span class="text-[9px] font-mono-num opacity-75 ml-1">24词</span>';
    grid.appendChild(btn);
  });
}

function renderTermTiles(animate) {
  const box = $('term-tiles-box');
  if (!box) return;
  box.innerHTML = '';
  GLOSSARY_36[currentIndustryId].items.forEach((t, idx) => {
    const tile = document.createElement('div');
    tile.onclick = () => {
      t.active = !t.active;
      SFX.microTick(t.active ? 820 : 480);
      renderTermTiles(false);
      renderSubCards(false);
    };
    tile.className = 'term-tile-anim p-3 rounded-[20px] cursor-pointer flex flex-col justify-between ' +
      (t.active ? 'bg-indigo-500/15 shadow-[inset_0_1px_0_rgba(165,180,252,0.25),inset_0_0_0_1px_rgba(99,102,241,0.3)] text-white hover:scale-[1.02]'
                : 'bg-white/[0.02] shadow-[inset_0_1px_0_rgba(255,255,255,0.05)] text-zinc-500 hover:bg-white/[0.04]');
    tile.style.animation = animate ? '' : 'none';
    if (animate) tile.style.animationDelay = (idx * 12) + 'ms';
    tile.innerHTML =
      '<div class="flex items-center justify-between text-[10px] font-mono-num">' +
        '<span class="truncate pr-1 text-zinc-300">' + t.src + '</span>' +
        '<span class="px-2 py-0.5 rounded-full text-[9px] shrink-0 ' +
          (t.active ? 'bg-emerald-500/20 text-emerald-300' : 'bg-white/5 text-zinc-500') + '">' +
          (t.active ? '锁定' : '停用') + '</span>' +
      '</div>' +
      '<div class="text-xs font-bold mt-1 ' + (t.active ? 'text-emerald-300' : 'text-zinc-500') + '">' + t.zh + '</div>';
    box.appendChild(tile);
  });
  const n = GLOSSARY_36[currentIndustryId].items.filter(i => i.active).length;
  const cnt = $('active-term-count');
  if (cnt) cnt.innerText = '(' + n + '/' + GLOSSARY_36[currentIndustryId].items.length + ')';
}

function toggleAllCurrentCategory() {
  SFX.actionPop(640);
  const items = GLOSSARY_36[currentIndustryId].items;
  const all = items.every(i => i.active);
  items.forEach(i => { i.active = !all; });
  renderTermTiles(true);
  renderSubCards(false);
  toast(all ? '已停用本行业全部术语' : '已锁定本行业全部 24 个术语');
}

function addTermTile() {
  const s = $('term-src-in').value.trim();
  const z = $('term-zh-in').value.trim();
  if (!s || !z) return;
  SFX.chord();
  GLOSSARY_36[currentIndustryId].items.unshift({ src: s, zh: z, active: true });
  $('term-src-in').value = '';
  $('term-zh-in').value = '';
  renderTermTiles(true);
  renderSubCards(false);
}

function getAllActiveTerms() {
  return GLOSSARY_36[currentIndustryId].items.filter(i => i.active);
}

/* 术语强制锁定开关：关掉后翻译不带术语表，译文更自然 */
function toggleTermLock(btn) {
  SFX.microTick(700);
  state.termLock = !state.termLock;
  refreshTermLockBtn();
  toast(state.termLock
    ? '术语锁定已开启：专业术语会强制写进译文'
    : '术语锁定已关闭：翻译更自然，不再强制套术语（适合生活类/剧情类视频）');
}

function refreshTermLockBtn() {
  // 设置页 + 术语魔方面板 两处按钮同步
  ['term-lock-btn', 'term-lock-btn-2'].forEach(id => {
    const btn = $(id);
    if (!btn) return;
    btn.innerText = state.termLock ? '已开启' : '已关闭';
    btn.className = (id === 'term-lock-btn' ? 'px-3 py-1' : 'px-2.5 py-0.5') +
      ' rounded-full text-[11px] font-medium ' +
      (state.termLock ? 'bg-emerald-500/20 text-emerald-300' : 'bg-white/[0.08] text-zinc-400');
  });
}

/* 按开关决定要传给后端的术语表 */
function buildGlossary() {
  if (!state.termLock) return '';
  return getAllActiveTerms().map(t => t.src + '=' + t.zh).join('\n');
}

/* ============================ 7. 右舱面板 / 可视化切换 ============================ */

function switchRightPanel(key, index) {
  SFX.microTick(650);
  const glider = $('tab-glider');
  glider.style.transform = 'translateX(' + (index * 100) + '%) scaleX(1.06)';
  setTimeout(() => { glider.style.transform = 'translateX(' + (index * 100) + '%) scaleX(1)'; }, 180);

  for (let i = 0; i < 3; i++) {
    $('tab-btn-' + i).className = i === index
      ? 'relative z-10 py-2 rounded-full text-white font-semibold flex items-center justify-center space-x-1.5 transition-colors'
      : 'relative z-10 py-2 rounded-full text-zinc-400 hover:text-zinc-200 font-medium flex items-center justify-center space-x-1.5 transition-colors';
  }
  ['subs', 'pad', 'terms'].forEach(k => {
    const el = $('panel-' + k);
    if (k === key) { el.classList.remove('panel-hidden'); el.classList.add('panel-active'); }
    else { el.classList.remove('panel-active'); el.classList.add('panel-hidden'); }
  });

  if (key === 'subs') setTimeout(() => Glider.refresh(), 300);
  if (key === 'terms') { renderSubIndustryBar(); renderTermTiles(true); }
}

function switchVisualizer(mode, idx) {
  SFX.microTick(580);
  state.visMode = mode;
  $('vis-glider').style.transform = 'translateX(' + (idx * 96) + 'px)';
  const hints = [
    '引力场已就绪 · 拖动中间分界可双向缩放界面',
    '0ms 零延迟胡克琴弦 · 划过琴弦即刻拨响',
    '双线对称差分探针 · 上下双轨镜像联动'
  ];
  $('vis-hint-pill').innerText = hints[idx];
  for (let i = 0; i < 3; i++) {
    $('vis-btn-' + i).className = i === idx
      ? 'relative z-10 w-[96px] py-1 rounded-full text-white font-medium transition'
      : 'relative z-10 w-[96px] py-1 rounded-full text-zinc-400 hover:text-zinc-200 transition';
  }
}

/* ============================ 8. 字幕卡片 / 时间轴 / 播放 ============================ */

let historyStack = [];

function saveSnapshot() {
  historyStack.push(JSON.stringify(state.subs));
  if (historyStack.length > 20) historyStack.shift();
  $('undo-btn').disabled = false;
}

function undo() {
  if (!historyStack.length) return;
  SFX.actionPop(400);
  state.subs = JSON.parse(historyStack.pop());
  $('undo-btn').disabled = historyStack.length === 0;
  refreshUI(true);
  toast('已撤销上一步修改');
}

function toggleDualSpeakerMode() {
  SFX.actionPop(620);
  state.dualSpeaker = !state.dualSpeaker;
  const btn = $('dual-speaker-btn');
  if (btn) {
    btn.innerText = state.dualSpeaker ? '说话人：双人模式 (A/B)' : '说话人：单人模式';
    btn.className = state.dualSpeaker
      ? 'px-2.5 py-1 rounded-full bg-indigo-500/25 border border-indigo-400/40 text-indigo-200 text-[10px] font-semibold transition'
      : 'px-2.5 py-1 rounded-full bg-white/[0.06] hover:bg-white/[0.12] text-zinc-300 text-[10px] font-medium transition';
  }
  refreshUI(true);
  toast(state.dualSpeaker
    ? '已开启双人模式：可在字幕卡片手工标记说话人 A / B，真实时间重叠时舞台自动分轨显示'
    : '已切回单人模式：统一使用单人字幕样式，避免误触发双行色条');
}

function getFailedSubCount() {
  return (state.subs || []).filter(s => {
    const hasSrc = Boolean(String(s.src || '').trim());
    const hasZh = Boolean(String(s.zh || '').trim());
    return s.translate_status === 'failed' || (hasSrc && !hasZh && s.translate_status === 'missing');
  }).length;
}

function updateFailedRetryButton() {
  const btn = $('retry-failed-btn');
  if (!btn) return;
  const n = getFailedSubCount();
  if (n > 0) {
    btn.classList.remove('hidden');
    btn.innerText = '补翻失败条目 (' + n + ')';
  } else {
    btn.classList.add('hidden');
  }
}

function renderSubCards(forceRebuild) {
  const wrapper = $('sub-cards-wrapper');
  if (!wrapper) return;

  updateFailedRetryButton();

  if (!forceRebuild && wrapper.querySelectorAll('.sub-squircle-card').length === state.subs.length) {
    Glider.refresh();
    return;
  }

  wrapper.innerHTML = '';
  const activeTerms = getAllActiveTerms();

  state.subs.forEach((sub, idx) => {
    const srcText = String(sub.src || '');
    const zhText = String(sub.zh || '');
    const isFailed = sub.translate_status === 'failed' || (Boolean(srcText.trim()) && !Boolean(zhText.trim()) && sub.translate_status === 'missing');
    const hits = activeTerms.filter(t =>
      srcText.toLowerCase().includes(String(t.src || '').toLowerCase()) ||
      zhText.includes(String(t.zh || ''))).slice(0, 3);

    const card = document.createElement('div');
    card.setAttribute('data-sub-id', sub.id);
    card.className = 'sub-squircle-card p-3.5 cursor-pointer group' +
      (isFailed ? ' border border-rose-500/45 bg-rose-500/[0.06]' : '');
    card.onclick = () => {
      if (state.activeId !== sub.id) {
        SFX.microTick(1080);
        state.activeId = sub.id;
        seekTo(sub.start + 0.01);
        Glider.refresh();
        updateActiveTimelineCapsule(state.activeId);
      }
    };

    const badges = hits.map(h =>
      '<span class="px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 text-[10px] font-medium">' + h.zh + '</span>'
    ).join('');
    const speakerId = Number(sub.speaker) === 1 ? 1 : 0;
    const speakerLabel = speakerId === 1 ? 'B' : 'A';
    const speakerClass = speakerId === 1
      ? 'bg-sky-500/20 text-sky-200 hover:bg-sky-500/35'
      : 'bg-amber-500/20 text-amber-200 hover:bg-amber-500/35';

    const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const failedBadge = isFailed
      ? '<span class="px-2 py-0.5 rounded-full bg-rose-500/25 text-rose-200 text-[10px] font-semibold" title="' + esc(sub.translate_error || '本句未获得有效译文，可点 AI 重译或补翻失败条目') + '">待补译</span>'
      : '';
    const speakerBtnHtml = state.dualSpeaker
      ? '<button class="speaker-btn px-2 py-0.5 rounded-full text-[10px] font-semibold ' + speakerClass + '" title="手动切换说话人 A / B">说话人 ' + speakerLabel + '</button>'
      : '';

    // 双语上下顺序跟随 state.style.order（zh_top = 译文在上 / en_top = 原文在上）
    const enTop = state.style.order === 'en_top';
    const makeInput = (key, text, top) =>
      '<input class="' + key + (key === 'zh-in'
        ? ' w-full bg-transparent text-[13px] font-medium text-zinc-100 outline-none focus:bg-black/35 rounded-xl px-2 py-1 transition'
        : ' w-full bg-transparent text-[11px] text-zinc-400 outline-none focus:bg-black/35 rounded-xl px-2 py-0.5 transition') +
        (top ? '' : ' mt-0.5') + '" value="' + esc(text) + '"' +
        (key === 'zh-in' && isFailed ? ' placeholder="未获得有效译文，点击右侧 AI 重译或手动输入..."' : '') + ' />';
    const fieldHtml = enTop
      ? makeInput('src-in', sub.src, true) + makeInput('zh-in', sub.zh, false)
      : makeInput('zh-in', sub.zh, true) + makeInput('src-in', sub.src, false);

    card.innerHTML =
      '<div class="pl-2.5">' +
        '<div class="flex items-center justify-between mb-1.5">' +
          '<div class="flex items-center space-x-2 flex-wrap gap-y-1">' +
            '<span class="sub-idx-badge px-2 py-0.5 rounded-full bg-white/[0.06] text-zinc-400 font-mono-num text-[10px] font-semibold transition-colors duration-300">#' + (idx + 1) + '</span>' +
            '<span class="font-mono-num text-[11px] text-zinc-400">' + formatTime(sub.start) + ' → ' + formatTime(sub.end) + '</span>' +
            failedBadge +
            '<div class="flex items-center space-x-1">' + badges + '</div>' +
          '</div>' +
          '<div class="' + (state.dualSpeaker || isFailed ? 'opacity-100' : 'opacity-0 group-hover:opacity-100') + ' flex items-center space-x-1.5 transition-opacity">' +
            speakerBtnHtml +
            '<button class="polish-btn px-2.5 py-0.5 rounded-full bg-indigo-500/20 hover:bg-indigo-500/35 text-indigo-200 text-[10px] font-medium">AI 重译</button>' +
            '<button class="del-btn w-5 h-5 rounded-full hover:bg-rose-500/25 flex items-center justify-center text-zinc-500 hover:text-rose-300">✕</button>' +
          '</div>' +
        '</div>' +
        fieldHtml +
      '</div>';

    card.querySelector('.zh-in').addEventListener('input', e => updateSubField(idx, 'zh', e.target.value));
    card.querySelector('.src-in').addEventListener('input', e => updateSubField(idx, 'src', e.target.value));
    const spkBtn = card.querySelector('.speaker-btn');
    if (spkBtn) {
      spkBtn.addEventListener('click', e => {
        e.stopPropagation();
        saveSnapshot();
        sub.speaker = Number(sub.speaker) === 1 ? 0 : 1;
        refreshUI(true);
        toast('已将当前字幕标记为说话人 ' + (sub.speaker === 1 ? 'B' : 'A'));
      });
    }
    card.querySelector('.polish-btn').addEventListener('click', e => { e.stopPropagation(); polishSub(idx, e.currentTarget); });
    card.querySelector('.del-btn').addEventListener('click', e => { e.stopPropagation(); deleteSub(idx); });

    wrapper.appendChild(card);
  });

  Glider.invalidate();
  Glider.refresh();
}

/* 鼠标聚光：只更新当前悬停的那张卡，且合并到 rAF */
let _spotEv = null, _spotRaf = 0;
function updateCardSpotlight(e) {
  _spotEv = e;
  if (_spotRaf) return;
  _spotRaf = requestAnimationFrame(() => {
    _spotRaf = 0;
    const ev = _spotEv;
    const card = ev.target && ev.target.closest ? ev.target.closest('.sub-squircle-card') : null;
    if (!card) return;
    const r = card.getBoundingClientRect();
    card.style.setProperty('--mx', (ev.clientX - r.left) + 'px');
    card.style.setProperty('--my', (ev.clientY - r.top) + 'px');
  });
}

function updateSubField(idx, field, val) {
  if (!state.subs[idx]) return;
  state.subs[idx][field] = val;
  if (field === 'zh' && String(val || '').trim()) {
    state.subs[idx].translate_status = 'ok';
    state.subs[idx].translate_error = '';
    updateFailedRetryButton();
  }
  invalidateStageCache();
  Glider.invalidate();
  const cap = _timelineCapMap.get(state.subs[idx].id);
  if (cap && cap.el) {
    if (cap.width >= 2.8) cap.el.innerText = state.subs[idx].zh || state.subs[idx].src || '';
    cap.el.title = '#' + (idx + 1) + ' ' + formatTime(state.subs[idx].start) + ' → ' + formatTime(state.subs[idx].end) + '\n' + (state.subs[idx].zh || state.subs[idx].src || '');
  }
  syncPlayheadTarget(state.currentTime);
}

async function polishSub(idx, btn) {
  SFX.actionPop(680);
  const s = state.subs[idx];
  if (!s) return;
  const srcText = String(s.src || '').trim();
  if (!srcText) {
    toast('当前条目原文为空，无法重译');
    return;
  }
  if (!state.native) {
    toast('（浏览器预览模式）AI 重译需在桌面应用中连接翻译引擎');
    return;
  }
  const origLabel = btn ? btn.innerText : '';
  if (btn) { btn.disabled = true; btn.innerText = '重译中…'; }
  try {
    const ctxBefore = state.subs.slice(Math.max(0, idx - 2), idx).map(x => String(x.src || '').trim()).filter(Boolean);
    const ctxAfter = state.subs.slice(idx + 1, idx + 3).map(x => String(x.src || '').trim()).filter(Boolean);
    const lang = state.sourceLang === 'auto' ? 'auto' : state.sourceLang;
    const r = await Bridge.translateSingle(srcText, ctxBefore, ctxAfter, buildGlossary(), lang);
    if (r && r.status === 'success' && String(r.zh_text || '').trim()) {
      saveSnapshot();
      s.zh = r.zh_text;
      s.translate_status = 'ok';
      s.translate_error = '';
      renderSubCards(true);
      syncPlayheadTarget(state.currentTime);
      toast('第 #' + (idx + 1) + ' 句已完成上下文 AI 重译');
    } else {
      showError((r && r.message) || ('第 #' + (idx + 1) + ' 句重译失败'));
    }
  } catch (e) {
    showError('单句重译出错：' + (e && e.message ? e.message : e));
  } finally {
    if (btn) { btn.disabled = false; btn.innerText = origLabel || 'AI 重译'; }
  }
}

async function retryFailedTranslations() {
  SFX.actionPop(660);
  const failedCount = getFailedSubCount();
  if (!failedCount) {
    toast('当前没有失败或缺失的译文条目');
    return;
  }
  const label = $('island-status-label');
  const onProgress = (p, t) => { if (label) label.innerText = t + ' ' + Math.round(p) + '%'; };
  const lang = state.sourceLang === 'auto' ? 'auto' : state.sourceLang;
  try {
    if (label) label.innerText = '正在补翻失败条目…';
    const tres = await Bridge.translate(
      state.subs, buildGlossary(), lang, state.engine.batch, state.engine.context, onProgress, true, state.engine.temp
    );
    if (tres && (tres.status === 'success' || tres.status === 'partial') && tres.segments) {
      saveSnapshot();
      state.subs = Bridge.toFrontSegments(tres.segments);
      refreshUI(true);
      const remain = getFailedSubCount();
      if (remain === 0) {
        if (state.prefs.completionSound) SFX.chord();
        toast('失败条目已全部补翻完成');
        if (label) label.innerText = '精翻完成';
      } else {
        showError('仍有 ' + remain + ' 条未获得有效译文，可继续重试或手工编辑。');
        if (label) label.innerText = '精翻部分完成 · ' + remain + ' 条待补';
      }
    } else {
      showError((tres && tres.message) || '补翻失败，请检查网络或 API Key');
      if (label) label.innerText = '补翻失败';
    }
  } catch (e) {
    showError('补翻出错：' + (e && e.message ? e.message : e));
  }
}

async function exportAlignmentCsv() {
  SFX.actionPop(600);
  if (!state.subs || !state.subs.length) {
    toast('当前没有字幕可导出核对表');
    return;
  }
  const stem = (state.videoTitle || 'subtitles').replace(/[\\/:*?"<>|]+/g, '_').slice(0, 60) || 'subtitles';
  if (state.native) {
    const r = await Bridge.exportAlignmentAudit(state.subs, stem);
    if (r && r.status === 'success') {
      toast('已导出时间轴核对表（CSV + JSON）：' + r.csv_path);
      if (state.prefs.autoOpenExport && r.csv_path) Bridge.openFolder(r.csv_path);
    } else {
      showError((r && r.message) || '导出时间轴核对表失败');
    }
    return;
  }
  const rows = [['index', 'id', 'start', 'end', 'duration', 'gap_from_prev', 'src_text', 'zh_text']];
  let prevEnd = null;
  state.subs.forEach((s, i) => {
    const st = Number(s.start) || 0, ed = Number(s.end) || 0;
    const gap = prevEnd == null ? '' : (st - prevEnd).toFixed(3);
    prevEnd = ed;
    rows.push([i + 1, s.id, st.toFixed(3), ed.toFixed(3), (ed - st).toFixed(3), gap, s.src || '', s.zh || '']);
  });
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g, '""') + '"').join(',')).join('\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = stem + '_alignment.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast('已导出 CSV 时间轴核对表');
}

function deleteSub(idx) {
  if (state.subs.length <= 1) return;
  SFX.actionPop(340);
  saveSnapshot();
  state.subs.splice(idx, 1);
  state.activeId = state.subs[Math.max(0, idx - 1)].id;
  refreshUI(true);
}

let _timelineCapMap = new Map();
let _lastActiveCapId = null;

function _capsuleClass(width, sel, isFailed) {
  return 'absolute top-1 bottom-1 rounded-full flex items-center text-[11px] truncate transition-colors duration-150 cursor-pointer ' +
    (width >= 2.8 ? 'px-2.5 ' : 'px-0.5 ') +
    (sel ? 'bg-gradient-to-r from-indigo-500/70 to-violet-500/70 text-white font-medium shadow-[inset_0_1px_0_rgba(255,255,255,0.35)] z-10'
         : (isFailed ? 'bg-rose-500/35 text-rose-200 hover:bg-rose-500/50'
                     : 'bg-white/[0.08] text-zinc-400 hover:bg-white/[0.16]'));
}

function updateActiveTimelineCapsule(nextId) {
  if (_lastActiveCapId === nextId) return;
  if (_lastActiveCapId != null) {
    const prev = _timelineCapMap.get(_lastActiveCapId);
    if (prev && prev.el) prev.el.className = _capsuleClass(prev.width, false, prev.isFailed);
  }
  const cur = _timelineCapMap.get(nextId);
  if (cur && cur.el) cur.el.className = _capsuleClass(cur.width, true, cur.isFailed);
  _lastActiveCapId = nextId;
}

function renderTimelineCapsules() {
  const box = $('timeline-capsules');
  if (!box) return;
  box.innerHTML = '';
  _timelineCapMap = new Map();
  _lastActiveCapId = state.activeId;
  const dur = Math.max(0.1, Number(state.duration) || 24);
  state.subs.forEach((s, i) => {
    const start = Math.max(0, Number(s.start) || 0);
    const end = Math.max(start + 0.05, Number(s.end) || start + 0.05);
    const left = Math.max(0, Math.min(99.6, (start / dur) * 100));
    const rawW = ((end - start) / dur) * 100;
    const width = Math.max(0.45, Math.min(100 - left, rawW));
    const sel = s.id === state.activeId;
    const isFailed = s.translate_status === 'failed';
    const cap = document.createElement('div');
    cap.className = _capsuleClass(width, sel, isFailed);
    cap.style.left = left + '%';
    cap.style.width = width + '%';
    cap.title = '#' + (i + 1) + ' ' + formatTime(start) + ' → ' + formatTime(end) + '\n' + (s.zh || s.src || '');
    if (width >= 2.8) cap.innerText = s.zh || s.src || '';
    cap.addEventListener('mousedown', (ev) => {
      ev.stopPropagation();
      state.activeId = s.id;
      seekTo(start + 0.01);
      Glider.refresh();
      updateActiveTimelineCapsule(state.activeId);
    });
    box.appendChild(cap);
    _timelineCapMap.set(s.id, { el: cap, width, isFailed });
  });
}

/* 不再用停顿/问号猜说话人：没有真实 diarization 标签时统一单人模式，
   避免单人喘气或断句时红蓝乱跳。 */
function assignSpeakers() {
  for (const s of state.subs) {
    if (s.speaker === '0') s.speaker = 0;
    else if (s.speaker === '1') s.speaker = 1;
    else if (s.speaker !== 0 && s.speaker !== 1) s.speaker = 0;
  }
}

/* 只显示当前时间真正覆盖到的字幕区间；不额外保留上一句，避免尾句滞留/双人幽灵行。
   单人模式下始终单行；开启双人模式（或存在手工标记的不同说话人重叠）才显示双行。 */
function activeSubsAt(t) {
  const subs = state.subs;
  const active = subs.filter(s => t >= Number(s.start) && t < Number(s.end));
  active.sort((a, b) => Number(a.start) - Number(b.start));
  if (state.dualSpeaker === false) {
    return active.length ? [active[active.length - 1]] : [];
  }
  // 同一说话人发生时间重叠时只保留最新一条；只有 A/B 两位不同说话人
  // 的区间确实重叠，舞台才显示双行。
  const latestBySpeaker = new Map();
  for (const item of active) {
    const speaker = Number(item.speaker) === 1 ? 1 : 0;
    latestBySpeaker.set(speaker, item);
  }
  return Array.from(latestBySpeaker.values())
    .sort((a, b) => Number(a.start) - Number(b.start))
    .slice(-2);
}

function _setRow(row, zhEl, srcEl, bar, item, showBar) {
  if (!item) return;
  const isSpeakerB = state.dualSpeaker && Number(item.speaker) === 1;
  const color = isSpeakerB ? (state.style.speakerBColor || '#7dd3fc') : (state.style.zhColor || '#fcd34d');
  if (bar) {
    bar.style.background = color;
    bar.classList.toggle('hidden', !showBar);
  }
  if (zhEl) {
    zhEl.style.color = color;
    zhEl.innerText = item.zh || '';
    zhEl.style.display = (item.zh || '').trim() ? '' : 'none';
  }
  if (srcEl) {
    srcEl.style.color = state.style.srcColor || '#d4d4d8';
    srcEl.innerText = item.src || '';
    srcEl.style.display = (item.src || '').trim() ? '' : 'none';
  }
}

let _lastTimeNowStr = '';
let _lastTimeTotalStr = '';
let _lastStageSig = '';

function invalidateStageCache() {
  _lastStageSig = '';
}

function syncPlayheadTarget(t) {
  const nowStr = formatTime(t);
  if (nowStr !== _lastTimeNowStr) {
    _lastTimeNowStr = nowStr;
    const tn = $('time-now');
    if (tn) tn.innerText = nowStr;
  }
  const totalStr = formatTime(state.duration);
  if (totalStr !== _lastTimeTotalStr) {
    _lastTimeTotalStr = totalStr;
    const tt = $('time-total');
    if (tt) tt.innerText = totalStr;
  }

  const list = activeSubsAt(t);
  const box = $('floating-sub');
  const row1 = $('sub-row-1'), row2 = $('sub-row-2');
  const divider = $('sub-divider');

  if (!list.length) {
    if (_lastStageSig !== 'empty') {
      _lastStageSig = 'empty';
      if (box) {
        box.style.opacity = '0';
        box.style.pointerEvents = 'none';
      }
      if ($('stage-zh')) $('stage-zh').innerText = '';
      if ($('stage-src')) $('stage-src').innerText = '';
      if ($('stage-zh-2')) $('stage-zh-2').innerText = '';
      if ($('stage-src-2')) $('stage-src-2').innerText = '';
      if (row2) { row2.classList.add('hidden'); row2.classList.remove('flex'); }
      if (divider) divider.classList.add('hidden');
    }
    if (state.activeId !== -1) {
      state.activeId = -1;
      updateActiveTimelineCapsule(-1);
    }
    return;
  }

  const first = list[0];
  const second = list[1];
  const hasSpeakerB = state.subs.some(s => Number(s.speaker) === 1);
  const showBar = Boolean((state.dualSpeaker && hasSpeakerB) || second);
  const sig = [
    first.id, first.zh || '', first.src || '', Number(first.speaker) === 1 ? 1 : 0,
    second ? second.id : '', second ? (second.zh || '') : '', second ? (second.src || '') : '', second ? (Number(second.speaker) === 1 ? 1 : 0) : '',
    showBar ? 1 : 0, state.dualSpeaker ? 1 : 0,
    state.style.zhColor, state.style.srcColor, state.style.speakerBColor
  ].join('\x1f');

  if (sig !== _lastStageSig) {
    _lastStageSig = sig;
    if (box) {
      box.style.opacity = '1';
      box.style.pointerEvents = '';
    }
    // 第 1 行永远是较早说的那句
    _setRow(row1, $('stage-zh'), $('stage-src'), $('spk-bar-1'), first, showBar);
    if (row1) { row1.classList.remove('hidden'); row1.classList.add('flex'); }

    if (second) {
      _setRow(row2, $('stage-zh-2'), $('stage-src-2'), $('spk-bar-2'), second, true);
      if (row2) { row2.classList.remove('hidden'); row2.classList.add('flex'); }
      if (divider) divider.classList.remove('hidden');
    } else {
      if (row2) { row2.classList.add('hidden'); row2.classList.remove('flex'); }
      if (divider) divider.classList.add('hidden');
      if ($('stage-zh-2')) $('stage-zh-2').innerText = '';
      if ($('stage-src-2')) $('stage-src-2').innerText = '';
    }
  }

  const active = list[list.length - 1];
  if (state.activeId !== active.id) {
    state.activeId = active.id;
    Glider.refresh();
    updateActiveTimelineCapsule(state.activeId);
  }
}

function updatePlayButtonUI() {
  const svg = $('play-svg');
  if (!svg) return;
  svg.innerHTML = state.isPlaying
    ? '<path d="M6 5h4v14H6V5zm8 0h4v14h-4V5z"/>'
    : '<path d="M8 5.14v14l11-7-11-7z"/>';
  svg.classList.toggle('translate-x-[1px]', !state.isPlaying);
}

function seekTo(sec) {
  state.currentTime = Math.max(0, Math.min(state.duration, sec));
  if (state.usingRealVideo) $('real-video').currentTime = state.currentTime;
  syncPlayheadTarget(state.currentTime);
}

function togglePlay() {
  SFX.actionPop(state.isPlaying ? 420 : 620);
  state.isPlaying = !state.isPlaying;
  if (state.usingRealVideo && !state.videoFailed) {
    const v = $('real-video');
    const p = state.isPlaying ? v.play() : v.pause();
    if (p && typeof p.catch === 'function') {
      p.catch(() => { state.isPlaying ? startSimPlayback() : stopSimPlayback(); });
    }
  } else {
    // 没有可播放画面时，用模拟时钟驱动时间轴（字幕编辑照样能用）
    state.isPlaying ? startSimPlayback() : stopSimPlayback();
  }
  updatePlayButtonUI();
}

function stepSub(dir) {
  SFX.microTick(950);
  const idx = state.subs.findIndex(s => s.id === state.activeId);
  const target = state.subs[Math.max(0, Math.min(state.subs.length - 1, idx + dir))];
  if (target) {
    state.activeId = target.id;
    seekTo(target.start + 0.05);
    Glider.refresh();
    updateActiveTimelineCapsule(state.activeId);
  }
}

function splitCurrentSub() {
  const idx = state.subs.findIndex(s => s.id === state.activeId);
  const sub = state.subs[idx];
  if (!sub || (sub.end - sub.start) < 1) return;
  SFX.actionPop(720);
  saveSnapshot();
  const mid = Number(((sub.start + sub.end) / 2).toFixed(2));
  const halfZh = Math.ceil(sub.zh.length / 2);
  const words = sub.src.split(' ');
  const halfSrc = Math.ceil(words.length / 2);
  state.subs.splice(idx, 1,
    { id: Date.now(), start: sub.start, end: mid, zh: sub.zh.slice(0, halfZh), src: words.slice(0, halfSrc).join(' ') },
    { id: Date.now() + 1, start: mid, end: sub.end, zh: sub.zh.slice(halfZh) || sub.zh, src: words.slice(halfSrc).join(' ') || sub.src }
  );
  refreshUI(true);
  toast('已将当前字幕段一分为二');
}

function mergeCurrentSub() {
  const idx = state.subs.findIndex(s => s.id === state.activeId);
  if (idx < 0 || idx >= state.subs.length - 1) return;
  SFX.actionPop(480);
  saveSnapshot();
  const a = state.subs[idx], b = state.subs[idx + 1];
  state.subs.splice(idx, 2, { id: a.id, start: a.start, end: b.end, zh: a.zh + ' ' + b.zh, src: a.src + ' ' + b.src });
  refreshUI(true);
  toast('已将相邻两句字幕丝滑合并');
}

function addNewSegment() {
  SFX.actionPop(640);
  saveSnapshot();
  const last = state.subs[state.subs.length - 1];
  const s = last ? Number((last.end + 0.2).toFixed(1)) : 0;
  const e = s + 2.5;
  if (e > state.duration) state.duration = e + 2;
  const id = Date.now();
  state.subs.push({ id, start: s, end: e, zh: '新增双语字幕胶囊段落...', src: 'New subtitle segment...' });
  state.activeId = id;
  refreshUI(true);
}

function startTimelineScrub(e) {
  const box = $('timeline-capsules');
  let lastTick = 0;
  function scrub(ev) {
    const r = box.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
    seekTo(pct * state.duration);
    const now = performance.now();
    if (now - lastTick > 48) { SFX.microTick(750 + Math.round(pct * 500)); lastTick = now; }
  }
  scrub(e);
  window.addEventListener('mousemove', scrub);
  window.addEventListener('mouseup', () => window.removeEventListener('mousemove', scrub), { once: true });
}

let waveBars = [];
function initPlayableWaveform() {
  const box = $('interactive-waveform');
  if (!box) return;
  box.innerHTML = '';
  waveBars = [];
  for (let i = 0; i < 68; i++) {
    const baseH = 24 + Math.abs(Math.sin(i * 0.38) * 54 + Math.cos(i * 0.9) * 18);
    const bar = document.createElement('div');
    bar.className = 'wave-pill flex-1 bg-zinc-700/60 rounded-full cursor-pointer';
    bar.style.height = baseH + '%';
    bar.dataset.base = baseH;
    bar.addEventListener('mouseenter', () => {
      SFX.wavePluck(i);
      for (let off = -3; off <= 3; off++) {
        const t = waveBars[i + off];
        if (!t) continue;
        const boost = Math.exp(-(off * off) / 2.2) * 65;
        t.style.height = Math.min(100, Number(t.dataset.base) + boost) + '%';
        t.style.backgroundColor = off === 0 ? '#34d399' : '#818cf8';
        setTimeout(() => { t.style.height = t.dataset.base + '%'; t.style.backgroundColor = ''; }, 240);
      }
    });
    bar.addEventListener('mousedown', () => seekTo((i / 68) * state.duration));
    box.appendChild(bar);
    waveBars.push(bar);
  }
}

/* ============================ 9. 造型台 + 调色台 ============================ */

function applyStylePreset(preset, idx) {
  SFX.microTick(880);
  $('preset-glider').style.transform = 'translateX(' + (idx * 100) + '%)';
  const names = ['B站硬核科普', 'Netflix 沉浸纪录片', 'Apple 极简发布会', '赛博霓虹发光'];
  $('preset-tag-label').innerText = '当前：' + names[idx];
  for (let i = 0; i < 4; i++) {
    $('preset-' + i).className = i === idx
      ? 'relative z-10 py-1.5 rounded-full text-white font-semibold transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white transition';
  }

  const map = {
    bili:    { zh: '#fcd34d', src: '#d4d4d8', blur: 12, bg: 72, stroke: 75 },
    netflix: { zh: '#ffffff', src: '#c9c9cf', blur: 0,  bg: 0,  stroke: 92 },
    apple:   { zh: '#f4f4f6', src: '#b8bcc6', blur: 20, bg: 55, stroke: 55 },
    cyber:   { zh: '#67e8f9', src: '#a78bfa', blur: 16, bg: 82, stroke: 88 }
  }[preset];

  setSubColor(map.zh, 'zh', true);
  setSubColor(map.src, 'src', true);
  changeBackdropBlur(map.blur);
  changeStrokeDepth(map.stroke);
  state.style.bgOpacity = map.bg;
  $('sub-backdrop').style.background = 'rgba(12, 12, 18, ' + (map.bg / 100) + ')';
  updateBgOpacityLabel();
}

function changeFontSize(val) {
  state.style.zhSize = Number(val);
  if ($('stage-zh')) $('stage-zh').style.fontSize = val + 'px';
  if ($('stage-zh-2')) $('stage-zh-2').style.fontSize = Math.max(12, Math.round(Number(val) * 0.88)) + 'px';
  if ($('font-size-val')) $('font-size-val').innerText = val + ' px';
  updateScaleDemo();
  SFX.microTick(600 + val * 20);
}

/* 渲染页里的「19px → 57px」演示，随字号/分辨率联动 */
function updateScaleDemo() {
  const { w, h } = renderTarget();
  const scale = Math.min(w, h) / 360;
  if ($('scale-demo')) $('scale-demo').innerText = state.style.zhSize;
  if ($('scale-demo-out')) $('scale-demo-out').innerText = Math.round(state.style.zhSize * scale);
}

function changeSrcFontSize(val) {
  state.style.srcSize = Number(val);
  if ($('stage-src')) $('stage-src').style.fontSize = val + 'px';
  if ($('stage-src-2')) $('stage-src-2').style.fontSize = Math.max(10, Math.round(Number(val) * 0.92)) + 'px';
  if ($('src-size-val')) $('src-size-val').innerText = val + ' px';
  SFX.microTick(500 + val * 25);
}

function changeBackdropBlur(val) {
  state.style.blur = Number(val);
  const box = $('sub-backdrop');
  if (box) {
    box.style.backdropFilter = 'blur(' + val + 'px)';
    box.style.webkitBackdropFilter = 'blur(' + val + 'px)';
  }
  if ($('blur-val')) $('blur-val').innerText = val + ' px';
  if ($('slider-blur')) $('slider-blur').value = val;
}

function changeStrokeDepth(val) {
  state.style.stroke = Number(val);
  const a = (val / 100).toFixed(2);
  const shadow =
    '0 3px 14px rgba(0,0,0,' + a + '), -1.2px -1.2px 0 rgba(0,0,0,' + a + '), 1.2px -1.2px 0 rgba(0,0,0,' + a + '), -1.2px 1.2px 0 rgba(0,0,0,' + a + '), 1.2px 1.2px 0 rgba(0,0,0,' + a + ')';
  if ($('stage-zh')) $('stage-zh').style.textShadow = shadow;
  if ($('stage-zh-2')) $('stage-zh-2').style.textShadow = shadow;
  if ($('stroke-val')) $('stroke-val').innerText = val == 0 ? '无描边' : val + '%';
}

function setFontFamily(type, idx) {
  SFX.microTick(750);
  state.style.font = type;
  $('font-glider').style.transform = 'translateX(' + (idx * 100) + '%)';
  $('floating-sub').style.fontFamily = type === 'mono'
    ? '"JetBrains Mono", "Microsoft YaHei", monospace'
    : '"Inter", "PingFang SC", sans-serif';
  for (let i = 0; i < 2; i++) {
    $('fnt-' + i).className = i === idx
      ? 'relative z-10 py-1 rounded-full text-white font-medium text-[11px] transition'
      : 'relative z-10 py-1 rounded-full text-zinc-400 hover:text-white text-[11px] transition';
  }
}

function setOrder(ord, idx) {
  SFX.microTick(540);
  state.style.order = ord;
  if ($('order-glider')) $('order-glider').style.transform = 'translateX(' + (idx * 100) + '%)';
  [['stage-zh', 'stage-src'], ['stage-zh-2', 'stage-src-2']].forEach(([zhId, srcId]) => {
    const zh = $(zhId), src = $(srcId);
    if (!zh || !src) return;
    if (ord === 'en_top') { src.style.order = 1; zh.style.order = 2; }
    else { zh.style.order = 1; src.style.order = 2; }
  });
  if ($('ord-zh')) $('ord-zh').className = ord === 'zh_top'
    ? 'relative z-10 py-1 rounded-full text-white font-medium text-[11px] transition'
    : 'relative z-10 py-1 rounded-full text-zinc-400 hover:text-white text-[11px] transition';
  if ($('ord-en')) $('ord-en').className = ord === 'en_top'
    ? 'relative z-10 py-1 rounded-full text-white font-medium text-[11px] transition'
    : 'relative z-10 py-1 rounded-full text-zinc-400 hover:text-white text-[11px] transition';

  // 关键：重渲染右侧双语字幕流卡片，让列表顺序跟着切换（否则看起来像「没刷新」）
  renderSubCards(true);
  renderTimelineCapsules();
}

/* ---------- 调色台 ---------- */

const COLOR_PRESETS = ['#fcd34d', '#ffffff', '#67e8f9', '#6ee7b7', '#f472b6', '#a78bfa', '#fca5a5', '#d4d4d8'];

function normalizeHex(v) {
  let s = String(v || '').trim().replace('#', '');
  if (s.length === 3) s = s.split('').map(c => c + c).join('');
  if (!/^[0-9a-fA-F]{6}$/.test(s)) return null;
  return '#' + s.toLowerCase();
}

function renderColorPresets() {
  [['preset-zh', 'zh'], ['preset-src', 'src']].forEach(([boxId, target]) => {
    const box = $(boxId);
    if (!box) return;
    box.innerHTML = '';
    COLOR_PRESETS.forEach(hex => {
      const dot = document.createElement('div');
      dot.className = 'swatch-dot';
      dot.style.background = hex;
      dot.title = hex.toUpperCase();
      dot.onclick = () => setSubColor(hex, target);
      box.appendChild(dot);
    });
  });
}

function setSubColor(hex, target, silent) {
  const norm = normalizeHex(hex);
  if (!norm) return;
  const isZh = target === 'zh';
  const el = isZh ? $('stage-zh') : $('stage-src');

  if (el) el.style.color = norm;
  if (isZh) {
    state.style.zhColor = norm;
  } else {
    state.style.srcColor = norm;
    if ($('stage-src-2')) $('stage-src-2').style.color = norm;
  }

  const pick = isZh ? $('pick-zh') : $('pick-src');
  const box = isZh ? $('hex-zh') : $('hex-src');
  const lbl = isZh ? $('hex-zh-label') : $('hex-src-label');
  if (box) box.value = norm.toUpperCase();
  if (pick) pick.value = norm;
  if (lbl) lbl.innerText = norm.toUpperCase();
  syncPlayheadTarget(state.currentTime);
  if (!silent) SFX.microTick(isZh ? 820 : 620);
}

function resetColors() {
  setSubColor('#fcd34d', 'zh');
  setSubColor('#d4d4d8', 'src');
  toast('已恢复默认配色');
}

function updateBgOpacityLabel() {
  const el = $('bg-op-val');
  if (el) el.innerText = Math.round(state.style.bgOpacity) + '%';
}

function startXYPadDrag(e) {
  const pad = $('xy-pad'), puck = $('xy-puck'), lx = $('xy-line-x'), ly = $('xy-line-y');
  let lastTick = 0;
  function move(ev) {
    const r = pad.getBoundingClientRect();
    const xPct = Math.max(0, Math.min(95, Math.round(((ev.clientX - r.left) / r.width) * 100)));
    const yPct = Math.max(3, Math.min(45, Math.round(((r.bottom - ev.clientY) / r.height) * 45)));
    puck.style.left = xPct + '%';
    puck.style.bottom = (yPct / 45) * 100 + '%';
    lx.style.left = xPct + '%';
    ly.style.bottom = (yPct / 45) * 100 + '%';
    state.style.bgOpacity = xPct;
    state.style.marginBottom = yPct;
    $('sub-backdrop').style.background = 'rgba(12, 12, 18, ' + (xPct / 100) + ')';
    $('floating-sub').style.bottom = yPct + '%';
    $('xy-readout').innerText = 'X: ' + xPct + '% 底框 · Y: ' + yPct + '% 边距';
    updateBgOpacityLabel();
    const now = performance.now();
    if (now - lastTick > 55) { SFX.microTick(600 + yPct * 15); lastTick = now; }
  }
  move(e);
  window.addEventListener('mousemove', move);
  window.addEventListener('mouseup', () => window.removeEventListener('mousemove', move), { once: true });
}

function startFreeSubDrag(e) {
  e.preventDefault();
  const stage = $('stage-box'), subEl = $('floating-sub');
  function move(ev) {
    const r = stage.getBoundingClientRect();
    const xPct = Math.max(15, Math.min(85, ((ev.clientX - r.left) / r.width) * 100));
    const yPct = Math.max(4, Math.min(75, ((r.bottom - ev.clientY) / r.height) * 100));
    subEl.style.left = (Math.abs(xPct - 50) < 4 ? 50 : xPct) + '%';
    subEl.style.bottom = yPct + '%';
    state.style.marginBottom = yPct;
  }
  window.addEventListener('mousemove', move);
  window.addEventListener('mouseup', () => window.removeEventListener('mousemove', move), { once: true });
}

/* ============================ 10. 偏好设置 ============================ */

const PREF_PAGES = ['general', 'engine', 'render', 'audio', 'shortcuts', 'about'];

function switchPrefTab(pageKey, idx) {
  SFX.microTick(900);
  $('pref-nav-glider').style.transform = 'translateY(' + (idx * 44) + 'px)';
  for (let i = 0; i < PREF_PAGES.length; i++) {
    $('pnav-' + i).className = i === idx
      ? 'relative z-10 w-full h-10 px-3.5 rounded-2xl text-white font-semibold flex items-center space-x-2.5 transition'
      : 'relative z-10 w-full h-10 px-3.5 rounded-2xl text-zinc-400 hover:text-zinc-200 font-medium flex items-center space-x-2.5 transition';
  }
  PREF_PAGES.forEach(k => {
    const el = $('pref-page-' + k);
    if (!el) return;
    if (k === pageKey) { el.classList.remove('panel-hidden'); el.classList.add('panel-active'); }
    else { el.classList.remove('panel-active'); el.classList.add('panel-hidden'); }
  });
}

let _cfgSaveTimer = null;
function _saveEngineSettingsDebounced() {
  if (!state.native) return;
  clearTimeout(_cfgSaveTimer);
  _cfgSaveTimer = setTimeout(() => {
    Bridge.saveSettings({
      temperature: state.engine.temp,
      batch_size: state.engine.batch,
      context_size: state.engine.context
    }).catch(() => {});
  }, 350);
}

function uiCfg(key, val, el) {
  if (key === 'temp') {
    state.engine.temp = Number(val);
    if ($('temp-val')) $('temp-val').innerText = Number(val).toFixed(2);
    _saveEngineSettingsDebounced();
  }
  if (key === 'batch') {
    state.engine.batch = Number(val);
    if ($('batch-val')) $('batch-val').innerText = val + ' 条';
    _saveEngineSettingsDebounced();
  }
  if (key === 'context') {
    state.engine.context = Number(val);
    if ($('ctx-val')) $('ctx-val').innerText = val + ' 条';
    _saveEngineSettingsDebounced();
  }
  if (key === 'fps') { state.render.fps = val; }
  if (el) SFX.microTick(700);
}

function toggleSfxChannel(ch, btn) {
  const map = { btn: 'btnEnabled', wave: 'waveEnabled', tick: 'tickEnabled' };
  const prop = map[ch];
  if (!prop) return;
  const next = !SFX[prop];
  SFX[prop] = next;
  if (btn) {
    btn.dataset.on = next ? '1' : '0';
    btn.textContent = next ? '开' : '关';
    btn.className = next
      ? 'px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300 text-[10px]'
      : 'px-2 py-0.5 rounded-full bg-white/[0.08] text-zinc-400 text-[10px]';
  }
  if (next) SFX.actionPop(680);
}

function syncBurnSubUI() {
  const on = state.render.burnSub !== false;
  const prefBtn = $('pref-burn-sub-btn');
  if (prefBtn) {
    prefBtn.dataset.on = on ? '1' : '0';
    prefBtn.textContent = on ? '已开启' : '已关闭';
    prefBtn.className = on
      ? 'px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-300 text-[11px] font-medium'
      : 'px-3 py-1 rounded-full bg-white/[0.08] text-zinc-400 text-[11px] font-medium';
  }
  const expBtn = $('export-burn-btn');
  const expDesc = $('export-burn-desc');
  const expTag = $('export-burn-tag');
  if (expBtn) expBtn.classList.toggle('opacity-50', !on);
  if (expDesc) expDesc.innerText = on
    ? '自动匹配本机最佳编码器 (NVENC / libx264)'
    : '已在「偏好设置 → 渲染清晰度」关闭硬压，当前仅导出 SRT/ASS';
  if (expTag) expTag.innerText = on ? '启动' : '已关闭';
}

function toggleBurnSub(btn) {
  state.render.burnSub = !(state.render.burnSub !== false);
  syncBurnSubUI();
  SFX.microTick(state.render.burnSub ? 760 : 480);
  if (state.native) {
    Bridge.saveSettings({ burn_sub: state.render.burnSub }).catch(() => {});
  }
  toast(state.render.burnSub ? '已开启导出时硬压字幕' : '已关闭硬压：仅导出 SRT / ASS 字幕轨');
}

function setExportNameTpl(val) {
  state.render.nameTpl = String(val || '').trim() || '{title}_{lang}_{res}';
  if (state.native) {
    Bridge.saveSettings({ export_name_tpl: state.render.nameTpl }).catch(() => {});
  }
  toast('命名模板已更新：' + state.render.nameTpl);
}

function checkUpdate() {
  const el = $('update-status');
  if (el) el.innerText = '当前离线版本 V9.2.0（本机版暂未接入自动更新服务器，检查于 ' + new Date().toLocaleTimeString() + '）';
  SFX.actionPop(620);
  toast('当前版本 V9.2.0（暂未接入在线自动更新服务）');
}

function togglePrefFlag(key, btn) {
  const cur = Boolean(state.prefs[key]);
  const next = !cur;
  state.prefs[key] = next;
  if (btn) {
    btn.dataset.on = next ? '1' : '0';
    btn.textContent = next ? '已开启' : '已关闭';
    btn.className = next
      ? 'px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-300 text-[11px] font-medium'
      : 'px-3 py-1 rounded-full bg-white/[0.08] text-zinc-400 text-[11px] font-medium';
  }
  SFX.microTick(next ? 760 : 480);
  if (state.native) {
    Bridge.saveSettings({
      auto_open_export: state.prefs.autoOpenExport,
      completion_sound: state.prefs.completionSound
    }).catch(() => {});
  }
}

async function pickExportDir() {
  SFX.actionPop(580);
  if (!state.native) {
    toast('（浏览器预览模式）导出将使用浏览器默认下载目录');
    return;
  }
  const r = await Bridge.pickExportDir();
  if (r && r.status === 'success' && r.export_dir) {
    state.prefs.exportDir = r.export_dir;
    if ($('export-dir-input')) $('export-dir-input').value = r.export_dir;
    toast('已设置导出目录：' + r.export_dir);
  } else if (r && r.status !== 'cancel') {
    toast((r && r.message) || '未更改导出目录');
  }
}

function setTheme(mode, idx, btn) {
  SFX.microTick(700);
  const glider = $('theme-glider');
  if (glider) glider.style.transform = 'translateX(' + (idx * 100) + '%)';
  const btns = btn && btn.parentElement ? btn.parentElement.querySelectorAll('button') : [];
  btns.forEach((b, i) => {
    b.className = i === idx
      ? 'relative z-10 py-1.5 rounded-full text-white font-semibold transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white transition';
  });
  const sysLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
  const isLight = mode === 'light' || (mode === 'system' && sysLight);
  document.documentElement.classList.toggle('light', isLight);
  toast(mode === 'system' ? '主题跟随系统' : (isLight ? '已切换亮色主题' : '已切换暗色主题'));
}

function setUiZoom(pct, idx) {
  SFX.microTick(720);
  $('zoom-glider').style.transform = 'translateX(' + (idx * 100) + '%)';
  for (let i = 0; i < 3; i++) {
    $('zoom-' + i).className = i === idx
      ? 'relative z-10 py-1.5 rounded-full text-white font-semibold text-[11px] transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white text-[11px] transition';
  }
  document.body.style.zoom = pct / 100;
  toast('界面缩放：' + pct + '%');
}

function toggleReduceMotion(btn) {
  const on = document.body.classList.toggle('reduce-motion');
  btn.textContent = on ? '已开启' : '已关闭';
  btn.className = on
    ? 'px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-300 text-[11px] font-medium'
    : 'px-3 py-1 rounded-full bg-white/[0.08] text-zinc-300 text-[11px] font-medium';
  toast(on ? '已开启减少动效（低配机更顺）' : '已关闭减少动效');
}

function syncComputeModeUI(mode) {
  const idx = mode === 'cuda' ? 1 : (mode === 'cpu' ? 2 : 0);
  if ($('compute-glider')) $('compute-glider').style.transform = 'translateX(' + (idx * 100) + '%)';
  for (let i = 0; i < 3; i++) {
    const b = $('cmp-' + i);
    if (!b) continue;
    b.className = i === idx
      ? 'relative z-10 py-1.5 rounded-full text-white font-semibold transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white transition';
  }
}

async function switchComputeMode(mode, idx) {
  SFX.actionPop(560);
  state.computeMode = mode;
  syncComputeModeUI(mode);
  if (state.native) {
    const r = await Bridge.setComputeMode(mode);
    if (r && r.status === 'success') {
      const accel = r.device === 'cuda';
      const pill = $('hw-top-pill');
      if (pill) {
        pill.innerText = accel ? ('CUDA ' + String(r.compute_type || 'FP16').toUpperCase()) : 'CPU INT8';
        pill.className = 'text-[10px] font-mono-num px-2 py-0.5 rounded-full ' +
          (accel ? 'text-emerald-300 bg-emerald-500/15' : 'text-amber-300 bg-amber-500/15');
      }
      toast(r.message || '计算设备已更新');
      return;
    } else if (r && r.message) {
      showError(r.message);
      return;
    }
  }
  if ($('hw-top-pill')) $('hw-top-pill').innerText = ['GPU 加速', 'CUDA FP16', 'CPU INT8'][idx];
}

function _whisperHintText(tier, idx) {
  const base = [
    '精度优先 · 完整模型约 1.5GB（推荐独显）',
    '速度与精度均衡 · 完整模型约 1.4GB',
    '轻量快速 · 完整模型约 461MB，识别响应极快'
  ][idx];
  const info = state.modelStatus && state.modelStatus.models && state.modelStatus.models[tier];
  if (!info) return base;
  if (info.cached || info.complete) return '✅ 本地已缓存完整模型（' + info.size_mb + ' MB）· ' + base;
  if (info.corrupt) return '⚠️ 本地残留不完整缓存（首次识别将自动重下）· ' + base;
  return '⬇️ 本地未缓存（首次识别需联网下载）· ' + base;
}

function switchWhisperTier(tier, idx, explicit) {
  SFX.actionPop(580);
  state.model = tier;
  if (explicit !== false) state.modelExplicit = true;
  syncWhisperTierUI();
  if (state.native && explicit !== false) {
    Bridge.saveSettings({ whisper_model: tier }).catch(() => {});
  }
}

/* 启动时同步默认模型与设置页选中态，避免内部已用 large-v3-turbo、界面却仍高亮 small。 */
function syncWhisperTierUI() {
  const idx = state.model === 'small' ? 2 : (state.model === 'medium' ? 1 : 0);
  const glider = $('whisper-glider');
  if (glider) glider.style.transform = 'translateX(' + (idx * 100) + '%)';
  for (let i = 0; i < 3; i++) {
    const btn = $('wsp-' + i);
    if (!btn) continue;
    btn.className = i === idx
      ? 'relative z-10 py-1.5 rounded-full text-white font-semibold transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white transition';
  }
  const hint = $('whisper-vram-hint');
  if (hint) hint.innerText = _whisperHintText(state.model, idx);
}

function changeMasterVolume(val) {
  SFX.masterVol = Number(val) / 100;
  $('master-vol-lbl').innerText = val + '%';
  SFX.actionPop(560);
}

function changeMicroVolume(val) {
  SFX.microVol = Number(val) / 100;
  const v = Number(val);
  $('micro-vol-lbl').innerText = v === 0 ? '已静音' : (v <= 25 ? val + '% (柔音)' : val + '%');
  $('top-audio-label').innerText = v === 0 ? '微声效: 静音' : '微声效: ' + val + '%';
  SFX.wavePluck(4);
}

function setSoundProfile(key, idx) {
  SFX.profile = key;
  $('sound-glider').style.transform = 'translateX(' + (idx * 100) + '%)';
  for (let i = 0; i < 5; i++) {
    const b = $('sp-' + i);
    b.firstElementChild.className = i === idx ? 'font-semibold text-white' : 'font-semibold text-zinc-300';
  }
  SFX.actionPop(560);
  setTimeout(() => SFX.stringPluck(3), 90);
}

function changeMagneticPull(val) {
  state.magneticStrength = Number(val) / 100;
  $('mag-val-label').innerText = val == 0 ? '关闭' : val + '%';
}

function setAppAccent(hex, glow, name) {
  SFX.actionPop(600);
  document.documentElement.style.setProperty('--accent', hex);
  document.documentElement.style.setProperty('--accent-glow', glow);
  toast('已切换主题光晕：' + name);
}

async function refreshWorkspaceCacheInfo() {
  const lbl = $('cache-size-label');
  if (!lbl) return;
  if (!state.native) {
    lbl.innerText = '当前占用空间：浏览器预览模式无本地缓存';
    return;
  }
  try {
    const info = await Bridge.getWorkspaceCacheInfo();
    if (info && info.status === 'success') {
      lbl.innerText = '当前占用空间：' + Number(info.mb || 0).toFixed(1) + ' MB（' + (info.count || 0) + ' 个临时文件）';
    }
  } catch (e) { /* noop */ }
}

async function clearWorkspaceCache() {
  SFX.chord();
  if (!state.native) {
    if ($('cache-size-label')) $('cache-size-label').innerText = '当前占用空间：0.0 MB';
    toast('浏览器预览模式无本地缓存');
    return;
  }
  const r = await Bridge.clearWorkspaceCache();
  if (r && r.status === 'success') {
    toast(r.message || ('已清理 ' + Number(r.freed_mb || 0).toFixed(1) + ' MB 临时缓存'));
    await refreshWorkspaceCacheInfo();
  } else {
    showError((r && r.message) || '清理缓存失败');
  }
}

function detectLocalHardware() {
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
    if (gl) {
      const dbg = gl.getExtension('WEBGL_debug_renderer_info');
      if (dbg) {
        const renderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) || '';
        const m = renderer.match(/(NVIDIA\s+GeForce\s+[A-Za-z0-9\s]+|AMD\s+Radeon\s+[A-Za-z0-9\s]+|Intel\(R\)\s+[A-Za-z0-9\s]+|Apple\s+M\d[A-Za-z\s]*)/i);
        if (m && m[1]) {
          $('detected-gpu-label').innerText = '已识别本机硬件: ' + m[1].trim();
          return;
        }
      }
    }
  } catch (err) { /* ignore */ }
  const el = $('detected-gpu-label');
  if (el) el.innerText = '已启用本地 GPU / CPU 自适应加速';
}

/* ---------- 渲染清晰度 ---------- */

function updateRenderBadges() {
  const { w, h } = renderTarget();
  const txt = w + ' × ' + h;
  if ($('res-readout')) $('res-readout').innerText = txt;
  if ($('export-res-badge')) $('export-res-badge').innerText = txt + ' · CRF ' + state.render.crf;
  if ($('crf-val')) $('crf-val').innerText = 'CRF ' + state.render.crf;
  updateScaleDemo();
}

function setResolution(key, idx) {
  SFX.microTick(760);
  state.render.res = key;
  for (let i = 0; i <= 4; i++) {
    const b = $('res-' + i);
    if (!b) continue;
    b.className = i === idx
      ? 'relative z-10 py-1.5 rounded-full bg-indigo-500 text-white font-semibold transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white transition';
  }
  if (key === 'original') {
    const s = sourceSize();
    $('res-w').value = s.w;
    $('res-h').value = s.h;
  } else if (key !== 'custom') {
    const p = RES_MAP[key];
    state.render.customW = p[0];
    state.render.customH = p[1];
    $('res-w').value = p[0];
    $('res-h').value = p[1];
  }
  updateRenderBadges();
}

function setCustomSize(which, val) {
  const n = Math.max(1, Number(val) || 1);
  if (which === 'w') state.render.customW = n; else state.render.customH = n;
  state.render.res = 'custom';
  for (let i = 0; i <= 4; i++) {
    const b = $('res-' + i);
    if (b) b.className = 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white transition';
  }
  updateRenderBadges();
}

function setCrf(val) {
  state.render.crf = Number(val);
  updateRenderBadges();
}

function setEncoder(key, idx) {
  SFX.microTick(700);
  state.render.encoder = key;
  $('enc-glider').style.transform = 'translateX(' + (idx * 100) + '%)';
  for (let i = 0; i < 2; i++) {
    $('enc-' + i).className = i === idx
      ? 'relative z-10 py-1.5 rounded-full text-white font-semibold text-[11px] transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white text-[11px] transition';
  }
}

function setAudioBitrate(key, idx) {
  SFX.microTick(700);
  state.render.audioBitrate = key;
  $('abr-glider').style.transform = 'translateX(' + (idx * 100) + '%)';
  for (let i = 0; i < 3; i++) {
    $('abr-' + i).className = i === idx
      ? 'relative z-10 py-1.5 rounded-full text-white font-semibold text-[11px] transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white text-[11px] transition';
  }
}

/* ============================ 11. 舞台 Canvas ============================ */

const canvas = $('interactive-canvas');
const mouse = { x: 380, y: 214, prevY: 214, vx: 0, vy: 0, active: false };
let particles = [], shockwaves = [];
let physStrings = [
  { yOffset: 0, vel: 0, color: '#818cf8', noteIdx: 0 },
  { yOffset: 0, vel: 0, color: '#34d399', noteIdx: 2 },
  { yOffset: 0, vel: 0, color: '#fbbf24', noteIdx: 4 },
  { yOffset: 0, vel: 0, color: '#f472b6', noteIdx: 6 },
  { yOffset: 0, vel: 0, color: '#38bdf8', noteIdx: 8 }
];

function initPhysicsPlayground() {
  if (!canvas) return;
  for (let i = 0; i < 80; i++) {
    particles.push({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 1.1,
      vy: (Math.random() - 0.5) * 1.1,
      r: 1.6 + Math.random() * 2.2,
      hue: 225 + Math.random() * 55
    });
  }

  canvas.addEventListener('mousemove', (e) => {
    const r = canvas.getBoundingClientRect();
    const nx = (e.clientX - r.left) * (canvas.width / r.width);
    const ny = (e.clientY - r.top) * (canvas.height / r.height);
    mouse.prevY = mouse.y;
    mouse.vx = nx - mouse.x;
    mouse.vy = ny - mouse.y;
    mouse.x = nx; mouse.y = ny; mouse.active = true;

    if (state.visMode !== 'strings') return;
    const h = canvas.height;
    physStrings.forEach((st, idx) => {
      const baseY = (h / 6) * (idx + 1);
      const crossed = (mouse.prevY <= baseY && ny >= baseY) || (mouse.prevY >= baseY && ny <= baseY);
      const near = Math.abs(ny - baseY) < 14 && Math.abs(mouse.vy) > 1.2;
      if (crossed || near) {
        st.yOffset = Math.max(-52, Math.min(52, (ny - baseY) * 2.4 + mouse.vy * 2.8)) || (mouse.vy >= 0 ? 28 : -28);
        st.vel = mouse.vy * 4;
        const now = performance.now();
        if (!st.lastSound || now - st.lastSound > 75) { SFX.stringPluck(st.noteIdx); st.lastSound = now; }
      }
    });
  });

  canvas.addEventListener('mouseleave', () => { mouse.active = false; });

  canvas.addEventListener('mousedown', (e) => {
    const r = canvas.getBoundingClientRect();
    const cx = (e.clientX - r.left) * (canvas.width / r.width);
    const cy = (e.clientY - r.top) * (canvas.height / r.height);
    shockwaves.push({ x: cx, y: cy, r: 6, alpha: 1 });

    if (state.visMode === 'strings') {
      physStrings.forEach((st, i) => {
        st.yOffset = (i % 2 === 0 ? 35 : -35);
        setTimeout(() => SFX.stringPluck(st.noteIdx), i * 35);
      });
    } else {
      SFX.microTick(620);
      particles.forEach(p => {
        const dx = p.x - cx, dy = p.y - cy;
        const dist = Math.hypot(dx, dy) || 1;
        if (dist < 180) {
          const force = (180 - dist) / 14;
          p.vx += (dx / dist) * force;
          p.vy += (dy / dist) * force;
        }
      });
    }
  });

  let last = performance.now();
  function loop(now) {
    const dt = (now - last) / 1000;
    last = now;

    // 拖拽分屏期间：暂停画布渲染 + 指针 DOM 写入。
    // 这是掉帧主因——每帧改 footer 里的 playhead 会强制重算整条 blur 背景，
    // 叠加 760×428 画布 60fps 重绘，再叠加两舱 resize，主线程直接打满。
    const dragging = document.body.classList.contains('is-dragging');

    if (!dragging) {
      if (state.usingRealVideo && !state.videoFailed) {
        const rv = $('real-video');
        // 若 requestVideoFrameCallback 已激活，由其按视频真实帧率单源驱动，不在 60/144Hz rAF 中重复抢占
        if (_vfcId == null && rv && !isNaN(rv.currentTime) && Math.abs(rv.currentTime - state.currentTime) > 0.001) {
          state.currentTime = rv.currentTime;
          syncPlayheadTarget(state.currentTime);
        }
      } else if (state.isPlaying) {
        // 单一时钟源驱动无视频 / 视频不可播放时的模拟播放，避免 rAF + setInterval 双计时导致 2 倍速
        state.currentTime += Math.min(dt, 0.25);
        if (state.currentTime >= state.duration) {
          state.currentTime = state.duration;
          state.isPlaying = false;
          updatePlayButtonUI();
        }
        syncPlayheadTarget(state.currentTime);
      }

      const target = Math.max(2.2, Math.min(97.8, (state.currentTime / Math.max(0.1, state.duration)) * 100));
      if (state.usingRealVideo && state.isPlaying) {
        playheadX = target;
        playheadVel = 0;
      } else {
        const diff = target - playheadX;
        playheadVel = playheadVel * 0.72 + diff * 0.24;
        playheadX += playheadVel;
      }

      // 视频播放期间节流底栏（含 28px backdrop-filter）的 DOM layout 写入，避免每帧重算整条毛玻璃导致视频掉帧
      const shouldPaintFooter = !state.isPlaying || (now - lastFooterPaint >= 60);
      if (shouldPaintFooter) {
        lastFooterPaint = now;
        if (Math.abs(playheadX - lastPlayheadX) > 0.06) {
          lastPlayheadX = playheadX;
          const ph = $('fluid-playhead'), needle = $('playhead-needle');
          if (ph) {
            ph.style.left = playheadX.toFixed(2) + '%';
            if (needle) {
              const tilt = Math.max(-14, Math.min(14, -playheadVel * 7.5));
              needle.style.transform = 'rotate(' + tilt.toFixed(1) + 'deg)';
            }
          }
        }

        const ts = state.currentTime.toFixed(2);
        if (ts !== lastBadgeTs) {
          lastBadgeTs = ts;
          const badge = $('playhead-badge');
          if (badge) badge.innerText = ts + 's';
        }
      }

      if (!state.usingRealVideo && canvas) renderPlayground(canvas, now / 1000);
    }
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);
}

let playheadX = 22.0, playheadVel = 0, lastPlayheadX = -1, lastBadgeTs = '', lastFooterPaint = 0;

function renderPlayground(cv, t) {
  const ctx = cv.getContext('2d');
  const w = cv.width, h = cv.height;
  ctx.fillStyle = '#040406';
  ctx.fillRect(0, 0, w, h);

  for (let i = shockwaves.length - 1; i >= 0; i--) {
    const sw = shockwaves[i];
    sw.r += 6; sw.alpha -= 0.024;
    if (sw.alpha <= 0) { shockwaves.splice(i, 1); continue; }
    ctx.strokeStyle = 'rgba(129, 140, 248, ' + sw.alpha + ')';
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(sw.x, sw.y, sw.r, 0, Math.PI * 2); ctx.stroke();
  }

  if (state.visMode === 'particles') {
    particles.forEach((p, i) => {
      if (mouse.active) {
        const dx = mouse.x - p.x, dy = mouse.y - p.y;
        const d = Math.hypot(dx, dy) || 1;
        if (d < 130) { p.vx -= (dx / d) * 0.45; p.vy -= (dy / d) * 0.45; }
      }
      p.x += p.vx; p.y += p.vy;
      p.vx *= 0.985; p.vy *= 0.985;
      if (Math.hypot(p.vx, p.vy) < 0.35) {
        p.vx += (Math.random() - 0.5) * 0.14;
        p.vy += (Math.random() - 0.5) * 0.14;
      }
      if (p.x < 0) p.x = w; if (p.x > w) p.x = 0;
      if (p.y < 0) p.y = h; if (p.y > h) p.y = 0;

      ctx.fillStyle = 'hsla(' + p.hue + ', 85%, 72%, 0.85)';
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();

      for (let j = i + 1; j < particles.length; j++) {
        const p2 = particles[j];
        const dist = Math.hypot(p.x - p2.x, p.y - p2.y);
        if (dist < 85) {
          ctx.strokeStyle = 'rgba(99, 102, 241, ' + ((1 - dist / 85) * 0.25) + ')';
          ctx.lineWidth = 1;
          ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(p2.x, p2.y); ctx.stroke();
        }
      }
    });
  } else if (state.visMode === 'strings') {
    physStrings.forEach((st, idx) => {
      const baseY = (h / 6) * (idx + 1);
      st.vel = (st.vel + (-0.28 * st.yOffset)) * 0.86;
      st.yOffset += st.vel;

      const wave = state.isPlaying ? Math.sin(t * 8 + idx) * 6 : 0;
      const cx = mouse.active ? mouse.x : w / 2;
      const cy = baseY + (st.yOffset + wave) * 1.8;

      ctx.save();
      ctx.strokeStyle = st.color;
      ctx.lineWidth = Math.abs(st.yOffset) > 3 ? 3.2 : 2.2;
      if (Math.abs(st.yOffset) > 3) { ctx.shadowColor = st.color; ctx.shadowBlur = 14; }
      ctx.beginPath();
      ctx.moveTo(0, baseY);
      ctx.quadraticCurveTo(cx, cy, w, baseY);
      ctx.stroke();
      ctx.restore();
    });
  } else {
    const speed = t * 145;
    const hiBase = h * 0.36, loBase = h * 0.52;
    const probe = (x) => {
      if (!mouse.active) return 0;
      const dist = Math.abs(x - mouse.x);
      if (dist > 95) return 0;
      return (mouse.y - h * 0.44) * 0.48 * Math.cos((dist / 95) * (Math.PI / 2));
    };

    ctx.lineWidth = 2.4;
    ctx.strokeStyle = '#818cf8';
    ctx.beginPath();
    for (let x = 0; x < w; x += 3) {
      const bit = Math.sin((x + speed) * 0.05) > 0.1 ? -34 : 0;
      const y = hiBase + bit + probe(x);
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

    ctx.strokeStyle = '#34d399';
    ctx.beginPath();
    for (let x = 0; x < w; x += 3) {
      const bit = Math.sin((x + speed) * 0.05) > 0.1 ? 34 : 0;
      const y = loBase + bit - probe(x);
      x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.stroke();

    if (mouse.active) {
      const bitAt = Math.sin((mouse.x + speed) * 0.05) > 0.1 ? -34 : 0;
      const pv = probe(mouse.x);
      const yH = hiBase + bitAt + pv;
      const yL = loBase - bitAt - pv;

      ctx.strokeStyle = 'rgba(251, 191, 36, 0.75)';
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(mouse.x, yH); ctx.lineTo(mouse.x, yL); ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = '#818cf8'; ctx.beginPath(); ctx.arc(mouse.x, yH, 4.5, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#34d399'; ctx.beginPath(); ctx.arc(mouse.x, yL, 4.5, 0, Math.PI * 2); ctx.fill();

      const dv = Math.abs((yL - yH) / 38).toFixed(2);
      ctx.fillStyle = '#fcd34d';
      ctx.font = '11px "JetBrains Mono", monospace';
      ctx.fillText('ΔV = ' + dv + 'V (' + (dv > 1.2 ? 'Dominant' : 'Recessive') + ')',
        Math.min(w - 175, mouse.x + 10), (yH + yL) / 2 + 4);
    }
  }
}

function triggerPulseWave() {
  SFX.actionPop(620);
  if (canvas) shockwaves.push({ x: canvas.width / 2, y: canvas.height / 2, r: 6, alpha: 1 });
}

/* ============================ 12. 导出 ============================ */

function buildAssText() {
  const { w, h } = renderTarget();
  const st = state.style;
  const scale = Math.min(w, h) / 360;              // 与后端 SubtitleRenderer 完全同一条公式
  const zhSize = Math.max(8, st.zhSize * scale);
  const enSize = Math.max(8, st.srcSize * scale);
  const mb = h * (st.marginBottom / 100);

  const boxed = st.bgOpacity > 0.5;
  const borderStyle = boxed ? 3 : 1;
  const zhOutline = Math.max(1, Math.round(zhSize * (boxed ? 0.16 : 0.055) * 10) / 10);
  const enOutline = Math.max(1, Math.round(enSize * (boxed ? 0.16 : 0.055) * 10) / 10);
  const shadow = boxed ? 0 : 1;

  const zhMv = st.order === 'zh_top' ? mb + enSize * 1.15 : mb;
  const enMv = st.order === 'zh_top' ? mb : mb + zhSize * 1.15;
  const rowStep = (zhSize + enSize) * 1.25;

  const back = boxed ? hexToAss('#000000', st.bgOpacity) : '&HFF000000';
  const outlineCol = '&H00000000';

  const head = [
    '[Script Info]',
    '; Generated by SubFlow Studio V9.2',
    'ScriptType: v4.00+',
    'PlayResX: ' + w,
    'PlayResY: ' + h,
    'WrapStyle: 2',
    'ScaledBorderAndShadow: yes',
    'YCbCr Matrix: TV.709',
    '',
    '[V4+ Styles]',
    'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
    'Style: ZH,Microsoft YaHei,' + zhSize.toFixed(1) + ',' + hexToAss(st.zhColor) + ',&H000000FF,' + outlineCol + ',' + back + ',0,0,0,0,100,100,0,0,' + borderStyle + ',' + zhOutline + ',' + shadow + ',2,40,40,' + Math.round(zhMv) + ',1',
    'Style: ZH_B,Microsoft YaHei,' + zhSize.toFixed(1) + ',' + hexToAss(st.speakerBColor || '#7dd3fc') + ',&H000000FF,' + outlineCol + ',' + back + ',0,0,0,0,100,100,0,0,' + borderStyle + ',' + zhOutline + ',' + shadow + ',2,40,40,' + Math.round(zhMv) + ',1',
    'Style: EN,Arial,' + enSize.toFixed(1) + ',' + hexToAss(st.srcColor) + ',&H000000FF,' + outlineCol + ',' + back + ',0,0,0,0,100,100,0,0,' + borderStyle + ',' + enOutline + ',' + shadow + ',2,40,40,' + Math.round(enMv) + ',1',
    '',
    '[Events]',
    'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text'
  ];

  const esc = (s) => String(s).replace(/\r?\n/g, ' ').replace(/\{/g, '｛').replace(/\}/g, '｝');

  const lines = [];
  const laneEnd = [-1, -1];
  const laneSpk = [-1, -1];
  state.subs.forEach(s => {
    const startSec = Number(s.start) || 0;
    const endSec = Math.max(Number(s.end) || 0, startSec + 0.08);
    const a = toAssTime(startSec), b = toAssTime(endSec);
    const zh = esc(s.zh), en = esc(s.src);
    const spk = Number(s.speaker) === 1 ? 1 : 0;
    const zhSt = spk === 1 ? 'ZH_B' : 'ZH';
    const lane = (startSec < laneEnd[0] - 0.02 && laneSpk[0] !== spk) ? 1 : 0;
    laneEnd[lane] = Math.max(laneEnd[lane], endSec);
    laneSpk[lane] = spk;
    const shift = lane === 1 ? rowStep : 0;
    const first = st.order === 'zh_top' ? { st: zhSt, mv: Math.round(zhMv + shift), tx: zh } : { st: 'EN', mv: Math.round(enMv + shift), tx: en };
    const second = st.order === 'zh_top' ? { st: 'EN', mv: Math.round(enMv + shift), tx: en } : { st: zhSt, mv: Math.round(zhMv + shift), tx: zh };
    if (first.tx) lines.push('Dialogue: 0,' + a + ',' + b + ',' + first.st + ',,0,0,' + first.mv + ',,' + first.tx);
    if (second.tx) lines.push('Dialogue: 0,' + a + ',' + b + ',' + second.st + ',,0,0,' + second.mv + ',,' + second.tx);
  });

  return head.join('\n') + '\n' + lines.join('\n') + '\n';
}

function buildSrtText() {
  let out = '';
  const enTop = state.style.order === 'en_top';
  state.subs.forEach((s, i) => {
    out += (i + 1) + '\n';
    out += toSrtTime(s.start) + ' --> ' + toSrtTime(Math.max(s.end, s.start + 0.08)) + '\n';
    const first = enTop ? (s.src || '') : (s.zh || '');
    const second = enTop ? (s.zh || '') : (s.src || '');
    if (first) out += first + '\n';
    if (second && second !== first) out += second + '\n';
    out += '\n';
  });
  return out;
}

function _sanitizeFileNamePart(s, fallback) {
  const clean = String(s || '').replace(/[\\/:*?"<>|\r\n]+/g, '_').replace(/\s+/g, ' ').trim();
  return clean ? clean.slice(0, 80) : (fallback || 'SubFlow');
}

function buildExportFilename(ext) {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const dateStr = now.getFullYear() + pad(now.getMonth() + 1) + pad(now.getDate());
  const timeStr = pad(now.getHours()) + pad(now.getMinutes()) + pad(now.getSeconds());
  const rawTitle = state.videoTitle || (state.videoPath ? String(state.videoPath).split(/[\\/]/).pop().replace(/\.[^.]+$/, '') : 'SubFlow');
  const title = _sanitizeFileNamePart(rawTitle, 'SubFlow');
  const lang = _sanitizeFileNamePart(state.targetLang || 'zh', 'zh');
  const { h } = renderTarget();
  const res = String(h || 1080) + 'p';
  const tpl = String(state.render.nameTpl || '{title}_{lang}_{res}').trim() || '{title}_{lang}_{res}';
  let base = tpl
    .replace(/\{title\}/gi, title)
    .replace(/\{lang\}/gi, lang)
    .replace(/\{res\}/gi, res)
    .replace(/\{date\}/gi, dateStr)
    .replace(/\{time\}/gi, timeStr);
  base = _sanitizeFileNamePart(base, 'SubFlow_' + dateStr + '_' + timeStr);
  return base + '.' + ext;
}

function downloadFile(ext) {
  if (state.prefs.completionSound) SFX.chord();
  closeModal('export-modal');

  const fileName = buildExportFilename(ext);

  // 原生环境：走后端真实导出（与 core_pipeline 完全一致），优先写到用户选定导出目录或 workspace
  if (state.native) {
    const outPath = state.prefs.exportDir
      ? (state.prefs.exportDir.replace(/[\\/]+$/, '') + '/' + fileName)
      : ('workspace/' + fileName);
    (async () => {
      if (ext === 'srt') {
        const r = await Bridge.exportSrt(state.subs, outPath, state.style.order);
        if (r && r.status === 'success') {
          toast('已导出 SRT → ' + r.srt_path);
          if (state.prefs.autoOpenExport) Bridge.openFolder(r.srt_path);
        } else showError((r && r.message) || '导出 SRT 失败');
      } else {
        const { w, h } = renderTarget();
        const r = await Bridge.exportAss(state.subs, outPath, Bridge.styleConfig(), w, h);
        if (r && r.status === 'success') {
          toast('已导出 ASS → ' + r.ass_path);
          if (state.prefs.autoOpenExport) Bridge.openFolder(r.ass_path);
        } else showError((r && r.message) || '导出 ASS 失败');
      }
    })();
    return;
  }

  // 浏览器预览：直接下载
  const text = ext === 'ass' ? buildAssText() : buildSrtText();
  const blob = new Blob(['\ufeff' + text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = fileName;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  const { w, h } = renderTarget();
  toast(ext === 'ass' ? ('已导出 .ASS（' + w + '×' + h + ' PlayRes）') : '已导出双语 .SRT');
}

function triggerNvencBurn() {
  SFX.actionPop(660);
  closeModal('export-modal');

  if (state.render.burnSub === false) {
    toast('已在「偏好设置 → 渲染清晰度」关闭硬压烧录；如需压制请先开启该开关，或直接导出 SRT/ASS。');
    return;
  }

  if (!state.native) {
    const { w, h } = renderTarget();
    toast('（浏览器预览模式）无法压制，请在桌面应用中操作 · ' + w + '×' + h);
    return;
  }

  // 需要一个源视频的本地绝对路径
  if (!state.videoPath) {
    showError('请先「导入本地视频」，或「提取并翻译」一个视频后，再压制字幕。');
    return;
  }

  const fileName = buildExportFilename('mp4');
  const outPath = state.prefs.exportDir
    ? (state.prefs.exportDir.replace(/[\\/]+$/, '') + '/' + fileName)
    : ('workspace/' + fileName);
  const label = $('island-status-label');
  const { w, h } = renderTarget();
  const preferNvenc = state.render.encoder !== 'x264';
  const renderConfig = {
    crf: Number(state.render.crf) || 23,
    audio_bitrate: state.render.audioBitrate || '192k',
    target_width: state.render.res === 'original' ? null : w,
    target_height: state.render.res === 'original' ? null : h,
    res_mode: state.render.res
  };

  (async () => {
    try {
      const r = await Bridge.burn(
        state.videoPath, state.subs, outPath, Bridge.styleConfig(), preferNvenc,
        (p, t) => { if (label) label.innerText = t + ' ' + Math.round(p) + '%'; },
        state.render.fps,
        renderConfig
      );
      if (r && r.status === 'success') {
        if (state.prefs.completionSound) SFX.chord();
        const fbNote = r.fallback_reason ? ('，已自动回退：' + r.fallback_reason) : '';
        toast('压制完成（' + r.encoder_used + fbNote + '）→ ' + r.output_video_path);
        if (state.prefs.autoOpenExport) Bridge.openFolder(r.output_video_path);
        if (label) label.innerText = '压制完成';
      } else {
        showError((r && r.message) || '压制失败');
        if (label) label.innerText = '压制失败';
      }
    } catch (e) {
      showError('压制出错：' + (e && e.message ? e.message : e));
      if (label) label.innerText = '压制出错';
    }
  })();
}

/* ============================ 12.5 无边框全屏控制 ============================ */

let _appFullscreen = false;
let _stageFullscreen = false;
let _stageIdleTimer = null;

function _syncFullscreenIcons() {
  const expandPath = '<path stroke-linecap="round" stroke-linejoin="round" d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />';
  const shrinkPath = '<path stroke-linecap="round" stroke-linejoin="round" d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />';
  const appIcon = $('app-fs-icon');
  const appLbl = $('app-fs-label');
  if (appIcon) appIcon.innerHTML = _appFullscreen ? shrinkPath : expandPath;
  if (appLbl) appLbl.innerText = _appFullscreen ? '退出全屏' : '全屏';
  const stageIcon = $('stage-fs-icon');
  if (stageIcon) stageIcon.innerHTML = _stageFullscreen ? shrinkPath : expandPath;
}

function _bumpStageControls() {
  if (!_stageFullscreen) return;
  document.body.classList.remove('stage-controls-idle');
  if (_stageIdleTimer) clearTimeout(_stageIdleTimer);
  _stageIdleTimer = setTimeout(() => {
    if (_stageFullscreen) document.body.classList.add('stage-controls-idle');
  }, 2400);
}

async function toggleAppFullscreen(forceState) {
  SFX.microTick(680);
  const want = (typeof forceState === 'boolean') ? forceState : !_appFullscreen;
  try {
    const res = await Bridge.setFullscreen(want);
    _appFullscreen = (res && typeof res.fullscreen === 'boolean') ? res.fullscreen : want;
  } catch (e) {
    _appFullscreen = want;
  }
  document.body.classList.toggle('is-fullscreen', _appFullscreen);
  if (!_appFullscreen && _stageFullscreen) {
    _stageFullscreen = false;
    document.body.classList.remove('is-stage-fullscreen', 'stage-controls-idle');
    if (_stageIdleTimer) { clearTimeout(_stageIdleTimer); _stageIdleTimer = null; }
  }
  _syncFullscreenIcons();
  setTimeout(() => { Glider.invalidate(); Glider.refresh(); }, 180);
}

async function toggleStageFullscreen(forceState) {
  SFX.actionPop(640);
  const want = (typeof forceState === 'boolean') ? forceState : !_stageFullscreen;
  _stageFullscreen = want;
  document.body.classList.toggle('is-stage-fullscreen', want);
  document.body.classList.remove('stage-controls-idle');
  if (_stageIdleTimer) { clearTimeout(_stageIdleTimer); _stageIdleTimer = null; }
  if (want) {
    if (!_appFullscreen) {
      await toggleAppFullscreen(true);
    }
    _bumpStageControls();
  } else {
    if (_appFullscreen) {
      await toggleAppFullscreen(false);
    }
  }
  _syncFullscreenIcons();
  setTimeout(() => { Glider.invalidate(); Glider.refresh(); }, 180);
}

async function exitAllFullscreen() {
  if (_stageFullscreen) {
    await toggleStageFullscreen(false);
  } else if (_appFullscreen) {
    await toggleAppFullscreen(false);
  }
}

window.__onWindowStateChange = function (info) {
  const isMax = Boolean(info && info.maximized);
  document.body.classList.toggle('is-maximized', isMax && !_appFullscreen && !_stageFullscreen);
  setTimeout(() => { Glider.invalidate(); Glider.refresh(); }, 120);
};

/* ============================ 13. 导入与帧级同步 ============================ */

let _vfcId = null;
let _vfcVideo = null;

function stopVideoFrameSync() {
  if (_vfcId != null && _vfcVideo && typeof _vfcVideo.cancelVideoFrameCallback === 'function') {
    try { _vfcVideo.cancelVideoFrameCallback(_vfcId); } catch (e) { /* noop */ }
  }
  _vfcId = null;
  _vfcVideo = null;
}

function attachVideoSync(v) {
  if (!v) return;
  stopVideoFrameSync();
  _vfcVideo = v;

  const syncNow = (mediaTime) => {
    const t = (typeof mediaTime === 'number' && !isNaN(mediaTime)) ? mediaTime : v.currentTime;
    if (typeof t === 'number' && !isNaN(t)) {
      state.currentTime = t;
      syncPlayheadTarget(t);
    }
  };

  if (typeof v.requestVideoFrameCallback === 'function') {
    const onFrame = (_now, metadata) => {
      if (_vfcVideo !== v) return;
      if (state.usingRealVideo && !state.videoFailed) {
        syncNow(metadata && typeof metadata.mediaTime === 'number' ? metadata.mediaTime : v.currentTime);
      }
      _vfcId = v.requestVideoFrameCallback(onFrame);
    };
    _vfcId = v.requestVideoFrameCallback(onFrame);
  }

  v.onplay = () => { state.isPlaying = true; updatePlayButtonUI(); syncNow(v.currentTime); };
  v.onpause = () => { state.isPlaying = false; updatePlayButtonUI(); syncNow(v.currentTime); };
  v.onended = () => { state.isPlaying = false; updatePlayButtonUI(); syncNow(v.duration || v.currentTime); };
  v.onseeking = () => syncNow(v.currentTime);
  v.onseeked = () => syncNow(v.currentTime);
  v.onratechange = () => syncNow(v.currentTime);
  v.onemptied = () => { state.isPlaying = false; updatePlayButtonUI(); };
  // 禁用低频抖动的 ontimeupdate，改由 requestVideoFrameCallback + rAF 驱动
  v.ontimeupdate = null;
}

function loadLocalVideo(e) {
  const file = e.target.files[0];
  if (!file) return;
  const v = $('real-video');
  stopVideoFrameSync();
  state.videoTitle = String(file.name || '').replace(/\.[^.]+$/, '');
  v.src = URL.createObjectURL(file);
  v.classList.remove('hidden');
  if (canvas) canvas.classList.add('hidden');
  state.usingRealVideo = true;
  state.videoFailed = false;
  attachVideoSync(v);
  v.onloadedmetadata = () => {
    state.duration = Math.max(10, v.duration || 24);
    $('island-status-label').innerText = file.name;
    if (state.render.res === 'original') updateRenderBadges();
    SFX.chord();
    refreshUI(true);
    toast('已载入本地视频：' + file.name);
  };
}

function loadLocalSrt(e) {
  const file = e.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = (ev) => {
    const blocks = ev.target.result.trim().replace(/\r\n/g, '\n').split(/\n\n+/);
    const parsed = [];
    blocks.forEach((b, i) => {
      const lines = b.split('\n').map(l => l.trim()).filter(Boolean);
      if (lines.length < 2) return;
      const ti = lines[0].includes('-->') ? 0 : 1;
      const m = lines[ti] && lines[ti].match(/(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)/);
      if (!m) return;
      const sec = (h, mm, s, ms) => Number(h) * 3600 + Number(mm) * 60 + Number(s) + Number(ms) / 1000;
      const txts = lines.slice(ti + 1);
      parsed.push({
        id: i + 1,
        start: sec(m[1], m[2], m[3], m[4]),
        end: sec(m[5], m[6], m[7], m[8]),
        zh: txts[0] || '',
        src: txts[1] || txts[0] || ''
      });
    });
    if (!parsed.length) { toast('没有解析到有效字幕'); return; }
    saveSnapshot();
    state.subs = parsed;
    state.duration = Math.max(state.duration, parsed[parsed.length - 1].end + 2);
    SFX.chord();
    refreshUI(true);
    toast('成功导入 ' + parsed.length + ' 条字幕');
  };
  reader.readAsText(file, 'utf-8');
}

function clickImportVideo() {
  toggleIsland(false);
  if (state.native) {
    (async () => {
      const p = await (window.Bridge && Bridge.pickVideo());
      if (p) setVideoByPath(p);
    })();
  } else {
    document.getElementById('video-upload').click();
  }
}

function clickImportSrt() {
  toggleIsland(false);
  if (state.native) {
    (async () => {
      const p = await (window.Bridge && Bridge.pickSrt());
      if (p) loadSrtByPath(p);
    })();
  } else {
    document.getElementById('srt-upload').click();
  }
}

/* 本地视频也走 expose_video 的 HTTP 预览通道，避免 WebView2 拦截 file://。 */
function setVideoByPath(path) {
  if (!path) return;
  previewVideo(String(path)).catch(e => {
    state.usingRealVideo = false;
    showError('本地视频预览失败：' + (e && e.message ? e.message : e));
  });
}

async function previewVideo(path) {
  state.videoPath = String(path);
  const fileBase = String(path).split(/[\\/]/).pop() || '';
  if (!state.videoTitle && fileBase) state.videoTitle = fileBase.replace(/\.[^.]+$/, '');
  const v = $('real-video');
  stopVideoFrameSync();

  // 关键：页面是 http:// 加载的，<video src="file:///..."> 会被 Chromium 拦截。
  // 让后端通过本机 127.0.0.1 HTTP Range 流媒体服务提供零拷贝预览 URL。
  let url = null;
  let exposeMessage = '';
  try {
    const r = await Bridge.exposeVideo(path);
    if (r && r.status === 'success' && r.url) url = r.url;
    else exposeMessage = (r && r.message) || '';
  } catch (e) { exposeMessage = e && e.message ? e.message : String(e); }
  if (!url) {
    state.usingRealVideo = false;
    state.videoFailed = true;
    showError('无法为 WebView2 提供本地视频预览。' + (exposeMessage ? '\n' + exposeMessage : ''));
    return;
  }

  v.classList.remove('hidden');
  if (canvas) canvas.classList.add('hidden');
  state.usingRealVideo = true;
  state.videoFailed = false;
  attachVideoSync(v);

  v.onerror = () => {
    state.videoFailed = true;
    state.usingRealVideo = false;
    stopVideoFrameSync();
    showError('视频画面加载失败（可能是不支持的编码格式）。\n字幕编辑、翻译和导出仍然可以正常使用，时间轴也照常可拖。');
  };
  v.onloadedmetadata = () => {
    state.videoFailed = false;
    state.duration = Math.max(10, v.duration || state.duration);
    const label = $('island-status-label');
    if (label) label.innerText = fileBase || String(path);
    if (state.render.res === 'original') updateRenderBadges();
    refreshUI(true);
  };
  v.src = url;
  try { v.load(); } catch (e) { /* noop */ }
}

/* ---- 模拟播放由 initPhysicsPlayground() 内的主 rAF 单一时钟驱动，此处仅同步播放态 ---- */
function startSimPlayback() {
  state.isPlaying = true;
  updatePlayButtonUI();
}
function stopSimPlayback() {
  state.isPlaying = false;
  updatePlayButtonUI();
}

/* 用后端解析 .srt（走同一套 MAX_SEG_WIDTH/MAX_SEG_DUR 分段清洗） */
async function loadSrtByPath(path) {
  try {
    const r = await Bridge.parseSrt(path);
    if (r && r.status === 'success' && r.segments && r.segments.length) {
      saveSnapshot();
      state.subs = Bridge.toFrontSegments(r.segments);
      const maxEnd = state.subs.reduce((m, s) => Math.max(m, s.end || 0), 0);
      if (maxEnd > 2) state.duration = maxEnd + 2;
      refreshUI(true);
      toast('成功导入 ' + r.segments.length + ' 条字幕（已自动分段清洗）');
    } else {
      toast((r && r.message) || '字幕解析失败');
    }
  } catch (e) {
    toast('解析出错：' + (e && e.message ? e.message : e));
  }
}

/* 保存 / 测试大模型 Key */
/* 翻译服务商一键预设：任何 OpenAI 兼容接口都能用 */
const LLM_PRESETS = {
  deepseek:    { url: 'https://api.deepseek.com', model: 'deepseek-chat', trust: '需填 Key', hint: 'DeepSeek（便宜快，推荐）' },
  openai:      { url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', trust: '需填 Key', hint: 'OpenAI（需海外网络）' },
  qwen:        { url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', trust: '需填 Key', hint: '阿里通义千问' },
  kimi:        { url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k', trust: '需填 Key', hint: '月之暗面 Kimi' },
  zhipu:       { url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash', trust: '需填 Key', hint: '智谱 GLM（有免费额度）' },
  siliconflow: { url: 'https://api.siliconflow.cn/v1', model: 'Qwen/Qwen2.5-7B-Instruct', trust: '需填 Key', hint: '硅基流动（有免费模型）' },
  ollama:      { url: 'http://localhost:11434/v1', model: 'qwen2.5:7b', trust: '本地免费', hint: '本地 Ollama（完全免费，需先安装）' }
};

function applyLlmPreset(name) {
  const p = LLM_PRESETS[name];
  if (!p) return;
  SFX.microTick(700);
  if ($('llm-base-url')) $('llm-base-url').value = p.url;
  if ($('llm-model')) $('llm-model').value = p.model;
  // 本地 Ollama 不校验 key，随便填个占位即可
  if (name === 'ollama' && $('llm-api-key') && !$('llm-api-key').value.trim()) {
    $('llm-api-key').value = 'ollama';
  }
  if (name === 'ollama') {
    ensureOllama();       // 检测有没有装，没装就弹窗问要不要自动装
    return;
  }
  toast(p.hint + '：已填好地址和模型，填上你的 Key 后点「保存 Key」');
}

/* ---------- 本地 Ollama：检测 → 弹窗 → 自动安装 → 拉模型 ---------- */
async function ensureOllama() {
  const info = await Bridge.checkOllama();
  const st = $('ollama-status');
  const btn = $('ollama-action-btn');
  const bar = $('ollama-progress-bar');
  const body = $('ollama-body');

  if (info && info.installed && info.running) {
    const models = info.models || [];
    if (models.some(m => m.startsWith('qwen2.5'))) {
      if (st) st.innerText = '已就绪，模型：' + models.join('、');
      toast('本地 Ollama 已就绪（模型 ' + models.join('、') + '），点「翻译字幕」就能用');
      return;
    }
    // 装了服务也在，但没模型
    if (body) body.innerHTML = '检测到 Ollama 已安装，但还<b class="text-amber-300">没有下载模型</b>。<br>需要下载 <span class="font-mono-num text-emerald-300">qwen2.5:3b</span>（约 1.9GB）才能翻译。';
    if (btn) btn.innerText = '下载模型（1.9GB）';
    if (st) st.innerText = '';
    window.__ollamaStep = 'model';
    openModal('ollama-modal');
    return;
  }

  // 没装
  if (body) body.innerHTML = '检测到<b class="text-amber-300">还没安装 Ollama</b>（免费离线翻译引擎）。<br><br>软件可以帮你<b class="text-emerald-300">自动下载并安装</b>：<br>· 安装包约 150MB<br>· 翻译模型约 1.9GB<br>· 共需 10-20 分钟（看网速）<br><br>装好后翻译<b>永久免费、可离线</b>，不用注册任何账号。';
  if (btn) btn.innerText = '自动下载安装';
  if (st) st.innerText = '';
  if (bar) bar.style.width = '0%';
  window.__ollamaStep = 'install';
  openModal('ollama-modal');
}

async function startOllamaSetup() {
  const st = $('ollama-status');
  const bar = $('ollama-progress-bar');
  const btn = $('ollama-action-btn');
  const onProgress = (p, t) => {
    if (st) st.innerText = t;
    if (bar) bar.style.width = Math.min(100, Math.max(0, Number(p) || 0)) + '%';
  };
  if (btn) { btn.disabled = true; btn.classList.add('opacity-50'); }

  try {
    const info = await Bridge.checkOllama();
    if (!info || !info.installed) {
      if (st) st.innerText = '正在下载安装包…';
      const r = await Bridge.installOllama(onProgress);
      if (!r || r.status !== 'success') {
        if (st) st.innerText = (r && r.message) || '安装失败';
        showError((r && r.message) || 'Ollama 自动安装失败');
        if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
        return;
      }
    }
    const model = (info && info.default_model) || 'qwen2.5:3b';
    if (st) st.innerText = '正在下载模型…';
    const r2 = await Bridge.pullOllamaModel(model, onProgress);
    if (r2 && r2.status === 'success') {
      if (st) st.innerText = '✅ 全部就绪';
      if (bar) bar.style.width = '100%';
      toast('Ollama + 模型已就绪，现在点「翻译字幕」就能出中文了');
      SFX.chord();
      setTimeout(() => closeModal('ollama-modal'), 1200);
    } else {
      if (st) st.innerText = (r2 && r2.message) || '模型下载失败';
      showError((r2 && r2.message) || '模型下载失败');
    }
  } catch (e) {
    if (st) st.innerText = '出错：' + (e && e.message ? e.message : e);
    showError('Ollama 安装出错：' + (e && e.message ? e.message : e));
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove('opacity-50'); }
  }
}

async function saveLlmSettings() {
  const key = $('llm-api-key') ? $('llm-api-key').value.trim() : '';
  const base = $('llm-base-url') ? $('llm-base-url').value.trim() : '';
  const model = $('llm-model') ? $('llm-model').value.trim() : 'deepseek-chat';
  SFX.actionPop(620);
  const r = await Bridge.setTranslator(key, base, model);
  toast(r && r.message ? r.message : '已保存');
}

async function testApiKey() {
  const key = $('llm-api-key') ? $('llm-api-key').value.trim() : '';
  const base = $('llm-base-url') ? $('llm-base-url').value.trim() : '';
  const model = $('llm-model') ? $('llm-model').value.trim() : 'deepseek-chat';
  if (!key) { toast('请先填写 API Key'); return; }
  if (!state.native) {
    toast('（浏览器预览模式）请在桌面应用中测试真实连通性');
    return;
  }
  SFX.actionPop(600);
  toast('正在保存配置并测试接口连通性…');
  await Bridge.setTranslator(key, base, model);
  const r = await Bridge.testTranslator();
  if (r && r.status === 'success') {
    SFX.chord();
    toast(r.message || ('连通成功（' + (r.elapsed_ms || 0) + ' ms）'));
  } else {
    showError((r && r.message) || '接口连通性测试失败，请检查 Key / 接口地址 / 网络');
  }
}

async function setCookieSource(source, idx, btn) {
  // source: '' / 'edge' / 'chrome' / 'firefox'
  const val = String(source || '');
  SFX.microTick(700);
  const glider = $('cookie-glider');
  if (glider) glider.style.transform = 'translateX(' + (idx * 100) + '%)';
  const btns = btn && btn.parentElement ? btn.parentElement.querySelectorAll('button') : [];
  btns.forEach((b, i) => {
    b.className = i === idx
      ? 'relative z-10 py-1.5 rounded-full text-white font-semibold transition'
      : 'relative z-10 py-1.5 rounded-full text-zinc-400 hover:text-white transition';
  });

  if (!state.native) {
    toast('（浏览器预览模式）下载需在桌面应用中进行');
    return;
  }

  const r = await Bridge.setCookieSource(val);
  const status = $('cookie-status');
  if (status) {
    const active = val === '' ? '未设置' : (val === 'firefox' ? 'Firefox 直读' : '浏览器 ' + val);
    status.innerText = active;
    status.className = 'px-2.5 py-0.5 rounded-full text-[10px] ' + (val === '' ? 'bg-amber-500/15 text-amber-300' : 'bg-emerald-500/15 text-emerald-300');
  }
  toast(r && r.message ? r.message : 'Cookie 来源已更新');
}

async function saveProxy(val) {
  SFX.microTick(700);
  if (!state.native) { toast('（浏览器预览模式）代理需在桌面应用中使用'); return; }
  const r = await Bridge.setProxy(val);
  toast(r && r.message ? r.message : '代理已更新');
}

async function importCookies() {
  SFX.actionPop(620);
  if (!state.native) { toast('（浏览器预览模式）下载需在桌面应用中进行'); return; }
  const r = await Bridge.importCookies();
  toast(r && r.message ? r.message : '导入失败');
  if (r && r.status === 'success') {
    const status = $('cookie-status');
    if (status) { status.innerText = 'cookies.txt 已就绪'; status.className = 'px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 text-[10px]'; }
  }
}

/* ---------- B站扫码登录 ---------- */
let _biliTimer = null;
let _biliOnDone = null;   // 登录成功后的回调（如自动重试下载）

async function biliLogin(onDone) {
  if (typeof onDone === 'function') _biliOnDone = onDone; else _biliOnDone = null;
  toggleIsland(false);
  SFX.actionPop(660);
  if (!state.native) { toast('（浏览器预览模式）扫码登录需在桌面应用中进行'); return; }

  const r = await Bridge.biliQrGenerate();
  if (!r || r.status !== 'success') { toast((r && r.message) || '生成二维码失败'); return; }

  $('bili-qr-img').src = 'data:image/png;base64,' + r.qr_base64;
  $('bili-qr-status').innerText = '请用手机 B站 App 扫码';
  openModal('bili-login-modal');

  const key = r.qrcode_key;
  _biliTimer = setInterval(async () => {
    const p = await Bridge.biliQrPoll(key);
    if (!p) return;
    const st = $('bili-qr-status');
    if (st) st.innerText = p.message || '';
    if (p.done) {
      clearInterval(_biliTimer); _biliTimer = null;
      const status = $('cookie-status');
      if (status) { status.innerText = 'B站已登录'; status.className = 'px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 text-[10px]'; }
      setTimeout(() => closeBiliLogin(), 700);
      toast('B站登录成功');
      SFX.chord();
      // 登录后若来自「提取并翻译」，自动继续刚才的下载
      if (typeof _biliOnDone === 'function') {
        const cb = _biliOnDone;
        _biliOnDone = null;
        setTimeout(cb, 900);
      }
    } else if (p.status === 'error' || p.code === 86038) {
      clearInterval(_biliTimer); _biliTimer = null;
      if (st) st.innerText = (p.message || '登录失败') + '，可关闭重新打开';
    }
  }, 2000);
}

function closeBiliLogin() {
  if (_biliTimer) { clearInterval(_biliTimer); _biliTimer = null; }
  closeModal('bili-login-modal');
}

/* ---------- 平台识别徽章 ---------- */
const PLATFORM_BADGES = [
  { id: 'bilibili', name: 'B站', color: '#fb7299', hint: '可扫码登录 · 国内直连', hosts: ['bilibili.com', 'b23.tv'], needs_proxy: false },
  { id: 'youtube', name: 'YouTube', color: '#ff0000', hint: '需代理 + 登录', hosts: ['youtube.com', 'youtu.be'], needs_proxy: true },
  { id: 'douyin', name: '抖音', color: '#25f4ee', hint: '国内直连', hosts: ['douyin.com', 'iesdouyin.com'], needs_proxy: false },
  { id: 'kuaishou', name: '快手', color: '#ff5000', hint: '国内直连', hosts: ['kuaishou.com', 'gifshow.com'], needs_proxy: false },
  { id: 'xiaohongshu', name: '小红书', color: '#ff2442', hint: '国内直连', hosts: ['xiaohongshu.com', 'xhslink.com'], needs_proxy: false },
  { id: 'weibo', name: '微博', color: '#ea7132', hint: '国内直连', hosts: ['weibo.com', 'weibo.cn'], needs_proxy: false },
  { id: 'tiktok', name: 'TikTok', color: '#010101', hint: '需代理', hosts: ['tiktok.com'], needs_proxy: true },
  { id: 'twitter', name: 'X / 推特', color: '#1d9bf0', hint: '需代理 + 登录', hosts: ['twitter.com', 'x.com', 't.co'], needs_proxy: true },
  { id: 'instagram', name: 'Instagram', color: '#e4405f', hint: '需代理 + 登录', hosts: ['instagram.com'], needs_proxy: true },
  { id: 'facebook', name: 'Facebook', color: '#1877f2', hint: '需代理 + 登录', hosts: ['facebook.com', 'fb.watch'], needs_proxy: true },
  { id: 'vimeo', name: 'Vimeo', color: '#1ab7ea', hint: '需代理', hosts: ['vimeo.com'], needs_proxy: true },
  { id: 'youku', name: '优酷', color: '#1aa0f3', hint: '国内直连', hosts: ['youku.com'], needs_proxy: false },
  { id: 'iqiyi', name: '爱奇艺', color: '#00be06', hint: '国内直连', hosts: ['iqiyi.com'], needs_proxy: false },
  { id: 'tencent', name: '腾讯视频', color: '#ff6a00', hint: '国内直连', hosts: ['v.qq.com'], needs_proxy: false },
  { id: 'mgtv', name: '芒果TV', color: '#ff7f00', hint: '国内直连', hosts: ['mgtv.com'], needs_proxy: false },
  { id: 'ixigua', name: '西瓜视频', color: '#ff2e4d', hint: '国内直连', hosts: ['ixigua.com', 'toutiao.com'], needs_proxy: false },
];

function detectPlatformLocal(url) {
  const low = String(url || '').trim().toLowerCase();
  for (const p of PLATFORM_BADGES) {
    for (const h of p.hosts) if (low.includes(h)) return p;
  }
  return { id: 'unknown', name: '其他平台', color: '#9ca3af', hint: '支持 1000+ 站点，粘贴视频页链接即可' };
}

function updatePlatformBadge() {
  const raw = $('url-input') ? $('url-input').value : '';
  const p = detectPlatformLocal(raw);
  const badge = $('platform-badge');
  const dot = $('platform-dot'), name = $('platform-name'), hint = $('platform-hint');
  const biliBtn = $('platform-bili-login');

  if (!raw.trim()) { badge.classList.add('hidden'); return; }
  badge.classList.remove('hidden');
  if (dot) dot.style.background = p.color;
  if (name) name.innerText = p.name;
  if (hint) hint.innerText = p.hint;
  // 只有 B站 显示扫码登录入口
  if (biliBtn) biliBtn.classList.toggle('hidden', p.id !== 'bilibili');
}

/* 从剪贴板粘贴链接（比 Ctrl+V 更直观，也允许右键粘贴） */
async function pasteUrl() {
  const input = $('url-input');
  if (!input) return;
  try {
    const text = await navigator.clipboard.readText();
    if (text && text.trim()) {
      input.value = text.trim();
      updatePlatformBadge();
      SFX.microTick(700);
    }
  } catch (e) {
    // clipboard API 不可用时也给焦点让用户 Ctrl+V 或右键粘贴
    input.focus();
    input.select();
  }
}

/* ---------- 自定义右键菜单 ---------- */
let _ctxTarget = null;

function initContextMenu() {
  const menu = $('ctx-menu');
  if (!menu) return;

  document.addEventListener('contextmenu', (e) => {
    const el = e.target;
    const isField = el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' ||
                           el.isContentEditable);
    if (!isField) { hideCtxMenu(); return; }
    e.preventDefault();
    _ctxTarget = el;
    // 定位（防止超出窗口）
    const w = 140, h = 150;
    const x = Math.min(e.clientX, window.innerWidth - w - 8);
    const y = Math.min(e.clientY, window.innerHeight - h - 8);
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    menu.classList.remove('hidden');
    SFX.microTick(820);
  }, true);

  document.addEventListener('mousedown', (e) => {
    if (!menu.contains(e.target)) hideCtxMenu();
  }, true);
  window.addEventListener('blur', hideCtxMenu);
  document.addEventListener('scroll', hideCtxMenu, true);
}

function hideCtxMenu() {
  const menu = $('ctx-menu');
  if (menu) menu.classList.add('hidden');
  _ctxTarget = null;
}

async function ctxAction(kind) {
  const el = _ctxTarget;
  hideCtxMenu();
  if (!el) return;
  el.focus();
  try {
    if (kind === 'paste') {
      const text = await navigator.clipboard.readText();
      if (text == null) return;
      const s = el.selectionStart ?? el.value.length;
      const e2 = el.selectionEnd ?? el.value.length;
      const v = String(el.value || '');
      el.value = v.slice(0, s) + text + v.slice(e2);
      const pos = s + text.length;
      try { el.setSelectionRange(pos, pos); } catch (_) {}
      el.dispatchEvent(new Event('input', { bubbles: true }));
    } else if (kind === 'copy') {
      const s = el.selectionStart ?? 0, e2 = el.selectionEnd ?? 0;
      const sel = String(el.value || '').slice(s, e2);
      if (sel) await navigator.clipboard.writeText(sel);
    } else if (kind === 'cut') {
      const s = el.selectionStart ?? 0, e2 = el.selectionEnd ?? 0;
      const v = String(el.value || '');
      const sel = v.slice(s, e2);
      if (sel) {
        await navigator.clipboard.writeText(sel);
        el.value = v.slice(0, s) + v.slice(e2);
        try { el.setSelectionRange(s, s); } catch (_) {}
        el.dispatchEvent(new Event('input', { bubbles: true }));
      }
    } else if (kind === 'selectall') {
      el.select();
    }
  } catch (err) {
    // 剪贴板被系统拒绝时，退回到让用户用快捷键
    toast('剪贴板不可用，请用 Ctrl+V / Ctrl+C');
  }
}

/* 只翻译当前已有字幕（不重新下载/识别）——填完 API Key 后一键出中文 */
async function translateExisting() {
  SFX.actionPop(660);
  if (!state.subs || !state.subs.length) {
    showError('还没有字幕。请先「提取并翻译」一个视频，或导入本地字幕。');
    return;
  }
  const label = $('island-status-label');
  const onProgress = (p, t) => { if (label) label.innerText = t + ' ' + Math.round(p) + '%'; };
  const glossary = buildGlossary();
  const lang = state.sourceLang === 'auto' ? 'auto' : state.sourceLang;
  try {
    if (label) label.innerText = '正在翻译…';
    const tres = await Bridge.translate(
      state.subs, glossary, lang, state.engine.batch, state.engine.context, onProgress, false, state.engine.temp
    );
    if (tres && (tres.status === 'success' || tres.status === 'partial') && tres.segments) {
      saveSnapshot();
      state.subs = Bridge.toFrontSegments(tres.segments);
      refreshUI(true);
      const missing = getFailedSubCount();
      if (tres.status === 'partial' || missing > 0) {
        if (label) label.innerText = '精翻部分完成 · ' + missing + ' 条待补';
        showError((tres.message || ('有 ' + missing + ' 条字幕未获得有效译文')) + '\n可点击字幕流顶部的「补翻失败条目」或单句「AI 重译」恢复，不会用原文冒充译文。');
      } else {
        if (state.prefs.completionSound) SFX.chord();
        toast(tres.message || ('翻译完成，共 ' + state.subs.length + ' 条'));
        if (label) label.innerText = '精翻完成';
      }
    } else {
      showError((tres && tres.message) || '翻译失败。请到「偏好设置 → 算力与翻译引擎」检查 API Key。');
      if (label) label.innerText = '翻译失败';
    }
  } catch (e) {
    showError('翻译出错：' + (e && e.message ? e.message : e));
    if (label) label.innerText = '翻译出错';
  }
}

function confirmLargeModelChoice(choice) {
  closeModal('large-model-modal');
  if (choice === 'small') {
    switchWhisperTier('small', 2, true);
    toast('已切换为 Small 模型（约 460MB），开始提取并翻译…');
  } else {
    switchWhisperTier('large-v3-turbo', 0, true);
    toast('继续使用 Large-v3-Turbo（首次需下载约 1.5GB）…');
  }
  startAiPipeline(true);
}

function openOnboardingModal() {
  SFX.actionPop(620);
  openModal('onboarding-modal');
}

function dismissOnboarding() {
  closeModal('onboarding-modal');
  if (state.native) {
    Bridge.saveSettings({ onboarding_seen: true }).catch(() => {});
  }
}

async function runQuickstartDemo() {
  SFX.actionPop(660);
  closeModal('onboarding-modal');
  const label = $('island-status-label');
  if (label) label.innerText = '正在加载 10 秒内置双语示例…';
  try {
    const r = await Bridge.prepareDemoSample((p, t) => {
      if (label) label.innerText = t + ' ' + Math.round(p) + '%';
    });
    if (r && r.status === 'success' && r.segments) {
      saveSnapshot();
      state.videoTitle = r.title || 'SubFlow_10秒极速上手示例';
      state.dualSpeaker = true;
      const btn = $('dual-speaker-btn');
      if (btn) {
        btn.innerText = '说话人：双人模式 (A/B)';
        btn.className = 'px-2.5 py-1 rounded-full bg-indigo-500/25 border border-indigo-400/40 text-indigo-200 text-[10px] font-semibold transition';
      }
      state.subs = Bridge.toFrontSegments(r.segments);
      state.duration = 11.5;
      refreshUI(true);
      if (r.video_path && state.native) {
        await previewVideo(r.video_path);
      }
      seekTo(0.3);
      if (!state.isPlaying) togglePlay();
      if (state.prefs.completionSound) SFX.chord();
      toast(r.message || '已载入内置 10 秒双人双语示例！');
      if (label) label.innerText = '示例播放中 · 可随时导出成片';
      if (state.native) {
        Bridge.saveSettings({ onboarding_seen: true }).catch(() => {});
      }
    } else {
      showError((r && r.message) || '加载内置示例失败');
    }
  } catch (e) {
    showError('加载示例出错：' + (e && e.message ? e.message : e));
  }
}

async function startAiPipeline(skipModelConfirm) {
  toggleIsland(false);
  SFX.actionPop(660);
  const label = $('island-status-label');
  const source = String($('url-input').value || '').trim();
  if (!source) { toast('请先粘贴视频链接，或输入本地视频路径'); return; }

  // P0-1: 选用 large-v3-turbo 且本机尚未缓存时，首次弹窗确认是否改用 small（460MB）
  const largeCached = Boolean(
    state.modelStatus && state.modelStatus.models &&
    state.modelStatus.models['large-v3-turbo'] &&
    (state.modelStatus.models['large-v3-turbo'].cached || state.modelStatus.models['large-v3-turbo'].complete)
  );
  if (!skipModelConfirm && state.model === 'large-v3-turbo' && !largeCached) {
    openModal('large-model-modal');
    return;
  }

  const onProgress = (p, t) => { if (label) label.innerText = t + ' ' + Math.round(p) + '%'; };

  try {
    // 1) 准备（本地文件 / URL 下载 / 原生字幕 / 16k 音轨）
    if (label) label.innerText = '正在解析视频…';
    const prep = await Bridge.prepare(source, state.sourceLang, onProgress);
    if (!prep || prep.status !== 'success') {
      const msg = (prep && prep.message) || '准备失败';
      const plat = detectPlatformLocal(source);

      // B站：未登录 → 自动弹二维码，登录后自动重试
      if (plat.id === 'bilibili' && /登录|扫码|cookie|412|风控|Code 412/i.test(msg)) {
        toast('需要先登录 B站，正在打开二维码…');
        if (label) label.innerText = 'B站登录后自动继续';
        biliLogin(() => startAiPipeline());
        return;
      }
      // 国外平台：直连不通，提示用代理
      if (plat.needs_proxy && /超时|timed out|连接|connect|timeout|10060|10061/i.test(msg)) {
        showError(plat.name + ' 需要网络代理才能直连。\n请在「偏好设置 → 通用」填代理地址，或改用本地视频 / B站。');
        if (label) label.innerText = '联网失败';
        return;
      }
      showError(msg);
      if (label) label.innerText = '准备失败';
      return;
    }
    state.videoTitle = String(prep.title || '').trim() ||
      (prep.video_path ? String(prep.video_path).split(/[\\/]/).pop().replace(/\.[^.]+$/, '') : 'SubFlow');
    if (label) label.innerText = '已就绪：' + state.videoTitle;
    // 注意：这里先不快进预览。等识别 + 翻译都完成后再把视频放上来，
    // 避免"还没翻译完就急着上片"的体验问题。
    const pendingVideo = prep.video_path;

    // 2) 识别（或直接用原生字幕）
    let lang = state.sourceLang === 'auto' ? 'auto' : state.sourceLang;
    let segments = [];
    if (prep.native_srt_path) {
      const srt = await Bridge.parseSrt(prep.native_srt_path);
      if (srt && srt.status === 'success' && srt.segments && srt.segments.length) {
        segments = Bridge.toFrontSegments(srt.segments);
        if (srt.detected_lang) lang = srt.detected_lang;
        if (label) label.innerText = '已抓到原生字幕，跳过识别';
      }
    }
    if (!segments.length && prep.audio_path) {
      const tr = await Bridge.transcribe(prep.audio_path, lang, state.model, onProgress);
      if (!tr || tr.status !== 'success') {
        showError((tr && tr.message) || '识别失败');
        if (label) label.innerText = '识别失败';
        return;
      }
      segments = Bridge.toFrontSegments(tr.segments);
      if (lang === 'auto' && tr.detected_lang) lang = tr.detected_lang;
      if (label) label.innerText = '识别完成，共 ' + segments.length + ' 条';
    }
    if (!segments.length) {
      state.subs = [];
      refreshUI(true);
      showError('没有识别到任何字幕。\n可能是纯音乐或静音，或换个视频试试。');
      return;
    }

    // 3) 翻译（无 Key 时保留识别结果并提示；部分失败时明确标注可补翻）
    const glossary = buildGlossary();
    const tres = await Bridge.translate(segments, glossary, lang, state.engine.batch, state.engine.context, onProgress, false, state.engine.temp);
    if (tres && (tres.status === 'success' || tres.status === 'partial') && tres.segments) {
      state.subs = Bridge.toFrontSegments(tres.segments);
      const missing = getFailedSubCount();
      if (tres.status === 'partial' || missing > 0) {
        if (label) label.innerText = '精翻部分完成 · ' + missing + ' 条待补';
        showError((tres.message || ('有 ' + missing + ' 条字幕未获得有效译文')) + '\n可点「补翻失败条目」重试，不会用原文冒充译文。');
      } else {
        if (state.prefs.completionSound) SFX.chord();
        toast(tres.message || '翻译完成');
        if (label) label.innerText = '精翻完成';
      }
    } else {
      state.subs = segments;
      toast((tres && tres.message) || '翻译未完成（可随时点右上角「翻译字幕」或手工编辑）');
      if (label) label.innerText = '识别完成（未翻译）';
    }

    const maxEnd = state.subs.reduce((m, s) => Math.max(m, s.end || 0), 0);
    if (maxEnd > 2) state.duration = maxEnd + 2;
    refreshUI(true);
    refreshWorkspaceCacheInfo();

    // 全部完成才把视频放上舞台
    if (pendingVideo) {
      if (label) label.innerText = '正在装载视频…';
      await previewVideo(pendingVideo);
    }
  } catch (e) {
    showError('出错了：' + (e && e.message ? e.message : e));
    if (label) label.innerText = '出错了';
  }
}

/* ============================ 14. 刷新与启动 ============================ */

function refreshUI(rebuild) {
  assignSpeakers();          // 保留手工 A/B 标签；未标记的字幕默认说话人 A
  invalidateStageCache();
  renderSubCards(rebuild);
  renderTimelineCapsules();
  renderSubIndustryBar();
  renderTermTiles(false);
  syncPlayheadTarget(state.currentTime);
  updateRenderBadges();
}

function initMagneticButtons() {
  document.querySelectorAll('.magnetic-btn').forEach(btn => {
    btn.addEventListener('mousemove', (e) => {
      if (state.magneticStrength <= 0) return;
      const r = btn.getBoundingClientRect();
      const dx = e.clientX - (r.left + r.width / 2);
      const dy = e.clientY - (r.top + r.height / 2);
      btn.style.transform = 'translate(' + (dx * state.magneticStrength) + 'px, ' + (dy * state.magneticStrength) + 'px)';
    });
    btn.addEventListener('mouseleave', () => { btn.style.transform = 'translate(0px, 0px)'; });
  });
}

function _applyPrefButtonState(btnId, enabled) {
  const btn = $(btnId);
  if (!btn) return;
  btn.dataset.on = enabled ? '1' : '0';
  btn.textContent = enabled ? '已开启' : '已关闭';
  btn.className = enabled
    ? 'px-3 py-1 rounded-full bg-emerald-500/20 text-emerald-300 text-[11px] font-medium'
    : 'px-3 py-1 rounded-full bg-white/[0.08] text-zinc-400 text-[11px] font-medium';
}

/* 桌面环境启动：读硬件状态 + 本地模型缓存 + 已保存配置回填到设置页 */
async function initDesktopRuntime() {
  try {
    const hw = await Bridge.hwInfo();
    if (hw) {
      const pill = $('hw-top-pill');
      const accel = hw.accelerated === true || hw.device === 'cuda';
      if (pill) {
        pill.innerText = accel ? ('CUDA ' + String(hw.compute_type || 'FP16').toUpperCase()) : 'CPU 模式';
        pill.className = 'text-[10px] font-mono-num px-2 py-0.5 rounded-full ' +
          (accel ? 'text-emerald-300 bg-emerald-500/15' : 'text-amber-300 bg-amber-500/15');
      }
      const gl = $('detected-gpu-label');
      if (gl) {
        if (hw.gpu_name) {
          gl.innerText = '已识别：' + hw.gpu_name + (hw.total_vram_gb ? ' · ' + hw.total_vram_gb + 'GB' : '') +
            (hw.gpu_pack_missing ? '（未装 GPU 加速包，当前 CPU 模式）' : '');
        } else {
          gl.innerText = '已启用本地 GPU / CPU 自适应加速';
        }
      }
      const obHw = $('ob-hw-status');
      if (obHw) {
        if (accel && hw.gpu_name) {
          obHw.innerText = '已启用 GPU 加速：' + hw.gpu_name + ' (CUDA FLOAT16)';
        } else if (hw.gpu_pack_missing && hw.gpu_name) {
          obHw.innerText = '检测到 ' + hw.gpu_name + '（当前轻量 CPU 模式；解压 GPU 加速包可提速 8~10 倍）';
        } else {
          obHw.innerText = 'WebView2 与 FFmpeg 已就绪（CPU INT8 模式）';
        }
      }
      if (hw.gpu_pack_missing && hw.gpu_name) {
        toast('检测到显卡 ' + hw.gpu_name + '：解压附带的 GPU 加速包可让识别提速 8~10 倍');
      }
    }
  } catch (e) { /* noop */ }

  let savedWhisperModel = '';
  try {
    const s = await Bridge.loadSettings();
    if (s) {
      if (s.api_key && $('llm-api-key')) {
        $('llm-api-key').value = s.api_key;
        if ($('ob-key-status')) $('ob-key-status').innerText = '✅ 已配置 API Key，可直接开始精翻';
      }
      if (s.base_url && $('llm-base-url')) $('llm-base-url').value = s.base_url;
      if (s.model && $('llm-model')) $('llm-model').value = s.model;
      if (s.proxy && $('proxy-input')) $('proxy-input').value = s.proxy;
      if (s.export_dir) {
        state.prefs.exportDir = s.export_dir;
        if ($('export-dir-input')) $('export-dir-input').value = s.export_dir;
      }
      if (typeof s.auto_open_export === 'boolean') {
        state.prefs.autoOpenExport = s.auto_open_export;
        _applyPrefButtonState('pref-auto-open-btn', s.auto_open_export);
      }
      if (typeof s.completion_sound === 'boolean') {
        state.prefs.completionSound = s.completion_sound;
        _applyPrefButtonState('pref-sound-btn', s.completion_sound);
      }
      if (typeof s.burn_sub === 'boolean') {
        state.render.burnSub = s.burn_sub;
        syncBurnSubUI();
      }
      if (typeof s.export_name_tpl === 'string' && s.export_name_tpl.trim()) {
        state.render.nameTpl = s.export_name_tpl.trim();
        if ($('export-name-tpl')) $('export-name-tpl').value = state.render.nameTpl;
      }
      if (typeof s.temperature === 'number' && !isNaN(s.temperature)) {
        state.engine.temp = Math.max(0, Math.min(1, s.temperature));
        if ($('slider-temp')) $('slider-temp').value = Math.round(state.engine.temp * 100);
        if ($('temp-val')) $('temp-val').innerText = state.engine.temp.toFixed(2);
      }
      if (typeof s.batch_size === 'number' && s.batch_size >= 1) {
        state.engine.batch = Math.min(30, Math.round(s.batch_size));
        if ($('slider-batch')) $('slider-batch').value = state.engine.batch;
        if ($('batch-val')) $('batch-val').innerText = state.engine.batch + ' 条';
      }
      if (typeof s.context_size === 'number' && s.context_size >= 0) {
        state.engine.context = Math.min(8, Math.round(s.context_size));
        if ($('slider-ctx')) $('slider-ctx').value = state.engine.context;
        if ($('ctx-val')) $('ctx-val').innerText = state.engine.context + ' 条';
      }
      if (s.compute_mode && ['auto', 'cuda', 'cpu'].includes(s.compute_mode)) {
        state.computeMode = s.compute_mode;
        syncComputeModeUI(s.compute_mode);
      }
      if (s.whisper_model && ['large-v3-turbo', 'medium', 'small'].includes(s.whisper_model)) {
        savedWhisperModel = s.whisper_model;
        state.model = s.whisper_model;
        state.modelExplicit = true;
      }
      // 回填 Cookie 来源状态
      const csrc = s.cookies_from_browser || '';
      const st = $('cookie-status');
      if (csrc && ['edge', 'chrome', 'firefox'].includes(csrc)) {
        const idx = csrc === 'edge' ? 1 : (csrc === 'chrome' ? 2 : 3);
        const glider = $('cookie-glider');
        if (glider) glider.style.transform = 'translateX(' + (idx * 100) + '%)';
        if (st) { st.innerText = csrc === 'firefox' ? 'Firefox 直读' : ('浏览器 ' + csrc); st.className = 'px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 text-[10px]'; }
      } else if (s.cookies_file_exists && st) {
        st.innerText = 'cookies.txt 已就绪';
        st.className = 'px-2.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-300 text-[10px]';
      }
    }
  } catch (e) { /* noop */ }

  try {
    const ms = await Bridge.getModelStatus();
    if (ms && ms.status === 'success') {
      state.modelStatus = ms;
      // 若用户尚未显式指定模型，优先选中本地已完整缓存的推荐模型（如 small），避免首次识别卡在 1.5GB 下载
      if (!savedWhisperModel && !state.modelExplicit && ms.recommended && ['large-v3-turbo', 'medium', 'small'].includes(ms.recommended)) {
        state.model = ms.recommended;
      }
      syncWhisperTierUI();
    }
  } catch (e) { /* noop */ }

  await refreshWorkspaceCacheInfo();
}

window.addEventListener('DOMContentLoaded', () => {
  detectLocalHardware();
  initPhysicsPlayground();
  initPlayableWaveform();
  initMagneticButtons();
  initContextMenu();
  renderUiLangGrid();
  renderColorPresets();
  setUiLanguage('zh-CN');
  updateRenderBadges();
  updateBgOpacityLabel();
  refreshUI(true);
  updatePlatformBadge();   // 识别默认 URL 的平台显示徽章
  refreshTermLockBtn();
  syncWhisperTierUI();
  syncBurnSubUI();
  if (state.sourceLang === 'auto') {
    if ($('label-source-lang')) $('label-source-lang').innerText = '自动检测';
    if ($('pill-src-code')) $('pill-src-code').innerText = 'AUTO';
  }

  // 桌面环境初始化：pywebview 注入 window.pywebview 有延迟 → 轮询等待
  (function pollNative(n) {
    const native = !!(window.pywebview && window.pywebview.api);
    state.native = native;
    if (native) {
      initDesktopRuntime();
    } else if (n < 20) {
      setTimeout(() => pollNative(n + 1), 250);   // 最多等 5 秒
    }
  })(0);

  // 右侧面板尺寸变化 → 只标记 dirty，不立即测量
  if (window.ResizeObserver) {
    const wrapper = $('sub-cards-wrapper');
    if (wrapper) {
      let roRaf = 0;
      const ro = new ResizeObserver(() => {
        if (roRaf) return;
        roRaf = requestAnimationFrame(() => {
          roRaf = 0;
          Glider.invalidate();
          Glider.refresh();
        });
      });
      ro.observe(wrapper);
    }
  }

  window.addEventListener('resize', () => { Glider.invalidate(); Glider.refresh(); });
  window.addEventListener('mousemove', () => { if (_stageFullscreen) _bumpStageControls(); });
  document.addEventListener('fullscreenchange', () => {
    if (!state.native) {
      const on = Boolean(document.fullscreenElement);
      _appFullscreen = on;
      document.body.classList.toggle('is-fullscreen', on);
      if (!on && _stageFullscreen) {
        _stageFullscreen = false;
        document.body.classList.remove('is-stage-fullscreen', 'stage-controls-idle');
      }
      _syncFullscreenIcons();
    }
  });

  window.addEventListener('pointerdown', (e) => {
    if (!state.islandOpen) return;
    const island = $('dynamic-island');
    if (island && !island.contains(e.target)) toggleIsland(false);
  });

  window.addEventListener('keydown', (e) => {
    if (e.key === 'F11') {
      e.preventDefault();
      toggleAppFullscreen();
      return;
    }
    if (e.key === 'Escape') {
      if (_stageFullscreen || _appFullscreen) {
        e.preventDefault();
        exitAllFullscreen();
        return;
      }
      if (state.islandOpen) toggleIsland(false);
      ['export-modal', 'preferences-modal', 'onboarding-modal', 'large-model-modal'].forEach(closeModal);
      return;
    }
    const tag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
      e.preventDefault();
      Split.collapse(Split.collapsed === 'right' ? null : 'right');
    }
    else if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); stepSub(-1); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); stepSub(1); }
    else if (e.key.toLowerCase() === 's') { e.preventDefault(); splitCurrentSub(); }
    else if (e.key.toLowerCase() === 'm') { e.preventDefault(); mergeCurrentSub(); }
  });
});