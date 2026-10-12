/* The member's bookings from CourtReserve, synced through the server. */
(function (M) {
  var $ = M.$, esc = M.esc, state = M.state;
  var timer = null;
  var ACTIVE = ['armed', 'preflight', 'firing', 'watching'];

  async function load(force) {
    var f = M.activeFacility(); if (!f || !$('bookings')) return;
    M.setMsg('bookings-msg', 'loading…');
    var data = await M.api('GET', '/api/facilities/' + f.id + '/bookings' + (force ? '?refresh=1' : ''));
    if (!$('bookings')) return;
    if (!data.ok) { M.setMsg('bookings-msg', data.offline ? '' : data.error, 'err'); return; }
    M.setMsg('bookings-msg', '');
    state.bookings = data.bookings;
    render(data);
    if (data.adopted.length || data.closed.length) { await M.pollLog(); M.targets.load(); }
    if (state.day) { M.courts.load(); M.events.load(); }
    clearTimeout(timer);
    timer = setTimeout(load, 600000);
  }

  function watcherFor(b) {
    return (state.targets || []).find(function (x) {
      if (ACTIVE.indexOf(x.status) < 0) return false;
      if (x.params.reservation_id === b.reservation_id) return true;
      return x.params.event_name && M.norm(x.params.event_name) === M.norm(b.name) && x.params.date === b.start.slice(0, 10) && x.params.start === b.start.slice(11, 16);
    });
  }

  function render(data) {
    var el = $('bookings');
    var t = data.fetched_at ? new Date(data.fetched_at) : null;
    $('bookings-hint').textContent = '· ' + data.bookings.length + ' upcoming' + (t ? ' · synced ' + M.fmt.hm(t) : '');
    if (!data.bookings.length) { el.innerHTML = '<div class="empty">nothing booked or waitlisted on CourtReserve</div>'; return; }
    el.innerHTML = data.bookings.map(function (b) {
      var st = b.waitlisted ? 'waitlisted' : (b.registered ? 'booked' : 'other');
      var w = watcherFor(b);
      var d = new Date(b.start), e = new Date(b.end);
      var when = M.fmt.dayShort(d) + ' ' + M.fmt.hm(d) + '-' + M.fmt.hm(e);
      var others = b.members.slice(0, -1).slice(0, 2);
      var extra = (b.courts ? ' · ' + b.courts.toLowerCase() : '') + (others.length ? ' · with ' + others.join(', ') : '') + (w ? ' · watched by #' + w.id : '');
      return '<div class="bk"><span class="st ' + st + '">' + st + '</span><span class="what">' + esc(b.name.toLowerCase()) + '<span>' + esc(extra) + '</span></span><span class="when">' + esc(when) + '</span></div>';
    }).join('');
  }

  function find(e) {
    return (state.bookings || []).find(function (b) {
      if (b.cancelled || b.start.slice(0, 10) !== state.day) return false;
      return b.reservation_id === e.reservation_id || (M.norm(b.name) === M.norm(e.name) && b.start.slice(11, 16) === e.start);
    });
  }

  M.bookings = { load: load, find: find };
})(window.MWP);
