/* Status line, facility tabs, the session panel, and the add/check/remove actions. */
(function (M) {
  var $ = M.$, esc = M.esc, state = M.state;

  function renderStatus() {
    var f = M.activeFacility();
    $('st-facility').innerHTML = f ? 'org <b>' + esc(f.org_id) + '</b> ' + esc(M.slug(f.name)) : 'no facility';
    $('st-session').innerHTML = f ? 'session <b class="' + esc(f.session.status) + '">' + esc(f.session.status) + '</b> ' + esc(f.username) : '';
  }

  function renderTabs() {
    var html = '';
    state.facilities.forEach(function (f, i) {
      var on = !state.adding && f.id === state.active;
      html += '<a href="#" data-fid="' + f.id + '" class="' + (on ? 'active' : '') + '"><span class="key">' + (i + 1) + '</span> ' + esc(M.slug(f.name)) + '</a>';
    });
    html += '<a href="#" data-add="1" class="add ' + (state.adding ? 'active' : '') + '"><span class="key">+</span> add facility</a>';
    $('tabs').innerHTML = html;
  }

  function renderSession(f) {
    var s = f.session;
    $('sess-hint').textContent = '· ' + f.name + ' · org ' + f.org_id;
    var rows = [
      ['account', '<em>' + esc(f.username) + '</em>' + (f.member_id ? ' member ' + esc(f.member_id) : '')],
      ['session', '<span class="' + esc(s.status) + '">' + esc(s.status) + '</span>' + (s.error ? ' · ' + esc(s.error) : '') + (s.checked_at ? ' · checked ' + esc(M.fmt.stamp(s.checked_at)) : '')],
      ['verified', esc(M.fmt.stamp(f.verified_at))],
      ['ids', 'costType=' + esc(f.cost_type_id || '?') + ' tz=' + esc(f.timezone || '?')]
    ];
    $('sess-rows').innerHTML = rows.map(function (r) {
      return '<div class="row"><span class="k">' + r[0] + '</span><span class="v">' + r[1] + '</span></div>';
    }).join('');
  }

  function replaceFacility(f) {
    var i = state.facilities.findIndex(function (x) { return x.id === f.id; });
    if (i >= 0) state.facilities[i] = f;
  }

  async function onAdd(e) {
    e.preventDefault();
    if (state.busy) return;
    var body = { facility: $('f-facility').value, username: $('f-username').value, password: $('f-password').value };
    state.busy = true; $('add-submit').disabled = true;
    M.setMsg('add-msg', 'verifying with CourtReserve…');
    M.note('info', 'verifying ' + body.username + ' at ' + body.facility);
    var data = await M.api('POST', '/api/facilities', body);
    state.busy = false;
    if (!data.ok) {
      $('add-submit').disabled = false;
      M.setMsg('add-msg', data.error, 'err');
      $('f-password').value = '';
      await M.pollLog();
      return;
    }
    M.setMsg('add-msg', 'saved ' + data.facility.name, 'ok');
    state.adding = false; state.active = data.facility.id;
    await M.pollLog();
    await M.refresh();
  }

  async function onCheck() {
    var f = M.activeFacility(); if (!f || state.busy) return;
    state.busy = true; $('b-check').disabled = true; M.setMsg('sess-msg', 'checking…');
    var data = await M.api('POST', '/api/facilities/' + f.id + '/check');
    state.busy = false;
    if (data.facility) replaceFacility(data.facility);
    await M.pollLog();
    M.render();
    M.setMsg('sess-msg', data.ok ? 'session live' : data.error, data.ok ? 'ok' : 'err');
  }

  async function onRemove() {
    var f = M.activeFacility(); if (!f || state.busy) return;
    var b = $('b-remove');
    if (b.dataset.confirm !== '1') {
      b.dataset.confirm = '1';
      b.textContent = 'x really remove ' + M.slug(f.name) + '?';
      M.setMsg('sess-msg', 'removes the facility and its password from the keychain; click again to confirm');
      setTimeout(function () { var x = $('b-remove'); if (x) { x.dataset.confirm = ''; x.textContent = 'x remove facility'; } }, 6000);
      return;
    }
    state.busy = true;
    var data = await M.api('DELETE', '/api/facilities/' + f.id);
    state.busy = false;
    if (!data.ok) { M.setMsg('sess-msg', data.error, 'err'); return; }
    state.active = null;
    await M.pollLog();
    await M.refresh();
  }

  M.session = { renderStatus: renderStatus, renderTabs: renderTabs, renderSession: renderSession,
                onAdd: onAdd, onCheck: onCheck, onRemove: onRemove };
})(window.MWP);
