'use strict';

/* RemoteLink 手机端：WebCodecs 硬解 H.264 + 触控映射 + 画质调节 */

const $ = (id) => document.getElementById(id);
const el = {
  stage: $('stage'), canvas: $('screen'), stats: $('stats'), toast: $('toast'),
  keys: $('keys'), settings: $('settings'), login: $('login'),
  tokenInput: $('tokenInput'), kbd: $('kbd'), empty: $('empty'),
  emptyText: $('emptyText'), infoBox: $('infoBox'), monitorGroup: $('monitorGroup'),
  monitorSeg: document.querySelector('.seg[data-field="monitor"]'),
};
const ctx = el.canvas.getContext('2d', { alpha: false, desynchronized: true });

const store = {
  get(k, d) {
    try { const v = localStorage.getItem('rl.' + k); return v === null ? d : v; } catch { return d; }
  },
  set(k, v) { try { localStorage.setItem('rl.' + k, v); } catch { /* ignore */ } },
};

const state = {
  ws: null, ctl: null, connected: false, attempt: 0, manualClose: false,
  decoder: null, codecStr: null, awaitingKey: true, rendered: false,
  meta: null,
  fps: Number(store.get('fps', 30)),
  quality: store.get('quality', 'original'),
  mode: store.get('mode', 'direct'),
  monitor: 1,
  scale: 1, panX: 0, panY: 0,
  frames: 0, bytes: 0, rtt: 0,
  cursor: { x: 0.5, y: 0.5 },
  lastRecover: 0,
};

/* ---------------------------------------------------------------- 工具 */

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

function toast(msg, ms = 1400) {
  el.toast.textContent = msg;
  el.toast.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.toast.classList.remove('show'), ms);
}

function vibrate(ms) {
  if (navigator.vibrate) navigator.vibrate(ms);
}

function fmtRate(kbps) {
  return kbps >= 1000 ? (kbps / 1000).toFixed(1) + ' Mbps' : Math.round(kbps) + ' kbps';
}

function setEmpty(text, spinning) {
  el.empty.classList.toggle('hidden', !spinning);
  if (text) el.emptyText.textContent = text;
}

/* ---------------------------------------------------------------- 连接 */

function getToken() {
  return (store.get('token', '') || '').trim();
}

function saveToken(t) {
  store.set('token', t);
}

function connect() {
  if (state.ws) return;
  const token = getToken();
  if (!token) { showLogin(); return; }
  state.manualClose = false;
  setEmpty('正在连接桌面…', true);
  connectCtl();

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws?k=${encodeURIComponent(token)}`);
  ws.binaryType = 'arraybuffer';
  state.ws = ws;

  ws.onopen = () => {
    state.connected = true;
    state.attempt = 0;
    el.login.classList.add('hidden');
    setEmpty('等待画面…', true);
    toast('已连接');
    sendCfg();
  };

  ws.onmessage = (ev) => {
    if (typeof ev.data === 'string') onJson(ev.data);
    else onVideo(new Uint8Array(ev.data));
  };

  ws.onclose = (ev) => {
    state.connected = false;
    state.ws = null;
    resetDecoder();
    if (ev.code === 4401) { showLogin('口令不正确，请重新输入'); return; }
    if (state.manualClose) return;
    scheduleReconnect();
  };

  ws.onerror = () => { /* 交给 onclose 处理 */ };
}

function connectCtl() {
  if (state.ctl) return;
  const token = getToken();
  if (!token) return;
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ctl?k=${encodeURIComponent(token)}`);
  state.ctl = ws;
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.t === 'ping') {
      try { ws.send(JSON.stringify({ t: 'pong', ts: msg.ts })); } catch { /* ignore */ }
    } else if (msg.t === 'pong') {
      state.rtt = Math.max(1, Date.now() - msg.ts);
    }
  };
  ws.onclose = () => {
    state.ctl = null;
    if (state.manualClose) return;
    setTimeout(() => { if (!state.ctl && !state.manualClose) connectCtl(); }, 1000);
  };
  ws.onerror = () => { /* 交给 onclose 处理 */ };
}

function scheduleReconnect() {
  state.attempt += 1;
  const wait = Math.min(400 * state.attempt, 4000);
  setEmpty(`连接已断开，${(wait / 1000).toFixed(1)} 秒后重试…`, true);
  el.stats.classList.add('hidden');
  setTimeout(() => { if (!state.ws && !state.manualClose) connect(); }, wait);
}

