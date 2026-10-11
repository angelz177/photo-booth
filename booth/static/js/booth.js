import { api, sleep } from './api.js';

const $ = (sel) => document.querySelector(sel);
const live = $('#live');

let status = null;
let config = null;
let session = null;
let busy = false;
let reviewTimer = null;
let reviewDeadline = 0;

// ---------- screens ----------

function show(name) {
  document.querySelectorAll('.screen').forEach((el) => el.classList.toggle('active', el.id === `screen-${name}`));
}

function toast(text, ms = 2500) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.add('hidden'), ms);
}

function showError(message) {
  stopLive();
  $('#error-msg').textContent = message;
  show('error');
}

// ---------- status polling ----------

async function refreshStatus() {
  try {
    status = await api.status();
  } catch (err) {
    status = { camera: { connected: false, error: 'Booth service unreachable' }, config: config || {} };
  }
  config = status.config;
  $('#attract-title').textContent = config.event_name || 'Photo Booth';
  document.body.classList.toggle('mirror', !!config.mirror_preview);
  $('#btn-print').classList.toggle('hidden', !config.print_enabled);
  const banner = $('#camera-banner');
  if (status.camera.connected) {
    banner.classList.add('hidden');
  } else {
    banner.textContent = status.camera.error ? `Camera: ${status.camera.error}` : 'Connecting to camera…';
    banner.classList.remove('hidden');
  }
}

// ---------- live view ----------

function startLive() {
  live.src = `/api/live?t=${Date.now()}`;
}

function stopLive() {
  // Setting an empty src closes the MJPEG connection so the camera can idle.
  live.removeAttribute('src');
}

// ---------- session flow ----------

async function countdown(seconds) {
  const el = $('#countdown');
  el.classList.remove('hidden');
  for (let n = seconds; n >= 1; n--) {
    el.textContent = String(n);
    el.classList.remove('tick');
    void el.offsetWidth; // restart the animation
    el.classList.add('tick');
    await sleep(1000);
  }
  el.classList.add('hidden');
}

function flash() {
  const el = $('#flash');
  el.classList.add('on');
  setTimeout(() => el.classList.remove('on'), 350);
}

function addThumb(url) {
  const img = document.createElement('img');
  img.src = url;
  img.alt = '';
  $('#filmstrip').appendChild(img);
}

async function runSession() {
  if (busy) return;
  if (!status || !status.camera.connected) {
    toast(status?.camera?.error || 'Camera is not connected yet');
    return;
  }
  busy = true;
  requestFullscreen();
  requestWakeLock();
  $('#filmstrip').innerHTML = '';
  $('#live-msg').textContent = 'Get ready!';
  $('#shot-counter').textContent = '';
  show('live');
  startLive();
  try {
    session = await api.createSession();
    const total = session.shots_required;
    await sleep(1500);
    for (let i = 1; i <= total; i++) {
      $('#shot-counter').textContent = total > 1 ? `Photo ${i} of ${total}` : '';
      $('#live-msg').textContent = '';
      await countdown(config.countdown_seconds || 3);
      flash();
      const shot = await api.capture(session.id);
      addThumb(shot.url);
      if (i < total) {
        $('#live-msg').textContent = i === total - 1 ? 'Last one!' : 'Nice!';
        await sleep((config.shot_delay_seconds ?? 1.5) * 1000);
      }
    }
    show('processing');
    stopLive();
    session = await api.finish(session.id);
    showReview();
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    busy = false;
  }
}

// ---------- review ----------

function showReview() {
  $('#review-img').src = session.share_url;
  $('#btn-print').disabled = false;
  $('#btn-print').textContent = 'Print';
  show('review');
  reviewDeadline = Date.now() + (config.review_timeout_seconds || 45) * 1000;
  clearInterval(reviewTimer);
  reviewTimer = setInterval(() => {
    const left = Math.max(0, Math.ceil((reviewDeadline - Date.now()) / 1000));
    $('#review-timer').textContent = `Back to start in ${left}s`;
    if (left <= 0) finishReview();
  }, 500);
}

function extendReview() {
  reviewDeadline = Date.now() + (config.review_timeout_seconds || 45) * 1000;
}

function finishReview() {
  clearInterval(reviewTimer);
  $('#share-modal').classList.add('hidden');
  session = null;
  show('attract');
}

async function printPhoto() {
  if (!session) return;
  extendReview();
  const btn = $('#btn-print');
  btn.disabled = true;
  btn.textContent = 'Printing…';
  try {
    await api.print(session.id);
    btn.textContent = 'Sent to printer ✓';
    toast('Your print is on its way!');
  } catch (err) {
    btn.disabled = false;
    btn.textContent = 'Print';
    toast(`Print failed: ${err.message}`, 5000);
  }
}

function sharePhoto() {
  if (!session) return;
  extendReview();
  $('#qr-img').src = `${session.qr_url}?t=${Date.now()}`;
  $('#share-url').textContent = session.share_page_url || '';
  $('#share-modal').classList.remove('hidden');
}

// ---------- kiosk helpers ----------

function requestFullscreen() {
  const el = document.documentElement;
  if (!document.fullscreenElement && el.requestFullscreen) {
    el.requestFullscreen().catch(() => {});
  }
}

let wakeLock = null;
async function requestWakeLock() {
  try {
    if ('wakeLock' in navigator && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    }
  } catch (_) { /* not supported or denied */ }
}

// Hidden admin access: hold the top-left corner for two seconds, then enter the PIN.
let holdTimer = null;
let pin = '';

function openPinPad() {
  pin = '';
  $('#pin-display').textContent = ' ';
  $('#pin-modal').classList.remove('hidden');
}

async function submitPin() {
  try {
    const result = await api.verifyPin(pin);
    if (result.ok) {
      window.location.href = '/admin';
      return;
    }
  } catch (_) { /* fall through */ }
  pin = '';
  $('#pin-display').textContent = 'Wrong PIN';
}

function bindEvents() {
  $('#screen-attract').addEventListener('click', runSession);
  $('#btn-print').addEventListener('click', printPhoto);
  $('#btn-share').addEventListener('click', sharePhoto);
  $('#btn-share-close').addEventListener('click', () => { extendReview(); $('#share-modal').classList.add('hidden'); });
  $('#btn-done').addEventListener('click', finishReview);
  $('#btn-error-ok').addEventListener('click', () => show('attract'));
  $('#screen-review').addEventListener('click', extendReview, true);

  const hotspot = $('#admin-hotspot');
  const cancelHold = () => clearTimeout(holdTimer);
  hotspot.addEventListener('pointerdown', (e) => {
    e.stopPropagation();
    holdTimer = setTimeout(openPinPad, 2000);
  });
  hotspot.addEventListener('pointerup', cancelHold);
  hotspot.addEventListener('pointerleave', cancelHold);
  hotspot.addEventListener('pointercancel', cancelHold);
  hotspot.addEventListener('click', (e) => e.stopPropagation());

  $('#pin-modal').querySelectorAll('[data-pin]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.pin;
      if (key === 'clear') pin = '';
      else if (key === 'ok') return submitPin();
      else if (pin.length < 8) pin += key;
      $('#pin-display').textContent = pin ? '•'.repeat(pin.length) : ' ';
    });
  });
  $('#btn-pin-cancel').addEventListener('click', () => $('#pin-modal').classList.add('hidden'));

  document.addEventListener('contextmenu', (e) => e.preventDefault());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) requestWakeLock(); });
}

bindEvents();
refreshStatus();
setInterval(refreshStatus, 3000);
