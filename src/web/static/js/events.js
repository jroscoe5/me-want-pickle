/* Open play grid: columns are event names, rows are time ranges. */
(function (M) {
  var $ = M.$, esc = M.esc, state = M.state;
  var seq = 0, timer = null;

  async function load() {
    var f = M.activeFacility(); if (!f || !$('events')) return;
    var mine = ++seq;
    M.setMsg('events-msg', 'loading…');
    var data = await M.api('GET', '/api/facilities/' + f.id + '/events?date=' + state.day);
    if (mine !== seq || !$('events')) return;
    if (!data.ok) { M.setMsg('events-msg', data.offline ? '' : data.error, 'err'); return; }
    M.setMsg('events-msg', '');
    render(data);
    clearTimeout(timer);
    timer = setTimeout(load, 120000);
  }

  function action(e) {
    var act = (e.action || '').toLowerCase();
    if (act.indexOf('wait') >= 0) return { cls: 'wait', label: 'waitlist me' };
    if (act.indexOf('register') >= 0) return { cls: 'reg', label: 'register me' };
    return { cls: 'want', label: 'me want' };
  }

  function range(e) { return (e.start || '') + (e.end ? '-' + e.end : ''); }

  function cell(e, t) {
    var spots = e.spots_left != null && e.spots_total ? e.spots_left + '/' + e.spots_total : e.spots;
    var mine = M.bookings.find(e);
    if (mine) {
      var word = mine.waitlisted ? 'waitlisted' : 'registered';
      return '<div class="evc"><span class="mine">' + word + '</span><span class="s ' + (mine.waitlisted ? 'low' : 'open') + '">' + esc(spots) + '</span></div>';
    }
    var a = action(e), cls = 'open';
    if (a.cls === 'want') cls = 'soon';
    else if (e.spots_left != null && e.spots_total) cls = e.spots_left === 0 ? 'full' : (e.spots_left <= 3 ? 'low' : 'open');
    var path = (e.details_url || '').replace(/^https?:\/\/[^/]+/, '');
    return '<div class="evc"><button type="button" class="act ' + a.cls + '" data-kind="event" data-act="' + a.cls + '" data-rid="' + esc(e.reservation_id) + '" data-path="' + esc(path) + '" data-name="' + esc(e.name) + '" data-start="' + esc(e.start || '') + '" data-end="' + esc(e.end || '') + '" data-when="' + esc(state.day + ' ' + t) + '">' + a.label + '</button><span class="s ' + cls + '">' + esc(spots) + '</span></div>';
  }

  function render(data) {
    var el = $('events');
    if (!data.events.length) { el.innerHTML = '<div class="empty">no open play listed for ' + esc(M.fmt.day(data.date)) + '</div>'; return; }
    var names = [], times = [];
    data.events.forEach(function (e) {
      if (names.indexOf(e.name) < 0) names.push(e.name);
      if (times.indexOf(range(e)) < 0) times.push(range(e));
    });
    names.sort(); times.sort();
    el.style.gridTemplateColumns = '11ch ' + names.map(function () { return 'max-content'; }).join(' ');
    var html = '<div class="head">time</div>' + names.map(function (n) {
      return '<div class="head"><span class="tag">' + esc(n.replace(/ open play$/i, '').toLowerCase()) + '</span></div>';
    }).join('');
    times.forEach(function (t) {
      html += '<div class="hr">' + esc(t) + '</div>';
      names.forEach(function (n) {
        var e = data.events.find(function (x) { return x.name === n && range(x) === t; });
        html += e ? cell(e, t) : '<div class="evc"><span class="act-slot"></span><span class="s dim">·</span></div>';
      });
    });
    el.innerHTML = html;
  }

  M.events = { load: load };
})(window.MWP);
