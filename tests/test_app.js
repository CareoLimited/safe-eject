// Isolated dashboard tests: no browser, network, disk or process actions are started.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const startup = 'applyTheme(state.theme);\nfetchStatus();\nfetchVolumes();\nstartAutoRefresh();';
const appSource = fs.readFileSync(path.join(__dirname, '../web/app.js'), 'utf8');
assert.ok(appSource.includes(startup), 'app.js startup block changed; update the tests');
const source = appSource.replace(startup, '');

class Element {
  constructor() {
    this.children = [];
    this.selectors = new Map();
    this.listeners = new Map();
    this.style = {};
    this.dataset = {};
    this.textContent = '';
    const classes = new Set();
    this.classList = {
      add: name => classes.add(name), remove: name => classes.delete(name),
      contains: name => classes.has(name),
      toggle: (name, force = !classes.has(name)) => force ? classes.add(name) : classes.delete(name),
    };
  }
  set innerHTML(value) { this.html = value; this.children = []; this.selectors.clear(); }
  get innerHTML() { return this.html || ''; }
  appendChild(child) { this.children.push(child); }
  prepend(child) { this.children.unshift(child); }
  addEventListener(type, callback) { this.listeners.set(type, callback); }
  querySelector(selector) {
    if (!this.selectors.has(selector)) this.selectors.set(selector, new Element());
    return this.selectors.get(selector);
  }
}

const volumeA = { name: 'A', mount_point: '/Volumes/A', is_external: true, is_ejectable: true, used_percent: 50 };
const volumeB = { ...volumeA, name: 'B', mount_point: '/Volumes/B' };
const lock = { pid: 99999, name: 'node', user: 'test', files: [{ path: '/Volumes/A/test.txt', kind: 'file' }],
  kinds: ['file'], origin: {}, category_id: 'dev', category_label: 'Developer tool', file_count: 1, is_system: false };
const systemLock = { ...lock, pid: 389, name: 'mds_stores', category_id: 'system', category_label: 'macOS service', is_system: true };

function response(data, status = 200) {
  return { ok: status >= 200 && status < 300, status, json: async () => data };
}

function app() {
  const elements = new Map();
  const get = id => {
    if (!elements.has(id)) elements.set(id, new Element());
    return elements.get(id);
  };
  const documentListeners = new Map();
  const context = vm.createContext({
    document: { getElementById: get, querySelectorAll: () => [], querySelector: get,
      createElement: () => new Element(), body: new Element(), hidden: false,
      addEventListener: (type, callback) => documentListeners.set(type, callback) },
    localStorage: { getItem: () => null, setItem() {} },
    fetch: () => { throw new Error('Real network request blocked'); },
    AbortController, setInterval: () => 1, clearInterval() {}, setTimeout: () => 1,
  });
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  function ready(locks = []) {
    context.testVolume = volumeA;
    context.testLocks = locks;
    run(`state.selectedVolume = testVolume; state.locks = testLocks; state.lockStatus = 'ready';
      state.isLocksLoading = false; state.actionInProgress = false; updateLockStats(); renderLocks();`);
  }
  function deferredFetch() {
    const requests = [];
    context.fetch = (url, options = {}) => new Promise(resolve => {
      requests.push({ url, options, resolve: (data, status) => resolve(response(data, status)) });
    });
    return requests;
  }
  // Answers each URL prefix with a canned response; anything else fails the test
  function routes(table) {
    const calls = [];
    context.fetch = async (url, options = {}) => {
      calls.push({ url, body: options.body ? JSON.parse(options.body) : null });
      if (url === '/api/session') return response({ token: 'test-token' });
      for (const [prefix, handler] of Object.entries(table)) {
        if (url.startsWith(prefix)) return handler(calls.filter(call => call.url.startsWith(prefix)).length, options);
      }
      if (url.startsWith('/api/spotlight?')) return response({ indexing: null });
      throw new Error('Unexpected request: ' + url);
    };
    return calls;
  }
  return { context, run, get, ready, deferredFetch, routes, documentListeners };
}

const lockRequests = requests => requests.filter(r => r.url.startsWith('/api/locks?'));

const tests = [];
const test = (name, callback) => tests.push({ name, callback });

