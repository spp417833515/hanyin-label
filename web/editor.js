'use strict';
/* 标签设计页: 模板列表 + 元素画布 (拖动 / 改宽 / Del 删除 / 方向键微调 / 撤销) + 属性 + 打印 */

const QR_SVG = '<svg viewBox="0 0 21 21" fill="currentColor" fill-rule="evenodd" shape-rendering="crispEdges" aria-hidden="true"><path d="M0 0h7v7H0zM1 1h5v5H1zM2 2h3v3H2zM14 0h7v7h-7zM15 1h5v5h-5zM16 2h3v3h-3zM0 14h7v7H0zM1 15h5v5H1zM2 16h3v3H2zM9 0h1v1H9zM11 1h2v1h-2zM8 2h1v2H8zM10 3h1v1h-1zM12 4h1v2h-1zM9 5h2v1H9zM8 8h2v1H8zM11 8h1v2h-1zM13 8h3v1h-3zM17 8h1v1h-1zM19 8h2v1h-2zM0 9h2v1H0zM4 9h2v1H4zM9 10h1v1H9zM14 10h2v2h-2zM18 10h1v2h-1zM2 11h1v1H2zM6 11h1v2H6zM10 12h3v1h-3zM16 12h1v1h-1zM20 12h1v2h-1zM8 14h1v2H8zM11 14h1v1h-1zM13 14h2v1h-2zM17 14h2v1h-2zM9 16h2v1H9zM14 16h1v2h-1zM16 16h1v1h-1zM19 16h2v1h-2zM8 18h2v1H8zM11 18h2v1h-2zM17 18h1v2h-1zM9 20h1v1H9zM12 20h3v1h-3zM19 19h2v2h-2z"/></svg>';
const CHANNEL = {usb: 'USB', driver: 'USB 经驱动', bt: '蓝牙'};
const VAR_CHIPS = [['{日期}', '今天的日期'], ['{时间}', '当前时间'], ['{流水号}', '每打一张 +1'],
                   ['{日期+30天}', '30 天后的日期，天数可改'], ['{日期:yyyy年MM月dd日}', '自定义日期格式']];

const E = {
  sizes: [], templates: [],
  t: null,            // 正在编辑的模板 (副本)
  saved: '',          // 上次保存的快照, 判断有没有改动
  selId: null,        // 选中的元素
  fields: {},         // 本次打印填的变量值
  copies: 1, undo: [], redo: [], k: 16, drag: null, lastInput: null, varSig: '', search: '', printing: false,
};

const r2 = v => Math.round(v * 100) / 100;
const fmtNum = v => String(Math.round(Number(v) * 10) / 10);
const sizeKey = s => `${fmtNum(s[0])}x${fmtNum(s[1])}`;
const snapTo = (v, fine) => (fine ? Math.round(v * 10) / 10 : Math.round(v * 2) / 2);
const clone = o => JSON.parse(JSON.stringify(o));
const snapshot = t => JSON.stringify([t.name, t.size, t.elements, t.serial]);
const isDirty = () => !!E.t && (!E.t.id || snapshot(E.t) !== E.saved);
const selEl = () => (E.t && E.t.elements.find(e => e.id === E.selId)) || null;
const newId = () => 'e' + Math.random().toString(36).slice(2, 8);
const textH = pt => pt * PT_MM * LINE;

function box(e) {
  if (e.type === 'text') return {x: e.x, y: e.y, w: e.w, h: textH(e.pt)};
  if (e.type === 'qr') return {x: e.x, y: e.y, w: e.size, h: e.size};
  return {x: e.x, y: e.y, w: e.w, h: e.h};
}

