/* Court reservations grid: one row per hour, one column per court group. */
(function (M) {
  var $ = M.$, esc = M.esc, state = M.state;
  var seq = 0, timer = null;

  async function load() {
    var f = M.activeFacility(); if (!f || !$('slots')) return;
    var mine = ++seq;
    $('courts-date').textContent = M.fmt.day(state.day);
    M.setMsg('courts-msg', 'loading…');
    var data = await M.api('GET', '/api/facilities/' + f.id + '/slots?date=' + state.day);
    if (mine !== seq || !$('slots')) return;
    if (!data.ok) { M.setMsg('courts-msg', data.offline ? '' : data.error, 'err'); return; }
    M.setMsg('courts-msg', '');
    if (data.horizon_max && f.horizon_max !== data.horizon_max) { f.horizon_max = data.horizon_max; M.days.render(); }
    render(data);
    clearTimeout(timer);
    timer = setTimeout(load, 60000);
  }

  function action(sl) {
    if (!sl.open) return { cls: 'want', label: 'me want' };
    if (sl.available) return { cls: 'reg', label: 'register me' };
    if (sl.waitlisted) return { cls: 'mine', label: 'waiting' };
    if (sl.waitlistable) return { cls: 'wait', label: 'waitlist me' };
    return null;
  }

  function bar(sl, total, group) {
    var past = sl.past || sl.closed;
    var cls = 'bar' + (past ? ' past' : '');
    var dots = '·'.repeat(Math.max(total, 1));
    var a = past ? null : action(sl);
    var act = a
      ? '<button type="button" class="act ' + a.cls + '" data-kind="court" data-act="' + a.cls + '" data-sid="' + esc(group.id) + '" data-ct="' + esc(sl.court_type_id || 9) + '" data-name="' + esc(group.name) + '" data-start="' + esc(sl.start) + '" data-opens="' + esc(sl.opens_at || '') + '" data-when="' + esc(state.day + ' ' + sl.start) + '">' + a.label + '</button>'
      : '<span class="act-slot"></span>';
    var on = '█'.repeat(sl.available), off = '░'.repeat(Math.max(0, total - sl.available));
    var count = String(sl.available).padStart(2, ' ') + '/' + total;
    var filled = '<span class="on">' + on + '</span><span class="off">' + off + '</span> <span class="n">' + count + '</span>';
    if (!sl.open && !past) {
      var fill = sl.available ? filled : '<span class="n">' + dots + ' full</span>';
      return '<div class="' + cls + ' soon">' + act + fill + ' <span class="n">· opens ' + esc(M.fmt.opens(sl.opens_at)) + '</span></div>';
    }
    if (!sl.available) {
      var wl = sl.waitlist ? ' · ' + sl.waitlist + ' waiting' : '';
      var word = sl.closed ? 'closed' : (sl.waitlistable ? 'full' : 'full · no waitlist');
      return '<div class="' + cls + ' none' + (sl.waitlisted ? ' mine' : '') + '">' + act + '<span class="n">' + dots + ' ' + word + esc(wl) + '</span></div>';
    }
    return '<div class="' + cls + '">' + act + filled + '</div>';
  }

  function render(data) {
    var groups = data.schedulers, el = $('slots');
    el.style.gridTemplateColumns = '6ch ' + groups.map(function () { return 'minmax(14ch, 1fr)'; }).join(' ');
    var hours = {};
    groups.forEach(function (g) { g.slots.forEach(function (s) { hours[s.start] = 1; }); });
    var html = '<div class="head">hour</div>' + groups.map(function (g) {
      var hrs = g.open_time && g.close_time ? ' · ' + esc(g.open_time) + '-' + esc(g.close_time) : '';
      return '<div class="head">' + esc(g.name.toLowerCase()) + ' <span class="dim">' + g.court_count + ' courts' + hrs + '</span></div>';
    }).join('');
    Object.keys(hours).sort().forEach(function (k) {
      html += '<div class="hr">' + esc(k) + '</div>';
      groups.forEach(function (g) {
        var sl = g.slots.find(function (s) { return s.start === k; });
        html += sl ? bar(sl, g.court_count, { id: g.scheduler_id, name: g.name }) : '<div class="bar past"><span class="act-slot"></span><span class="n">·</span></div>';
      });
    });
    el.innerHTML = html;
    var t = new Date(data.fetched_at);
    var hz = data.horizon_max ? ' · bookable through ' + M.fmt.day(data.horizon_max.slice(0, 10)) : '';
    $('courts-hint').textContent = '· ' + (data.timezone || '') + hz + ' · feed ' + M.fmt.hms(t);
  }

  M.courts = { load: load };
})(window.MWP);
