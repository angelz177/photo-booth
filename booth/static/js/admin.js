import { selectBackend } from './backend.js';

const $ = (sel) => document.querySelector(sel);
const form = $('#config-form');
const liveEls = { img: $('#admin-live'), video: $('#admin-live-video') };
let api = null;
let previewOn = false;

function fillForm(config) {
  for (const [key, value] of Object.entries(config)) {
    const field = form.elements[key];
    if (!field) continue;
    if (field.type === 'checkbox') field.checked = !!value;
    else field.value = value ?? '';
  }
}

function readForm() {
  const config = {};
  for (const field of form.elements) {
    if (!field.name) continue;
    if (field.type === 'checkbox') config[field.name] = field.checked;
    else if (field.type === 'number') config[field.name] = Number(field.value);
    else config[field.name] = field.value;
  }
  return config;
}

async function loadPrinters(selected) {
  const select = form.elements.printer_name;
  try {
    const { printers } = await api.printers();
    for (const name of printers) {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name;
      select.appendChild(opt);
    }
  } catch (_) { /* printing unavailable */ }
  if (selected && ![...select.options].some((o) => o.value === selected)) {
    const opt = document.createElement('option');
    opt.value = selected;
    opt.textContent = `${selected} (not found)`;
    select.appendChild(opt);
  }
  select.value = selected || '';
}

async function refreshCamera() {
  try {
    const { camera } = await api.status();
    $('#cam-adapter').textContent = camera.adapter;
    $('#cam-model').textContent = camera.model || '—';
    $('#cam-connected').textContent = camera.connected ? 'Connected' : 'Not connected';
    $('#cam-connected').className = camera.connected ? 'ok' : 'bad';
    $('#cam-frames').textContent = `${camera.frame_seq} (${camera.viewers} viewer${camera.viewers === 1 ? '' : 's'})`;
    $('#cam-error').textContent = camera.error || '';
  } catch (err) {
    $('#cam-connected').textContent = 'Service unreachable';
    $('#cam-connected').className = 'bad';
  }
}

async function testCapture() {
  const btn = $('#btn-test-capture');
  btn.disabled = true;
  btn.textContent = 'Capturing…';
  try {
    if (!previewOn) await setPreview(true);
    const session = await api.createSession();
    const shot = await api.capture(session.id);
    $('#test-img').src = shot.url.startsWith('blob:') ? shot.url : `${shot.url}?t=${Date.now()}`;
    $('#test-result').classList.remove('hidden');
  } catch (err) {
    alert(`Capture failed: ${err.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Test capture';
  }
}

async function setPreview(on) {
  previewOn = on;
  try {
    if (on) await api.startLive(liveEls);
    else api.stopLive(liveEls);
  } catch (err) {
    previewOn = false;
    $('#cam-error').textContent = err.message;
  }
  $('#btn-preview-toggle').textContent = previewOn ? 'Stop preview' : 'Start preview';
}

form.addEventListener('submit', async (e) => {
  e.preventDefault();
  const statusEl = $('#save-status');
  statusEl.textContent = 'Saving…';
  try {
    await api.saveConfig(readForm());
    statusEl.textContent = 'Saved ✓';
  } catch (err) {
    statusEl.textContent = `Error: ${err.message}`;
  }
});

$('#btn-test-capture').addEventListener('click', testCapture);
$('#btn-preview-toggle').addEventListener('click', () => setPreview(!previewOn));

(async () => {
  ({ api } = await selectBackend());
  $('#demo-note').classList.toggle('hidden', api.mode !== 'demo');
  const config = await api.config();
  fillForm(config);
  await loadPrinters(config.printer_name);
  await setPreview(true);
  refreshCamera();
  setInterval(refreshCamera, 2000);
})();