// 选中后显示的拖拽手柄: 二维码只有四角 (保持正方形), 其余四角 + 四边
const HANDLES = {qr: ['nw', 'ne', 'sw', 'se'], other: ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']};
const handlesHTML = e => (HANDLES[e.type] || HANDLES.other).map(h => `<span class="hd hd-${h}" data-h="${h}"></span>`).join('');

const mctx = document.createElement('canvas').getContext('2d');
function textW(s, px, bold) { mctx.font = `${bold ? 700 : 400} ${px}px ${LABEL_FONT}`; return mctx.measureText(s).width; }

// ---------------------------------------------------------------- 撤销 / 重做

const histState = () => JSON.stringify({name: E.t.name, size: E.t.size, elements: E.t.elements});

function pushHistory() {
  if (!E.t) return;
  const s = histState();
  if (E.undo[E.undo.length - 1] === s) return;
  E.undo.push(s);
  if (E.undo.length > 100) E.undo.shift();
  E.redo = [];
}

function stepHistory(from, to) {
  if (!E.t) return;
  const cur = histState();
  while (from.length && from[from.length - 1] === cur) from.pop();
  if (!from.length) { toast(from === E.undo ? '没有可以撤销的操作' : '没有可以重做的操作'); return; }
  to.push(cur);
  const o = JSON.parse(from.pop());
  Object.assign(E.t, o);
  if (!selEl()) E.selId = null;
  renderAll();
}
const undo = () => stepHistory(E.undo, E.redo);
const redo = () => stepHistory(E.redo, E.undo);

// ---------------------------------------------------------------- 画面

function elementsHTML(t, fields, serial, k, selId) {
  const [wm, hm] = t.size;
  return t.elements.map(e => {
    const b = box(e), sel = e.id === selId;
    const out = b.x < -0.05 || b.y < -0.05 || b.x + b.w > wm + 0.05 || b.y + b.h > hm + 0.05;
    let inner = '', extra = '';
    if (e.type === 'text') {
      let fs = e.pt * PT_MM * k;
      const tw = textW(expand(e.text, fields, serial), fs, e.bold);
      if (e.shrink && tw > e.w * k && tw > 0) fs *= (e.w * k) / tw;
      const html = e.text ? expandHTML(e.text, fields, serial) : '<span class="ph">空文本</span>';
      inner = `<span class="tx" style="font-size:${fs.toFixed(2)}px;line-height:${(b.h * k).toFixed(2)}px;font-weight:${e.bold ? 700 : 400};text-align:${e.align}">${html}</span>`;
    } else if (e.type === 'qr') {
      inner = QR_SVG;
    } else if (e.type === 'box') {
      // 方框中间是空的: 只有四条边能点中, 不挡住框里的其他元素
      extra = `;border:${Math.max(1, e.t * k).toFixed(2)}px solid #111`;
      inner = '<span class="edge e-t"></span><span class="edge e-b"></span><span class="edge e-l"></span><span class="edge e-r"></span>';
    }
    return `<div class="el el-${e.type}${sel ? ' sel' : ''}${out ? ' out' : ''}" data-id="${esc(e.id)}" `
      + `style="left:${b.x * k}px;top:${b.y * k}px;width:${b.w * k}px;height:${b.h * k}px${extra}">${inner}${sel ? handlesHTML(e) : ''}</div>`;
  }).join('');
}

function fitScale() {
  const st = $('#stage'), [wm, hm] = E.t.size;
  const aw = Math.max(160, st.clientWidth - 110), ah = Math.max(120, st.clientHeight - 130);
  return Math.max(3, Math.min(aw / wm, ah / hm, 22));
}

function renderCanvas() {
  const c = $('#canvas');
  if (!E.t) {
    c.className = 'canvas empty';
    c.style.width = c.style.height = '';
    c.textContent = '还没有模板，点「新建模板」开始';
    return;
  }
  const [wm, hm] = E.t.size;
  const k = E.k = E.drag ? E.k : fitScale();
  c.className = 'canvas';
  c.style.width = `${wm * k}px`;
  c.style.height = `${hm * k}px`;
  let html = elementsHTML(E.t, E.fields, E.t.serial, k, E.selId);
  const g = (E.drag && E.drag.guides) || [];
  if (g.includes('v')) html += `<div class="guide gv" style="left:${(wm * k) / 2}px"></div>`;
  if (g.includes('h')) html += `<div class="guide gh" style="top:${(hm * k) / 2}px"></div>`;
  const de = E.drag && E.drag.moved && selEl();
  if (de) {  // 拖动时显示位置 / 尺寸
    const b = box(de), f = v => (Math.round(v * 10) / 10).toFixed(1);
    const txt = E.drag.h ? `${f(b.w)} × ${f(b.h)} mm${de.type === 'text' ? ` · ${de.pt}pt` : ''}` : `X ${f(b.x)}  Y ${f(b.y)} mm`;
    html += `<div class="dim" style="left:${b.x * k}px;top:${(b.y + b.h) * k + 8}px">${txt}</div>`;
  }
  c.innerHTML = html;
  $('#size-name').textContent = `${fmtNum(wm)} × ${fmtNum(hm)} mm`;
}

function renderSizes() {
  const cur = E.t ? sizeKey(E.t.size) : '';
  let html = E.sizes.map(s => `<button class="chip${sizeKey(s) === cur ? ' on' : ''}" data-size="${sizeKey(s)}" title="右键可从预设中删除">${fmtNum(s[0])}×${fmtNum(s[1])}</button>`).join('');
  if (E.t && !E.sizes.some(s => sizeKey(s) === cur)) html += `<button class="chip on" data-size="${cur}">${fmtNum(E.t.size[0])}×${fmtNum(E.t.size[1])}</button>`;
  $('#size-chips').innerHTML = html;
}

function renderList() {
  const q = E.search.trim().toLowerCase();
  const rows = E.templates.filter(t => !q || t.name.toLowerCase().includes(q)).map(t => {
    const on = E.t && E.t.id === t.id;
    const cur = on ? E.t : t;
    const nv = varsOf(cur).filter(v => !v.auto).length;
    return `<button class="tpl${on ? ' on' : ''}" data-id="${esc(t.id)}"><span class="thumb"><span class="mini" data-id="${esc(t.id)}"></span></span>`
      + `<span>${esc(cur.name)}${on && isDirty() ? ' <span class="unsaved" title="未保存">●</span>' : ''}`
      + `<small>${fmtNum(cur.size[0])}×${fmtNum(cur.size[1])} · ${nv ? nv + ' 个变量' : '无变量'}</small></span></button>`;
  });
  if (E.t && !E.t.id) {
    rows.unshift(`<button class="tpl on" data-id=""><span class="thumb"><span class="mini" data-id=""></span></span><span>${esc(E.t.name)} <span class="unsaved">●</span><small>新模板，还没保存</small></span></button>`);
  }
  const list = $('#tpl-list');
  list.innerHTML = rows.join('') || '<div class="muted" style="padding:10px">没有匹配的模板</div>';
  for (const el of $$('.mini', list)) {
    const own = !el.dataset.id || (E.t && E.t.id === el.dataset.id);
    const t = own ? E.t : E.templates.find(x => x.id === el.dataset.id);
    if (!t) continue;
    const k = Math.min(64 / t.size[0], 40 / t.size[1]);
    el.className = 'canvas';
    el.style.cssText = `display:block;width:${t.size[0] * k}px;height:${t.size[1] * k}px;border-radius:3px;pointer-events:none`;
    el.innerHTML = elementsHTML(t, own ? E.fields : t.fields, t.serial, k, null);
  }
}

function numField(prop, label, val, unit = 'mm', step = 0.5) {
  return `<label class="nf"><span>${label}</span><span class="box"><input type="number" step="${step}" data-prop="${prop}" value="${val}"><em>${unit}</em></span></label>`;
}

function chipsHTML() {
  return `<div class="vchips">${VAR_CHIPS.map(([v, tip]) => `<button class="vchip" data-insert="${esc(v)}" title="${esc(tip)}">${esc(v)}</button>`).join('')}`
    + '<button class="vchip" data-newvar title="新建一个打印时才填写的变量">+ 自定义变量</button></div>';
}

function renderProps() {
  const host = $('#el-props'), e = selEl();
  if (!E.t) { host.innerHTML = ''; return; }
  if (!e) {
    host.innerHTML = `<div class="sec-title">元素</div><div class="hint">点画布上的文字、二维码、线条或方框来编辑它；用上方工具栏添加新的元素。<br>
      选中后：拖中间移动 · 拖四角 / 四边的小方块改大小（文本拖上下边改字号）· <kbd>Del</kbd> 删除 · <kbd>Ctrl+D</kbd> 复制 · 方向键微调 · <kbd>Esc</kbd> 取消选中</div>`;
    return;
  }
  if (e.type === 'text') {
    host.innerHTML = `<div class="sec-title">文本</div>
      <label class="lbl"><span>内容</span><input class="txt mono" data-prop="text" maxlength="200" value="${esc(e.text)}"></label>
      ${chipsHTML()}
      <div class="conv"><span>打印时会变成</span><b id="conv"></b></div>
      <div class="row">
        <label class="nf" style="width:128px"><span>字号</span><span class="box"><button type="button" data-step="pt" data-d="-0.5" aria-label="字号减小">−</button><input type="number" step="0.5" min="3" max="72" data-prop="pt" value="${e.pt}"><em>pt</em><button type="button" data-step="pt" data-d="0.5" aria-label="字号增大">+</button></span></label>
        <button class="tool${e.bold ? ' on' : ''}" data-toggle="bold" aria-pressed="${e.bold}" style="height:32px"><b>B</b> 加粗</button>
        <span class="seg" role="group" aria-label="文字对齐">${[['left', '左'], ['center', '中'], ['right', '右']].map(([a, n]) => `<button data-align="${a}" class="${e.align === a ? 'on' : ''}" aria-pressed="${e.align === a}">${n}</button>`).join('')}</span>
      </div>
      <div class="grid3">${numField('x', 'X', e.x)}${numField('y', 'Y', e.y)}${numField('w', '宽', e.w)}</div>
      <label class="chk"><input type="checkbox" data-prop="shrink"${e.shrink ? ' checked' : ''}> 内容超过宽度时自动缩小（否则保持字号）</label>`;
  } else if (e.type === 'qr') {
    host.innerHTML = `<div class="sec-title">二维码</div>
      <label class="lbl"><span>内容（扫出来的文字）</span><input class="txt mono" data-prop="data" maxlength="500" value="${esc(e.data)}"></label>
      ${chipsHTML()}
      <div class="conv"><span>打印时会变成</span><b id="conv"></b></div>
      <div class="grid3">${numField('x', 'X', e.x)}${numField('y', 'Y', e.y)}${numField('size', '边长', e.size)}</div>
      <div class="muted">内容越长二维码越密；扫不出来就把边长调大。</div>`;
  } else if (e.type === 'box') {
    host.innerHTML = `<div class="sec-title">方框</div>
      <div class="grid2">${numField('x', 'X', e.x)}${numField('y', 'Y', e.y)}${numField('w', '宽', e.w)}${numField('h', '高', e.h)}</div>
      <div class="grid2">${numField('t', '线宽', e.t, 'mm', 0.1)}</div>
      <div class="muted">拖四角 / 四边的小方块改大小；框中间是空的，可以直接点里面的文字。</div>`;
  } else {
    host.innerHTML = `<div class="sec-title">线条</div>
      <div class="grid2">${numField('x', 'X', e.x)}${numField('y', 'Y', e.y)}${numField('w', '宽', e.w, 'mm', 0.1)}${numField('h', '高 / 粗细', e.h, 'mm', 0.1)}</div>
      <div class="muted">高度很小是横线，宽度很小是竖线，都大就是实心块。</div>`;
  }
  updateConv();
}

function updateConv() {
  const e = selEl(), c = $('#conv');
  if (!e || !c) return;
  c.textContent = expand(e.type === 'text' ? e.text : e.data, E.fields, E.t.serial) || '（空）';
}

function updatePosInputs() {
  const e = selEl();
  if (!e) return;
  for (const p of ['x', 'y', 'w', 'h', 'size', 'pt']) {
    const inp = $(`#el-props [data-prop="${p}"]`);
    if (inp && document.activeElement !== inp && e[p] !== undefined) inp.value = e[p];
  }
}

function renderFields(force = false) {
  if (!E.t) { $('#fields').innerHTML = ''; E.varSig = ''; return; }
  const vars = varsOf(E.t);
  const sig = vars.map(v => (v.auto ? v.raw : 'f:' + v.name)).join('|');
  if (!force && sig === E.varSig) return;
  E.varSig = sig;
  let serialShown = false;
  $('#fields').innerHTML = vars.map(v => {
    if (v.auto && v.name === '流水号' && !serialShown) {
      serialShown = true;
      return `<label class="fld auto serial"><span class="fld-k">${esc(v.raw)}</span><input type="number" min="0" id="f-serial" value="${E.t.serial}" aria-label="下一张的流水号"><em>下一张</em></label>`;
    }
    if (v.auto) return `<div class="fld auto"><span class="fld-k">${esc(v.raw)}</span><span class="fld-v"><span>${esc(expand(v.raw, {}, E.t.serial))}</span><em>自动</em></span></div>`;
    return `<label class="fld"><span class="fld-k" title="${esc(v.name)}">${esc(v.name)}</span><input data-field="${esc(v.name)}" value="${esc(E.fields[v.name] ?? '')}" placeholder="打印时填写"></label>`;
  }).join('') || '<div class="muted">这个模板没有变量，直接打印即可</div>';
}

const printBlock = () => (E.t ? printBlocked() : '没有模板');

function renderFoot() {
  const t = E.t, why = printBlock(), btn = $('#btn-print');
  btn.disabled = !!why || E.printing;
  btn.classList.toggle('blocked', !!why);
  $('#print-label').textContent = why || (E.printing ? '正在打印…' : `打印 ${E.copies} 张${connMode() === 'bt' ? '（蓝牙）' : ''}`);
  $('#offline-tip').hidden = !['打印机未连接', '蓝牙连不上'].includes(why);
  $('#offline-tip').textContent = printBlockedHint(why);
  $('#btn-batch').disabled = !t || !!why;
  $('#copies').value = E.copies;
  ['#btn-save', '#btn-saveas', '#btn-deltpl', '#t-text', '#t-qr', '#t-line', '#t-box', '#t-undo', '#t-redo', '#f-name']
    .forEach(s => { $(s).disabled = !t; });
  const e = selEl();
  ['#t-dup', '#t-del', '#t-ch', '#t-cv'].forEach(s => { $(s).disabled = !e; });
  if (!t) { $('#foot-info').textContent = ''; document.title = '汉印标签打印'; return; }
  $('#foot-info').innerHTML = `模板：<b>${esc(t.name)}</b>${isDirty() ? ' · <span class="dirty">未保存（打印时会自动保存）</span>' : ''}`
    + (usesSerial(t) ? ` · 下一张流水号 <b>${String(t.serial).padStart(4, '0')}</b>` : '');
  $('#btn-save').classList.toggle('attn', isDirty());
  document.title = `${isDirty() ? '* ' : ''}${t.name} - 汉印标签打印`;
}

function renderAll() {
  renderSizes();
  renderList();
  renderCanvas();
  renderProps();
  renderFields(true);
  renderFoot();
  $('#f-name').value = E.t ? E.t.name : '';
  schedulePreview();
}

function afterEdit() {
  renderCanvas();
  updateConv();
  renderFields();
  renderFoot();
  renderList();
  schedulePreview();
}

// 右侧"实际打印效果": 服务器按真实打印流程渲染的位图
let pvTimer, pvSeq = 0, pvURL = null;
function schedulePreview() { clearTimeout(pvTimer); pvTimer = setTimeout(loadPreview, 300); }
async function loadPreview() {
  if (!E.t) { $('#real').innerHTML = ''; return; }
  const seq = ++pvSeq;
  try {
    const fields = pickFields();
    const blob = await api('POST', '/api/preview', {template_obj: {...E.t, fields}, fields, scale: 2});
    if (seq !== pvSeq) return;
    if (pvURL) URL.revokeObjectURL(pvURL);
    pvURL = URL.createObjectURL(blob);
    $('#real').innerHTML = `<img src="${pvURL}" alt="实际打印效果">`;
  } catch (err) {
    if (seq === pvSeq) $('#real').innerHTML = `<span class="err">${esc(err.message)}</span>`;
  }
}

function pickFields() {
  const o = {};
  for (const n of fieldNames(E.t)) o[n] = E.fields[n] ?? '';
  return o;
}

// ---------------------------------------------------------------- 元素操作

function addElement(e) {
  pushHistory();
  e.id = newId();
  E.t.elements.push(e);
  E.selId = e.id;
  renderAll();
}

function addText() {
  const [wm, hm] = E.t.size, pt = 9;
  addElement({type: 'text', x: 1, y: r2(Math.max(0, (hm - textH(pt)) / 2)), w: r2(wm - 2), text: '文本', pt,
              bold: false, align: 'center', shrink: true});
  const inp = $('#el-props [data-prop="text"]');
  if (inp) { inp.focus(); inp.select(); }
}

function addQR() {
  const [wm, hm] = E.t.size, side = r2(Math.max(5, Math.min(wm, hm) - 2));
  const f = fieldNames(E.t)[0];
  addElement({type: 'qr', x: 1, y: r2((hm - side) / 2), size: side, data: f ? `{${f}}` : '{品名}'});
}

function addLine() {
  const [wm, hm] = E.t.size;
  addElement({type: 'rect', x: 1, y: r2(hm / 2), w: r2(wm - 2), h: 0.3});
}

function addBox() {
  const [wm, hm] = E.t.size;
  addElement({type: 'box', x: 0.5, y: 0.5, w: r2(wm - 1), h: r2(hm - 1), t: 0.3});
}

function duplicateEl() {
  const e = selEl();
  if (!e) return;
  addElement({...clone(e), x: r2(e.x + 1), y: r2(e.y + 1)});
}

function deleteEl() {
  const e = selEl();
  if (!e) return;
  pushHistory();
  E.t.elements = E.t.elements.filter(x => x.id !== e.id);
  E.selId = null;
  renderAll();
}

function centerEl(axis) {
  const e = selEl();
  if (!e) return;
  pushHistory();
  const b = box(e), [wm, hm] = E.t.size;
  if (axis === 'h') e.x = r2((wm - b.w) / 2); else e.y = r2((hm - b.h) / 2);
  renderProps();
  afterEdit();
}

function nudge(dx, dy) {
  const e = selEl();
  if (!e) return;
  e.x = r2(e.x + dx);
  e.y = r2(e.y + dy);
  updatePosInputs();
  afterEdit();
}

function clampProp(p, v) {
  const lim = {pt: [3, 72], w: [0.1, 200], h: [0.1, 200], size: [3, 100], x: [-100, 300], y: [-100, 300], t: [0.1, 5]}[p];
  return r2(Math.min(Math.max(v, lim[0]), lim[1]));
}

function insertToken(tok) {
  let inp = E.lastInput && E.lastInput.isConnected ? E.lastInput : $('#el-props [data-prop="text"], #el-props [data-prop="data"]');
  if (!inp) { toast('先在画布上选中一个文本或二维码', 'err'); return; }
  pushHistory();
  const a = inp.selectionStart ?? inp.value.length, z = inp.selectionEnd ?? a;
  inp.value = inp.value.slice(0, a) + tok + inp.value.slice(z);
  inp.dispatchEvent(new Event('input', {bubbles: true}));
  inp.focus();
  inp.setSelectionRange(a + tok.length, a + tok.length);
}

// ---------------------------------------------------------------- 画布拖动

function onPointerDown(ev) {
  if (!E.t || ev.button !== 0) return;
  const div = ev.target.closest('.el');
  if (!div) {
    if (E.selId) { E.selId = null; renderCanvas(); renderProps(); renderFoot(); }
    return;
  }
  const e = E.t.elements.find(x => x.id === div.dataset.id);
  if (!e) return;
  const changed = E.selId !== e.id;
  E.selId = e.id;
  const handle = ev.target.closest('.hd');
  E.drag = {id: e.id, h: handle ? handle.dataset.h : null, x0: ev.clientX, y0: ev.clientY, orig: clone(e), moved: false, guides: null};
  $('#canvas').setPointerCapture(ev.pointerId);
  if (document.activeElement && document.activeElement !== document.body) document.activeElement.blur();
  if (changed) { renderCanvas(); renderProps(); renderFoot(); }
  ev.preventDefault();
}

function onPointerMove(ev) {
  const d = E.drag;
  if (!d) return;
  const dxp = ev.clientX - d.x0, dyp = ev.clientY - d.y0;
  if (!d.moved && Math.abs(dxp) < 3 && Math.abs(dyp) < 3) return;
  const e = E.t.elements.find(x => x.id === d.id);
  if (!e) return;
  if (!d.moved) { d.moved = true; pushHistoryFrom(d.orig); }
  const k = E.k, dx = dxp / k, dy = dyp / k, fine = ev.altKey, o = d.orig, [wm, hm] = E.t.size;
  d.guides = [];
  if (!d.h) {
    const b = box(o);
    let nx = snapTo(o.x + dx, fine), ny = snapTo(o.y + dy, fine);
    nx = Math.min(Math.max(nx, 0), Math.max(0, wm - b.w));
    ny = Math.min(Math.max(ny, 0), Math.max(0, hm - b.h));
    if (Math.abs(nx + b.w / 2 - wm / 2) < 0.6) { nx = (wm - b.w) / 2; d.guides.push('v'); }
    if (Math.abs(ny + b.h / 2 - hm / 2) < 0.6) { ny = (hm - b.h) / 2; d.guides.push('h'); }
    e.x = r2(nx);
    e.y = r2(ny);
  } else {
    resizeEl(e, o, d.h, dx, dy, fine);
  }
  renderCanvas();
  updatePosInputs();
}

// 拖手柄改尺寸. h 是手柄方向 (n/s/e/w 及组合), 对边保持不动
function resizeEl(e, o, h, dx, dy, fine) {
  const b = box(o);
  const minW = {text: 2, qr: 4, rect: 0.1, box: 0.5}[e.type];
  const minH = {text: textH(3), qr: 4, rect: 0.1, box: 0.5}[e.type];
  let x1 = b.x, y1 = b.y, x2 = b.x + b.w, y2 = b.y + b.h;
  if (h.includes('w')) x1 = Math.min(snapTo(b.x + dx, fine), x2 - minW);
  if (h.includes('e')) x2 = Math.max(snapTo(x2 + dx, fine), x1 + minW);
  if (h.includes('n')) y1 = Math.min(snapTo(b.y + dy, fine), y2 - minH);
  if (h.includes('s')) y2 = Math.max(snapTo(y2 + dy, fine), y1 + minH);

  if (e.type === 'qr') {
    // 正方形: 取变化大的那个方向, 以对角为锚点
    const side = r2(Math.max(minW, Math.max(x2 - x1, y2 - y1)));
    e.size = side;
    e.x = r2(h.includes('w') ? b.x + b.w - side : b.x);
    e.y = r2(h.includes('n') ? b.y + b.h - side : b.y);
    return;
  }
  e.x = r2(x1);
  e.w = r2(x2 - x1);
  if (e.type === 'text') {
    // 文本高度由字号决定: 拖上下边 / 四角 = 改字号
    if (h.includes('n') || h.includes('s')) {
      e.pt = Math.min(72, Math.max(3, Math.round((y2 - y1) / (PT_MM * LINE) * 2) / 2));
      e.y = r2(h.includes('n') ? y2 - textH(e.pt) : y1);
    }
    return;
  }
  e.y = r2(y1);
  e.h = r2(y2 - y1);
}

// 拖动开始时记录拖动前的状态 (此时元素还没被改)
function pushHistoryFrom(orig) {
  const cur = E.t.elements.find(x => x.id === orig.id);
  const keep = clone(cur);
  Object.assign(cur, orig);
  pushHistory();
  Object.assign(cur, keep);
}

function onPointerUp() {
  const d = E.drag;
  if (!d) return;
  E.drag = null;
  if (d.moved) { renderCanvas(); updatePosInputs(); afterEdit(); }
}

// ---------------------------------------------------------------- 模板

function confirmDiscard() {
  return !isDirty() || confirm(`「${E.t.name}」有未保存的修改，继续的话这些修改会丢失。继续吗？`);
}

function loadTemplate(t) {
  E.t = clone(t);
  E.saved = t.id ? snapshot(E.t) : '';
  E.fields = {...(t.fields || {})};
  E.selId = null;
  E.undo = [];
  E.redo = [];
  renderAll();
}

function selectTemplate(id) {
  if (E.t && E.t.id === id) return;
  if (!confirmDiscard()) return;
  const t = E.templates.find(x => x.id === id);
  if (!t) return;
  try { localStorage.setItem('lastTemplate', id); } catch (err) { /* 无痕模式 */ }
  loadTemplate(t);
}

function newTemplate() {
  if (!confirmDiscard()) return;
  let name = '新模板', i = 2;
  while (E.templates.some(t => t.name === name)) name = `新模板 ${i++}`;
  const size = E.t ? [...E.t.size] : [...(E.sizes[0] || [25, 15])];
  const w = r2(size[0] - 2);
  loadTemplate({id: null, name, size, serial: 1, serial_step: 1, fields: {}, elements: [
    {id: newId(), type: 'text', x: 1, y: 1, w, text: '{品名}', pt: 11, bold: true, align: 'center', shrink: true},
    {id: newId(), type: 'text', x: 1, y: r2(1 + textH(11) + 0.5), w, text: '生产 {日期}', pt: 9, bold: false, align: 'center', shrink: true},
  ]});
  $('#f-name').focus();
  $('#f-name').select();
}

async function save(quiet = false) {
  if (!E.t) return false;
  const body = {...E.t, fields: pickFields()};
  try {
    const r = E.t.id ? await api('PUT', `/api/templates/${encodeURIComponent(E.t.id)}`, body)
                     : await api('POST', '/api/templates', body);
    const t = r.template;
    const i = E.templates.findIndex(x => x.id === t.id);
    if (i >= 0) E.templates[i] = t; else E.templates.push(t);
    const sel = E.selId;
    E.t = clone(t);
    E.saved = snapshot(E.t);
    E.selId = E.t.elements.some(x => x.id === sel) ? sel : null;
    try { localStorage.setItem('lastTemplate', t.id); } catch (err) { /* ignore */ }
    if (!quiet) toast('已保存', 'ok');
    renderAll();
    return true;
  } catch (err) {
    toast(`保存失败：${err.message}`, 'err');
    return false;
  }
}

async function saveAs() {
  if (!E.t) return;
  let name = `${E.t.name} 副本`, i = 2;
  while (E.templates.some(t => t.name === name)) name = `${E.t.name} 副本 ${i++}`;
  E.t = {...clone(E.t), id: null, name};
  await save();
}

async function deleteTemplate() {
  if (!E.t) return;
  if (E.t.id) {
    if (!confirm(`确定删除模板「${E.t.name}」？删除后不能恢复。`)) return;
    try { await api('DELETE', `/api/templates/${encodeURIComponent(E.t.id)}`); }
    catch (err) { toast(`删除失败：${err.message}`, 'err'); return; }
    E.templates = E.templates.filter(t => t.id !== E.t.id);
    toast('模板已删除', 'ok');
  }
  E.t = null;
  E.saved = '';
  if (E.templates.length) selectTemplate(E.templates[0].id); else renderAll();
}

// ---------------------------------------------------------------- 打印

async function doPrint() {
  if (!E.t || E.printing) return;
  const why = printBlock();
  if (why) { toast(printBlockedHint(why), 'err'); return; }
  const fields = pickFields();
  const miss = Object.keys(fields).filter(k => !String(fields[k]).trim());
  if (miss.length) {
    toast(`请先填写：${miss.join('、')}`, 'err');
    const el = document.querySelector(`[data-field="${CSS.escape(miss[0])}"]`);
    if (el) el.focus();
    return;
  }
  E.printing = true;
  renderFoot();
  try {
    if (isDirty() && !(await save(true))) return;
    afterPrint(await api('POST', '/api/print', {template: E.t.id, fields, copies: E.copies}));
  } catch (err) {
    toast(`打印失败：${err.message}`, 'err');
    pollStatus();
  } finally {
    E.printing = false;
    renderFoot();
  }
}

function afterPrint(r) {
  E.t.serial = r.serial_next;
  E.saved = snapshot(E.t);
  const i = E.templates.findIndex(x => x.id === E.t.id);
  if (i >= 0) E.templates[i].serial = r.serial_next;
  toast(`已打印 ${r.printed} 张（${CHANNEL[r.channel] || r.channel}）`, 'ok');
  renderCanvas();
  renderFields(true);
  renderFoot();
  renderList();
  schedulePreview();
}

function parseCSV(text) {
  const rows = [];
  let row = [], cell = '', q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"') { if (text[i + 1] === '"') { cell += '"'; i++; } else q = false; }
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === ',' || c === '\t') { row.push(cell); cell = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && text[i + 1] === '\n') i++;
      row.push(cell); rows.push(row); row = []; cell = '';
    } else cell += c;
  }
  if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
  return rows.filter(r => r.some(c => c.trim()));
}