test('File, process, disk and toast text is never rendered as HTML', () => {
  const a = app();
  const malicious = '"><img src=x onerror=alert(1)>';
  a.ready([{ ...lock, name: malicious, user: malicious, executable: malicious,
    origin: { app: malicious, parent: malicious }, kinds: [malicious],
    files: [{ path: '/Volumes/A/' + malicious, kind: malicious }] }]);
  let markup = a.get('locks-container').children[0].innerHTML;
  assert.ok(!markup.includes('<img'));
  assert.ok(markup.includes('&lt;img'));
  assert.ok(markup.includes('&quot;'));
  a.context.message = malicious;
  a.run("showToast(message, 'error')");
  assert.ok(!a.get('toast-container').children[0].innerHTML.includes('<img'));
  a.context.testVolumes = [{ ...volumeA, name: malicious, bus_protocol: malicious }];
  a.run('state.volumes = testVolumes; renderVolumes()');
  markup = a.get('volume-list').children.map(child => child.innerHTML + child.textContent).join('');
  assert.ok(markup.includes('&lt;img'));
  assert.ok(!markup.includes('<img'));
});

test("An old disk's scan can't replace the new disk's processes", async () => {
  const a = app();
  const requests = a.deferredFetch();
  a.context.volumeA = volumeA;
  a.context.volumeB = volumeB;
  const oldScan = a.run('selectVolume(volumeA)');
  const newScan = a.run('selectVolume(volumeB)');
  const [oldRequest, newRequest] = lockRequests(requests);
  assert.equal(oldRequest.options.signal.aborted, true);
  newRequest.resolve({ volume: volumeB.mount_point, locks: [{ ...lock, pid: 222 }] });
  await newScan;
  oldRequest.resolve({ volume: volumeA.mount_point, locks: [{ ...lock, pid: 111 }] });
  await oldScan;
  assert.equal(a.run('state.selectedVolume.mount_point'), volumeB.mount_point);
  assert.equal(a.run('state.locks[0].pid'), 222);
});

test('A late, older scan of the same disk is ignored', async () => {
  const a = app();
  a.ready();
  const requests = a.deferredFetch();
  const oldScan = a.run('fetchLocks()');
  const newScan = a.run('fetchLocks()');
  requests[1].resolve({ volume: volumeA.mount_point, locks: [{ ...lock, pid: 222 }] });
  await newScan;
  requests[0].resolve({ volume: volumeA.mount_point, locks: [{ ...lock, pid: 111 }] });
  await oldScan;
  assert.equal(a.run('state.locks[0].pid'), 222);
});

test("Live mode doesn't keep cancelling a slow scan", async () => {
  const a = app();
  a.ready();
  const requests = a.deferredFetch();
  const first = a.run('fetchLocks(true)');
  assert.equal(await a.run('fetchLocks(true)'), false);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].options.signal.aborted, false);
  requests[0].resolve({ volume: volumeA.mount_point, locks: [] });
  await first;
  assert.equal(a.run('state.lockStatus'), 'ready');
});

test('Picking a disk clears old processes and any open dialog right away', async () => {
  const a = app();
  a.ready([lock]);
  a.run('openModal({title: "test", onConfirm: () => {}})');
  a.context.volumeB = volumeB;
  const requests = a.deferredFetch();
  const scan = a.run('selectVolume(volumeB)');
  assert.equal(a.run('state.locks.length'), 0);
  assert.equal(a.run('modalAction'), null);
  assert.equal(a.get('btn-kill-all').disabled, true);
  assert.equal(a.get('btn-safe-eject').disabled, true);
  lockRequests(requests)[0].resolve({ volume: volumeB.mount_point, locks: [] });
  await scan;
});

