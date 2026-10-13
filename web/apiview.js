'use strict';
/* API 与密钥页: 服务设置 / 密钥管理 / 调用记录 / 接口文档 四个子页签 */

const APIV = (() => {
  let inited = false, current = null, callsTimer = null, templates = [];

  function baseUrl() {
    const s = STATUS.data && STATUS.data.server;
    if (!s) return `${location.protocol}//${location.host}`;
    return s.lan && s.addresses.length ? `http://${s.addresses[0]}:${s.port}` : `http://127.0.0.1:${s.port}`;
  }

  // ---------------------------------------------------------------- 服务设置

  function renderService(st) {
    $$('.base').forEach(el => { el.textContent = baseUrl(); });
    if (!st) return;
    const s = st.server;
    $('#addr-local').textContent = `http://127.0.0.1:${s.port}`;
    $('#addr-lan').innerHTML = s.addresses.length
      ? s.addresses.map((ip, i) => `<code${s.lan ? '' : ' style="opacity:.55"'}>http://${esc(ip)}:${s.port}</code>`
        + `<button class="copy" data-copy-text="http://${esc(ip)}:${s.port}">复制</button>${i === 0 && s.addresses.length > 1 ? '<span class="muted">（主网卡）</span>' : ''}`).join(' ')
        + (s.lan ? '' : ' <span class="muted">（开启局域网访问后可用）</span>')
      : '<span class="muted">没有检测到局域网网卡</span>';
    $('#lan-switch').setAttribute('aria-checked', String(s.lan));
    $('#ver').textContent = `版本 ${s.version} · 端口 ${s.port} · 监听 ${s.listen}`;
  }

  async function waitForServer(ms = 12000) {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      await new Promise(r => setTimeout(r, 500));
      try { await api('GET', '/api/ping'); return true; } catch (e) { /* 还在重启 */ }
    }
    return false;
  }

  async function toggleLan() {
    const want = $('#lan-switch').getAttribute('aria-checked') !== 'true';
    if (want && !confirm('开启后，同一局域网内拿到密钥的设备都能调用打印。\n如果 Windows 防火墙弹窗，请选择「允许」。\n\n确定开启局域网访问？')) return;
    try {
      const r = await api('POST', '/api/settings', {lan: want});
      if (r.restarting) {
        const ov = overlay('正在切换监听地址…');
        const ok = await waitForServer();
        ov.remove();
        if (!ok) { toast('服务重启超时，请稍后刷新', 'err'); return; }
      }
      await pollStatus();
      const lan = STATUS.data && STATUS.data.server.lan;
      if (want && !lan) toast('开启失败（端口可能被占用），已退回仅本机模式', 'err');
      else toast(lan ? '已开启局域网访问' : '已关闭局域网访问，只有本机能用', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  }

  // ---------------------------------------------------------------- 密钥

  const fmtTime = s => (s ? s.replace('T', ' ') : '从未');

  async function loadKeys() {
    try {
      const {keys} = await api('GET', '/api/keys');
      $('#key-rows').innerHTML = keys.length ? keys.map(k => `<tr>
          <td>${esc(k.name)}</td>
          <td class="nowrap"><code>${esc(k.prefix)}…${esc(k.suffix)}</code></td>
          <td class="nowrap">${esc(fmtTime(k.created))}</td>
          <td class="nowrap">${esc(fmtTime(k.last_used))}</td>
          <td>${k.calls || 0}</td>
          <td><button class="danger-link" data-revoke="${esc(k.id)}" data-name="${esc(k.name)}">撤销</button></td>
        </tr>`).join('')
        : '<tr><td colspan="6" class="empty">还没有密钥。局域网或其他程序要调用 API，先在上面生成一个。</td></tr>';
    } catch (e) { toast(`读取密钥失败：${e.message}`, 'err'); }
  }

  async function createKey(ev) {
    ev.preventDefault();
    const name = $('#key-name').value.trim();
    if (!name) { toast('先给密钥起个名字，方便以后区分', 'err'); $('#key-name').focus(); return; }
    try {
      const {key} = await api('POST', '/api/keys', {name});
      $('#nk-name').textContent = key.name;
      $('#nk-key').textContent = key.key;
      $('#key-name').value = '';
      $('#dlg-key').showModal();
      loadKeys();
    } catch (e) { toast(`生成失败：${e.message}`, 'err'); }
  }

  async function revokeKey(id, name) {
    if (!confirm(`撤销密钥「${name}」？\n撤销后用这个密钥的设备会立刻无法调用。`)) return;
    try { await api('DELETE', `/api/keys/${id}`); toast('已撤销', 'ok'); loadKeys(); }
    catch (e) { toast(e.message, 'err'); }
  }

  // ---------------------------------------------------------------- 调用记录

  async function loadCalls() {
    try {
      const {calls} = await api('GET', '/api/log');
      $('#call-rows').innerHTML = calls.length ? calls.slice(0, 100).map(c => `<tr>
          <td class="nowrap">${esc(fmtTime(c.time))}</td>
          <td class="nowrap"><code>${esc(c.ip)}</code></td>
          <td>${esc(c.who)}</td>
          <td class="nowrap"><code>${esc(c.method)} ${esc(c.path)}</code></td>
          <td class="${c.status < 400 ? 'st-ok' : 'st-err'}">${c.status}</td>
        </tr>`).join('')
        : '<tr><td colspan="5" class="empty">暂无调用</td></tr>';
    } catch (e) { /* 服务重启中 */ }
  }

  // ---------------------------------------------------------------- 文档

  function renderVarTable() {
    const now = new Date(), plus = n => { const d = new Date(now); d.setDate(d.getDate() + n); return d; };
    const rows = [
      ['{日期}', fmtDT(now, 'yyyy-MM-dd'), '今天'],
      ['{日期:yyyy年MM月dd日}', fmtDT(now, 'yyyy年MM月dd日'), '冒号后面写格式：yyyy 年 / MM 月 / dd 日 / HH 时 / mm 分 / ss 秒'],
      ['{日期:MM/dd}', fmtDT(now, 'MM/dd'), ''],
      ['{日期+30天}', fmtDT(plus(30), 'yyyy-MM-dd'), '保质期、到期日；也可以写 -7天'],
      ['{时间}', fmtDT(now, 'HH:mm'), '当前时间，可加格式如 {时间:HH:mm:ss}'],
      ['{流水号}', '0025', '每打一张 +1，打印成功后保存在模板里，下次接着打'],
      ['{流水号:000000}', '000025', '按 0 的个数补位'],
      ['{品名}', '调用时 fields 里传的值', '花括号里写任意名字就是自定义变量'],
      ['{输入:备注}', '同上', '和 {备注} 等价'],
    ];
    $('#var-rows').innerHTML = rows.map(([a, b, c]) => `<tr><td class="nowrap"><code>${esc(a)}</code></td><td>${esc(b)}</td><td class="muted">${esc(c)}</td></tr>`).join('');
  }

  function addCopyButton(pre) {
    if (pre.querySelector(':scope > .copy')) return;
    const b = document.createElement('button');
    b.className = 'copy';
    b.type = 'button';
    b.textContent = '复制';
    b.onclick = () => copyText([...pre.childNodes].filter(n => n !== b).map(n => n.textContent).join(''));
    pre.appendChild(b);
  }

  async function loadTry() {
    try {
      templates = (await api('GET', '/api/templates')).templates;
      $('#try-tpl').innerHTML = templates.map(t => `<option value="${esc(t.id)}">${esc(t.name)}（${t.size[0]}×${t.size[1]}）</option>`).join('');
      renderTryFields();
    } catch (e) { toast(e.message, 'err'); }
  }

  function renderTryFields() {
    const t = templates.find(x => x.id === $('#try-tpl').value);
    if (!t) return;
    $('#try-fields').innerHTML = fieldNames(t).map(n => `<label class="fld"><span class="fld-k">${esc(n)}</span><input data-field="${esc(n)}" value="${esc(t.fields[n] || '')}"></label>`).join('')
      || '<div class="muted">这个模板没有需要填写的变量</div>';
    renderTryReq();
  }

  function tryBody() {
    const t = templates.find(x => x.id === $('#try-tpl').value);
    const fields = {};
    $$('#try-fields [data-field]').forEach(el => { fields[el.dataset.field] = el.value; });
    return {template: t ? t.name : '', fields, copies: 1};
  }

  function renderTryReq() {
    $('#try-req').textContent = `POST ${baseUrl()}/api/print\nAuthorization: Bearer hm_你的密钥\n\n${JSON.stringify(tryBody(), null, 2)}`;
    addCopyButton($('#try-req'));
  }

  async function tryPreview() {
    const b = tryBody();
    try {
      const blob = await api('POST', '/api/preview', {template: b.template, fields: b.fields, scale: 3});
      $('#try-img').innerHTML = `<img src="${URL.createObjectURL(blob)}" alt="接口生成的标签预览">`;
    } catch (e) { $('#try-img').innerHTML = `<span class="err">${esc(e.message)}</span>`; }
  }

  // ---------------------------------------------------------------- 子页签

  function show(sub) {
    if (!inited) init2();
    current = sub;
    $$('#view-api .stab').forEach(a => { const on = a.dataset.sub === sub; a.classList.toggle('on', on); a.setAttribute('aria-selected', String(on)); });
    $$('#view-api .apanel').forEach(p => { p.hidden = p.dataset.sub !== sub; });
    $('#ascroll').scrollTop = 0;
    clearInterval(callsTimer);
    if (sub === 'keys') loadKeys();
    if (sub === 'calls') { loadCalls(); callsTimer = setInterval(loadCalls, 5000); }
    if (sub === 'docs') { renderVarTable(); renderService(STATUS.data); loadTry(); }
    if (sub === 'service') renderService(STATUS.data);
  }

  function hide() { clearInterval(callsTimer); current = null; }

  // 第一次进入 API 页签时再加载
  function init2() { inited = true; }

  function init() {
    $('#lan-switch').onclick = toggleLan;
    $('#btn-stop').onclick = async () => {
      if (!confirm('停止后，界面和 API 都不能用了，需要再双击「启动标签打印」才能恢复。\n确定停止服务？')) return;
      try { await api('POST', '/api/admin/shutdown'); } catch (e) { /* 已断开 */ }
      overlay('打印服务已停止。<br>需要时双击程序目录里的「启动标签打印.bat」重新启动。');
    };
    $('#key-form').onsubmit = createKey;
    $('#key-rows').onclick = ev => { const b = ev.target.closest('[data-revoke]'); if (b) revokeKey(b.dataset.revoke, b.dataset.name); };
    document.addEventListener('click', ev => {
      const b = ev.target.closest('[data-copy-from],[data-copy-text]');
      if (b) copyText(b.dataset.copyText ?? $(b.dataset.copyFrom).textContent);
    });
    $$('.codetabs [data-code]').forEach(b => {
      b.onclick = () => {
        $$('.codetabs [data-code]').forEach(x => x.classList.toggle('on', x === b));
        $$('[data-codepanel]').forEach(p => { p.hidden = p.dataset.codepanel !== b.dataset.code; });
      };
    });
    $$('.dtoc [data-goto]').forEach(b => {
      b.onclick = () => { const h = document.getElementById(b.dataset.goto); if (h) h.scrollIntoView({behavior: 'smooth', block: 'start'}); };
    });
    $$('#view-api pre.code').forEach(addCopyButton);
    $('#try-tpl').onchange = renderTryFields;
    $('#try-fields').oninput = renderTryReq;
    $('#try-go').onclick = tryPreview;
    STATUS.listeners.push(st => { if (current === 'service' || current === 'docs') renderService(st); });
  }

  return {init, show, hide};
})();