/* 输入与心跳走独立的控制通道，避免排在视频数据后面导致操作延迟 */
const CTRL_TYPES = new Set(['move', 'btn', 'wheel', 'key', 'text', 'ping']);

function send(obj) {
  const target = CTRL_TYPES.has(obj.t) ? state.ctl : state.ws;
  if (target && target.readyState === WebSocket.OPEN) {
    target.send(JSON.stringify(obj));
  }
}

function sendCfg() {
  send({ t: 'cfg', fps: state.fps, quality: state.quality, monitor: state.monitor });
}

function onJson(text) {
  let msg;
  try { msg = JSON.parse(text); } catch { return; }
  if (msg.t === 'meta') {
    state.meta = msg;
    el.stats.classList.remove('hidden');
    el.infoBox.textContent =
      `主机：${msg.host || '-'}\n分辨率：${msg.w}×${msg.h}\n编码：${msg.quality || '-'} / ${msg.fps}fps`;
  } else if (msg.t === 'pong') {
    state.rtt = Math.max(1, Date.now() - msg.ts);
  }
}

/* -------------------------------------------------------- WebCodecs 解码 */

function resetDecoder() {
  if (state.decoder) {
    try { state.decoder.close(); } catch { /* ignore */ }
  }
  state.decoder = null;
  state.codecStr = null;
  state.awaitingKey = true;
}

function requestRecover() {
  const now = Date.now();
  if (now - state.lastRecover < 500) return;
  state.lastRecover = now;
  send({ t: 'recover' });
}

function nalList(u8) {
  const out = [];
  let i = 0;
  const n = u8.length;
  while (i + 4 < n) {
    if (u8[i] === 0 && u8[i + 1] === 0 && u8[i + 2] === 1) {
      out.push({ type: u8[i + 3] & 0x1f, at: i + 3 });
      i += 3;
    } else {
      i += 1;
    }
  }
  return out;
}

function auHasKey(u8) {
  return nalList(u8).some((n) => n.type === 5);
}

function codecFromAU(u8) {
  const sps = nalList(u8).find((n) => n.type === 7);
  if (!sps || sps.at + 3 >= u8.length) return null;
  const hex = (v) => v.toString(16).toUpperCase().padStart(2, '0');
  return `avc1.${hex(u8[sps.at + 1])}${hex(u8[sps.at + 2])}${hex(u8[sps.at + 3])}`;
}

function makeDecoder(codec) {
  resetDecoder();
  const decoder = new VideoDecoder({
    output: onFrame,
    error: (err) => {
      console.warn('解码器错误', err);
      resetDecoder();
      requestRecover();
    },
  });
  decoder.configure({
    codec,
    avc: { format: 'annexb' },
    optimizeForLatency: true,
    hardwareAcceleration: 'prefer-hardware',
  });
  state.decoder = decoder;
  state.codecStr = codec;
}

function onVideo(u8) {
  if (!u8.byteLength) return;
  state.bytes += u8.byteLength;
  const key = auHasKey(u8);

  if (!state.decoder || state.awaitingKey) {
    if (!key) { requestRecover(); return; }
    const codec = codecFromAU(u8);
    if (!codec) { requestRecover(); return; }
    if (!state.decoder || state.codecStr !== codec) {
      try {
        makeDecoder(codec);
      } catch (err) {
        console.warn('解码器配置失败', codec, err);
        return;
      }
    } else {
      try {
        state.decoder.reset();
        state.decoder.configure({
          codec, avc: { format: 'annexb' },
          optimizeForLatency: true, hardwareAcceleration: 'prefer-hardware',
        });
      } catch { /* ignore */ }
    }
    state.awaitingKey = false;
  }

  try {
    state.decoder.decode(new EncodedVideoChunk({
      type: key ? 'key' : 'delta',
      timestamp: Math.round(performance.now() * 1000),
      data: u8,
    }));
  } catch (err) {
    resetDecoder();
    requestRecover();
  }
}

function onFrame(frame) {
  state.frames += 1;
  const w = frame.displayWidth;
  const h = frame.displayHeight;
  if (el.canvas.width !== w || el.canvas.height !== h) {
    el.canvas.width = w;
    el.canvas.height = h;
  }
  try {
    ctx.drawImage(frame, 0, 0, w, h);
  } finally {
    frame.close();
  }
  if (!state.rendered) {
    state.rendered = true;
    el.empty.classList.add('hidden');
  }
}

/* ------------------------------------------------------------ 触控映射 */

