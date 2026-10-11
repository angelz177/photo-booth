// HTTP client for the Python booth service. All paths are relative so the
// same files work when served from a sub-path (e.g. GitHub Pages).

async function request(method, url, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (data.detail) message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
    } catch (_) { /* no JSON body */ }
    throw new Error(message);
  }
  return res.json();
}

export const api = {
  status: () => request('GET', 'api/status'),
  config: () => request('GET', 'api/config'),
  saveConfig: (config) => request('PUT', 'api/config', config),
  verifyPin: (pin) => request('POST', 'api/admin/verify', { pin }),
  printers: () => request('GET', 'api/printers'),
  createSession: () => request('POST', 'api/sessions'),
  capture: (id) => request('POST', `api/sessions/${id}/capture`),
  finish: (id) => request('POST', `api/sessions/${id}/finish`),
  print: (id) => request('POST', `api/sessions/${id}/print`),
  sessions: (limit = 100) => request('GET', `api/sessions?limit=${limit}`),
};

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
