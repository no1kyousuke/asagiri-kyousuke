'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const rootDir = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(rootDir, 'app.js'), 'utf8');
const bridgeSource = fs.readFileSync(path.join(rootDir, 'bridge.js'), 'utf8');

// 1. activeSubsAt：默认单人严格单行，双人模式真实重叠时显示双行
const start = src.indexOf('function activeSubsAt(t) {');
const end = src.indexOf('\nfunction _setRow', start);
assert(start >= 0 && end > start, 'activeSubsAt() block not found');
const state = {
  dualSpeaker: true,
  subs: [
    { id: 1, start: 0.0, end: 1.0 },
    { id: 2, start: 1.2, end: 2.0, speaker: 0 },
    { id: 3, start: 1.5, end: 2.5, speaker: 1 },
  ]
};
const context = { state };
vm.runInNewContext(src.slice(start, end) + '\nglobalThis.activeSubsAt = activeSubsAt;', context);
assert.deepStrictEqual(Array.from(context.activeSubsAt(0.0), x => x.id), [1]);
assert.deepStrictEqual(Array.from(context.activeSubsAt(1.0), x => x.id), []); // end 严格左闭右开
assert.deepStrictEqual(Array.from(context.activeSubsAt(1.6), x => x.id), [2, 3]);
assert.deepStrictEqual(Array.from(context.activeSubsAt(3.0), x => x.id), []);

// 单人模式下即使手工存在两条重叠，也严格只展示单行
state.dualSpeaker = false;
assert.deepStrictEqual(Array.from(context.activeSubsAt(1.6), x => x.id), [3]);

// 同一说话人意外时间重叠绝不产生双人假行
state.dualSpeaker = true;
state.subs = [
  { id: 20, start: 0, end: 2, speaker: 0 },
  { id: 21, start: 1, end: 3, speaker: 0 },
];
assert.deepStrictEqual(Array.from(context.activeSubsAt(1.5), x => x.id), [21]);

// 2. assignSpeakers：保留手工 A/B 标签，未标记一律回落 A，绝不启发式猜说话人
const speakerStart = src.indexOf('function assignSpeakers() {');
const speakerEnd = src.indexOf('\n}', speakerStart) + 2;
assert(speakerStart >= 0 && speakerEnd > speakerStart, 'assignSpeakers() block not found');
state.subs = [{ id: 10, speaker: 1 }, { id: 11 }, { id: 12, speaker: 'unknown' }, { id: 13, speaker: '1' }];
vm.runInNewContext(src.slice(speakerStart, speakerEnd) + '\nassignSpeakers();', context);
assert.deepStrictEqual(Array.from(state.subs, x => x.speaker), [1, 0, 0, 1]);

// 3. Bridge 序列化：完整透传 speaker / translate_status / raw_start / raw_end
const frontBlockStart = bridgeSource.indexOf('function frontToBack(list) {');
const frontBlockEnd = bridgeSource.indexOf('\n  function backToFront', frontBlockStart);
assert(frontBlockStart >= 0 && frontBlockEnd > frontBlockStart, 'frontToBack() block not found');
const bridgeContext = {};
vm.runInNewContext(bridgeSource.slice(frontBlockStart, frontBlockEnd) + '\nglobalThis.frontToBack = frontToBack;', bridgeContext);
const serialized = Array.from(bridgeContext.frontToBack([
  { id: 1, start: 0, end: 1, src: 'A', zh: '甲', speaker: 0, translate_status: 'ok' },
  { id: 2, start: 1, end: 2, src: 'B', zh: '', speaker: 1, translate_status: 'failed' },
  { id: 3, start: 2, end: 3, src: 'C', zh: '丙' },
]), x => ({ id: x.id, speaker: x.speaker, translate_status: x.translate_status }));
assert.deepStrictEqual(serialized, [
  { id: 1, speaker: 0, translate_status: 'ok' },
  { id: 2, speaker: 1, translate_status: 'failed' },
  { id: 3, speaker: undefined, translate_status: undefined },
]);

// 4. 导出命名模板：正确替换 {title}/{lang}/{res} 并清洗非法字符
const nameStart = src.indexOf('function _sanitizeFileNamePart(');
const nameEnd = src.indexOf('\nfunction downloadFile(', nameStart);
assert(nameStart >= 0 && nameEnd > nameStart, 'buildExportFilename() block not found');
const nameCtx = {
  state: {
    videoTitle: '日语会话:第1课/测试*',
    videoPath: '',
    targetLang: 'zh-CN',
    render: { nameTpl: '{title}_{lang}_{res}' }
  },
  renderTarget: () => ({ w: 1920, h: 1080 })
};
vm.runInNewContext(src.slice(nameStart, nameEnd) + '\nglobalThis.buildExportFilename = buildExportFilename;', nameCtx);
assert.strictEqual(nameCtx.buildExportFilename('mp4'), '日语会话_第1课_测试__zh-CN_1080p.mp4');

// 5. 本地预览与帧级同步、真实单句 AI 重译、硬压参数契约检查
const setVideoStart = src.indexOf('function setVideoByPath(path) {');
const setVideoEnd = src.indexOf('\nasync function previewVideo(path)', setVideoStart);
assert(setVideoStart >= 0 && setVideoEnd > setVideoStart, 'setVideoByPath() block not found');
const setVideoBlock = src.slice(setVideoStart, setVideoEnd);
assert(setVideoBlock.includes('previewVideo('), 'local import must use previewVideo()');
assert(!setVideoBlock.includes("'file:///"), 'local import must not set file:// directly');

assert(src.includes('requestVideoFrameCallback'), 'video sync must use requestVideoFrameCallback');
assert(!src.includes('（已精修）'), 'polishSub must not append fake suffix');
assert(src.includes('Bridge.translateSingle('), 'polishSub must call Bridge.translateSingle');
assert(src.includes('state.render.encoder !== \'x264\''), 'burn must respect encoder choice');
assert(src.includes('renderConfig'), 'burn must pass renderConfig to backend');
assert(bridgeSource.includes('detectPlatform: async function'), 'bridge detectPlatform missing');
assert(bridgeSource.includes('if (!isNative()) {\n        return { id: \'unknown\''), 'bridge detectPlatform must guard !isNative()');
assert(!src.includes('_simTimer = setInterval'), 'must not run duplicate setInterval simulation clock');
assert(src.includes('function toggleSfxChannel('), 'SFX channel toggle missing');
assert(src.includes('function toggleBurnSub('), 'burnSub toggle missing');

// 6. 确认 index.html 已无空壳 toggleSwitch() 假开关
const htmlSource = fs.readFileSync(path.join(rootDir, 'index.html'), 'utf8');
assert(!htmlSource.includes('toggleSwitch('), 'index.html still contains unwired toggleSwitch()');

console.log('frontend timing + dual speaker + naming + bridge + sync logic: PASS');
