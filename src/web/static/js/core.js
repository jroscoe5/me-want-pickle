/* Shared state, API helper, formatting, and the log box. Every module hangs off window.MWP. */
window.MWP = (function () {
  var state = { facilities: [], active: null, adding: false, lastLogId: 0, busy: false, day: null, targets: [], bookings: [] };
  var $ = function (id) { return document.getElementById(id); };
  var pad = function (n) { return (n < 10 ? '0' : '') + n; };
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  };
  var norm = function (s) { return String(s || '').toLowerCase().replace(/\s+/g, ' ').trim(); };
  var slug = function (name) { return String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, ''); };

  var offlineSince = null;

  function setOnline(online) {
    var el = $('st-server');
    if (online && offlineSince) {
      var secs = Math.round((Date.now() - offlineSince) / 1000);
      offlineSince = null;
      el.hidden = true;
      document.body.classList.remove('offline');
      note('ok', 'server back after ' + secs + 's', 'server');
      if (M.refresh) M.refresh();
    } else if (!online && !offlineSince) {
      offlineSince = Date.now();
      el.hidden = false;
      el.innerHTML = 'server <b class="down">offline</b> since ' + fmt.hms(new Date());
      document.body.classList.add('offline');
      note('error', 'server offline · retrying every 2s', 'server');
    }
  }

  async function api(method, path, body) {
    var r;
    try {
      r = await fetch(path, {
        method: method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined
      });
    } catch (e) {
      setOnline(false);
      return { ok: false, error: 'server offline', offline: true };
    }
    var data = null;
    try { data = await r.json(); } catch (e) { data = { ok: false, error: 'HTTP ' + r.status }; }
    if (r.status >= 502 && r.status <= 504 && !data.ok) { setOnline(false); data.offline = true; data.error = 'server offline'; return data; }
    setOnline(true);
    if (!data.ok && !data.error) data.error = 'HTTP ' + r.status;
    return data;
  }

  function activeFacility() {
    return state.facilities.find(function (f) { return f.id === state.active; }) || null;
  }

  function setMsg(id, text, cls) {
    var el = $(id); if (!el) return;
    el.textContent = text || ''; el.className = 'form-msg ' + (cls || '');
  }

  var fmt = {
    isoOf: function (d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); },
    isoToday: function () { return fmt.isoOf(new Date()); },
    day: function (iso) {
      return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toLowerCase();
    },
    dayShort: function (d) { return d.toLocaleDateString('en-US', { weekday: 'short' }).toLowerCase() + ' ' + d.getDate(); },
    hm: function (d) { return pad(d.getHours()) + ':' + pad(d.getMinutes()); },
    hms: function (d) { return fmt.hm(d) + ':' + pad(d.getSeconds()); },
    opens: function (iso) {
      var d = new Date(iso); if (isNaN(d)) return iso || '';
      return fmt.dayShort(d) + ' ' + fmt.hm(d);
    },
    fire: function (iso) {
      var d = new Date(iso); if (isNaN(d)) return iso || '';
      return fmt.dayShort(d) + ' ' + fmt.hms(d);
    },
    stamp: function (iso) {
      if (!iso) return 'never';
      var d = new Date(iso); if (isNaN(d)) return iso;
      return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
    }
  };

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

  function note(level, message, source) {
    appendLogLine({ ts: new Date().toISOString(), level: level, source: source || 'ui', message: message });
  }

  async function pollLog() {
    var data = await api('GET', '/api/log?after=' + state.lastLogId);
    if (data.ok) data.log.forEach(appendLogLine);
  }

  var M = { state: state, $: $, pad: pad, esc: esc, norm: norm, slug: slug, api: api, activeFacility: activeFacility,
            setMsg: setMsg, fmt: fmt, appendLogLine: appendLogLine, note: note, pollLog: pollLog, offline: function () { return !!offlineSince; } };
  return M;
})();
