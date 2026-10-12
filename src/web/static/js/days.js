/* The day strip: 28 days from today, eight visible, arrows and buttons change the selected day. */
(function (M) {
  var $ = M.$, state = M.state;
  var DAYS_SHOWN = 28;

  function list() {
    var out = [], d = new Date(M.fmt.isoToday() + 'T12:00:00');
    for (var i = 0; i < DAYS_SHOWN; i++) { out.push(M.fmt.isoOf(d)); d.setDate(d.getDate() + 1); }
    return out;
  }

  function render() {
    var f = M.activeFacility(); if (!f || !$('days')) return;
    var days = list(), i = days.indexOf(state.day);
    var horizon = f.horizon_max ? f.horizon_max.slice(0, 10) : null;
    $('days').innerHTML = days.map(function (d) {
      var cls = (d === state.day ? 'active' : '') + (horizon && d > horizon ? ' next' : '');
      return '<button type="button" data-day="' + d + '" class="' + cls + '">' + M.fmt.dayShort(new Date(d + 'T12:00:00')) + '</button>';
    }).join('');
    $('d-prev').disabled = i <= 0;
    $('d-next').disabled = i >= days.length - 1;
    var el = $('days'), btn = el.children[i];
    if (btn) {
      var left = btn.offsetLeft - el.offsetLeft, right = left + btn.offsetWidth;
      if (left < el.scrollLeft) el.scrollLeft = left;
      else if (right > el.scrollLeft + el.clientWidth) el.scrollLeft = right - el.clientWidth;
    }
  }

  function select(day) {
    state.day = day;
    render();
    M.courts.load();
    M.events.load();
  }

  function shift(n) {
    if (!M.activeFacility()) return;
    var days = list(), i = days.indexOf(state.day) + n;
    if (i >= 0 && i < days.length) select(days[i]);
  }

  function mount() {
    $('d-prev').addEventListener('click', function () { shift(-1); });
    $('d-next').addEventListener('click', function () { shift(1); });
    $('days').addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (b) select(b.dataset.day);
    });
    if (list().indexOf(state.day) < 0) state.day = M.fmt.isoToday();
    render();
  }

  M.days = { list: list, render: render, select: select, shift: shift, mount: mount };
})(window.MWP);
