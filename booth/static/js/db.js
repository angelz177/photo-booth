// Tiny IndexedDB wrapper for demo-mode sessions. Falls back to memory when
// IndexedDB is unavailable (private browsing, old browsers).
const DB_NAME = 'photo-booth-demo';
const STORE = 'sessions';
const memory = new Map();

function open() {
  return new Promise((resolve, reject) => {
    if (!('indexedDB' in window)) return reject(new Error('no IndexedDB'));
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: 'id' });
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function run(mode, fn) {
  const database = await open();
  return new Promise((resolve, reject) => {
    const tx = database.transaction(STORE, mode);
    const req = fn(tx.objectStore(STORE));
    tx.oncomplete = () => resolve(req.result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  });
}

export const db = {
  async put(record) {
    memory.set(record.id, record);
    try { await run('readwrite', (store) => store.put(record)); } catch (_) { /* memory only */ }
    return record;
  },
  async get(id) {
    if (memory.has(id)) return memory.get(id);
    try { return await run('readonly', (store) => store.get(id)); } catch (_) { return undefined; }
  },
  async all() {
    try {
      const rows = await run('readonly', (store) => store.getAll());
      return rows.sort((a, b) => (a.id < b.id ? 1 : -1));
    } catch (_) {
      return [...memory.values()].sort((a, b) => (a.id < b.id ? 1 : -1));
    }
  },
};
