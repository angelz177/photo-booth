import { selectBackend } from './backend.js';

const $ = (sel) => document.querySelector(sel);
let api = null;
let lastKey = '';

function formatTime(iso) {
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

async function refresh() {
  const [status, sessions] = await Promise.all([api.status(), api.sessions(200)]);
  $('#gallery-title').textContent = status.config.event_name || 'Gallery';
  document.title = `${status.config.event_name} · Gallery`;
  const key = sessions.map((s) => `${s.id}:${s.printed}`).join(',');
  if (key === lastKey) return;
  lastKey = key;
  const grid = $('#gallery');
  grid.innerHTML = '';
  $('#gallery-empty').classList.toggle('hidden', sessions.length > 0);
  for (const s of sessions) {
    const a = document.createElement('a');
    a.href = s.share_page_url ? `s/${s.id}` : s.share_url;
    if (!s.share_page_url) a.target = '_blank';
    a.className = 'tile';
    a.innerHTML = `<img src="${s.share_url}" alt="" loading="lazy"><span>${formatTime(s.created_at)}${s.printed ? ' · printed' : ''}</span>`;
    grid.appendChild(a);
  }
}

(async () => {
  ({ api } = await selectBackend());
  await refresh();
  setInterval(refresh, 10000);
})();