const ptrs = new Map();
let lpTimer = null, lpFired = false, downSent = false, moved = false;
let startPt = null, lastPt = null;
let gesture = null, pinch = null, lastMid = null;

function twoFinger() {
  const [a, b] = [...ptrs.values()];
  return {
    mid: { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 },
    dist: Math.hypot(a.x - b.x, a.y - b.y) || 1,
  };
}

function normFromPoint(cx, cy) {
  const r = el.canvas.getBoundingClientRect();
  if (!r.width || !r.height) return { x: 0.5, y: 0.5 };
  return { x: clamp((cx - r.left) / r.width, 0, 1), y: clamp((cy - r.top) / r.height, 0, 1) };
}

function applyTransform() {
  el.canvas.style.transform = `translate(${state.panX}px, ${state.panY}px) scale(${state.scale})`;
}

function setScale(v) {
  state.scale = clamp(v, 1, 5);
  if (state.scale <= 1.02) {
    state.scale = 1;
    state.panX = 0;
    state.panY = 0;
  }
  applyTransform();
}

el.stage.addEventListener('pointerdown', (e) => {
  e.preventDefault();
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });

  if (ptrs.size === 1) {
    moved = false;
    lpFired = false;
    downSent = false;
    startPt = { x: e.clientX, y: e.clientY, t: performance.now() };
    lastPt = { x: e.clientX, y: e.clientY };

    if (state.mode === 'direct') {
      const n = normFromPoint(e.clientX, e.clientY);
      send({ t: 'move', x: n.x, y: n.y });
    }

    clearTimeout(lpTimer);
    lpTimer = setTimeout(() => {
      if (!moved && ptrs.size === 1) {
        lpFired = true;
        send({ t: 'btn', b: 2, d: 1 });
        send({ t: 'btn', b: 2, d: 0 });
        vibrate(18);
        toast('右键单击', 800);
      }
    }, 520);
  } else if (ptrs.size === 2) {
    clearTimeout(lpTimer);
    if (downSent) { send({ t: 'btn', b: 0, d: 0 }); downSent = false; }
    const st = twoFinger();
    pinch = { dist: st.dist, scale: state.scale };
    lastMid = st.mid;
    gesture = state.scale > 1.02 ? 'pan' : 'scroll';
  }
}, { passive: false });

window.addEventListener('pointermove', (e) => {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });

  if (ptrs.size >= 2) {
    const st = twoFinger();
    if (pinch && Math.abs(st.dist - pinch.dist) > 14) {
      setScale(pinch.scale * (st.dist / pinch.dist));
      gesture = 'pinch';
    }
    if (gesture === 'pan' && lastMid) {
      state.panX += st.mid.x - lastMid.x;
      state.panY += st.mid.y - lastMid.y;
      applyTransform();
    } else if (gesture === 'scroll' && lastMid) {
      const dy = (st.mid.y - lastMid.y) / 42;
      const dx = (st.mid.x - lastMid.x) / 42;
      if (Math.abs(dy) > 0.06 || Math.abs(dx) > 0.06) send({ t: 'wheel', dy, dx });
    }
    lastMid = st.mid;
    return;
  }

  if (!startPt) return;
  const dist = Math.hypot(e.clientX - startPt.x, e.clientY - startPt.y);
  if (!moved && dist > 7) {
    moved = true;
    clearTimeout(lpTimer);
  }

  if (state.mode === 'trackpad') {
    const r = el.canvas.getBoundingClientRect();
    const sens = 1.7;
    state.cursor.x = clamp(state.cursor.x + ((e.clientX - lastPt.x) / (r.width || 1)) * sens, 0, 1);
    state.cursor.y = clamp(state.cursor.y + ((e.clientY - lastPt.y) / (r.height || 1)) * sens, 0, 1);
    send({ t: 'move', x: state.cursor.x, y: state.cursor.y });
    lastPt = { x: e.clientX, y: e.clientY };
  } else {
    const n = normFromPoint(e.clientX, e.clientY);
    send({ t: 'move', x: n.x, y: n.y });
    if (moved && !downSent && !lpFired) {
      send({ t: 'btn', b: 0, d: 1 });
      downSent = true;
    }
  }
}, { passive: false });

