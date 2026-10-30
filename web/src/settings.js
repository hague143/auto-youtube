/* Persistent, browser-only settings. No plaintext keys in localStorage,
   sessionStorage, URLs or build output. The browser profile can still use them. */
(() => {
  let database;
  let pending = Promise.resolve();
  function open() {
    if (!database) database = new Promise((resolve, reject) => {
      if (!window.indexedDB || !window.crypto?.subtle) {
        reject(Error('This browser cannot securely save settings. Use a normal browser window on HTTPS.'));
        return;
      }
      const request = indexedDB.open('faceless-studio-settings', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('settings');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(Error('Browser settings storage is unavailable.'));
      request.onblocked = () => reject(Error('Close other studio tabs and try saving settings again.'));
    });
    return database;
  }
  async function transaction(mode, action) {
    const db = await open();
    return new Promise((resolve, reject) => {
      const tx = db.transaction('settings', mode);
      const request = action(tx.objectStore('settings'));
      tx.oncomplete = () => resolve(request.result);
      tx.onerror = tx.onabort = () => reject(Error('Settings could not be saved in this browser.'));
    });
  }
  function serial(action) {
    const next = pending.catch(() => {}).then(action);
    pending = next;
    return next;
  }
  window.STUDIO_SETTINGS = Object.freeze({
    load: () => serial(async () => {
      const record = await transaction('readonly', store => store.get('connection'));
      if (!record) return null;
      try {
        const plain = await crypto.subtle.decrypt({name: 'AES-GCM', iv: record.iv}, record.key, record.cipher);
        return JSON.parse(new TextDecoder().decode(plain));
      } catch {
        throw Error('Saved settings could not be read. Clear saved settings and enter them again.');
      }
    }),
    save: value => serial(async () => {
      const key = await crypto.subtle.generateKey({name: 'AES-GCM', length: 256}, false, ['encrypt', 'decrypt']);
      const iv = crypto.getRandomValues(new Uint8Array(12));
      const cipher = await crypto.subtle.encrypt({name: 'AES-GCM', iv}, key, new TextEncoder().encode(JSON.stringify(value)));
      await transaction('readwrite', store => store.put({key, iv, cipher}, 'connection'));
    }),
    clear: () => serial(() => transaction('readwrite', store => store.delete('connection'))),
  });
})();
