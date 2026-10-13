'use strict';
/* 打印机校准页: 放纸 / 位置校准 (X/Y 偏移) / 打印机自身参数 */

const PRINTERV = (() => {
  let inited = false, saved = [0, 0], step = 0.5, spec = null;
  const r1 = v => Math.round(v * 10) / 10;
  const fmt = v => (v > 0 ? '+' : '') + r1(v).toFixed(1);

  const cur = () => [r1(Number($('#cal-x').value) || 0), r1(Number($('#cal-y').value) || 0)];
  const size = () => $('#cal-size').value.split('x').map(Number);

  let pvTimer;
  function refreshPreview() {
    clearTimeout(pvTimer);
    pvTimer = setTimeout(() => {
      const [w, h] = size(), [x, y] = cur();
      $('#cal-img').src = `/api/calibrate/preview?w=${w}&h=${h}&x=${x}&y=${y}&scale=3&_=${Date.now()}`;
      $('#cal-img').style.width = `${Math.min(340, w * 12)}px`;
    }, 120);
  }

  function renderSaved() {
    const [x, y] = cur(), same = x === saved[0] && y === saved[1];
    $('#cal-saved').innerHTML = `已保存的默认偏移：X <b>${fmt(saved[0])}</b> mm，Y <b>${fmt(saved[1])}</b> mm`
      + (same ? '' : '　<span class="dirty">（当前调整还没保存）</span>');
  }

  function setOffset(x, y) {
    const lim = v => Math.max(-10, Math.min(10, r1(v)));
    $('#cal-x').value = lim(x);
    $('#cal-y').value = lim(y);
    renderSaved();
    refreshPreview();
  }

  // 打印走右上角选的连接方式 (USB / 蓝牙)
  async function printCal() {
    const why = printBlocked();
    if (why) { toast(printBlockedHint(why), 'err'); return; }
    const btn = $('#cal-print');
    btn.disabled = true;
    if (connMode() === 'bt') toast('正在通过蓝牙发送…');
    try {
      await api('POST', '/api/calibrate/print', {size: size(), offset: cur()});
      const [x, y] = cur();
      toast(`已打印校准标签（X ${fmt(x)}，Y ${fmt(y)}）`, 'ok');
    } catch (e) { toast(`打印失败：${e.message}`, 'err'); }
    finally { btn.disabled = false; }
  }

  async function saveOffset() {
    try {
      const {settings} = await api('POST', '/api/settings', {offset: cur()});
      saved = settings.offset;
      renderSaved();
      toast('已保存，之后所有打印都会按这个偏移', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  }

  async function feed() {
    const why = printBlocked();
    if (why) { toast(printBlockedHint(why), 'err'); return; }
    try { await api('POST', '/api/printer/feed', {}); toast('已走纸到下一张标签开头', 'ok'); }
    catch (e) { toast(e.message, 'err'); }
  }

  // ---------------------------------------------------------------- 打印机参数

  function fillSelect(sel, options, value) {
    if (!options.some(([v]) => v === value)) options.push([value, `${value}（当前）`]);
    sel.innerHTML = options.map(([v, t]) => `<option value="${v}"${v === value ? ' selected' : ''}>${esc(t)}</option>`).join('');
  }

  function renderParams(p) {
    $('#pp-model').textContent = p.model;
    fillSelect($('#pp-paper_type'), Object.entries(spec.paper_type.choices).map(([v, t]) => [Number(v), t]), p.paper_type);
    fillSelect($('#pp-density'), Object.entries(spec.density.choices).map(([v, t]) => [Number(v), t]), p.density);
    fillSelect($('#pp-sleep_sec'), [60, 120, 180, 300, 600].map(v => [v, `${v / 60} 分钟没动作后休眠`]), p.sleep_sec);
    fillSelect($('#pp-poweroff_min'), [10, 20, 30, 60, 90, 120].map(v => [v, `${v} 分钟没动作后关机`]), p.poweroff_min);
    $('#pp-form').hidden = false;
    $('#pp-learn').disabled = false;
  }

  async function busy(btn, msg, fn) {
    const all = ['#pp-read', '#pp-save', '#pp-learn'].map(s => $(s));
    all.forEach(b => { b.disabled = true; });
    $('#pp-msg').textContent = msg;
    try { await fn(); }
    catch (e) { toast(e.message, 'err'); $('#pp-msg').textContent = ''; }
    finally {
      $('#pp-read').disabled = false;
      $('#pp-save').disabled = false;
      $('#pp-learn').disabled = $('#pp-form').hidden;
    }
  }

  const readParams = () => busy($('#pp-read'), '正在经蓝牙读取…', async () => {
    const r = await api('GET', '/api/printer/params');
    spec = r.spec;
    renderParams(r.params);
    $('#pp-msg').textContent = '已读取';
  });

  const saveParams = () => busy($('#pp-save'), '正在写入打印机…', async () => {
    const body = {};
    document.querySelectorAll('#pp-form [data-param]').forEach(s => { body[s.dataset.param] = Number(s.value); });
    if (body.paper_type !== 2 && !confirm('纸张类型不是「标签纸」，用不干胶标签时会定位错乱。确定要改吗？')) {
      $('#pp-msg').textContent = '';
      return;
    }
    const r = await api('POST', '/api/printer/params', body);
    renderParams(r.params);
    $('#pp-msg').textContent = '已保存到打印机（读回确认无误）';
    toast('打印机参数已保存', 'ok');
  });

  const learn = () => busy($('#pp-learn'), '已发送…', async () => {
    await api('POST', '/api/printer/learn', {});
    $('#pp-msg').textContent = '已发送纸张学习指令（打印机会响一声）';
  });

  // ---------------------------------------------------------------- 初始化

  async function load() {
    inited = true;
    try {
      const [{settings}, {sizes}] = await Promise.all([api('GET', '/api/settings'), api('GET', '/api/presets')]);
      saved = settings.offset || [0, 0];
      $('#cal-size').innerHTML = sizes.map(s => `<option value="${s[0]}x${s[1]}">${s[0]} × ${s[1]} mm</option>`).join('');
      setOffset(saved[0], saved[1]);
    } catch (e) { toast(e.message, 'err'); }
  }

  function init() {
    $('#cal-size').onchange = refreshPreview;
    $('#cal-x').oninput = () => { renderSaved(); refreshPreview(); };
    $('#cal-y').oninput = () => { renderSaved(); refreshPreview(); };
    document.querySelectorAll('#view-printer [data-nudge]').forEach(b => {
      b.onclick = () => {
        const [axis, dir] = b.dataset.nudge.split(':');
        const [x, y] = cur(), d = step * Number(dir);
        setOffset(axis === 'x' ? x + d : x, axis === 'y' ? y + d : y);
      };
    });
    document.querySelectorAll('#view-printer [data-step]').forEach(b => {
      b.onclick = () => {
        step = Number(b.dataset.step);
        document.querySelectorAll('#view-printer [data-step]').forEach(x => x.classList.toggle('on', x === b));
      };
    });
    $('#cal-print').onclick = printCal;
    $('#cal-save').onclick = saveOffset;
    $('#cal-reset').onclick = () => setOffset(saved[0], saved[1]);
    $('#pr-feed').onclick = feed;
    $('#pp-read').onclick = readParams;
    $('#pp-save').onclick = saveParams;
    $('#pp-learn').onclick = learn;
  }

  return {
    init,
    show() { if (!inited) load(); else refreshPreview(); },
  };
})();
