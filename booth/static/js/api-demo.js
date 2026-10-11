// In-browser demo backend. Same method names as the service API, so the booth,
// settings and gallery pages run unchanged on static hosting (GitHub Pages):
//   * live view and capture use this device's camera (getUserMedia)
//   * strips are composed in a canvas (compose.js)
//   * settings live in localStorage, finished sessions in IndexedDB
//   * "print" opens the browser's print dialog, "share" saves the file
import { compose, LAYOUT_SHOTS } from './compose.js';
import { db } from './db.js';

const CONFIG_KEY = 'booth.demo.config';
const DEFAULTS = {
  event_name: 'My Event', layout: 'strip', countdown_seconds: 3, shot_delay_seconds: 1.5,
  mirror_preview: true, footer_text: '', review_timeout_seconds: 45, print_enabled: true,
  printer_name: '', print_copies: 1, admin_pin: '1234', share_base_url: '',
};

const sessions = new Map();
let stream = null;
let video = null;

function loadConfig() {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(CONFIG_KEY) || '{}') }; } catch (_) { return { ...DEFAULTS }; }
}

function cameraStatus() {
  const supported = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  const track = stream && stream.getVideoTracks()[0];
  return {
    adapter: 'browser',
    model: track ? track.label || 'Device camera' : 'Device camera (demo mode)',
    connected: supported,
    error: supported ? null : 'This browser cannot access a camera. Use Chrome, Edge or Safari over HTTPS.',
    viewers: stream ? 1 : 0,
    frame_seq: 0,
  };
}

function sessionId() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}-${Math.random().toString(16).slice(2, 6)}`;
}

function toJson(s) {
  return {
    id: s.id, event_name: s.event_name, layout: s.layout, shots_required: s.shots_required,
    created_at: s.created_at, printed: s.printed || 0,
    shots: s.shots.map((_, i) => `shot-${i + 1}.jpg`),
    shot_urls: s.shots.map((b) => URL.createObjectURL(b)),
    composite: s.print ? 'composite.jpg' : null,
    share: s.share ? 'share.jpg' : null,
    composite_url: s.print ? URL.createObjectURL(s.print) : null,
    share_url: s.share ? URL.createObjectURL(s.share) : null,
    qr_url: null, // nothing to point a phone at on static hosting
    share_page_url: null,
  };
}

async function getSession(id) {
  const s = sessions.get(id) || (await db.get(id));
  if (!s) throw new Error('Unknown session');
  return s;
}

const toBlob = (canvas) => new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.92));

export const demoApi = {
  mode: 'demo',

  async status() {
    const config = loadConfig();
    const { admin_pin, ...publicConfig } = config;
    return { version: 'demo', camera: cameraStatus(), config: publicConfig, shots: LAYOUT_SHOTS[config.layout], layouts: LAYOUT_SHOTS };
  },
  async config() { return loadConfig(); },
  async saveConfig(config) {
    const merged = { ...DEFAULTS, ...config };
    localStorage.setItem(CONFIG_KEY, JSON.stringify(merged));
    return merged;
  },
  async verifyPin(pin) { return { ok: String(pin) === String(loadConfig().admin_pin) }; },
  async printers() { return { printers: [] }; },

  async startLive(els) {
    if (!stream) {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1920 }, height: { ideal: 1080 }, facingMode: 'user' },
        audio: false,
      });
    }
    video = els.video;
    els.img.classList.add('hidden');
    video.classList.remove('hidden');
    video.srcObject = stream;
    await video.play().catch(() => {});
  },
  stopLive(els) {
    if (els.video) {
      els.video.pause();
      els.video.srcObject = null;
      els.video.classList.add('hidden');
    }
    els.img.classList.remove('hidden');
    if (stream) stream.getTracks().forEach((t) => t.stop());
    stream = null;
    video = null;
  },

  async createSession() {
    const config = loadConfig();
    const s = {
      id: sessionId(), event_name: config.event_name, layout: config.layout,
      shots_required: LAYOUT_SHOTS[config.layout], created_at: new Date().toISOString(),
      shots: [], print: null, share: null, printed: 0,
    };
    sessions.set(s.id, s);
    return toJson(s);
  },

  async capture(id) {
    const s = await getSession(id);
    if (s.shots.length >= s.shots_required) throw new Error('All shots already taken');
    if (!video || !video.videoWidth) throw new Error('Live view is not running');
    const canvas = document.createElement('canvas');
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext('2d').drawImage(video, 0, 0);
    const blob = await toBlob(canvas);
    s.shots.push(blob);
    return { index: s.shots.length, remaining: s.shots_required - s.shots.length, url: URL.createObjectURL(blob) };
  },

  async finish(id) {
    const s = await getSession(id);
    if (s.shots.length < s.shots_required) throw new Error(`Need ${s.shots_required} shots, have ${s.shots.length}`);
    const { print, share } = await compose(s.layout, s.shots, s.event_name, loadConfig().footer_text);
    s.print = print;
    s.share = share;
    await db.put({ ...s, shots: [] }); // keep the gallery small: composites only
    return toJson(s);
  },

  async print(id) {
    const s = await getSession(id);
    if (!s.print) throw new Error('Session has no composite yet');
    const url = URL.createObjectURL(s.print);
    const frame = document.createElement('iframe');
    frame.setAttribute('aria-hidden', 'true');
    frame.style.cssText = 'position:fixed;width:0;height:0;border:0;visibility:hidden';
    document.body.appendChild(frame);
    await new Promise((resolve) => {
      frame.onload = resolve;
      frame.srcdoc = `<!doctype html><html><head><style>
        @page { size: 4in 6in; margin: 0 } html, body { margin: 0; height: 100% }
        img { width: 100%; height: 100%; object-fit: contain }
      </style></head><body><img src="${url}" onload="setTimeout(() => { window.focus(); window.print(); }, 100)"></body></html>`;
    });
    setTimeout(() => { frame.remove(); URL.revokeObjectURL(url); }, 60000);
    s.printed = (s.printed || 0) + 1;
    await db.put({ ...s, shots: [] });
    return { ok: true, job: 'browser print dialog', printed: s.printed };
  },

  async sessions(limit = 100) {
    const rows = await db.all();
    const event = loadConfig().event_name;
    return rows.filter((r) => r.share && r.event_name === event).slice(0, limit).map(toJson);
  },
};