async function batchFromFile(file) {
  const buf = await file.arrayBuffer();
  let text;
  try { text = new TextDecoder('utf-8', {fatal: true}).decode(buf); } catch (err) { text = new TextDecoder('gbk').decode(buf); }
  const rows = parseCSV(text.replace(/^﻿/, ''));
  if (rows.length < 2) { toast('表格里没有数据行（第一行要是表头）', 'err'); return; }
  const head = rows[0].map(h => h.trim());
  const names = fieldNames(E.t);
  const copiesCol = head.findIndex(h => ['份数', '数量', 'copies'].includes(h.toLowerCase()));
  const matched = head.filter(h => names.includes(h));
  const unused = head.filter((h, i) => i !== copiesCol && h && !names.includes(h));
  const missing = names.filter(n => !head.includes(n));
  const base = pickFields();
  const data = rows.slice(1).map(r => {
    const fields = {...base};
    head.forEach((h, i) => { if (names.includes(h)) fields[h] = (r[i] || '').trim(); });
    return {fields, copies: copiesCol >= 0 ? Math.max(1, parseInt(r[copiesCol], 10) || 1) : 1};
  });
  const total = data.reduce((a, d) => a + d.copies, 0);
  const first = E.t.elements.filter(e => e.type === 'text').map(e => expand(e.text, data[0].fields, E.t.serial)).filter(s => s.trim()).join(' / ');
  $('#batch-summary').innerHTML = `
    <p>模板「<b>${esc(E.t.name)}</b>」· <b>${data.length}</b> 行 · 合计 <b>${total}</b> 张</p>
    <p>对上的列：${matched.length ? matched.map(esc).join('、') : '<span class="dirty">没有（表头要和变量名一样）</span>'}${copiesCol >= 0 ? '；份数列：' + esc(head[copiesCol]) : ''}</p>
    ${missing.length ? `<p>表格里没有的变量：${missing.map(n => `${esc(n)}（用当前填写的「${esc(base[n] || '空')}」）`).join('、')}</p>` : ''}
    ${unused.length ? `<p class="muted">没用到的列：${unused.map(esc).join('、')}</p>` : ''}
    <p class="muted">第一张会打成：${esc(first)}</p>`;
  $('#batch-ok').disabled = !matched.length && names.length > 0;
  showDialog('#dlg-batch', async () => {
    const why = printBlock();
    if (why) { toast(why, 'err'); return; }
    try {
      if (isDirty() && !(await save(true))) return;
      toast(`正在打印 ${total} 张…`);
      afterPrint(await api('POST', '/api/print', {template: E.t.id, rows: data}));
    } catch (err) { toast(`批量打印失败：${err.message}`, 'err'); }
  });
}

