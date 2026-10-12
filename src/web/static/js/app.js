/* Boot: load state, render the page, wire the prompt, keys, and click delegation. */
(function (M) {
  var $ = M.$, state = M.state;

  async function refresh() {
    var data = await M.api('GET', '/api/state');
    if (!data.ok) { M.note('error', data.error); return; }
    state.facilities = data.facilities;
    if (state.active == null || !state.facilities.some(function (f) { return f.id === state.active; })) {
      state.active = state.facilities.length ? state.facilities[0].id : null;
    }
    if (!state.facilities.length) state.adding = true;
    if (state.lastLogId === 0) data.log.forEach(M.appendLogLine);
    render();
  }

  function renderMain() {
    var main = $('main');
    main.innerHTML = '';
    var f = M.activeFacility();
    if (state.adding || !f) {
      main.appendChild($('tpl-onboard').content.cloneNode(true));
      Ball.mount($('ball'), { speed: 0.03 });
      $('add-cancel').hidden = !state.facilities.length;
      $('add-cancel').addEventListener('click', function () { state.adding = false; render(); });
      $('add-form').addEventListener('submit', M.session.onAdd);
      setTimeout(function () { var el = $('f-facility'); if (el) el.focus(); }, 0);
      return;
    }
    main.appendChild($('tpl-dash').content.cloneNode(true));
    Ball.mount($('ball'), { speed: 0.03 });
    M.session.renderSession(f);
    $('b-check').addEventListener('click', M.session.onCheck);
    $('b-remove').addEventListener('click', M.session.onRemove);
    $('b-sync').addEventListener('click', function () { M.bookings.load(true); });
    M.targets.mount();
    M.days.mount();
    M.courts.load();
    M.events.load();
    M.targets.load();
    M.bookings.load();
    M.clock.tick();
  }

  function render() { M.session.renderStatus(); M.session.renderTabs(); renderMain(); }

  var HELP = 'commands: add · check · remove · switch N · help';
  function onPrompt(e) {
    e.preventDefault();
    var cmd = $('cmd').value.trim(); if (!cmd) return;
    $('cmd').value = '';
    M.appendLogLine({ ts: new Date().toISOString(), level: 'echo', source: '🥒>', message: cmd });
    var parts = cmd.split(/\s+/), verb = parts[0].toLowerCase();
    var warn = function (m) { M.appendLogLine({ ts: '', level: 'warn', source: 'ui', message: m }); };
    if (verb === 'help' || verb === '?') return M.appendLogLine({ ts: '', level: 'info', source: 'ui', message: HELP });
    if (verb === 'add') { state.adding = true; return render(); }
    if (verb === 'check') { if (!M.activeFacility()) return warn('no facility selected'); if (state.adding) { state.adding = false; render(); } return M.session.onCheck(); }
    if (verb === 'remove') { if (state.adding) { state.adding = false; render(); } return M.session.onRemove(); }
    if (verb === 'switch') {
      var f = state.facilities[parseInt(parts[1], 10) - 1];
      if (!f) return warn('no facility ' + parts[1]);
      state.active = f.id; state.adding = false; return render();
    }
    warn('unknown command "' + cmd + '" · ' + HELP);
  }

  function onKey(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.metaKey || e.ctrlKey || e.altKey) return;
    var n = parseInt(e.key, 10);
    if (n >= 1 && n <= 9 && state.facilities[n - 1]) { state.active = state.facilities[n - 1].id; state.adding = false; render(); }
    if (e.key === '+') { state.adding = true; render(); }
    if (e.key === '/') { e.preventDefault(); $('cmd').focus(); }
    if (e.key === 'ArrowLeft' && $('days')) M.days.shift(-1);
    if (e.key === 'ArrowRight' && $('days')) M.days.shift(1);
    if (e.key === 'c' && $('b-check')) M.session.onCheck();
    if (e.key === 'x' && $('b-remove')) M.session.onRemove();
    if (e.key === 'r' && $('b-sync')) M.bookings.load(true);
  }

  function onMainClick(e) {
    var b = e.target.closest('button.act');
    if (b && b.dataset.kind) { M.targets.open(b.dataset); return; }
    var op = e.target.closest('button[data-op]');
    if (op) M.targets.op(op.dataset.op, parseInt(op.dataset.tid, 10));
  }

  function onTabClick(e) {
    var a = e.target.closest('a'); if (!a) return;
    e.preventDefault();
    if (a.dataset.add) state.adding = true;
    else { state.active = parseInt(a.dataset.fid, 10); state.adding = false; }
    render();
  }

  M.refresh = refresh;
  M.render = render;

  $('prompt-form').addEventListener('submit', onPrompt);
  $('main').addEventListener('click', onMainClick);
  $('tabs').addEventListener('click', onTabClick);
  document.addEventListener('keydown', onKey);
  setInterval(M.clock.tick, 1000);
  setInterval(M.pollLog, 2000);
  M.clock.tick();
  refresh();
})(window.MWP);
