/* ASCII pickleball: shaded sphere, 40 holes on a Fibonacci lattice. */
window.Ball = (function () {
  var W = 36, H = 18, HOLES = 40, HOLE_R = 0.19;
  var holes = [];
  var golden = Math.PI * (3 - Math.sqrt(5));
  for (var k = 0; k < HOLES; k++) {
    var y = 1 - (k / (HOLES - 1)) * 2;
    var r = Math.sqrt(1 - y * y);
    var th = golden * k;
    holes.push([Math.cos(th) * r, y, Math.sin(th) * r]);
  }
  var cosHole = Math.cos(HOLE_R);
  var ramp = ':-=+*#%@';
  var L = [-0.45, 0.55, 0.70];
  var ln = Math.hypot(L[0], L[1], L[2]); L = [L[0] / ln, L[1] / ln, L[2] / ln];
  var tilt = 0.45, ct = Math.cos(tilt), st = Math.sin(tilt);
  var reduced = false;
  try { reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) {}

  function frame(a) {
    var ca = Math.cos(a), sa = Math.sin(a), out = '';
    for (var j = 0; j < H; j++) {
      var py = -((j + 0.5) / H * 2 - 1), line = '', inHole = false;
      for (var i = 0; i < W; i++) {
        var px = (i + 0.5) / W * 2 - 1, r2 = px * px + py * py, ch = ' ', hole = false;
        if (r2 <= 1) {
          var pz = Math.sqrt(1 - r2);
          var y1 = py * ct - pz * st, z1 = py * st + pz * ct;
          var bx = px * ca - z1 * sa, bz = px * sa + z1 * ca, by = y1;
          for (var h = 0; h < HOLES; h++) {
            if (bx * holes[h][0] + by * holes[h][1] + bz * holes[h][2] > cosHole) { hole = true; break; }
          }
          var lum = px * L[0] + py * L[1] + pz * L[2];
          if (lum < 0) lum = 0;
          lum = 0.15 + 0.85 * lum;
          if (hole) ch = lum > 0.6 ? 'o' : '.';
          else ch = ramp[Math.min(ramp.length - 1, Math.floor(lum * (ramp.length - 1) + 0.5))];
        }
        if (hole !== inHole) { line += inHole ? '</span>' : '<span class="hole">'; inHole = hole; }
        line += ch;
      }
      if (inHole) line += '</span>';
      out += line + '\n';
    }
    return out;
  }

  function mount(pre, opts) {
    opts = opts || {};
    var speed = opts.speed || 0.05, fps = opts.fps || 12;
    var angle = 0.6, last = 0;
    pre.innerHTML = frame(angle);
    if (reduced) return;
    function spin(ts) {
      if (!pre.isConnected) return;
      if (ts - last > 1000 / fps) { angle += speed; pre.innerHTML = frame(angle); last = ts; }
      requestAnimationFrame(spin);
    }
    requestAnimationFrame(spin);
  }

  return { mount: mount, frame: frame };
})();