function endPointer(e) {
  if (!ptrs.has(e.pointerId)) return;
  ptrs.delete(e.pointerId);
  clearTimeout(lpTimer);

  if (ptrs.size === 0) {
    if (downSent) {
      send({ t: 'btn', b: 0, d: 0 });
      downSent = false;
    } else if (!lpFired) {
      const dur = performance.now() - (startPt ? startPt.t : 0);
      if (state.mode === 'direct') {
        const n = normFromPoint(e.clientX, e.clientY);
        send({ t: 'move', x: n.x, y: n.y });
        send({ t: 'btn', b: 0, d: 1 });
        send({ t: 'btn', b: 0, d: 0 });
      } else if (!moved && dur < 420) {
        send({ t: 'btn', b: 0, d: 1 });
        send({ t: 'btn', b: 0, d: 0 });
      }
    }
    gesture = null; pinch = null; lastMid = null;
  } else if (ptrs.size === 1) {
    const [p] = [...ptrs.values()];
    startPt = { x: p.x, y: p.y, t: performance.now() };
    lastPt = { x: p.x, y: p.y };
    moved = true;
    gesture = null; pinch = null; lastMid = null;
  }
}

window.addEventListener('pointerup', endPointer, { passive: false });
window.addEventListener('pointercancel', endPointer, { passive: false });
el.stage.addEventListener('contextmenu', (e) => e.preventDefault());

/* ------------------------------------------------------------ 键盘输入 */

const SPECIAL = new Set([
  'Enter', 'Backspace', 'Tab', 'Escape', 'Delete', 'Insert',
  'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight',
  'Home', 'End', 'PageUp', 'PageDown', 'Space',
  'F1', 'F2', 'F3', 'F4', 'F5', 'F6', 'F7', 'F8', 'F9', 'F10', 'F11', 'F12',
]);

const sticky = { ctrl: false, alt: false, meta: false, shift: false };
const MOD_CODE = { ctrl: 'ControlLeft', alt: 'AltLeft', meta: 'MetaLeft', shift: 'ShiftLeft' };

function tapKey(code, extraMods) {
  const mods = Object.keys(sticky).filter((k) => sticky[k]);
  if (extraMods) {
    for (const [k, on] of Object.entries(extraMods)) {
      if (on && !mods.includes(k)) mods.push(k);
    }
  }
  for (const m of mods) send({ t: 'key', c: MOD_CODE[m], d: 1 });
  send({ t: 'key', c: code, d: 1 });
  send({ t: 'key', c: code, d: 0 });
  for (const m of mods.slice().reverse()) send({ t: 'key', c: MOD_CODE[m], d: 0 });
}

function focusKeyboard() {
  el.kbd.value = '';
  el.kbd.focus({ preventScroll: true });
}

el.kbd.addEventListener('input', () => {
  const v = el.kbd.value;
  if (v) {
    send({ t: 'text', s: v });
    el.kbd.value = '';
  }
});

el.kbd.addEventListener('keydown', (e) => {
  if (SPECIAL.has(e.code) || SPECIAL.has(e.key)) {
    e.preventDefault();
    tapKey(e.code, { shift: e.shiftKey, ctrl: e.ctrlKey, alt: e.altKey, meta: e.metaKey });
  }
});

el.keys.querySelectorAll('button').forEach((btn) => {
  btn.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    if (btn.dataset.focus) { focusKeyboard(); return; }
    const mod = btn.dataset.mod;
    if (mod) {
      sticky[mod] = !sticky[mod];
      btn.classList.toggle('on', sticky[mod]);
      vibrate(10);
      return;
    }
    tapKey(btn.dataset.key);
  });
});

/* -------------------------------------------------------------- 工具栏 */

$('btnKeys').addEventListener('click', () => {
  const hidden = el.keys.classList.toggle('hidden');
  $('btnKeys').classList.toggle('on', !hidden);
  if (hidden) el.kbd.blur();
});

$('btnMode').addEventListener('click', () => {
  state.mode = state.mode === 'direct' ? 'trackpad' : 'direct';
  store.set('mode', state.mode);
  updateSegs();
  toast(state.mode === 'direct' ? '直接触控：点哪按哪' : '触控板：单指移动光标，轻点=左键', 1600);
});

$('btnFit').addEventListener('click', () => {
  if (state.scale > 1.02) {
    setScale(1);
    toast('原始比例');
  } else {
    setScale(2.2);
    toast('放大 2.2×（双指拖动可平移）');
  }
});

$('btnFull').addEventListener('click', async () => {
  try {
    if (!document.fullscreenElement) {
      await document.documentElement.requestFullscreen({ navigationUI: 'hide' });
      try { await screen.orientation.lock('landscape'); } catch { /* 部分浏览器不支持 */ }
    } else {
      await document.exitFullscreen();
    }
  } catch { /* ignore */ }
});

