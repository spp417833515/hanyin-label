'use strict';
/* 页签路由: #design 标签设计 / #printer 打印机校准 / #api/<子页签> API 与密钥.
   切换页签不刷新页面, 未保存的编辑不会丢. */

const API_SUBS = ['service', 'keys', 'calls', 'docs'];

function currentView() {
  const v = location.hash.slice(1).split('/')[0];
  return ['api', 'printer', 'settings'].includes(v) ? v : 'design';
}

function route() {
  const [, sub] = location.hash.slice(1).split('/');
  const view = currentView();
  $$('.view').forEach(s => { s.hidden = s.dataset.view !== view; });
  $$('.ttab').forEach(a => {
    const on = a.dataset.view === view;
    a.classList.toggle('on', on);
    a.setAttribute('aria-selected', String(on));
  });
  if (view === 'api') {
    const s = API_SUBS.includes(sub) ? sub : 'service';
    $('.ttab[data-view="api"]').setAttribute('href', `#api/${s}`);  // 回到 API 页签时停在上次的子页签
    APIV.show(s);
  } else {
    APIV.hide();
  }
  if (view === 'design') EDITOR.onShow();
  if (view === 'printer') PRINTERV.show();
  if (view === 'settings') SETTINGSV.show();
}

window.addEventListener('hashchange', route);
window.addEventListener('beforeunload', ev => {
  if (EDITOR.isDirty()) { ev.preventDefault(); ev.returnValue = ''; }
});

(function main() {
  if (!location.hash) history.replaceState(null, '', '#design');
  wireStatus();
  APIV.init();
  PRINTERV.init();
  SETTINGSV.init();
  EDITOR.init();
  route();
  pollStatus();
  setInterval(pollStatus, 3000);
})();
