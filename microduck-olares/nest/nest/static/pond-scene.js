/* The Pond's isometric room: Ah-Ah and Tee-Tee roaming, playing and sleeping in pixel art.
 *
 * Drawn on a small canvas (192×120) and scaled up with crisp pixels. Everything solid is a
 * tiny voxel model (ducks, chair, ball, blocks, plant, beds) rendered to a sprite once and
 * cached: top faces lighter, left faces base colour, right faces darker, then a one-pixel dark
 * outline and a soft shadow on the floor, like hand-drawn isometric sprites.
 *
 * The ducks follow the real Nest state (window.S from /api/status): what each duck is doing
 * sends its little self somewhere in the room (the ball when it plays, its bed when it's tired
 * or it's quiet hours, its friend when it seeks one, the front when it's called). Tapping a duck
 * pets it; tapping the ball kicks it; tapping the chair makes a duck peek behind it. None of
 * that moves the real robots: it's the tamagotchi screen.
 */
(function () {
  "use strict";

  const W = 192, H = 120;            // internal canvas pixels
  const TW = 16, TH = 8;             // one floor tile
  const N = 9;                       // the room is N×N tiles
  const OX = W / 2, OY = 38;         // screen position of the floor's back corner
  const WALL = 30;                   // wall height, pixels
  const OUTLINE = "#1c1a24";

  // ── colour helpers ─────────────────────────────────────────────────────────
  function hex(c) { const n = parseInt(c.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
  function css(r, g, b) { return `rgb(${r | 0},${g | 0},${b | 0})`; }
  function shade(c, f) {
    const [r, g, b] = hex(c);
    return f >= 0 ? css(r + (255 - r) * f, g + (255 - g) * f, b + (255 - b) * f) : css(r * (1 + f), g * (1 + f), b * (1 + f));
  }
  function grey(c) { const [r, g, b] = hex(c); const v = (r * 0.3 + g * 0.59 + b * 0.11) * 0.8 + 30; return "#" + [v, v, v + 8].map((x) => Math.min(255, x | 0).toString(16).padStart(2, "0")).join(""); }

  // ── projection ─────────────────────────────────────────────────────────────
  function iso(gx, gy, z) { return [OX + (gx - gy) * TW / 2, OY + (gx + gy) * TH / 2 - (z || 0)]; }

  // ── voxel models → cached sprites ──────────────────────────────────────────
  // A voxel is 1/4 of a tile: 2 px across, 1 px down per step, 2 px tall.
  const VX = 2, VY = 1, VZ = 2;

  function renderVoxels(voxels) {
    // voxels: [x, y, z, colour]; painter's order: back to front, bottom to top.
    voxels.sort((a, b) => (a[0] + a[1]) - (b[0] + b[1]) || a[2] - b[2] || a[0] - b[0]);
    let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    const pos = voxels.map(([x, y, z]) => {
      const px = (x - y) * VX, py = (x + y) * VY - z * VZ;
      minX = Math.min(minX, px - 2); maxX = Math.max(maxX, px + 2);
      minY = Math.min(minY, py); maxY = Math.max(maxY, py + 4);
      return [px, py];
    });
    const pad = 2;
    const w = maxX - minX + pad * 2, h = maxY - minY + pad * 2;
    const grid = new Array(w * h).fill(null);
    const put = (x, y, c) => { if (x >= 0 && y >= 0 && x < w && y < h) grid[y * w + x] = c; };
    voxels.forEach(([, , , c], i) => {
      const x = pos[i][0] - minX + pad - 2, y = pos[i][1] - minY + pad;
      const top = shade(c, 0.18), left = c, right = shade(c, -0.22);
      put(x + 1, y, top); put(x + 2, y, top);
      for (let k = 0; k < 4; k++) put(x + k, y + 1, top);
      put(x, y + 2, left); put(x + 1, y + 2, left); put(x + 2, y + 2, right); put(x + 3, y + 2, right);
      put(x, y + 3, left); put(x + 1, y + 3, left); put(x + 2, y + 3, right); put(x + 3, y + 3, right);
    });
    // The chunky dark outline.
    const out = grid.slice();
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      if (grid[y * w + x]) continue;
      if ((x > 0 && grid[y * w + x - 1]) || (x < w - 1 && grid[y * w + x + 1]) ||
          (y > 0 && grid[(y - 1) * w + x]) || (y < h - 1 && grid[(y + 1) * w + x])) out[y * w + x] = OUTLINE;
    }
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext("2d");
    out.forEach((c, i) => { if (c) { ctx.fillStyle = c; ctx.fillRect(i % w, (i / w) | 0, 1, 1); } });
    // Anchor: where voxel (0, 0, 0)'s top-left corner lands, so models share one origin.
    return {canvas, ax: -minX + pad - 2, ay: -minY + pad};
  }

  function box(list, x0, x1, y0, y1, z0, z1, c) {
    for (let x = x0; x <= x1; x++) for (let y = y0; y <= y1; y++) for (let z = z0; z <= z1; z++) list.push([x, y, z, c]);
  }
  function set(list, x, y, z, c) {
    for (let i = list.length - 1; i >= 0; i--) if (list[i][0] === x && list[i][1] === y && list[i][2] === z) list.splice(i, 1);
    list.push([x, y, z, c]);
  }
  // Rotate a model about the centre of its size×size body footprint for the four facings
  // (0 SE, 1 SW, 2 NW, 3 NE), so turning doesn't shift the duck on the floor.
  function rotate(voxels, dir, size) {
    const s = size - 1;
    return voxels.map(([x, y, z, c]) => {
      if (dir === 0) return [x, y, z, c];
      if (dir === 1) return [s - y, x, z, c];
      if (dir === 2) return [s - x, s - y, z, c];
      return [y, s - x, z, c];
    });
  }

  // The Microduck, facing +x, 12 voxels tall: big two-tone feet, dark shins, shell thigh pads,
  // a small body, a thin black neck, a boxy head with a face plate and one big eye, an R2-D2
  // light on top and a flat beak under the face.
  function duckModel(look, pose) {
    const v = [], dark = "#26262b";
    const f = pose.frame;
    const a = f ? 1 : 0, b = f ? 0 : 1;           // which foot is forward
    box(v, a, a + 2, 0, 0, 0, 0, look.sole); box(v, a, a + 2, 0, 0, 1, 1, look.foot);
    box(v, b, b + 2, 3, 3, 0, 0, look.sole); box(v, b, b + 2, 3, 3, 1, 1, look.foot);
    set(v, a + 1, 0, 2, dark); set(v, b + 1, 3, 2, dark);            // shins
    set(v, 1, 0, 3, look.shell); set(v, 1, 3, 3, look.shell);        // thigh pads
    box(v, 0, 3, 0, 3, 4, 4, look.shade);                            // body
    box(v, 0, 3, 0, 3, 5, 5, look.shell);
    set(v, 1, 1, 6, dark); set(v, 1, 2, 6, dark);                    // neck
    set(v, 1, 1, 7, dark); set(v, 1, 2, 7, dark);
    box(v, -1, 3, 0, 3, 8, 8, look.shade);                           // head
    box(v, -1, 3, 0, 3, 9, 10, look.shell);
    box(v, 0, 2, 0, 3, 11, 11, look.shell);
    for (let y = 0; y <= 3; y++) set(v, 3, y, 11, look.ring);        // ring stripe
    box(v, 4, 4, 0, 3, 9, 10, "#eceeea");                            // face plate
    const E = "#1b1d1c";
    if (pose.eye === "closed") { set(v, 4, 1, 9, E); set(v, 4, 2, 9, E); }
    else if (pose.eye === "happy") { set(v, 4, 0, 9, E); set(v, 4, 1, 10, E); set(v, 4, 2, 10, E); set(v, 4, 3, 9, E); set(v, 3, 0, 8, "#f4a6b8"); set(v, 3, 3, 8, "#f4a6b8"); }
    else { set(v, 4, 1, 9, E); set(v, 4, 2, 9, E); set(v, 4, 2, 10, E); set(v, 4, 1, 10, "#ffffff"); }
    set(v, 0, 3, 12, pose.light);                                    // the R2-D2 light
    box(v, 4, 5, 0, 3, 8, 8, look.beak);                             // beak
    if (pose.beakOpen) box(v, 4, 5, 0, 3, 6, 6, look.beak);
    return v;
  }

  const LOOKS = [
    {shell: "#8fd3e3", shade: "#62aec2", ring: "#f5a31a", beak: "#ee6a2a", foot: "#ee6a2a", sole: "#f2c14e"},
    {shell: "#c3b1e6", shade: "#9a84cc", ring: "#6fc3e6", beak: "#f2c94c", foot: "#f2c94c", sole: "#8a68c8"},
    {shell: "#6b7075", shade: "#4c5054", ring: "#8a68c8", beak: "#f2c94c", foot: "#f2c94c", sole: "#8a68c8"},
    {shell: "#efebe3", shade: "#cfc8ba", ring: "#f5a31a", beak: "#ee6a2a", foot: "#f5a31a", sole: "#ee6a2a"},
  ];

  const spriteCache = new Map();
  function duckSprite(i, dir, pose, offline) {
    const key = [i, dir, pose.frame, pose.eye, pose.beakOpen, pose.light, offline].join("|");
    if (!spriteCache.has(key)) {
      let look = LOOKS[i % LOOKS.length];
      if (offline) look = Object.fromEntries(Object.entries(look).map(([k, c]) => [k, grey(c)]));
      const p = offline ? {...pose, light: "#555a60"} : pose;
      spriteCache.set(key, renderVoxels(rotate(duckModel(look, p), dir, 4)));
    }
    return spriteCache.get(key);
  }

  function chairModel() {           // the green chair
    const v = [], wood = "#6b4a33", green = "#4f8a5b";
    for (const [x, y] of [[0, 0], [0, 5], [5, 0], [5, 5]]) box(v, x, x, y, y, 0, 5, wood);
    box(v, 0, 5, 0, 5, 6, 6, shade(green, -0.15)); box(v, 0, 5, 0, 5, 7, 7, green);
    box(v, 0, 0, 0, 5, 8, 15, green); box(v, 0, 0, 1, 4, 15, 15, shade(green, 0.2));
    return v;
  }
  function ballModel() {
    const v = [], c = "#f2c230";
    box(v, 0, 2, 0, 2, 0, 2, c);
    for (const [x, y, z] of [[0, 0, 0], [2, 0, 0], [0, 2, 0], [2, 2, 0], [0, 0, 2], [2, 0, 2], [0, 2, 2], [2, 2, 2]])
      v.splice(v.findIndex((q) => q[0] === x && q[1] === y && q[2] === z), 1);
    set(v, 1, 1, 3, "#fff3b0");
    return v;
  }
  function blocksModel() {
    const v = [];
    box(v, 0, 1, 0, 1, 0, 1, "#d9534f"); box(v, 2, 3, 0, 1, 0, 1, "#4a8fd9");
    box(v, 1, 2, 0, 1, 2, 3, "#f2c230");
    return v;
  }
  function plantModel() {
    const v = [];
    box(v, 1, 3, 1, 3, 0, 3, "#b8643c"); box(v, 1, 3, 1, 3, 4, 4, "#8a4a2a");
    const leaf = "#4f9a4a";
    box(v, 1, 3, 1, 3, 5, 7, leaf); box(v, 0, 4, 2, 2, 7, 8, leaf); box(v, 2, 2, 0, 4, 8, 9, leaf);
    box(v, 2, 2, 2, 2, 10, 11, shade(leaf, 0.2));
    return v;
  }
  function bedModel(c) {
    const v = [];
    box(v, 0, 5, 0, 5, 0, 0, shade(c, -0.2)); box(v, 0, 5, 0, 5, 1, 1, c);
    box(v, 0, 0, 0, 5, 2, 2, shade(c, -0.1)); box(v, 0, 5, 0, 0, 2, 2, shade(c, -0.1));
    return v;
  }
  const PROPS = {};
  function prop(name, make) { return PROPS[name] || (PROPS[name] = renderVoxels(make())); }

  // ── pixel icons for the mood bubbles ─────────────────────────────────────────
  const ICONS = {
    heart: {fill: "#c8324a", d: "M1 0h2v1h-2zM4 0h2v1h-2zM0 1h7v1h-7zM0 2h7v1h-7zM1 3h5v1h-5zM2 4h3v1h-3zM3 5h1v1h-1z"},
    zzz: {fill: "#2d4f7c", d: "M0 0h4v1h-4zM2 1h1v1h-1zM1 2h1v1h-1zM0 3h4v1h-4zM4 4h3v1h-3zM5 5h1v1h-1zM4 6h3v1h-3z"},
    bang: {fill: "#1e2421", d: "M1 0h1v1h-1zM1 1h1v1h-1zM1 2h1v1h-1zM1 3h1v1h-1zM1 5h1v1h-1z"},
    ask: {fill: "#1e2421", d: "M1 0h3v1h-3zM0 1h1v1h-1zM4 1h1v1h-1zM3 2h1v1h-1zM2 3h1v1h-1zM2 5h1v1h-1z"},
    note: {fill: "#1e2421", d: "M2 0h4v1h-4zM2 1h1v1h-1zM5 1h1v1h-1zM2 2h1v1h-1zM5 2h1v1h-1zM1 3h2v1h-2zM4 3h2v1h-2zM0 4h3v1h-3zM4 4h2v1h-2zM0 5h2v1h-2z"},
  };
  const bubbleCache = {};
  function bubble(kind) {
    if (bubbleCache[kind]) return bubbleCache[kind];
    const ic = ICONS[kind];
    const cells = [...ic.d.matchAll(/M(\d+) (\d+)h(\d+)/g)].map((m) => [+m[1], +m[2], +m[3]]);
    const iw = Math.max(...cells.map(([x, , l]) => x + l)), ih = Math.max(...cells.map(([, y]) => y)) + 1;
    const w = iw + 6, h = ih + 7;
    const c = document.createElement("canvas"); c.width = w; c.height = h;
    const g = c.getContext("2d");
    g.fillStyle = OUTLINE; g.fillRect(1, 0, w - 2, h - 3); g.fillRect(0, 1, w, h - 5);
    g.fillStyle = "#ffffff"; g.fillRect(1, 1, w - 2, h - 5); g.fillRect(2, 1, w - 4, h - 4);
    g.fillStyle = OUTLINE; g.fillRect((w >> 1) - 1, h - 3, 3, 1); g.fillRect(w >> 1, h - 2, 1, 1);
    g.fillStyle = "#ffffff"; g.fillRect(w >> 1, h - 3, 1, 1);
    g.fillStyle = ic.fill;
    cells.forEach(([x, y, l]) => g.fillRect(3 + x, 2 + y, l, 1));
    return (bubbleCache[kind] = c);
  }

  // ── the room ───────────────────────────────────────────────────────────────
  const BLOCKED = new Set();
  const block = (x, y) => BLOCKED.add(x + "," + y);
  const LAYOUT = {
    chair: {gx: 6, gy: 1}, plant: {gx: 0, gy: 0}, blocks: {gx: 3, gy: 0.4},
    beds: [{gx: 0.6, gy: 6.2}, {gx: 0.6, gy: 4.4}],
    pool: {x0: 5.5, y0: 5.5, x1: 8.3, y1: 8.3},
    rug: {x0: 2.5, y0: 2.5, x1: 5.5, y1: 5.5},
    front: {gx: 5, gy: 7.6},
  };
  block(6, 1); block(0, 0); block(3, 0);
  for (let x = 6; x <= 8; x++) for (let y = 6; y <= 8; y++) block(x, y);
  const free = (gx, gy) => gx >= 0.2 && gy >= 0.2 && gx <= N - 0.6 && gy <= N - 0.6 && !BLOCKED.has(Math.floor(gx) + "," + Math.floor(gy));

  function paintRoom(night) {
    const c = document.createElement("canvas"); c.width = W; c.height = H;
    const g = c.getContext("2d");
    const px = (x, y, col) => { g.fillStyle = col; g.fillRect(x, y, 1, 1); };
    g.fillStyle = night ? "#1c2233" : "#e9e2d2"; g.fillRect(0, 0, W, H);
    // back walls: left wall along gy = 0..N at gx = 0, right wall along gx = 0..N at gy = 0
    const [tx, ty] = iso(0, 0), [lx, ly] = iso(0, N), [rx, ry] = iso(N, 0);
    const wallL = night ? "#5a5f7a" : "#e7d3b0", wallR = night ? "#474b63" : "#d4bd97";
    for (let x = lx; x <= rx; x++) {
      const left = x <= tx;
      const base = left ? ly + (x - lx) * (ty - ly) / (tx - lx) : ty + (x - tx) * (ry - ty) / (rx - tx);
      for (let y = Math.round(base - WALL); y < Math.round(base); y++) {
        const v = base - y;
        let col = left ? wallL : wallR;
        if (v < 3) col = night ? "#3a3d52" : "#9b7a55";                    // skirting board
        else if (v > WALL - 2) col = night ? "#6a6f8c" : "#f3e6cc";        // top trim
        else if (((x + y) & 7) === 0) col = shade(col, -0.05);              // plaster texture
        px(x, y, col);
      }
    }
    // a window on the left wall, and a picture on the right
    for (let x = lx + 18; x < lx + 34; x++) {
      const base = ly + (x - lx) * (ty - ly) / (tx - lx);
      for (let y = Math.round(base - 25); y < Math.round(base - 9); y++) {
        const v = Math.round(base - y), u = x - lx - 18;
        const frame = u === 0 || u === 15 || v === 9 || v === 24 || u === 8;
        const sky = night ? (((x * 7 + y * 3) % 23 === 0) ? "#ffffff" : "#18244a") : (v > 19 ? "#bfe3f5" : "#9fd0ec");
        px(x, y, frame ? (night ? "#8a8fa8" : "#ffffff") : sky);
      }
    }
    if (night) { px(lx + 28, Math.round(ly + 32 * (ty - ly) / (tx - lx)) - 21, "#f5e8a0"); }
    for (let x = rx - 62; x < rx - 48; x++) {
      const base = ty + (x - tx) * (ry - ty) / (rx - tx);
      for (let y = Math.round(base - 22); y < Math.round(base - 12); y++) {
        const v = Math.round(base - y), u = x - (rx - 62);
        const edge = u === 0 || u === 13 || v === 12 || v === 21;
        px(x, y, edge ? "#6b4a33" : (v > 16 ? "#8ec48a" : (u < 7 ? "#f2c94c" : "#bfe3f5")));
      }
    }
    // the floor: warm boards in a diamond grid
    for (let gx = 0; gx < N; gx++) for (let gy = 0; gy < N; gy++) {
      const [sx, sy] = iso(gx, gy);
      const base = (gx + gy) % 2 ? (night ? "#4a4658" : "#c99a6a") : (night ? "#443f52" : "#c08f5f");
      diamond(g, sx, sy, base, (gx * 3 + gy) % 5 === 0 ? shade(base, -0.08) : null);
    }
    // the rug
    const {x0, y0, x1, y1} = LAYOUT.rug;
    floorPatch(g, x0, y0, x1, y1, (u, v) => {
      const border = u < 0.12 || v < 0.12 || u > 0.88 || v > 0.88;
      if (border) return night ? "#6a3a4a" : "#b8455a";
      return ((Math.floor(u * 6) + Math.floor(v * 6)) % 2) ? (night ? "#5a4a6a" : "#e7c16a") : (night ? "#4a3a5a" : "#d4a64a");
    });
    // the paddling pond, with a stone rim
    const p = LAYOUT.pool;
    floorPatch(g, p.x0, p.y0, p.x1, p.y1, (u, v) => {
      const d = Math.hypot(u - 0.5, v - 0.5);
      if (d > 0.5) return null;
      if (d > 0.42) return night ? "#6b6f7a" : "#a9aca3";
      return night ? "#233a5a" : (d > 0.3 ? "#4f86ad" : "#5f98c0");
    });
    return c;
  }
  function diamond(g, sx, sy, col, grain) {
    const widths = [2, 6, 10, 14, 14, 10, 6, 2];
    widths.forEach((w, r) => {
      g.fillStyle = col; g.fillRect(Math.round(sx - w / 2), Math.round(sy + r), w, 1);
      if (grain && r === 3) { g.fillStyle = grain; g.fillRect(Math.round(sx - 3), Math.round(sy + r), 6, 1); }
    });
  }
  function floorPatch(g, x0, y0, x1, y1, colour) {
    // Paint pixels whose floor coordinates fall inside [x0,x1]×[y0,y1].
    for (let sy = 0; sy < H; sy++) for (let sx = 0; sx < W; sx++) {
      const a = (sx - OX) / (TW / 2), b = (sy - OY) / (TH / 2);
      const gx = (a + b) / 2, gy = (b - a) / 2;
      if (gx < x0 || gx > x1 || gy < y0 || gy > y1) continue;
      const col = colour((gx - x0) / (x1 - x0), (gy - y0) / (y1 - y0));
      if (col) { g.fillStyle = col; g.fillRect(sx, sy, 1, 1); }
    }
  }

  // ── entities ─────────────────────────────────────────────────────────────────
  const scene = {
    canvas: null, ctx: null, room: {day: null, night: null}, t: 0,
    ducks: {}, ball: {gx: 4.2, gy: 3.8, vx: 0, vy: 0, spin: 0},
    near: false, cooldown: 0, container: null, buttons: {}, labels: null,
  };

  function duckState(name, i) {
    if (!scene.ducks[name]) {
      const bed = LAYOUT.beds[i % LAYOUT.beds.length];
      scene.ducks[name] = {i, name, gx: 2 + i * 3.5, gy: 2.5 + i * 1.5, dir: i ? 1 : 0, frame: 0, target: null,
                           emote: null, emoteT: 0, last: null, beakT: 0, blinkT: 0, idleT: 0,
                           told: false, bed, mode: "roam", peekT: 0, chaseT: 0, kickCool: 0};
      addButton(name, "Pet " + title(name) + " on the screen", () => emote(scene.ducks[name], "heart", 14));
    }
    return scene.ducks[name];
  }
  function title(n) { return String(n).split("-").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join("-"); }
  function emote(d, kind, ticks) { d.emote = kind; d.emoteT = ticks; d.last = kind; d.beakT = kind === "note" ? ticks : 3; }

  function addButton(key, label, onClick) {
    const b = document.createElement("button");
    b.className = "pet"; b.type = "button"; b.setAttribute("aria-label", label);
    b.addEventListener("click", onClick);
    scene.container.appendChild(b);
    scene.buttons[key] = b;
  }

  function randomFreeTile() {
    for (let k = 0; k < 40; k++) {
      const gx = 0.6 + Math.random() * (N - 1.4), gy = 0.6 + Math.random() * (N - 1.4);
      if (free(gx, gy)) return {gx, gy};
    }
    return {gx: 4, gy: 4};
  }
  function dirTowards(dx, dy) {
    // Screen-facing directions: 0 SE (+x), 1 SW (+y), 2 NW (-x), 3 NE (-y).
    return Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? 0 : 2) : (dy > 0 ? 1 : 3);
  }
  function stepTowards(d, goal, speed) {
    const dx = goal.gx - d.gx, dy = goal.gy - d.gy, dist = Math.hypot(dx, dy);
    if (dist < 0.15) return true;
    // Walk along one axis at a time, like a little robot on a grid.
    const axisX = Math.abs(dx) > 0.12 && (Math.abs(dx) >= Math.abs(dy) || Math.abs(dy) <= 0.12);
    const nx = d.gx + (axisX ? Math.sign(dx) * Math.min(speed, Math.abs(dx)) : 0);
    const ny = d.gy + (!axisX ? Math.sign(dy) * Math.min(speed, Math.abs(dy)) : 0);
    d.dir = dirTowards(nx - d.gx, ny - d.gy);
    if (!free(nx, ny)) { d.target = null; return true; }  // bumped into furniture: rethink
    // Personal space: never walk into the other duck; stop beside it instead.
    for (const other of Object.values(scene.ducks)) {
      if (other !== d && Math.hypot(other.gx - nx, other.gy - ny) < 1.0 &&
          Math.hypot(other.gx - nx, other.gy - ny) < Math.hypot(other.gx - d.gx, other.gy - d.gy)) {
        d.target = null; return true;
      }
    }
    d.gx = nx; d.gy = ny; d.frame = 1 - d.frame;
    return false;
  }

  function tick(S) {
    if (!S) return;
    scene.t += 1;
    const quiet = !!(S.quiet && S.quiet.now);
    const names = Object.keys(S.ducks);
    names.forEach((name, i) => {
      const d = duckState(name, i), real = S.ducks[name];
      d.offline = !real.connected;
      if (d.beakT > 0) d.beakT -= 1;
      if (d.blinkT > 0) d.blinkT -= 1; else if (Math.random() < 0.02) d.blinkT = 1;
      if (d.kickCool > 0) d.kickCool -= 1;
      if (real.just_told && !d.told) emote(d, "bang", 14);
      d.told = !!real.just_told;
      if (d.emoteT > 0) { d.emoteT -= 1; if (d.emoteT === 0) d.emote = null; }
      const band = real.tiredness;
      const sleepy = real.asleep || quiet || band === "charging";
      const b = real.behaviour || "";
      if (d.offline || real.paused) { d.frame = 0; if (!d.emote && Math.random() < 0.006) emote(d, "ask", 16); return; }
      if (d.emoteT > 0 && d.emote !== "zzz") return;   // stop to show the feeling
      // What the real duck is doing decides where the little one goes.
      let goal = null, mode = "roam";
      if (sleepy || ["go_to_bed", "rest", "nap"].includes(b) || band === "very_low") { mode = "bed"; goal = {gx: d.bed.gx + 0.6, gy: d.bed.gy + 0.6}; }
      else if (b === "play" || d.chaseT > 0) { mode = "chase"; goal = {gx: scene.ball.gx, gy: scene.ball.gy}; }
      else if (["seek_friend", "greet_friend"].includes(b)) {
        const other = scene.ducks[names.find((n) => n !== name)];
        if (other) { mode = "friend"; goal = {gx: other.gx + (other.gx > d.gx ? -0.9 : 0.9), gy: other.gy}; }
      }
      else if (["come_here", "call_out"].includes(b)) { mode = "front"; goal = LAYOUT.front; }
      else if (b === "investigate" || d.peekT > 0) { mode = "peek"; goal = {gx: LAYOUT.chair.gx + 1.6, gy: LAYOUT.chair.gy + 0.4}; }
      else if (["look_around", "show_something"].includes(b)) { mode = "look"; }
      d.mode = mode;
      if (d.peekT > 0) d.peekT -= 1;
      if (d.chaseT > 0) d.chaseT -= 1;

      const speed = 0.11;
      if (mode === "look") {
        d.frame = 0;
        if (scene.t % 8 === 0) d.dir = (d.dir + 1) % 4;
        if (!d.emote && Math.random() < 0.01) emote(d, "ask", 14);
      } else if (goal) {
        const arrived = stepTowards(d, goal, speed);
        if (arrived) {
          d.frame = 0;
          if (mode === "bed") { d.dir = 0; if (!d.emote && Math.random() < 0.03) emote(d, "zzz", 24); }
          if (mode === "front") d.dir = 1;
          if (mode === "peek") { d.dir = 3; if (!d.emote && Math.random() < 0.05) emote(d, Math.random() < 0.5 ? "ask" : "bang", 14); }
          if (mode === "chase" && d.kickCool === 0) kickBall(d);
        }
      } else {
        // roaming: pick a spot, walk, pause, sometimes feel something
        if (d.idleT > 0) { d.idleT -= 1; d.frame = 0; }
        else {
          if (!d.target) d.target = randomFreeTile();
          if (stepTowards(d, d.target, speed)) { d.target = null; d.idleT = 10 + ((Math.random() * 30) | 0); }
        }
        const pool = {high: ["note", "bang", "ask", "note", "heart"], medium: ["note", "ask", "bang"],
                      low: ["zzz", "ask", "note"]}[band] || ["ask"];
        if (!d.emote && Math.random() < 0.0055) {
          const choices = pool.filter((m) => m !== d.last);
          emote(d, choices[(Math.random() * choices.length) | 0] || pool[0], 16);
        }
      }
    });
    // The ball rolls and slows; it bounces off the walls and furniture.
    const ball = scene.ball;
    if (Math.hypot(ball.vx, ball.vy) > 0.01) {
      const nx = ball.gx + ball.vx, ny = ball.gy + ball.vy;
      if (free(nx, ball.gy)) ball.gx = nx; else ball.vx = -ball.vx * 0.6;
      if (free(ball.gx, ny)) ball.gy = ny; else ball.vy = -ball.vy * 0.6;
      ball.vx *= 0.9; ball.vy *= 0.9; ball.spin += 1;
    } else { ball.vx = ball.vy = 0; }
    // Meeting: a greeting only when they first come together, at most every ~40 s.
    const [a, b] = names.slice(0, 2).map((n) => scene.ducks[n]);
    scene.cooldown = Math.max(0, scene.cooldown - 1);
    if (a && b) {
      const near = Math.hypot(a.gx - b.gx, a.gy - b.gy) < 1.3;
      if (near && !scene.near && scene.cooldown === 0 && !a.emote && !b.emote && !a.offline && !b.offline && !quiet) {
        a.dir = dirTowards(b.gx - a.gx, b.gy - a.gy); b.dir = dirTowards(a.gx - b.gx, a.gy - b.gy);
        if (Math.random() < 0.45) { emote(a, "heart", 16); emote(b, "heart", 16); }
        else { emote(a, "note", 12); }
        scene.cooldown = 235;
      }
      scene.near = near;
    }
    draw(S);
  }

  function kickBall(d, strength) {
    const angle = [0, Math.PI / 2, Math.PI, -Math.PI / 2][d ? d.dir : (Math.random() * 4) | 0] + (Math.random() - 0.5) * 0.9;
    const s = strength || 0.35;
    scene.ball.vx = Math.cos(angle) * s; scene.ball.vy = Math.sin(angle) * s;
    if (d) { d.kickCool = 12; if (!d.emote) emote(d, "note", 10); }
  }

  // ── drawing ──────────────────────────────────────────────────────────────────
  function shadow(g, gx, gy, w) {
    const [sx, sy] = iso(gx, gy);
    g.fillStyle = "rgba(20,16,30,0.28)";
    const rows = [w - 4, w, w, w - 4];
    rows.forEach((rw, r) => g.fillRect(Math.round(sx - rw / 2), Math.round(sy - 2 + r), rw, 1));
  }
  function blit(g, sprite, gx, gy, lift) {
    const [sx, sy] = iso(gx, gy, lift || 0);
    // The sprite's voxel (0,0,0) is the footprint's back corner; centre a 6-wide footprint.
    g.drawImage(sprite.canvas, Math.round(sx - sprite.ax), Math.round(sy - sprite.ay - 1));
    return [sx, sy];
  }

  function draw(S) {
    if (!S || !scene.ctx) return;
    const g = scene.ctx;
    const night = !!(S.quiet && S.quiet.now);
    const room = night ? (scene.room.night || (scene.room.night = paintRoom(true)))
                       : (scene.room.day || (scene.room.day = paintRoom(false)));
    g.drawImage(room, 0, 0);
    // water shimmer
    const p = LAYOUT.pool;
    for (let k = 0; k < 4; k++) {
      const gx = p.x0 + 0.9 + ((k * 0.7 + scene.t * 0.02) % 1.2), gy = p.y0 + 0.9 + (k % 2) * 0.8;
      const [sx, sy] = iso(gx, gy);
      g.fillStyle = (scene.t >> 2) % 2 === k % 2 ? "#bfe0f0" : "#7fb2d4";
      g.fillRect(Math.round(sx), Math.round(sy), 3, 1);
    }
    // Things in the room, back to front.
    const things = [];
    things.push({depth: LAYOUT.plant.gx + LAYOUT.plant.gy + 1, draw: () => { shadow(g, 0.6, 0.6, 10); blit(g, prop("plant", plantModel), 0.1, 0.1); }});
    things.push({depth: LAYOUT.blocks.gx + LAYOUT.blocks.gy + 0.5, draw: () => { shadow(g, 3.5, 0.9, 10); blit(g, prop("blocks", blocksModel), 3, 0.4); }});
    things.push({depth: LAYOUT.chair.gx + LAYOUT.chair.gy + 1.4, key: "chair", draw: () => {
      shadow(g, LAYOUT.chair.gx + 0.8, LAYOUT.chair.gy + 0.8, 14);
      const [sx, sy] = blit(g, prop("chair", chairModel), LAYOUT.chair.gx, LAYOUT.chair.gy);
      place("chair", sx - 12, sy - 34, 24, 38);
    }});
    const names = Object.keys(S.ducks);
    names.forEach((name, i) => {
      const bed = LAYOUT.beds[i % LAYOUT.beds.length];
      things.push({depth: bed.gx + bed.gy + 0.2, draw: () => blit(g, prop("bed" + i, () => bedModel(LOOKS[i % LOOKS.length].shade)), bed.gx, bed.gy)});
    });
    const ball = scene.ball;
    things.push({depth: ball.gx + ball.gy + 0.4, key: "ball", draw: () => {
      shadow(g, ball.gx + 0.35, ball.gy + 0.35, 6);
      const bounce = Math.hypot(ball.vx, ball.vy) > 0.05 && (ball.spin >> 1) % 2 ? 1 : 0;
      const [sx, sy] = blit(g, prop("ball", ballModel), ball.gx, ball.gy, bounce);
      place("ball", sx - 6, sy - 8, 12, 12);
    }});
    names.forEach((name, i) => {
      const d = scene.ducks[name], real = S.ducks[name];
      if (!d) return;
      things.push({depth: d.gx + d.gy + 1, draw: () => {
        const sleeping = real.asleep || night || real.tiredness === "charging";
        const pose = {
          frame: d.frame,
          eye: d.emote === "zzz" || d.blinkT > 0 || sleeping ? "closed" : (d.emote === "heart" || d.emote === "note") ? "happy" : "open",
          beakOpen: d.beakT > 0 && d.beakT % 2 === 1,
          light: d.offline ? "#555a60" : ((scene.t + i * 3) % 8 < 4 ? "#e2474b" : "#4a8fd9"),
        };
        const sprite = duckSprite(i, d.dir, pose, d.offline);
        shadow(g, d.gx + 0.5, d.gy + 0.5, 10);
        const bob = d.frame ? 1 : 0;
        const [sx, sy] = blit(g, sprite, d.gx, d.gy, bob);
        place(name, sx - 9, sy - 26, 18, 30);
        if (d.emote) {
          const bb = bubble(d.emote);
          g.drawImage(bb, Math.round(sx - bb.width / 2), Math.round(sy - 29 - bb.height));
        }
      }});
    });
    things.sort((a, b) => a.depth - b.depth).forEach((t) => t.draw());
    if (night) { g.fillStyle = "rgba(20,26,60,0.28)"; g.fillRect(0, 0, W, H); }
    // labels
    if (scene.labels) {
      const text = names.slice(0, 2).map((n) => {
        const pct = S.ducks[n].battery_percent == null ? "--" : Math.round(S.ducks[n].battery_percent) + "%";
        return title(n).toUpperCase() + " " + pct;
      });
      scene.labels[0].textContent = text[0] || "";
      scene.labels[1].textContent = text[1] || "";
    }
  }

  function place(key, x, y, w, h) {
    const b = scene.buttons[key];
    if (!b) return;
    const s = scene.canvas.clientWidth / W;
    Object.assign(b.style, {left: x * s + "px", top: y * s + "px", width: w * s + "px", height: h * s + "px"});
  }

  // ── public ─────────────────────────────────────────────────────────────────
  window.PondScene = {
    init(container) {
      scene.container = container;
      const canvas = document.createElement("canvas");
      canvas.width = W; canvas.height = H;
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", "The pond: an isometric room where the two ducks roam, play and sleep");
      container.appendChild(canvas);
      scene.canvas = canvas;
      scene.ctx = canvas.getContext("2d");
      scene.ctx.imageSmoothingEnabled = false;
      const labels = document.createElement("div");
      labels.className = "pond-labels";
      labels.innerHTML = "<span></span><span></span>";
      container.appendChild(labels);
      scene.labels = labels.querySelectorAll("span");
      addButton("ball", "Kick the ball", () => {
        kickBall(null, 0.45);
        // the nearest awake duck gives chase
        const awake = Object.values(scene.ducks).filter((d) => !d.offline);
        const near = awake.sort((a, b) => Math.hypot(a.gx - scene.ball.gx, a.gy - scene.ball.gy) - Math.hypot(b.gx - scene.ball.gx, b.gy - scene.ball.gy))[0];
        if (near) near.chaseT = 35;   // about 6 s of chasing, then back to what it was doing
      });
      addButton("chair", "Look behind the green chair", () => {
        const awake = Object.values(scene.ducks).filter((d) => !d.offline);
        const d = awake[(Math.random() * awake.length) | 0];
        if (d) d.peekT = 40;
      });
    },
    tick, draw,
  };
})();