// ---------------------------------------------------------------- 键盘

function onKey(ev) {
  if (currentView() !== 'design' || document.querySelector('dialog[open]')) return;
  const typing = ev.target.matches('input, textarea, select');
  const ctrl = ev.ctrlKey || ev.metaKey;
  if (ctrl && !ev.altKey) {
    const key = ev.key.toLowerCase();
    if (key === 's') { ev.preventDefault(); save(); }
    else if (key === 'p') { ev.preventDefault(); doPrint(); }
    else if (!typing && key === 'z') { ev.preventDefault(); if (ev.shiftKey) redo(); else undo(); }
    else if (!typing && key === 'y') { ev.preventDefault(); redo(); }
    else if (!typing && key === 'd') { ev.preventDefault(); duplicateEl(); }
    return;
  }
  if (ev.key === 'Enter' && ev.target.matches('[data-field]')) { ev.preventDefault(); doPrint(); return; }
  if (typing || !selEl()) return;
  if (ev.key === 'Delete' || ev.key === 'Backspace') { ev.preventDefault(); deleteEl(); }
  else if (ev.key === 'Escape') { E.selId = null; renderCanvas(); renderProps(); renderFoot(); }
  else if (ev.key.startsWith('Arrow')) {
    ev.preventDefault();
    if (!ev.repeat) pushHistory();
    const s = ev.shiftKey ? 2 : ev.altKey ? 0.1 : 0.5;
    nudge(ev.key === 'ArrowLeft' ? -s : ev.key === 'ArrowRight' ? s : 0, ev.key === 'ArrowUp' ? -s : ev.key === 'ArrowDown' ? s : 0);
  }
}

