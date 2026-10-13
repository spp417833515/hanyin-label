'use strict';
/* 设置页: 开机启动 / 退出程序 / 打印机连接 (蓝牙串口) / 数据与版本 */

const SETTINGSV = (() => {
  let current = null;

  function render(s) {
    current = s;
    $('#auto-switch').setAttribute('aria-checked', String(!!s.autostart));
    $('#set-queue').textContent = s.queue;
    $('#set-version').textContent = `${s.version}${s.installed ? '' : '（开发版）'}`;
    $('#set-app-dir').textContent = s.app_dir;
    $('#set-data-dir').textContent = s.data_dir;
    renderUsb();
  }

  function renderUsb() {
    const st = STATUS.data;
    $('#set-usb').textContent = !st ? '' : st.printer.online ? '· USB 已连接' : '· USB 未连接';
  }

  async function loadPorts() {
    try {
      const {ports, current: cur} = await api('GET', '/api/printer/ports');
      const opts = ports.map(p => `<option value="${esc(p.port)}"${p.port === cur ? ' selected' : ''}>${esc(p.port)}${p.bluetooth ? ' · 蓝牙' : ''} — ${esc(p.name.replace(/\s*\(COM\d+\)/, ''))}</option>`);
      if (!ports.some(p => p.port === cur)) opts.unshift(`<option value="${esc(cur)}" selected>${esc(cur)}（当前设置，系统里没找到）</option>`);
      $('#set-bt-port').innerHTML = opts.join('') || '<option value="">没有找到串口</option>';
    } catch (e) { toast(`读取串口失败：${e.message}`, 'err'); }
  }

  async function load() {
    try { render((await api('GET', '/api/settings')).settings); } catch (e) { toast(e.message, 'err'); }
    loadPorts();
  }

  async function toggleAutostart() {
    const want = $('#auto-switch').getAttribute('aria-checked') !== 'true';
    try {
      const {settings} = await api('POST', '/api/settings', {autostart: want});
      render(settings);
      toast(settings.autostart ? '已开启开机自动启动（在右下角托盘运行）' : '已关闭开机自动启动', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  }

  async function saveBtPort() {
    const port = $('#set-bt-port').value;
    if (!port) return;
    try {
      render((await api('POST', '/api/settings', {bt_port: port})).settings);
      await pollStatus();
      await checkBluetooth();
    } catch (e) { toast(e.message, 'err'); }
  }

  async function quitApp() {
    if (!confirm('退出后，标签打印和局域网 API 都会停止。\n确定退出程序？')) return;
    try { await api('POST', '/api/admin/shutdown'); } catch (e) { /* 已断开 */ }
    overlay('程序已退出，可以关闭这个窗口。<br>需要时双击桌面上的「汉印标签打印」重新打开。');
  }

  function init() {
    $('#auto-switch').onclick = toggleAutostart;
    $('#set-bt-save').onclick = saveBtPort;
    $('#set-bt-refresh').onclick = loadPorts;
    $('#set-open-data').onclick = () => api('POST', '/api/admin/open-data').catch(e => toast(e.message, 'err'));
    $('#set-quit').onclick = quitApp;
    STATUS.listeners.push(() => { if (current) renderUsb(); });
  }

  return {init, show: load};
})();
