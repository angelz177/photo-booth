// Picks the backend: the Python service when it answers, otherwise the
// in-browser demo (device camera, canvas compositing, photos kept locally).
import { api as serverApi } from './api.js';
import { demoApi } from './api-demo.js';

const serverBackend = {
  ...serverApi,
  mode: 'server',
  async startLive(els) {
    if (els.video) els.video.classList.add('hidden');
    els.img.classList.remove('hidden');
    els.img.src = `api/live?t=${Date.now()}`;
  },
  stopLive(els) {
    // Dropping the src closes the MJPEG connection so the camera can idle.
    els.img.removeAttribute('src');
  },
};

export async function selectBackend() {
  try {
    const res = await fetch('api/status', { cache: 'no-store' });
    if (res.ok) {
      const status = await res.json();
      if (status && status.camera) return { api: serverBackend, status };
    }
  } catch (_) { /* no service here */ }
  return { api: demoApi, status: await demoApi.status() };
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