/* -------------------------------------------------------------- 设置面板 */

$('btnSettings').addEventListener('click', () => el.settings.classList.remove('hidden'));
$('btnCloseSheet').addEventListener('click', () => el.settings.classList.add('hidden'));

document.querySelectorAll('.seg').forEach((seg) => {
  seg.addEventListener('click', (e) => {
    const btn = e.target.closest('button');
    if (!btn) return;
    const field = seg.dataset.field;
    const value = btn.dataset.value;
    if (field === 'quality') {
      state.quality = value;
      store.set('quality', value);
      sendCfg();
    } else if (field === 'fps') {
      state.fps = Number(value);
      store.set('fps', value);
      sendCfg();
    } else if (field === 'mode') {
      state.mode = value;
      store.set('mode', value);
    } else if (field === 'monitor') {
      state.monitor = Number(value);
      sendCfg();
    }
    updateSegs();
  });
});

function updateSegs() {
  document.querySelectorAll('.seg[data-field="quality"] button')
    .forEach((b) => b.classList.toggle('active', b.dataset.value === state.quality));
  document.querySelectorAll('.seg[data-field="fps"] button')
    .forEach((b) => b.classList.toggle('active', Number(b.dataset.value) === state.fps));
  document.querySelectorAll('.seg[data-field="mode"] button')
    .forEach((b) => b.classList.toggle('active', b.dataset.value === state.mode));
  document.querySelectorAll('.seg[data-field="monitor"] button')
    .forEach((b) => b.classList.toggle('active', Number(b.dataset.value) === state.monitor));
}

async function loadMonitors() {
  try {
    const res = await fetch('/api/info', { cache: 'no-store' });
    const data = await res.json();
    const mons = data.monitors || [];
    if (mons.length <= 1) { el.monitorGroup.style.display = 'none'; return; }
    el.monitorSeg.innerHTML = '';
    for (const m of mons) {
      const b = document.createElement('button');
      b.dataset.value = String(m.index);
      b.textContent = `${m.index} · ${m.width}×${m.height}`;
      el.monitorSeg.appendChild(b);
    }
    el.monitorGroup.style.display = '';
    updateSegs();
  } catch { /* ignore */ }
}

/* ---------------------------------------------------------------- 登录 */

function showLogin(msg) {
  el.login.classList.remove('hidden');
  if (msg) el.tokenInput.value = '';
  setEmpty(msg || '需要访问口令', false);
  setTimeout(() => el.tokenInput.focus(), 50);
}

$('loginBtn').addEventListener('click', () => {
  const v = (el.tokenInput.value || '').trim();
  if (!v) return;
  if (/^https?:\/\//i.test(v)) {
    location.href = v.includes('?') ? v : v.replace(/\/$/, '') + '/';
    return;
  }
  saveToken(v);
  el.login.classList.add('hidden');
  connect();
});

el.tokenInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') $('loginBtn').click();
});

/* ---------------------------------------------------------------- 启动 */

function boot() {
  const params = new URLSearchParams(location.search);
  const k = params.get('k');
  if (k) saveToken(k);
  // App 通过 URL 参数下发默认画质/帧率/显示器（覆盖本地记忆）
  const q = params.get('quality');
  if (q) { state.quality = q; store.set('quality', q); }
  const f = params.get('fps');
  if (f) { state.fps = Number(f); store.set('fps', String(f)); }
  const m = params.get('monitor');
  if (m) state.monitor = Number(m);
  history.replaceState(null, '', location.pathname);

  updateSegs();
  applyTransform();
  loadMonitors();

  if (!('VideoDecoder' in window)) {
    setEmpty('当前浏览器不支持 WebCodecs，请用 Chrome / Edge 新版，或安装 Android App', false);
    el.stats.classList.add('hidden');
    return;
  }

  connect();

  setInterval(() => {
    if (!state.connected) return;
    const fps = state.frames;
    state.frames = 0;
    const kbps = (state.bytes * 8) / 1000;
    state.bytes = 0;
    el.stats.textContent = `${fps} fps · ${fmtRate(kbps)} · ${state.rtt} ms`;
  }, 1000);

  setInterval(() => { if (state.connected) send({ t: 'ping', ts: Date.now() }); }, 3000);
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && !state.ws && !state.manualClose) {
    state.attempt = 0;
    connect();
  }
});

window.addEventListener('beforeunload', () => {
  state.manualClose = true;
  if (state.ws) state.ws.close();
  if (state.ctl) state.ctl.close();
});

boot();
