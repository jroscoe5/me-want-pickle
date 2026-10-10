(function () {
  var state = { facilities: [], active: null, adding: false, lastLogId: 0, busy: false, day: null, slotsTimer: null, eventsTimer: null };
  var $ = function (id) { return document.getElementById(id); };
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var pad = function (n) { return (n < 10 ? '0' : '') + n; };

  /* ---------- api ---------- */
  async function api(method, path, body) {
    var r = await fetch(path, {
      method: method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined
    });
    var data = null;
    try { data = await r.json(); } catch (e) { data = { ok: false, error: 'HTTP ' + r.status }; }
    if (!data.ok && !data.error) data.error = 'HTTP ' + r.status;
    return data;
  }

  async function refresh() {
    var data = await api('GET', '/api/state');
    if (!data.ok) { appendLogLine({ ts: '', level: 'error', source: 'ui', message: data.error }); return; }
    state.facilities = data.facilities;
    if (state.active == null || !state.facilities.some(function (f) { return f.id === state.active; })) {
      state.active = state.facilities.length ? state.facilities[0].id : null;
    }
    if (!state.facilities.length) state.adding = true;
    if (state.lastLogId === 0) data.log.forEach(appendLogLine);
    render();
  }

  /* ---------- render ---------- */
  function activeFacility() {
    return state.facilities.find(function (f) { return f.id === state.active; }) || null;
  }

  function slug(name) { return String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); }

  function renderStatus() {
    var f = activeFacility();
    $('st-facility').innerHTML = f ? 'org <b>' + esc(f.org_id) + '</b> ' + esc(slug(f.name)) : 'no facility';
    if (f) {
      var s = f.session.status;
      $('st-session').innerHTML = 'session <b class="' + esc(s) + '">' + esc(s) + '</b> ' + esc(f.username);
    } else {
      $('st-session').innerHTML = '';
    }
  }

  function renderTabs() {
    var html = '';
    state.facilities.forEach(function (f, i) {
      var on = !state.adding && f.id === state.active;
      html += '<a href="#" data-fid="' + f.id + '" class="' + (on ? 'active' : '') + '"><span class="key">' + (i + 1) + '</span> ' + esc(slug(f.name)) + '</a>';
    });
    html += '<a href="#" data-add="1" class="add ' + (state.adding ? 'active' : '') + '"><span class="key">+</span> add facility</a>';
    $('tabs').innerHTML = html;
  }

  function renderMain() {
    var main = $('main');
    main.innerHTML = '';
    var f = activeFacility();
    if (state.adding || !f) {
      main.appendChild($('tpl-onboard').content.cloneNode(true));
      Ball.mount($('ball'), { speed: 0.03 });
      var cancel = $('add-cancel');
      cancel.hidden = !state.facilities.length;
      cancel.addEventListener('click', function () { state.adding = false; render(); });
      $('add-form').addEventListener('submit', onAdd);
      setTimeout(function () { var el = $('f-facility'); if (el) el.focus(); }, 0);
      return;
    }
    main.appendChild($('tpl-dash').content.cloneNode(true));
    Ball.mount($('ball'), { speed: 0.03 });
    renderSession(f);
    $('b-check').addEventListener('click', onCheck);
    $('b-remove').addEventListener('click', onRemove);
    $('d-prev').addEventListener('click', function () { shiftDay(-1); });
    $('d-next').addEventListener('click', function () { shiftDay(1); });
    $('days').addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      state.day = b.dataset.day; renderDays(); loadSlots(); loadEvents();
    });
    $('f-time').value = f.release_time || '00:00';
    $('f-time').addEventListener('change', onWindow);
    var days = dayList(f);
    if (days.indexOf(state.day) < 0) state.day = days[0];
    renderDays();
    loadSlots();
    loadEvents();
    tick();
  }

  var eventsSeq = 0;
  async function loadEvents() {
    var f = activeFacility(); if (!f || !$('events')) return;
    var seq = ++eventsSeq;
    setMsg('events-msg', 'loading…');
    var data = await api('GET', '/api/facilities/' + f.id + '/events?date=' + state.day);
    if (seq !== eventsSeq || !$('events')) return;
    if (!data.ok) { setMsg('events-msg', data.error, 'err'); return; }
    setMsg('events-msg', '');
    renderEvents(data);
    clearTimeout(state.eventsTimer);
    state.eventsTimer = setTimeout(loadEvents, 120000);
  }

  function renderEvents(data) {
    var el = $('events');
    if (!data.events.length) { el.innerHTML = '<div class="empty">no open play listed for ' + esc(fmtDay(data.date)) + '</div>'; return; }
    el.innerHTML = data.events.map(function (e) {
      var cls = 'full', txt = e.spots;
      if (e.spots_left != null && e.spots_total) {
        txt = e.spots_left + '/' + e.spots_total + ' open';
        cls = e.spots_left === 0 ? 'full' : (e.spots_left <= 3 ? 'low' : 'open');
      }
      var time = (e.start || '') + (e.end ? '-' + e.end : '');
      var tags = e.tags.length ? ' <span class="tag">' + esc(e.tags.join(' ')) + '</span>' : '';
      var cat = e.category && e.category.toLowerCase() !== 'open play' ? ' <span class="cat">· ' + esc(e.category.toLowerCase()) + '</span>' : '';
      return '<div class="ev"><span class="t">' + esc(time) + '</span><span class="n">' + esc(e.name) + tags + cat + '</span><span class="s ' + cls + '">' + esc(txt) + '</span><span class="a">' + esc(e.cost) + ' · <a href="' + esc(e.details_url) + '" target="_blank" rel="noopener">' + esc(e.action.toLowerCase() || 'details') + '</a></span></div>';
    }).join('');
  }

  function isoOf(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function dayList(f) {
    var out = [], d = new Date(isoToday() + 'T12:00:00');
    var last = f.horizon_max ? new Date(f.horizon_max.slice(0, 10) + 'T12:00:00') : d;
    while (d <= last) { out.push(isoOf(d)); d.setDate(d.getDate() + 1); }
    out.push(isoOf(d));
    return out;
  }

  function renderDays() {
    var f = activeFacility(); if (!f || !$('days')) return;
    var days = dayList(f), i = days.indexOf(state.day);
    $('days').innerHTML = days.map(function (d, idx) {
      var dt = new Date(d + 'T12:00:00');
      var label = dt.toLocaleDateString('en-US', { weekday: 'short' }).toLowerCase() + ' ' + dt.getDate();
      var cls = (d === state.day ? 'active' : '') + (idx === days.length - 1 ? ' next' : '');
      return '<button type="button" data-day="' + d + '" class="' + cls + '">' + label + '</button>';
    }).join('');
    $('d-prev').disabled = i <= 0;
    $('d-next').disabled = i >= days.length - 1;
  }

  async function onWindow() {
    var f = activeFacility(); if (!f) return;
    var data = await api('PATCH', '/api/facilities/' + f.id, { release_time: $('f-time').value || '00:00' });
    if (!data.ok) { setMsg('sess-msg', data.error, 'err'); return; }
    var i = state.facilities.findIndex(function (x) { return x.id === f.id; });
    if (i >= 0) state.facilities[i] = data.facility;
    await pollLog();
    loadSlots();
  }

  function isoToday() {
    var t = new Date();
    return t.getFullYear() + '-' + pad(t.getMonth() + 1) + '-' + pad(t.getDate());
  }
  function shiftDay(n) {
    var f = activeFacility(); if (!f) return;
    var days = dayList(f), i = days.indexOf(state.day) + n;
    if (i < 0 || i >= days.length) return;
    state.day = days[i];
    renderDays();
    loadSlots();
    loadEvents();
  }
  function fmtDay(iso) {
    return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toLowerCase();
  }

  var slotsSeq = 0;
  async function loadSlots() {
    var f = activeFacility(); if (!f || !$('slots')) return;
    var seq = ++slotsSeq;
    $('courts-date').textContent = fmtDay(state.day);
    setMsg('courts-msg', 'loading…');
    var data = await api('GET', '/api/facilities/' + f.id + '/slots?date=' + state.day);
    if (seq !== slotsSeq || !$('slots')) return;
    if (!data.ok) { setMsg('courts-msg', data.error, 'err'); return; }
    setMsg('courts-msg', '');
    if (data.horizon_max && f.horizon_max !== data.horizon_max) { f.horizon_max = data.horizon_max; renderDays(); }
    renderSlots(data);
    clearTimeout(state.slotsTimer);
    state.slotsTimer = setTimeout(loadSlots, 60000);
  }

  function fmtOpens(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleDateString('en-US', { weekday: 'short' }).toLowerCase() + ' ' + d.getDate() + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }

  function bar(sl, total) {
    var cls = 'bar', dots = '·'.repeat(Math.max(total, 1));
    if (sl.past || sl.closed) cls += ' past';
    if (!sl.open && !sl.past) {
      return '<div class="' + cls + ' soon"><span class="n">' + dots + ' opens ' + esc(fmtOpens(sl.opens_at)) + '</span></div>';
    }
    if (!sl.available) {
      cls += ' none';
      var wl = sl.waitlist ? ' · ' + sl.waitlist + ' waiting' : '';
      return '<div class="' + cls + '"><span class="n">' + dots + ' ' + (sl.closed ? 'closed' : 'full') + esc(wl) + '</span></div>';
    }
    var on = '█'.repeat(sl.available), off = '░'.repeat(Math.max(0, total - sl.available));
    return '<div class="' + cls + '"><span class="on">' + on + '</span><span class="off">' + off + '</span> <span class="n">' + String(sl.available).padStart(2, ' ') + '/' + total + '</span></div>';
  }

  function renderSlots(data) {
    var groups = data.schedulers;
    var el = $('slots');
    el.style.gridTemplateColumns = '6ch ' + groups.map(function () { return 'minmax(14ch, 1fr)'; }).join(' ');
    var hours = {};
    groups.forEach(function (g) { g.slots.forEach(function (s) { hours[s.start] = 1; }); });
    var keys = Object.keys(hours).sort();
    var html = '<div class="head">hour</div>' + groups.map(function (g) {
      return '<div class="head">' + esc(g.name.toLowerCase()) + ' <span class="dim">' + g.court_count + '</span></div>';
    }).join('');
    keys.forEach(function (k) {
      html += '<div class="hr">' + esc(k) + '</div>';
      groups.forEach(function (g) {
        var sl = g.slots.find(function (s) { return s.start === k; });
        html += sl ? bar(sl, g.court_count) : '<div class="bar past"><span class="n">·</span></div>';
      });
    });
    el.innerHTML = html;
    var t = new Date(data.fetched_at);
    var hz = data.horizon_max ? ' · bookable through ' + fmtDay(data.horizon_max.slice(0, 10)) : '';
    $('courts-hint').textContent = '· ' + (data.timezone || '') + hz + ' · feed ' + pad(t.getHours()) + ':' + pad(t.getMinutes()) + ':' + pad(t.getSeconds());
  }

  function fmtTs(iso) {
    if (!iso) return 'never';
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
  }

  function renderSession(f) {
    var s = f.session;
    $('sess-hint').textContent = '· ' + f.name + ' · org ' + f.org_id;
    var rows = [
      ['account', '<em>' + esc(f.username) + '</em>' + (f.member_id ? ' member ' + esc(f.member_id) : '')],
      ['session', '<span class="' + esc(s.status) + '">' + esc(s.status) + '</span>' + (s.error ? ' · ' + esc(s.error) : '') + (s.checked_at ? ' · checked ' + esc(fmtTs(s.checked_at)) : '')],
      ['verified', esc(fmtTs(f.verified_at))],
      ['ids', 'costType=' + esc(f.cost_type_id || '?') + ' tz=' + esc(f.timezone || '?')]
    ];
    $('sess-rows').innerHTML = rows.map(function (r) {
      return '<div class="row"><span class="k">' + r[0] + '</span><span class="v">' + r[1] + '</span></div>';
    }).join('');
  }

  function render() { renderStatus(); renderTabs(); renderMain(); }

  /* ---------- actions ---------- */
  function setMsg(id, text, cls) {
    var el = $(id); if (!el) return;
    el.textContent = text || ''; el.className = 'form-msg ' + (cls || '');
  }

  async function onAdd(e) {
    e.preventDefault();
    if (state.busy) return;
    var body = {
      facility: $('f-facility').value, username: $('f-username').value, password: $('f-password').value
    };
    state.busy = true; $('add-submit').disabled = true;
    setMsg('add-msg', 'verifying with CourtReserve…');
    appendLogLine({ ts: new Date().toISOString(), level: 'info', source: 'ui', message: 'verifying ' + body.username + ' at ' + body.facility });
    var data = await api('POST', '/api/facilities', body);
    state.busy = false;
    if (!data.ok) {
      $('add-submit').disabled = false;
      setMsg('add-msg', data.error, 'err');
      $('f-password').value = '';
      await pollLog();
      return;
    }
    setMsg('add-msg', 'saved ' + data.facility.name, 'ok');
    state.adding = false; state.active = data.facility.id;
    await pollLog();
    await refresh();
  }

  async function onCheck() {
    var f = activeFacility(); if (!f || state.busy) return;
    state.busy = true; $('b-check').disabled = true; setMsg('sess-msg', 'checking…');
    var data = await api('POST', '/api/facilities/' + f.id + '/check');
    state.busy = false;
    if (data.facility) {
      var i = state.facilities.findIndex(function (x) { return x.id === f.id; });
      if (i >= 0) state.facilities[i] = data.facility;
    }
    await pollLog();
    render();
    setMsg('sess-msg', data.ok ? 'session live' : data.error, data.ok ? 'ok' : 'err');
  }

  async function onRemove() {
    var f = activeFacility(); if (!f || state.busy) return;
    if ($('b-remove').dataset.confirm !== '1') {
      $('b-remove').dataset.confirm = '1';
      $('b-remove').textContent = 'x really remove ' + slug(f.name) + '?';
      setMsg('sess-msg', 'removes the facility and its password from the keychain; click again to confirm');
      setTimeout(function () { var b = $('b-remove'); if (b) { b.dataset.confirm = ''; b.textContent = 'x remove facility'; } }, 6000);
      return;
    }
    state.busy = true;
    var data = await api('DELETE', '/api/facilities/' + f.id);
    state.busy = false;
    if (!data.ok) { setMsg('sess-msg', data.error, 'err'); return; }
    state.active = null;
    await pollLog();
    await refresh();
  }

  /* ---------- log ---------- */
  function appendLogLine(l) {
    var log = $('log');
    var t = '';
    if (l.ts) {
      var d = new Date(l.ts);
      t = isNaN(d) ? l.ts : d.toLocaleTimeString('en-US', { hour12: false }) + '.' + pad(Math.floor(d.getMilliseconds() / 10));
    }
    var div = document.createElement('div');
    div.innerHTML = '<span class="t">' + esc(t) + '</span> <span class="src">' + esc(l.source) + '</span> <span class="' + esc(l.level) + '">' + esc(l.message) + '</span>';
    log.appendChild(div);
    while (log.childElementCount > 300) log.removeChild(log.firstChild);
    log.scrollTop = log.scrollHeight;
    if (l.id && l.id > state.lastLogId) state.lastLogId = l.id;
  }

  async function pollLog() {
    var data = await api('GET', '/api/log?after=' + state.lastLogId);
    if (data.ok) data.log.forEach(appendLogLine);
  }

  /* ---------- prompt ---------- */
  var HELP = 'commands: add · check · remove · switch N · help';
  $('prompt-form').addEventListener('submit', function (e) {
    e.preventDefault();
    var cmd = $('cmd').value.trim(); if (!cmd) return;
    $('cmd').value = '';
    appendLogLine({ ts: new Date().toISOString(), level: 'echo', source: '🥒>', message: cmd });
    var parts = cmd.split(/\s+/), verb = parts[0].toLowerCase();
    if (verb === 'help' || verb === '?') return appendLogLine({ ts: '', level: 'info', source: 'ui', message: HELP });
    if (verb === 'add') { state.adding = true; return render(); }
    if (verb === 'check') { if (!activeFacility()) return appendLogLine({ ts: '', level: 'warn', source: 'ui', message: 'no facility selected' }); if (state.adding) { state.adding = false; render(); } return onCheck(); }
    if (verb === 'remove') { if (state.adding) { state.adding = false; render(); } return onRemove(); }
    if (verb === 'switch') {
      var n = parseInt(parts[1], 10);
      var f = state.facilities[n - 1];
      if (!f) return appendLogLine({ ts: '', level: 'warn', source: 'ui', message: 'no facility ' + parts[1] });
      state.active = f.id; state.adding = false; return render();
    }
    appendLogLine({ ts: '', level: 'warn', source: 'ui', message: 'unknown command "' + cmd + '" · ' + HELP });
  });

  $('tabs').addEventListener('click', function (e) {
    var a = e.target.closest('a'); if (!a) return;
    e.preventDefault();
    if (a.dataset.add) { state.adding = true; }
    else { state.active = parseInt(a.dataset.fid, 10); state.adding = false; }
    render();
  });

  document.addEventListener('keydown', function (e) {
    if (e.target.tagName === 'INPUT' || e.metaKey || e.ctrlKey || e.altKey) return;
    var n = parseInt(e.key, 10);
    if (n >= 1 && n <= 9 && state.facilities[n - 1]) { state.active = state.facilities[n - 1].id; state.adding = false; render(); }
    if (e.key === '+') { state.adding = true; render(); }
    if (e.key === '/') { e.preventDefault(); $('cmd').focus(); }
    if (e.key === 'ArrowLeft' && $('days')) shiftDay(-1);
    if (e.key === 'ArrowRight' && $('days')) shiftDay(1);
    if (e.key === 'c' && $('b-check')) onCheck();
    if (e.key === 'x' && $('b-remove')) onRemove();
  });

  /* ---------- clock + countdown ---------- */
  var F = {
    '0': ['███', '█ █', '█ █', '█ █', '███'], '1': [' █ ', '██ ', ' █ ', ' █ ', '███'],
    '2': ['███', '  █', '███', '█  ', '███'], '3': ['███', '  █', '███', '  █', '███'],
    '4': ['█ █', '█ █', '███', '  █', '  █'], '5': ['███', '█  ', '███', '  █', '███'],
    '6': ['███', '█  ', '███', '█ █', '███'], '7': ['███', '  █', '  █', '  █', '  █'],
    '8': ['███', '█ █', '███', '█ █', '███'], '9': ['███', '█ █', '███', '  █', '███'],
    ':': [' ', '█', ' ', '█', ' '], '-': ['   ', '   ', '███', '   ', '   ']
  };
  function big(s) {
    var rows = ['', '', '', '', ''];
    for (var i = 0; i < s.length; i++) {
      var g = F[s[i]] || F['-'];
      for (var r = 0; r < 5; r++) rows[r] += g[r] + (i < s.length - 1 ? ' ' : '');
    }
    return rows.join('\n');
  }
  function tick() {
    var t = new Date();
    $('now').textContent = pad(t.getHours()) + ':' + pad(t.getMinutes()) + ':' + pad(t.getSeconds());
    var d = $('digits');
    if (d) d.textContent = big('--:--:--');
  }
  setInterval(tick, 1000);

  /* ---------- boot ---------- */
  tick();
  refresh();
  setInterval(pollLog, 2000);
})();