test("A failed scan doesn't look like a clean disk and disables actions", async () => {
  const a = app();
  a.ready([lock]);
  a.context.fetch = async () => response({ error: '<img src=x onerror=alert(1)>' }, 503);
  assert.equal(await a.run('fetchLocks()'), false);
  assert.equal(a.run('state.lockStatus'), 'error');
  assert.equal(a.get('btn-safe-eject').disabled, true);
  assert.equal(a.get('btn-kill-all').disabled, true);
  assert.match(a.get('status-title').textContent, /couldn't finish/);
  assert.equal(a.get('status-message').textContent, '<img src=x onerror=alert(1)>');
  assert.equal(a.get('locks-container').innerHTML, '');
  assert.ok(a.get('btn-safe-eject').classList.contains('hidden'));
});

test('A response for another disk is treated as an error', async () => {
  const a = app();
  a.ready();
  a.context.fetch = async () => response({ volume: volumeB.mount_point, locks: [lock] });
  assert.equal(await a.run('fetchLocks()'), false);
  assert.equal(a.run('state.lockStatus'), 'error');
});

test('Quit all and eject stay off for the system disk', () => {
  const a = app();
  a.ready();
  a.run("state.selectedVolume = {name:'System',mount_point:'/',is_system_root:true}; updateLockStats();renderLocks()");
  assert.equal(a.get('btn-safe-eject').disabled, true);
  assert.equal(a.get('btn-kill-all').disabled, true);
  assert.ok(a.get('btn-safe-eject').classList.contains('hidden'));
  assert.match(a.get('status-title').textContent, /system disk/);
});

test('POST requests carry the session token, which is fetched once', async () => {
  const a = app();
  const calls = [];
  a.context.fetch = async (url, options) => {
    calls.push({ url, options });
    return response(url === '/api/session' ? { token: 'test-token' } : { success: true });
  };
  await a.run("postJson('/api/kill', {pid:99999,force:false})");
  await a.run("postJson('/api/kill', {pid:99998,force:false})");
  assert.equal(calls.filter(call => call.url === '/api/session').length, 1);
  for (const call of calls.filter(call => call.url === '/api/kill')) {
    assert.equal(call.options.headers['X-SafeEject-Token'], 'test-token');
    assert.equal(call.options.headers['Content-Type'], 'application/json');
  }
});

test("Quit & eject doesn't eject when a process refuses or the scan fails", async () => {
  for (const result of [
    { success: false, killed_count: 0, failed_count: 1, skipped_count: 0, failed: [{ name: 'node', pid: 99999 }] },
    { success: false, killed_count: 0, failed_count: 0, skipped_count: 0, message: 'scan failed' },
  ]) {
    const a = app();
    a.ready([lock]);
    const calls = a.routes({
      '/api/kill_all': () => response(result),
      '/api/locks?': () => response({ volume: volumeA.mount_point, locks: [lock] }),
    });
    a.run('handleSafeEjectClick()');
    await a.run('modalAction()');
    assert.ok(!calls.some(call => call.url === '/api/eject'));
    assert.equal(a.run('state.actionInProgress'), false);
  }
});

test('Quit & eject ejects the same disk after everything quits', async () => {
  const a = app();
  a.ready([lock, systemLock]);
  const calls = a.routes({
    '/api/kill_all': () => response({ success: true, killed_count: 1, failed_count: 0, skipped_count: 1, failed: [] }),
    '/api/locks?': () => response({ volume: volumeA.mount_point, locks: [systemLock] }),
    '/api/eject': () => response({ success: true, message: 'mock ejected' }),
    '/api/volumes': () => response({ volumes: [] }),
  });
  a.run('handleSafeEjectClick()');
  await a.run('modalAction()');
  const urls = calls.map(call => call.url);
  assert.equal(urls.filter(url => url === '/api/eject').length, 1);
  assert.ok(urls.indexOf('/api/kill_all') < urls.indexOf('/api/eject'));
  assert.equal(a.run('state.selectedVolume'), null);
  assert.equal(a.run('state.actionInProgress'), false);
  assert.equal(a.get('btn-safe-eject').disabled, true);
});

test('Processes that refuse to quit are force quit only after a second confirmation', async () => {
  const a = app();
  a.ready([lock]);
  const calls = a.routes({
    '/api/kill_all': count => response(count === 1
      ? { success: false, killed_count: 0, failed_count: 1, skipped_count: 0, failed: [{ name: 'node', pid: 99999 }] }
      : { success: true, killed_count: 1, failed_count: 0, skipped_count: 0, failed: [] }),
    '/api/locks?': () => response({ volume: volumeA.mount_point,
      locks: calls.filter(call => call.url === '/api/kill_all').length < 2 ? [lock] : [] }),
    '/api/eject': () => response({ success: true, message: 'mock ejected' }),
    '/api/volumes': () => response({ volumes: [] }),
  });
  a.run('handleSafeEjectClick()');
  await a.run('modalAction()');
  assert.ok(!calls.some(call => call.url === '/api/eject'));
  assert.match(a.get('modal-confirm').textContent, /Force Quit/);
  await a.run('modalAction()');
  const killAll = calls.filter(call => call.url === '/api/kill_all');
  assert.deepEqual(killAll.map(call => call.body.force), [false, true]);
  assert.equal(calls.filter(call => call.url === '/api/eject').length, 1);
});

test('When only macOS services are left, Eject ejects right away', async () => {
  const a = app();
  a.ready([systemLock]);
  assert.equal(a.get('btn-kill-all').disabled, true);
  assert.equal(a.get('btn-safe-eject').disabled, false);
  assert.equal(a.get('status-title').textContent, 'Ready to eject');
  assert.ok(a.get('btn-kill-all').classList.contains('hidden'));
  const calls = a.routes({
    '/api/eject': () => response({ success: true, message: 'mock ejected' }),
    '/api/volumes': () => response({ volumes: [] }),
  });
  await a.run('handleSafeEjectClick()');
  assert.equal(a.run('modalAction'), null);
  assert.equal(calls.filter(call => call.url === '/api/eject').length, 1);
});

test('Each process shows why it holds the disk and where it came from', () => {
  const a = app();
  const shell = { ...lock, name: 'zsh', category_id: 'terminal', kinds: ['cwd'],
    files: [{ path: '/Volumes/A/photos', kind: 'cwd' }], origin: { app: 'iTerm', parent: null } };
  const python = { ...lock, pid: 3, name: 'python3.12', kinds: ['cwd', 'database'],
    origin: { app: 'iTerm', parent: 'claude' } };
  a.ready([shell, python]);
  const [shellCard, pythonCard] = a.get('locks-container').children.map(card => card.innerHTML);
  assert.ok(shellCard.includes('<code>cd ~</code>'));
  assert.ok(shellCard.includes('in iTerm'));
  assert.ok(pythonCard.includes('Open database'));
  assert.ok(pythonCard.includes('started by claude · in iTerm'));
});

test('The first process opens by itself and paths are shown relative to the disk', () => {
  const a = app();
  a.ready([lock, { ...lock, pid: 2 }]);
  const [first, second] = a.get('locks-container').children;
  assert.ok(first.className.includes('expanded'));
  assert.ok(!second.className.includes('expanded'));
  assert.ok(first.innerHTML.includes('<span class="path-root">/Volumes/A/</span>test.txt'));
});

test('The status card tells how many processes keep the disk busy', () => {
  const a = app();
  a.ready([lock, { ...lock, pid: 2 }, systemLock]);
  assert.equal(a.get('status-title').textContent, '2 processes are keeping this disk busy');
  assert.ok(a.get('btn-safe-eject').innerHTML.includes('Quit &amp; Eject') || a.get('btn-safe-eject').innerHTML.includes('Quit & Eject'));
  assert.equal(a.get('btn-kill-all').classList.contains('hidden'), false);
});

test('Turning off Spotlight asks first and sends enabled: false', async () => {
  const a = app();
  a.ready([lock]);
  a.run('state.spotlight = true; renderSpotlight()');
  assert.equal(a.get('spotlight-row').classList.contains('hidden'), false);
  const calls = a.routes({
    '/api/spotlight': () => response({ success: true, indexing: false, message: 'off' }),
    '/api/locks?': () => response({ volume: volumeA.mount_point, locks: [lock] }),
  });
  a.get('btn-spotlight').listeners.get('click')();
  assert.equal(calls.length, 0);
  await a.run('modalAction()');
  const post = calls.find(call => call.url === '/api/spotlight');
  assert.deepEqual(post.body, { volume: volumeA.mount_point, enabled: false });
  assert.equal(a.run('state.spotlight'), false);
});

test('Live mode stops while the tab is hidden', () => {
  const a = app();
  let timers = 0;
  a.context.setInterval = () => ++timers;
  a.run('state.autoRefresh = true; document.hidden = true; startAutoRefresh()');
  assert.equal(timers, 0);
  a.run('document.hidden = false');
  a.context.fetch = () => new Promise(() => {});
  a.documentListeners.get('visibilitychange')();
  assert.equal(timers, 1);
});

test('Clearing the selection drops pending scan results and disables actions', async () => {
  const a = app();
  a.ready([lock]);
  const requests = a.deferredFetch();
  const scan = a.run('fetchLocks()');
  a.run('selectVolume(null)');
  requests[0].resolve({ volume: volumeA.mount_point, locks: [lock] });
  await scan;
  assert.equal(a.run('state.selectedVolume'), null);
  assert.equal(a.run('state.locks.length'), 0);
  assert.equal(a.get('btn-safe-eject').disabled, true);
  assert.equal(a.get('btn-kill-all').disabled, true);
});

(async () => {
  for (const { name, callback } of tests) {
    await callback();
    console.log('OK:', name);
  }
  console.log(`${tests.length} dashboard tests passed.`);
})().catch(error => { console.error(error); process.exitCode = 1; });
