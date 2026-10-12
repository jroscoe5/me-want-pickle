/* Arming targets from the grid, the targets list, and its controls. */
(function (M) {
  var $ = M.$, esc = M.esc, state = M.state;
  var arm = null, timer = null, searchSeq = 0;
  var ACTIVE = ['armed', 'preflight', 'firing', 'watching'];

  function close() { arm = null; $('arm-box').hidden = true; }

  async function open(d) {
    var f = M.activeFacility(); if (!f) return;
    var day = d.when.split(' ')[0];
    arm = { kind: null, params: { date: day, start: d.start, end: d.end || '' }, label: '' };
    var rows, fields = '';
    if (d.kind === 'court') {
      arm.params.scheduler_id = parseInt(d.sid, 10); arm.params.court_type_id = parseInt(d.ct, 10) || 9;
      arm.params.court_type = 'Pickleball'; arm.params.scheduler_name = d.name; arm.params.partners = [];
      arm.kind = d.act === 'wait' ? 'court_waitlist' : 'court_reserve';
      if (d.act === 'want') arm.params.opens_at = d.opens;
      arm.label = (arm.kind === 'court_waitlist' ? 'waitlist ' : 'reserve ') + d.name.toLowerCase() + ' · ' + M.fmt.day(day) + ' ' + d.start;
      rows = [['court', d.name.toLowerCase()], ['slot', M.fmt.day(day) + ' ' + d.start], ['fires', d.act === 'want' ? 'opens ' + M.fmt.opens(d.opens) : 'now']];
      if (d.act === 'wait') rows.push(['then', 'joins the waitlist, then watches for a free court and reserves']);
      fields = '<label data-k="type"><select id="a-type"><option value="">loading…</option></select></label>'
             + '<label data-k="duration"><select id="a-dur"><option value="60">1 hour</option></select></label>'
             + '<label data-k="partners"><input id="a-q" placeholder="type 2+ letters to search members"></label>'
             + '<div class="pick" id="a-pick"></div><div class="chips" id="a-chips"></div>';
    } else {
      arm.params.details_path = d.path; arm.params.reservation_id = parseInt(d.rid, 10); arm.params.event_name = d.name;
      arm.params.register_open = d.act === 'reg';
      arm.kind = d.act === 'wait' ? 'event_waitlist' : 'event_register';
      arm.label = (d.act === 'wait' ? 'waitlist ' : 'register ') + d.name.toLowerCase() + ' · ' + M.fmt.day(day) + ' ' + d.start;
      rows = [['event', d.name.toLowerCase()], ['when', M.fmt.day(day) + ' ' + d.start + (d.end ? '-' + d.end : '')],
              ['fires', d.act === 'want' ? 'when registration opens' : 'now']];
      if (d.act === 'wait') rows.push(['then', 'joins the waitlist, then watches for a spot and registers']);
    }
    $('arm-hint').textContent = '· dry run · nothing is sent';
    $('arm-rows').innerHTML = rows.map(function (r) { return '<div class="row"><span class="k">' + r[0] + '</span><span class="v">' + esc(r[1]) + '</span></div>'; }).join('');
    $('arm-fields').innerHTML = fields;
    M.setMsg('arm-msg', '');
    $('arm-box').hidden = false;
    $('arm-box').scrollIntoView({ block: 'nearest' });
    if (d.kind === 'court') await loadTypes(f, day, d.start);
  }

  async function loadTypes(f, day, start) {
    var q = '/api/facilities/' + f.id + '/reservation-types?date=' + day + '&scheduler_id=' + arm.params.scheduler_id + '&start=' + start + '&court_type_id=' + arm.params.court_type_id;
    var data = await M.api('GET', q);
    if (!arm) return;
    if (!data.ok) { M.setMsg('arm-msg', data.error, 'err'); return; }
    arm.types = data.types;
    $('a-type').innerHTML = data.types.map(function (t) {
      return '<option value="' + t.id + '">' + esc(t.name) + (t.min_players > 1 ? ' · ' + t.min_players + '+ players' : '') + '</option>';
    }).join('');
    $('a-type').addEventListener('change', fillDurations);
    fillDurations();
    $('a-q').addEventListener('input', searchMembers);
  }

  function fillDurations() {
    var t = arm.types.find(function (x) { return String(x.id) === $('a-type').value; }); if (!t) return;
    $('a-dur').innerHTML = t.durations.map(function (m) {
      return '<option value="' + m + '">' + (m % 60 ? m + ' min' : (m / 60) + ' hour' + (m > 60 ? 's' : '')) + '</option>';
    }).join('');
    $('a-dur').value = String(t.durations[t.durations.length - 1]);
  }

  async function searchMembers() {
    var f = M.activeFacility(), q = $('a-q').value.trim();
    if (q.length < 2) { $('a-pick').innerHTML = ''; return; }
    var mine = ++searchSeq;
    var data = await M.api('GET', '/api/facilities/' + f.id + '/members?q=' + encodeURIComponent(q) + '&scheduler_id=' + arm.params.scheduler_id);
    if (mine !== searchSeq || !arm) return;
    if (!data.ok) { M.setMsg('arm-msg', data.error, 'err'); return; }
    $('a-pick').innerHTML = data.members.map(function (m) {
      return '<button type="button" data-oid="' + m.org_member_id + '" data-mid="' + (m.member_id || '') + '" data-name="' + esc(m.name) + '">' + esc(m.name) + '</button>';
    }).join('') || '<span class="dim">no matches</span>';
    $('a-pick').onclick = function (e) {
      var b = e.target.closest('button'); if (!b) return;
      var oid = parseInt(b.dataset.oid, 10);
      if (!arm.params.partners.some(function (p) { return p.org_member_id === oid; })) {
        arm.params.partners.push({ org_member_id: oid, member_id: b.dataset.mid ? parseInt(b.dataset.mid, 10) : null, name: b.dataset.name });
      }
      $('a-q').value = ''; $('a-pick').innerHTML = ''; renderChips();
    };
  }

  function renderChips() {
    $('a-chips').innerHTML = arm.params.partners.map(function (p, i) { return '<span class="chip" data-i="' + i + '">' + esc(p.name) + '</span>'; }).join('');
    $('a-chips').onclick = function (e) { var c = e.target.closest('.chip'); if (!c) return; arm.params.partners.splice(parseInt(c.dataset.i, 10), 1); renderChips(); };
  }

  async function submit() {
    var f = M.activeFacility(); if (!f || !arm) return;
    if (arm.kind === 'court_reserve' || arm.kind === 'court_waitlist') {
      arm.params.reservation_type_id = parseInt($('a-type').value, 10);
      arm.params.reservation_type = ($('a-type').selectedOptions[0] || {}).textContent;
      arm.params.duration = parseInt($('a-dur').value, 10);
    }
    $('arm-go').disabled = true; M.setMsg('arm-msg', 'arming…');
    var data = await M.api('POST', '/api/facilities/' + f.id + '/targets', { kind: arm.kind, params: arm.params, label: arm.label });
    $('arm-go').disabled = false;
    if (!data.ok) { M.setMsg('arm-msg', data.error, 'err'); await M.pollLog(); return; }
    close();
    await M.pollLog();
    load();
  }

  async function load() {
    var f = M.activeFacility(); if (!f || !$('targets')) return;
    var data = await M.api('GET', '/api/facilities/' + f.id + '/targets');
    if (!data.ok || !$('targets')) return;
    state.targets = data.targets;
    render(data);
    clearTimeout(timer);
    var active = data.targets.some(function (t) { return ACTIVE.indexOf(t.status) >= 0; });
    timer = setTimeout(load, active ? 5000 : 30000);
  }

  function summary(targets) {
    var n = function (st) { return targets.filter(function (t) { return t.status === st; }).length; };
    if (n('firing')) return { text: 'firing', cls: 'live' };
    if (n('preflight')) return { text: 'preflight', cls: 'live' };
    var parts = [];
    if (n('watching')) parts.push('watching ' + n('watching'));
    if (n('armed')) parts.push('armed ' + n('armed'));
    return parts.length ? { text: parts.join(' · '), cls: 'live' } : { text: 'idle', cls: '' };
  }

  function render(data) {
    var el = $('targets'), co = data.clock_offset || {};
    var sum = summary(data.targets);
    $('st-watcher').textContent = sum.text; $('st-watcher').className = sum.cls;
    var off = co.checked_at ? ' · clock ' + (co.seconds >= 0 ? '+' : '') + co.seconds.toFixed(3) + 's' + (co.rtt_ms != null ? ' · rtt ' + co.rtt_ms + 'ms' : '') : '';
    $('targets-hint').textContent = '· dry run only · ' + data.writes_today + ' writes today' + off;
    if (!data.targets.length) { el.innerHTML = '<div class="empty">nothing armed · click register me, waitlist me, or me want above</div>'; return; }
    el.innerHTML = data.targets.map(function (t) {
      var watcher = t.kind.indexOf('_spot') > 0;
      var when = t.status === 'watching' ? 'watching' : (t.fire_at ? 'fires ' + M.fmt.fire(t.fire_at) : 'now');
      var live = ACTIVE.indexOf(t.status) >= 0;
      var ops = '<button type="button" class="act" data-op="run" data-tid="' + t.id + '">' + (watcher ? 'poll now' : (live ? 'run dry now' : 'run dry again')) + '</button>'
              + (live ? '<button type="button" class="act wait" data-op="disarm" data-tid="' + t.id + '">disarm</button>'
                      : '<button type="button" class="act wait" data-op="delete" data-tid="' + t.id + '">x</button>');
      var meta = ' · ' + t.kind.replace('_', ' ') + (t.attempts ? ' · ' + t.attempts + ' attempt' + (t.attempts > 1 ? 's' : '') : '');
      var res = t.result ? '<div class="tg-result">' + esc(t.result) + '</div>' : '';
      return '<div class="tg"><span class="st ' + esc(t.status) + '">' + esc(t.status.replace('_', ' ')) + '</span><span class="what">#' + t.id + ' ' + esc(t.label) + ' <span>' + esc(meta) + '</span></span><span class="when">' + esc(when) + '</span><span class="ops">' + ops + '</span></div>' + res;
    }).join('');
  }

  async function op(name, tid) {
    var data = name === 'delete' ? await M.api('DELETE', '/api/targets/' + tid) : await M.api('POST', '/api/targets/' + tid + '/' + name);
    if (!data.ok) M.note('error', data.error, 'target');
    await M.pollLog();
    load();
  }

  function next() {
    var armed = (state.targets || []).filter(function (x) { return ['armed', 'preflight'].indexOf(x.status) >= 0 && x.fire_at; })
      .sort(function (a, b) { return a.fire_at < b.fire_at ? -1 : 1; })[0];
    if (armed) return { id: armed.id, label: armed.label, at: armed.fire_at, verb: 'fires' };
    var watching = (state.targets || []).filter(function (x) { return x.status === 'watching' && x.params.next_poll_at; })
      .sort(function (a, b) { return a.params.next_poll_at < b.params.next_poll_at ? -1 : 1; })[0];
    if (watching) return { id: watching.id, label: watching.label, at: watching.params.next_poll_at, verb: 'polls' };
    return null;
  }

  function mount() {
    $('arm-cancel').addEventListener('click', close);
    $('arm-go').addEventListener('click', submit);
  }

  M.targets = { open: open, close: close, load: load, op: op, next: next, mount: mount };
})(window.MWP);
