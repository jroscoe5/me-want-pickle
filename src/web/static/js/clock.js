/* Status-line clock and the block-digit countdown to the next armed target. */
(function (M) {
  var $ = M.$, pad = M.pad;
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
    $('now').textContent = M.fmt.hms(t);
    var d = $('digits'); if (!d) return;
    var next = M.targets.next();
    if (!next) {
      d.textContent = big('--:--:--'); d.className = 'big';
      $('count-lbl').textContent = 'until release · nothing armed';
      $('count-sub').textContent = (M.state.targets || []).length ? '' : 'no targets yet';
      return;
    }
    var s = Math.floor(Math.max(0, new Date(next.at) - t) / 1000);
    var days = Math.floor(s / 86400); s -= days * 86400;
    var h = Math.floor(s / 3600); s -= h * 3600;
    var m = Math.floor(s / 60); s -= m * 60;
    d.textContent = big(pad(h) + ':' + pad(m) + ':' + pad(s)); d.className = 'big live';
    $('count-lbl').textContent = 'until #' + next.id + ' ' + next.verb + (days ? ' · +' + days + 'd' : '');
    $('count-sub').textContent = next.label;
  }

  M.clock = { tick: tick };
})(window.MWP);
