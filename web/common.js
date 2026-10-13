'use strict';
/* 两个页签共用: 请求 / 提示 / 变量规则 / 打印机状态 */

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const pad2 = n => String(n).padStart(2, '0');
const PT_MM = 25.4 / 72;   // 1pt = 0.3528mm
const LINE = 1.25;         // 文本框高 = 字号 × 1.25 (和 core.py 一致)
const LABEL_FONT = '"Microsoft YaHei","微软雅黑","PingFang SC","Noto Sans SC",sans-serif';

async function api(method, path, body) {
  const opt = {method, headers: {}};
  if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  const r = await fetch(path, opt);
  const ct = r.headers.get('Content-Type') || '';
  if (r.ok && ct.startsWith('image/')) return r.blob();
  const j = await r.json().catch(() => ({ok: false, error: `HTTP ${r.status}`}));
  if (!r.ok || j.ok === false) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

let toastTimer;
function toast(msg, kind = '') {
  const el = $('#toast');
  el.textContent = msg;
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast'; }, kind === 'err' ? 4500 : 2500);
}

function overlay(html) {
  let el = $('#overlay');
  if (!el) { el = document.createElement('div'); el.id = 'overlay'; el.className = 'overlay'; document.body.appendChild(el); }
  el.innerHTML = `<div>${html}</div>`;
  return el;
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  toast('已复制', 'ok');
}

function showDialog(id, onOk) {
  const dlg = $(id);
  dlg.returnValue = '';
  dlg.onclose = () => { if (dlg.returnValue === 'ok') onOk(); };
  dlg.showModal();
}

// ---------------------------------------------------------------- 变量 (规则和 core.py 一致)

function parseVar(body) {
  const i = body.indexOf(':');
  return i < 0 ? [body.trim(), ''] : [body.slice(0, i).trim(), body.slice(i + 1)];
}

function fmtDT(d, f) {
  return f.replaceAll('yyyy', d.getFullYear()).replaceAll('MM', pad2(d.getMonth() + 1)).replaceAll('dd', pad2(d.getDate()))
    .replaceAll('HH', pad2(d.getHours())).replaceAll('mm', pad2(d.getMinutes())).replaceAll('ss', pad2(d.getSeconds()));
}

function expandVar(m, body, fields, serial, now) {
  let [h, f] = parseVar(body);
  const mo = h.match(/^日期([+-]\d+)天$/);
  if (h === '日期' || mo) {
    const d = new Date(now);
    if (mo) d.setDate(d.getDate() + Number(mo[1]));
    return fmtDT(d, f || 'yyyy-MM-dd');
  }
  if (h === '时间') return fmtDT(now, f || 'HH:mm');
  if (h === '流水号') return String(serial).padStart(f ? f.length : 4, '0');
  if (h === '输入') h = f.trim();
  const v = fields[h];
  return v === undefined || v === null || String(v) === '' ? null : String(v);
}

function expand(text, fields, serial) {
  const now = new Date();
  return text.replace(/\{([^{}]+)\}/g, (m, body) => expandVar(m, body, fields, serial, now) ?? m);
}

// 同 expand, 但变量部分包一层 <span class="tok">, 画布上用来标出哪些是变量
function expandHTML(text, fields, serial) {
  const now = new Date();
  let out = '', last = 0;
  for (const m of text.matchAll(/\{([^{}]+)\}/g)) {
    out += esc(text.slice(last, m.index));
    out += `<span class="tok">${esc(expandVar(m[0], m[1], fields, serial, now) ?? m[0])}</span>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

const AUTO_RE = /^(日期([+-]\d+天)?|时间|流水号)$/;
const textsOf = t => t.elements.filter(e => e.type === 'text' || e.type === 'qr').map(e => (e.type === 'text' ? e.text : e.data) || '');

function varsOf(t) {
  const out = [];
  for (const text of textsOf(t)) {
    for (const m of text.matchAll(/\{([^{}]+)\}/g)) {
      let [h, f] = parseVar(m[1]);
      if (h === '输入') h = f.trim();
      if (!h) continue;
      if (AUTO_RE.test(h)) { if (!out.some(v => v.auto && v.raw === m[0])) out.push({auto: true, raw: m[0], name: h}); }
      else if (!out.some(v => !v.auto && v.name === h)) out.push({auto: false, name: h});
    }
  }
  return out;
}
const fieldNames = t => varsOf(t).filter(v => !v.auto).map(v => v.name);
const usesSerial = t => textsOf(t).some(s => s.includes('{流水号'));

// ---------------------------------------------------------------- 打印机状态 / 连接方式 (所有页签共用一份)

const STATUS = {data: null, listeners: [], btChecking: false};

// 当前连接方式: 'bt' 蓝牙, 其余都按 USB
const connMode = () => (STATUS.data && STATUS.data.server.channel === 'bt' ? 'bt' : 'usb');

// 能不能打印, 不能就返回原因 (界面上所有打印按钮都用它)
function printBlocked() {
  const st = STATUS.data;
  if (!st) return '打印服务未连接';
  if (connMode() === 'bt') return st.printer.bt.state === 'fail' ? '蓝牙连不上' : '';
  return st.printer.online ? '' : '打印机未连接';
}

function printBlockedHint(why) {
  if (why === '蓝牙连不上') return '打印机没开机，或蓝牙正被手机连着；处理后点右上角状态重新检测';
  if (why === '打印机未连接') return '请插好 USB 线并打开打印机，或在右上角切换到「蓝牙」';
  return why;
}

function renderStatus() {
  const st = STATUS.data, pp = $('#pill-printer'), lp = $('#pill-lan'), mode = connMode();
  $$('.conn-seg [data-conn]').forEach(b => {
    const on = (b.dataset.conn === 'bt') === (mode === 'bt');
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', String(on));
  });
  let cls = 'pill err', text = '打印服务没有运行';
  if (st && mode === 'bt') {
    const b = st.printer.bt;
    if (STATUS.btChecking) [cls, text] = ['pill warn', '蓝牙检测中…'];
    else if (b.state === 'ok') [cls, text] = ['pill ok', `蓝牙已连接 · ${st.printer.bt_port}`];
    else if (b.state === 'fail') [cls, text] = ['pill err', '蓝牙连不上 · 点击重试'];
    else [cls, text] = ['pill warn', '蓝牙 · 点击检测'];
  } else if (st) {
    [cls, text] = st.printer.online ? ['pill ok', '打印机已连接 · USB'] : ['pill err', 'USB 未连接'];
  }
  pp.className = cls;
  pp.lastElementChild.textContent = text;
  pp.title = st && mode === 'bt' && st.printer.bt.message ? `${st.printer.bt.message}（点击重新检测）` : '点击重新检测';
  lp.className = st && st.server.lan ? 'pill ok' : 'pill';
  lp.lastElementChild.innerHTML = !st ? 'API 不可用'
    : st.server.lan ? `API 局域网<span class="wide"> · ${esc(st.server.addresses[0] || '')}:${st.server.port}</span>` : 'API 仅本机';
  lp.title = st && st.server.lan ? `局域网可用密钥调用：http://${st.server.addresses[0] || ''}:${st.server.port}` : '';
}

async function pollStatus() {
  try { STATUS.data = await api('GET', '/api/status'); } catch (e) { STATUS.data = null; }
  renderStatus();
  STATUS.listeners.forEach(f => f(STATUS.data));
}

async function checkBluetooth() {
  if (STATUS.btChecking) return;
  STATUS.btChecking = true;
  renderStatus();
  try { await api('POST', '/api/printer/bt-check'); } catch (e) { /* 结果在状态里 */ }
  STATUS.btChecking = false;
  await pollStatus();
  const b = STATUS.data && STATUS.data.printer.bt;
  if (b) toast(b.message, b.state === 'ok' ? 'ok' : 'err');
}

async function setConnMode(mode) {
  if (mode === connMode()) return;
  try {
    await api('POST', '/api/settings', {channel: mode === 'bt' ? 'bt' : 'auto'});
    await pollStatus();
    if (mode === 'bt') checkBluetooth();
    else toast('已切换到 USB 连接', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

function wireStatus() {
  $$('.conn-seg [data-conn]').forEach(b => { b.onclick = () => setConnMode(b.dataset.conn === 'bt' ? 'bt' : 'usb'); });
  $('#pill-printer').onclick = () => (connMode() === 'bt' ? checkBluetooth() : pollStatus());
}