// ---------------------------------------------------------------- 绑定

function wire() {
  $('#btn-new').onclick = newTemplate;
  $('#btn-new2').onclick = newTemplate;
  $('#btn-save').onclick = () => save();
  $('#btn-saveas').onclick = saveAs;
  $('#btn-deltpl').onclick = deleteTemplate;
  $('#btn-print').onclick = doPrint;
  $('#btn-batch').onclick = () => {
    const why = printBlock();
    if (why) { toast(why, 'err'); return; }
    $('#file-batch').value = '';
    $('#file-batch').click();
  };
  $('#file-batch').onchange = ev => { if (ev.target.files[0]) batchFromFile(ev.target.files[0]); };

  $('#tpl-search').oninput = ev => { E.search = ev.target.value; renderList(); };
  $('#tpl-list').onclick = ev => { const b = ev.target.closest('.tpl'); if (b && b.dataset.id) selectTemplate(b.dataset.id); };

  $('#size-chips').onclick = ev => {
    const b = ev.target.closest('[data-size]');
    if (!b || !E.t || b.dataset.size === sizeKey(E.t.size)) return;
    pushHistory();
    E.t.size = b.dataset.size.split('x').map(Number);
    renderAll();
    if ($$('.el.out', $('#canvas')).length) toast('有元素超出了新尺寸（红色虚线框），拖回标签里或调整大小', 'err');
  };
  $('#size-chips').oncontextmenu = async ev => {
    const b = ev.target.closest('[data-size]');
    if (!b) return;
    ev.preventDefault();
    if (!E.sizes.some(s => sizeKey(s) === b.dataset.size)) return;
    if (!confirm(`从尺寸预设里删除 ${b.textContent}？（已用这个尺寸的模板不受影响）`)) return;
    try { E.sizes = (await api('DELETE', `/api/presets/${b.dataset.size}`)).sizes; renderSizes(); }
    catch (err) { toast(err.message, 'err'); }
  };
  $('#btn-custom-size').onclick = () => {
    if (!E.t) return;
    $('#cs-w').value = E.t.size[0];
    $('#cs-h').value = E.t.size[1];
    showDialog('#dlg-size', async () => {
      const w = Number($('#cs-w').value), h = Number($('#cs-h').value);
      if (!(w >= 10 && w <= 50 && h >= 8 && h <= 200)) { toast('尺寸范围：宽 10~50mm，高 8~200mm', 'err'); return; }
      pushHistory();
      E.t.size = [w, h];
      if ($('#cs-save').checked) {
        try { E.sizes = (await api('POST', '/api/presets', {size: [w, h]})).sizes; } catch (err) { toast(err.message, 'err'); }
      }
      renderAll();
    });
  };

  const cv = $('#canvas');
  cv.addEventListener('pointerdown', onPointerDown);
  cv.addEventListener('pointermove', onPointerMove);
  cv.addEventListener('pointerup', onPointerUp);
  cv.addEventListener('pointercancel', onPointerUp);
  cv.addEventListener('dblclick', ev => {
    if (!ev.target.closest('.el')) return;
    const inp = $('#el-props [data-prop="text"], #el-props [data-prop="data"]');
    if (inp) { inp.focus(); inp.select(); }
  });
  $('#stage').addEventListener('pointerdown', ev => {  // 点画布外面的空白处取消选中
    if (ev.target === ev.currentTarget && E.selId) { E.selId = null; renderCanvas(); renderProps(); renderFoot(); }
  });

  $('#t-text').onclick = addText;
  $('#t-qr').onclick = addQR;
  $('#t-line').onclick = addLine;
  $('#t-box').onclick = addBox;
  $('#t-dup').onclick = duplicateEl;
  $('#t-del').onclick = deleteEl;
  $('#t-undo').onclick = undo;
  $('#t-redo').onclick = redo;
  $('#t-ch').onclick = () => centerEl('h');
  $('#t-cv').onclick = () => centerEl('v');

  $('#f-name').addEventListener('focus', pushHistory);
  $('#f-name').oninput = ev => { E.t.name = ev.target.value; renderList(); renderFoot(); };

  const props = $('#el-props');
  props.addEventListener('focusin', ev => {
    if (!ev.target.matches('input')) return;
    pushHistory();
    if (ev.target.matches('[data-prop="text"], [data-prop="data"]')) E.lastInput = ev.target;
  });
  props.addEventListener('input', ev => {
    const inp = ev.target, p = inp.dataset.prop, e = selEl();
    if (!p || !e) return;
    if (p === 'text' || p === 'data') e[p] = inp.value;
    else if (p === 'shrink') e.shrink = inp.checked;
    else {
      const v = parseFloat(inp.value);
      if (!Number.isFinite(v)) return;
      e[p] = clampProp(p, v);
    }
    afterEdit();
  });
  props.addEventListener('click', ev => {
    const e = selEl();
    const b = ev.target.closest('button');
    if (!b || !e) return;
    if (b.dataset.insert) { insertToken(b.dataset.insert); return; }
    if ('newvar' in b.dataset) {
      $('#nv-name').value = '';
      showDialog('#dlg-var', () => {
        const n = $('#nv-name').value.trim().replace(/[{}:]/g, '');
        if (n) insertToken(`{${n}}`);
      });
      return;
    }
    pushHistory();
    if (b.dataset.step) e.pt = clampProp('pt', e.pt + Number(b.dataset.d));
    else if (b.dataset.toggle === 'bold') e.bold = !e.bold;
    else if (b.dataset.align) e.align = b.dataset.align;
    else return;
    renderProps();
    afterEdit();
  });

  $('#fields').addEventListener('input', ev => {
    const inp = ev.target;
    if (inp.dataset.field !== undefined) E.fields[inp.dataset.field] = inp.value;
    else if (inp.id === 'f-serial') {
      const v = parseInt(inp.value, 10);
      if (!(v >= 0)) return;
      E.t.serial = v;
    } else return;
    renderCanvas();
    updateConv();
    renderList();
    renderFoot();
    schedulePreview();
  });

  $('#c-dec').onclick = () => { E.copies = Math.max(1, E.copies - 1); renderFoot(); };
  $('#c-inc').onclick = () => { E.copies = Math.min(500, E.copies + 1); renderFoot(); };
  $('#copies').onchange = ev => { E.copies = Math.max(1, Math.min(500, parseInt(ev.target.value, 10) || 1)); renderFoot(); };

  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', () => { if (E.t && currentView() === 'design') renderCanvas(); });
  STATUS.listeners.push(renderFoot);
}

const EDITOR = {
  isDirty,
  onShow() { if (E.t) renderCanvas(); },
  async init() {
    wire();
    renderAll();
    try {
      const [p, ts] = await Promise.all([api('GET', '/api/presets'), api('GET', '/api/templates')]);
      E.sizes = p.sizes;
      E.templates = ts.templates;
      let last = null;
      try { last = localStorage.getItem('lastTemplate'); } catch (err) { /* ignore */ }
      const first = E.templates.find(t => t.id === last) || E.templates[0];
      if (first) selectTemplate(first.id); else newTemplate();
    } catch (err) {
      toast(`连接打印服务失败：${err.message}`, 'err');
    }
  },
};
