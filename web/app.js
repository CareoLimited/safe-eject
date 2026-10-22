/**
 * SafeEject dashboard
 * Shows what keeps a disk busy, why, and lets the user quit it and eject.
 */

// App state
const state = {
  volumes: [],
  selectedVolume: null,
  locks: [],
  lockStatus: 'idle',
  lockError: '',
  isLocksLoading: false,
  locksRequestId: 0,
  locksController: null,
  volumesRequestId: 0,
  actionInProgress: false,
  activeCategory: 'all',
  searchQuery: '',
  spotlight: null,
  expanded: new Set(),
  autoExpanded: false,
  autoRefresh: true,
  refreshTimer: null,
  refreshIntervalMs: 3000,
  theme: readSavedTheme() || (globalThis.matchMedia?.('(prefers-color-scheme: light)')?.matches ? 'light' : 'dark'),
};

// DOM elements
const el = {
  volumeList: document.getElementById('volume-list'),
  toolbarTitle: document.getElementById('toolbar-title'),
  diskIcon: document.getElementById('disk-icon'),
  currentVolumeName: document.getElementById('current-volume-name'),
  diskMeta: document.getElementById('disk-meta'),
  diskUsage: document.getElementById('disk-usage'),
  usageFill: document.getElementById('usage-fill'),
  usageText: document.getElementById('usage-text'),
  statusCard: document.getElementById('status-card'),
  statusIcon: document.getElementById('status-icon'),
  statusTitle: document.getElementById('status-title'),
  statusMessage: document.getElementById('status-message'),
  spotlightRow: document.getElementById('spotlight-row'),
  spotlightText: document.getElementById('spotlight-text'),
  btnSpotlight: document.getElementById('btn-spotlight'),
  locksContainer: document.getElementById('locks-container'),
  btnRefreshVolumes: document.getElementById('btn-refresh-volumes'),
  btnRefreshLocks: document.getElementById('btn-refresh-locks'),
  btnKillAll: document.getElementById('btn-kill-all'),
  btnSafeEject: document.getElementById('btn-safe-eject'),
  btnAutoRefresh: document.getElementById('btn-auto-refresh'),
  statusText: document.getElementById('status-text'),
  btnThemeToggle: document.getElementById('btn-theme-toggle'),
  themeIcon: document.getElementById('theme-icon'),
  versionTag: document.getElementById('version-tag'),
  demoBadge: document.getElementById('demo-badge'),
  filterSearch: document.getElementById('filter-search'),
  categoryFilters: document.querySelectorAll('.filter-pill'),
  counts: {
    all: document.getElementById('count-all'),
    ai: document.getElementById('count-ai'),
    dev: document.getElementById('count-dev'),
    terminal: document.getElementById('count-terminal'),
    app: document.getElementById('count-app'),
    system: document.getElementById('count-system'),
  },
  logToggle: document.getElementById('log-toggle'),
  logContent: document.getElementById('log-content'),
  lastActionText: document.getElementById('last-action-text'),
  drawerArrow: document.querySelector('.drawer-arrow'),
  modal: document.getElementById('confirm-modal'),
  modalTitle: document.getElementById('modal-title'),
  modalMessage: document.getElementById('modal-message'),
  modalDetails: document.getElementById('modal-details'),
  modalConfirm: document.getElementById('modal-confirm'),
  modalCancel: document.getElementById('modal-cancel'),
  modalClose: document.getElementById('modal-close'),
  toastContainer: document.getElementById('toast-container'),
};

// Line icons drawn on a 24px grid; they take the text color
const svg = body => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
const ICONS = {
  eject: svg('<path d="M12 5.5 5.5 14h13L12 5.5Z" fill="currentColor"/><path d="M5.5 18h13" stroke-width="2.4"/>'),
  refresh: svg('<path d="M19.5 10.5A7.5 7.5 0 0 0 6 6.8L4.5 8.5"/><path d="M4.5 4.5v4h4"/><path d="M4.5 13.5A7.5 7.5 0 0 0 18 17.2l1.5-1.7"/><path d="M19.5 19.5v-4h-4"/>'),
  search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>'),
  sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2.5 12h2M19.5 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  moon: svg('<path d="M20 14.6A8 8 0 1 1 9.4 4a6.5 6.5 0 0 0 10.6 10.6Z"/>'),
  chevron: svg('<path d="m9 5.5 6.5 6.5L9 18.5"/>'),
  chevronUp: svg('<path d="m5.5 15 6.5-6.5 6.5 6.5"/>'),
  close: svg('<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>'),
  drive: svg('<rect x="2.5" y="7" width="19" height="10" rx="2.5"/><path d="M6 12h6"/><circle cx="17.2" cy="12" r="1.1" fill="currentColor" stroke="none"/>'),
  internal: svg('<rect x="4" y="5" width="16" height="11" rx="1.8"/><path d="M2.5 19h19"/>'),
  ai: svg('<path d="M10.5 3.5 12.3 8l4.5 1.8-4.5 1.8-1.8 4.5-1.8-4.5L4.2 9.8 8.7 8l1.8-4.5Z"/><path d="M18 14.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9.9-2.1Z"/>'),
  dev: svg('<path d="m8.5 8-4 4 4 4"/><path d="m15.5 8 4 4-4 4"/><path d="m13.5 5.5-3 13"/>'),
  terminal: svg('<rect x="3" y="4.5" width="18" height="15" rx="2.5"/><path d="m7 9.5 3 2.5-3 2.5"/><path d="M12.5 15H17"/>'),
  app: svg('<rect x="4" y="4" width="7" height="7" rx="2"/><rect x="13" y="4" width="7" height="7" rx="2"/><rect x="4" y="13" width="7" height="7" rx="2"/><rect x="13" y="13" width="7" height="7" rx="2"/>'),
  system: svg('<circle cx="12" cy="12" r="2.8"/><path d="M12 3.5v2.2M12 18.3v2.2M20.5 12h-2.2M5.7 12H3.5M18 6l-1.6 1.6M7.6 16.4 6 18M18 18l-1.6-1.6M7.6 7.6 6 6"/><circle cx="12" cy="12" r="6.2"/>'),
  check: svg('<circle cx="12" cy="12" r="8.5"/><path d="m8.3 12.3 2.5 2.5 5-5.3"/>'),
  alert: svg('<path d="M10.4 4.3 2.9 17.4A1.9 1.9 0 0 0 4.5 20.2h15a1.9 1.9 0 0 0 1.6-2.8L13.6 4.3a1.9 1.9 0 0 0-3.2 0Z"/><path d="M12 9.5v4.2"/><path d="M12 17h.01"/>'),
  lock: svg('<rect x="5" y="10.5" width="14" height="10" rx="2.5"/><path d="M8.2 10.5V8a3.8 3.8 0 0 1 7.6 0v2.5"/>'),
  info: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 11v5"/><path d="M12 8h.01"/>'),
  copy: svg('<rect x="8.5" y="8.5" width="11" height="11" rx="2.5"/><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2"/>'),
  folder: svg('<path d="M3.5 7.5a2 2 0 0 1 2-2h3.8l2 2h7.2a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-9.5Z"/>'),
  stop: svg('<circle cx="12" cy="12" r="8.5"/><path d="m9 9 6 6M15 9l-6 6"/>'),
};

const CATEGORY_ICONS = { ai: ICONS.ai, dev: ICONS.dev, terminal: ICONS.terminal, app: ICONS.app, system: ICONS.system };

// How a process holds the disk, from the most to the least serious
const KIND_PRIORITY = ['database', 'program', 'mapped', 'file', 'directory', 'cwd'];

const KIND_LABELS = {
  cwd: 'Working directory',
  program: 'Runs from this disk',
  mapped: 'Loaded in memory',
  database: 'Open database',
  directory: 'Open folder',
  file: 'Open file',
  system: 'macOS service',
};

const KIND_SHORT = {
  cwd: 'cwd', program: 'program', mapped: 'mapped', database: 'database', directory: 'folder', file: 'file',
};

// Static, trusted markup: these strings never contain data from the scan
const HINTS = {
  cwdTerminal: 'This shell is sitting in a folder on this disk. Run <code>cd ~</code> in that tab, or close the tab. No need to quit anything.',
  cwd: 'It was started from a folder on this disk and holds that folder until it exits. Quit it when you are done, or start it from another folder.',
  program: 'The program itself runs from this disk, so it has to quit before the disk can be ejected.',
  mapped: 'It has a file from this disk loaded in memory. Quitting it frees the disk.',
  database: 'It has a database open on this disk. Quit it normally so it can finish writing. Force quitting can corrupt the database.',
  directory: 'It is watching or reading a folder on this disk, which is usually an editor, file watcher or indexer. Quitting it frees the disk.',
  file: 'It has a file open on this disk. Close the file in the app, or quit the app.',
  system: 'A macOS service. It lets go of the disk by itself when you eject, so you can leave it alone.',
};

// --- HELPERS ---

function readSavedTheme() {
  try {
    return localStorage.getItem('safeeject_theme');
  } catch {
    return null;
  }
}

function escapeHtml(value) {
  const replacements = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
  return String(value).replace(/[&<>"']/g, char => replacements[char]);
}

function plural(count, word) {
  return `${count} ${word}${count === 1 ? '' : 'es'}`;
}

function canEject(vol) {
  return vol && !vol.is_system_root && vol.mount_point !== '/' && (vol.is_ejectable || vol.is_external);
}

function canAct(volumePath = state.selectedVolume?.mount_point) {
  return state.selectedVolume?.mount_point === volumePath && state.lockStatus === 'ready'
    && !state.isLocksLoading && !state.actionInProgress;
}

function blockingLocks() {
  return state.locks.filter(lock => !lock.is_system);
}

function setActionInProgress(active) {
  state.actionInProgress = active;
  if (active) stopAutoRefresh();
  else if (state.autoRefresh) startAutoRefresh();
  updateLockStats();
  renderSpotlight();
  renderLocks();
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1000)));
  return `${(bytes / Math.pow(1000, i)).toFixed(1)} ${units[i]}`;
}

function originText(lock) {
  const origin = lock.origin || {};
  const parts = [];
  if (origin.parent) parts.push(`started by ${origin.parent}`);
  if (origin.app && origin.app !== lock.name) parts.push(origin.own_app ? `part of ${origin.app}` : `in ${origin.app}`);
  return parts.join(' · ');
}

function lockHint(lock) {
  if (lock.is_system) return { kind: 'system', html: HINTS.system };
  const kinds = lock.kinds || [];
  const kind = KIND_PRIORITY.find(k => kinds.includes(k)) || 'file';
  if (kind === 'cwd') return { kind, html: lock.category_id === 'terminal' ? HINTS.cwdTerminal : HINTS.cwd };
  return { kind, html: HINTS[kind] };
}

// Dims the volume's own mount path so the part that matters stands out
function pathHtml(path, mountPoint) {
  const root = mountPoint && mountPoint !== '/' ? `${mountPoint}/` : '';
  if (root && path.startsWith(root)) {
    return `<span class="path-root">${escapeHtml(root)}</span>${escapeHtml(path.slice(root.length))}`;
  }
  return escapeHtml(path);
}

function showToast(message, type = 'info') {
  const icons = { success: ICONS.check, error: ICONS.alert, warning: ICONS.alert, info: ICONS.info };
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span class="toast-icon">${icons[type] || ICONS.info}</span><div>${escapeHtml(message)}</div>`;
  el.toastContainer.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

function addLog(message, type = 'info') {
  const time = new Date().toLocaleTimeString();
  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;
  entry.textContent = `[${time}] ${message}`;
  el.logContent.prepend(entry);
  el.lastActionText.textContent = message;
}

// Alert dialog
let modalAction = null;
function openModal({ title, message, details, confirmText, isDanger = true, onConfirm }) {
  el.modalTitle.textContent = title;
  el.modalMessage.textContent = message;
  el.modalDetails.textContent = details || '';
  el.modalDetails.style.display = details ? 'block' : 'none';
  el.modalConfirm.textContent = confirmText || 'Confirm';
  el.modalConfirm.className = isDanger ? 'btn btn-danger' : 'btn btn-primary';
  modalAction = onConfirm;
  el.modal.classList.remove('hidden');
  el.modalConfirm.focus?.();
}

function closeModal() {
  el.modal.classList.add('hidden');
  modalAction = null;
}

// --- THEME ---

function applyTheme(theme) {
  state.theme = theme;
  document.body.className = theme === 'light' ? 'light-theme' : 'dark-theme';
  el.themeIcon.innerHTML = theme === 'light' ? ICONS.moon : ICONS.sun;
  try {
    localStorage.setItem('safeeject_theme', theme);
  } catch {
    // The theme just won't be remembered
  }
}

el.btnThemeToggle.addEventListener('click', () => {
  applyTheme(state.theme === 'light' ? 'dark' : 'light');
});

// --- API ---

let sessionTokenPromise = null;

async function requestJson(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json();
  if (!res.ok) {
    if (res.status === 403) sessionTokenPromise = null;
    throw new Error(data.message || data.error || `HTTP ${res.status}`);
  }
  return data;
}

async function postJson(path, payload) {
  if (!sessionTokenPromise) {
    sessionTokenPromise = requestJson('/api/session').then(data => {
      if (typeof data.token !== 'string' || !data.token) throw new Error("Couldn't get a session token.");
      return data.token;
    }).catch(err => {
      sessionTokenPromise = null;
      throw err;
    });
  }
  const token = await sessionTokenPromise;
  return requestJson(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-SafeEject-Token': token },
    body: JSON.stringify(payload),
  });
}

async function fetchStatus() {
  try {
    const data = await requestJson('/api/status');
    el.versionTag.textContent = `Version ${data.version}`;
    el.demoBadge.classList.toggle('hidden', !data.demo);
  } catch {
    // The version label is optional
  }
}

async function fetchVolumes() {
  const requestId = ++state.volumesRequestId;
  try {
    const data = await requestJson('/api/volumes');
    if (requestId !== state.volumesRequestId) return false;
    state.volumes = data.volumes || [];
    const current = state.volumes.find(v => v.mount_point === state.selectedVolume?.mount_point);
    const next = current || state.volumes.find(v => v.is_external) || state.volumes[0] || null;
    selectVolume(next);
    return true;
  } catch (err) {
    if (requestId !== state.volumesRequestId) return false;
    addLog(`Couldn't load disks: ${err.message}`, 'error');
    showToast("Couldn't load the disk list.", 'error');
    return false;
  }
}

async function fetchLocks(isBackground = false) {
  if (!state.selectedVolume || (isBackground && state.isLocksLoading)) return false;
  const vol = state.selectedVolume;
  const requestId = ++state.locksRequestId;
  state.locksController?.abort();
  const controller = new AbortController();
  state.locksController = controller;
  state.isLocksLoading = true;
  if (state.lockStatus !== 'ready') state.lockStatus = 'loading';
  updateLockStats();
  renderLocks();
  const isCurrent = () => requestId === state.locksRequestId
    && state.selectedVolume?.mount_point === vol.mount_point;

  try {
    const data = await requestJson(`/api/locks?volume=${encodeURIComponent(vol.mount_point)}`, { signal: controller.signal });
    if (!isCurrent()) return false;
    if (data.volume !== vol.mount_point || !Array.isArray(data.locks)) {
      throw new Error("The scan result doesn't match the selected disk.");
    }
    state.locks = data.locks;
    state.lockStatus = 'ready';
    state.lockError = '';

    if (!isBackground) {
      addLog(`Scanned '${vol.name}': found ${plural(state.locks.length, 'process')}.`);
    }
    return true;
  } catch (err) {
    if (!isCurrent() || err.name === 'AbortError') return false;
    state.locks = [];
    state.lockStatus = 'error';
    state.lockError = err.message;
    if (!isBackground) {
      addLog(`Scan failed: ${err.message}`, 'error');
    }
    return false;
  } finally {
    if (isCurrent()) {
      state.isLocksLoading = false;
      state.locksController = null;
      updateLockStats();
      renderLocks();
    }
  }
}

async function fetchSpotlight(vol) {
  state.spotlight = null;
  renderSpotlight();
  if (!canEject(vol)) return;
  try {
    const data = await requestJson(`/api/spotlight?volume=${encodeURIComponent(vol.mount_point)}`);
    if (state.selectedVolume?.mount_point !== vol.mount_point) return;
    state.spotlight = typeof data.indexing === 'boolean' ? data.indexing : null;
  } catch {
    state.spotlight = null;
  }
  renderSpotlight();
}

async function setSpotlight(vol, enabled) {
  if (state.selectedVolume?.mount_point !== vol.mount_point || state.actionInProgress) return false;
  setActionInProgress(true);
  try {
    if (enabled === false) addLog(`Turning off Spotlight for '${vol.name}'. macOS may ask for your password.`);
    const data = await postJson('/api/spotlight', { volume: vol.mount_point, enabled });
    if (state.selectedVolume?.mount_point === vol.mount_point) state.spotlight = data.indexing;
    showToast(data.message, 'success');
    addLog(data.message, 'success');
    return true;
  } catch (err) {
    showToast(err.message, 'error');
    addLog(err.message, 'error');
    return false;
  } finally {
    setActionInProgress(false);
  }
}

async function revealFile(path) {
  try {
    const data = await postJson('/api/reveal', { path });
    addLog(data.message);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function killProcess(pid, force = false) {
  if (!canAct()) return false;
  setActionInProgress(true);
  try {
    const data = await postJson('/api/kill', { pid, force });
    showToast(data.message, 'success');
    addLog(data.message, 'success');
    await fetchLocks(true);
    return true;
  } catch (err) {
    showToast(err.message, 'error');
    addLog(`Couldn't close PID ${pid}: ${err.message}`, 'error');
    await fetchLocks(true);
  } finally {
    setActionInProgress(false);
  }
  return false;
}

// Returns the server's result, or null when the request itself failed
async function killAllProcesses(volumePath, force = false) {
  if (!canAct(volumePath) || volumePath === '/') return null;
  setActionInProgress(true);
  try {
    addLog(`${force ? 'Force quitting' : 'Quitting'} everything on '${volumePath}'...`, 'warning');
    const data = await postJson('/api/kill_all', { volume: volumePath, force });

    if (!data.success && data.message) throw new Error(data.message);

    if (data.killed_count > 0) {
      showToast(`Closed ${plural(data.killed_count, 'process')}.`, 'success');
      addLog(`Closed ${plural(data.killed_count, 'process')}, left ${data.skipped_count} macOS service(s) alone.`, 'success');
    } else if (data.failed_count === 0) {
      showToast('There was nothing to quit.', 'info');
    }
    if (data.failed_count > 0) {
      addLog(`${plural(data.failed_count, 'process')} didn't quit.`, 'warning');
    }

    await fetchLocks(true);
    return data;
  } catch (err) {
    showToast(`Couldn't quit everything: ${err.message}`, 'error');
    await fetchLocks(true);
    return null;
  } finally {
    setActionInProgress(false);
  }
}

async function ejectVolume(volumePath) {
  if (!canAct(volumePath) || !canEject(state.selectedVolume)) return false;
  setActionInProgress(true);
  try {
    addLog(`Ejecting '${volumePath}'...`, 'info');
    const data = await postJson('/api/eject', { volume: volumePath });
    showToast(data.message, 'success');
    addLog(data.message, 'success');
    if (state.selectedVolume?.mount_point === volumePath) selectVolume(null);
    await fetchVolumes();
    return true;
  } catch (err) {
    showToast(err.message, 'error');
    addLog(`Eject failed: ${err.message}`, 'error');
    await fetchLocks(true);
  } finally {
    setActionInProgress(false);
  }
  return false;
}

// Quits everything on the disk, offers a force quit for what's left, then ejects
async function quitAndEject(vol) {
  const result = await killAllProcesses(vol.mount_point, false);
  if (!result) return false;
  if (result.failed_count > 0) {
    openModal({
      title: `${plural(result.failed_count, 'process')} didn't quit`,
      message: 'They ignored the normal quit request. Force quit them and eject? Unsaved work in them will be lost.',
      details: result.failed.map(item => `${item.name} (PID ${item.pid})`).join('\n'),
      confirmText: 'Force Quit & Eject',
      onConfirm: async () => {
        const forced = await killAllProcesses(vol.mount_point, true);
        if (forced && forced.failed_count === 0) await ejectVolume(vol.mount_point);
      },
    });
    return false;
  }
  return ejectVolume(vol.mount_point);
}

// The eject button next to a disk in the sidebar, like in Finder
async function ejectFromSidebar(vol) {
  if (state.actionInProgress) return;
  if (state.selectedVolume?.mount_point !== vol.mount_point) await selectVolume(vol);
  if (state.selectedVolume?.mount_point === vol.mount_point) handleSafeEjectClick();
}

// --- RENDERING ---

function renderVolumes() {
  if (state.volumes.length === 0) {
    el.volumeList.innerHTML = '<div class="sidebar-placeholder">No disks found.</div>';
    return;
  }

  el.volumeList.innerHTML = '';
  const groups = [
    ['External', state.volumes.filter(v => v.is_external)],
    ['Internal', state.volumes.filter(v => !v.is_external)],
  ];
  groups.forEach(([label, volumes]) => {
    if (volumes.length === 0) return;
    const heading = document.createElement('div');
    heading.className = 'volume-group';
    heading.textContent = label;
    el.volumeList.appendChild(heading);

    volumes.forEach(vol => {
      const row = document.createElement('div');
      const isSelected = state.selectedVolume?.mount_point === vol.mount_point;
      const percent = Math.max(0, Math.min(100, Number(vol.used_percent) || 0));
      row.className = `volume-row${isSelected ? ' active' : ''}`;
      row.tabIndex = 0;
      row.title = vol.mount_point;
      row.innerHTML = `
        <div class="volume-icon">${vol.is_external ? ICONS.drive : ICONS.internal}</div>
        <div class="volume-info">
          <div class="volume-name">${escapeHtml(vol.name)}</div>
          <div class="volume-bar"><div class="volume-bar-fill${percent > 85 ? ' high' : ''}" style="width: ${percent}%"></div></div>
          <div class="volume-sub">${formatBytes(vol.used_space)} of ${formatBytes(vol.total_size)}</div>
        </div>
        ${canEject(vol) ? `<button class="icon-btn small volume-eject" title="Eject ${escapeHtml(vol.name)}" aria-label="Eject ${escapeHtml(vol.name)}">${ICONS.eject}</button>` : ''}
      `;

      row.addEventListener('click', () => selectVolume(vol));
      row.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') selectVolume(vol);
      });
      row.querySelector('.volume-eject')?.addEventListener('click', event => {
        event.stopPropagation();
        ejectFromSidebar(vol);
      });
      el.volumeList.appendChild(row);
    });
  });
}

function selectVolume(vol) {
  const changed = state.selectedVolume?.mount_point !== vol?.mount_point;
  if (changed || !vol) {
    state.locksRequestId++;
    state.locksController?.abort();
    state.locksController = null;
    state.isLocksLoading = false;
    state.locks = [];
    state.lockStatus = vol ? 'loading' : 'idle';
    state.lockError = '';
    state.spotlight = null;
    state.expanded = new Set();
    state.autoExpanded = false;
    closeModal();
  }
  state.selectedVolume = vol;
  renderVolumes();
  renderDiskHeader();
  updateLockStats();
  renderSpotlight();
  renderLocks();
  if (vol && changed) {
    const scan = fetchLocks();
    fetchSpotlight(vol);
    return scan;
  }
}

function renderDiskHeader() {
  const vol = state.selectedVolume;
  el.toolbarTitle.textContent = vol?.name || '';
  el.currentVolumeName.textContent = vol?.name || 'No disk selected';
  el.diskIcon.innerHTML = vol && !vol.is_external ? ICONS.internal : ICONS.drive;
  el.diskMeta.textContent = vol
    ? [vol.is_external ? vol.bus_protocol : 'Internal', vol.fs_type, vol.mount_point].filter(Boolean).join(' · ')
    : 'Pick a disk on the left.';
  el.diskUsage.classList.toggle('hidden', !vol);
  if (vol) {
    const percent = Math.max(0, Math.min(100, Number(vol.used_percent) || 0));
    el.usageFill.style.width = `${percent}%`;
    el.usageFill.className = `usage-fill${percent > 85 ? ' high' : ''}`;
    el.usageText.textContent = `${formatBytes(vol.used_space)} of ${formatBytes(vol.total_size)} used`;
  }
}

function renderSpotlight() {
  const indexing = state.spotlight;
  el.spotlightRow.classList.toggle('hidden', typeof indexing !== 'boolean');
  el.spotlightText.textContent = indexing
    ? 'Spotlight is indexing this disk, a common reason it stays busy.'
    : "Spotlight doesn't index this disk.";
  el.btnSpotlight.textContent = indexing ? 'Turn Off Spotlight…' : 'Turn On Spotlight…';
  el.btnSpotlight.disabled = state.actionInProgress;
}

function updateLockStats() {
  const counts = { all: state.locks.length, ai: 0, dev: 0, terminal: 0, app: 0, system: 0 };
  state.locks.forEach(lock => {
    if (counts[lock.category_id] !== undefined) {
      counts[lock.category_id]++;
    }
  });

  Object.entries(counts).forEach(([key, value]) => {
    el.counts[key].textContent = value;
  });
  el.categoryFilters.forEach(btn => btn.classList.toggle('is-empty', counts[btn.dataset.cat] === 0));

  const vol = state.selectedVolume;
  const blocking = blockingLocks().length;
  const ejectable = canEject(vol);
  el.btnKillAll.disabled = !canAct() || blocking === 0 || vol?.mount_point === '/';
  el.btnSafeEject.disabled = !canAct() || !ejectable;
  el.btnKillAll.classList.toggle('hidden', !ejectable || state.lockStatus !== 'ready' || blocking === 0);
  el.btnSafeEject.classList.toggle('hidden', !ejectable || state.lockStatus === 'error');
  el.btnSafeEject.innerHTML = `${ICONS.eject}<span>${blocking > 0 ? 'Quit & Eject' : 'Eject'}</span>`;

  let tone;
  let icon;
  let title;
  let message;
  if (!vol) {
    [tone, icon, title, message] = ['neutral', ICONS.drive, 'No disk selected', "Pick a disk to see what's keeping it busy."];
  } else if (state.lockStatus === 'error') {
    [tone, icon, title, message] = ['error', ICONS.alert, "The scan couldn't finish", state.lockError];
  } else if (state.lockStatus !== 'ready') {
    [tone, icon, title, message] = ['loading', '<div class="spinner"></div>', `Scanning ${vol.name}…`, 'Looking for apps and processes that use this disk.'];
  } else if (!ejectable) {
    [tone, icon, title, message] = ['neutral', ICONS.internal, 'This is your system disk',
      `It can't be ejected. ${plural(state.locks.length, 'process')} found using it.`];
  } else if (blocking > 0) {
    [tone, icon, title, message] = ['busy', ICONS.lock,
      `${plural(blocking, 'process')} ${blocking === 1 ? 'is' : 'are'} keeping this disk busy`,
      'Quit them all at once, or use the tip under each one.'];
  } else {
    [tone, icon, title, message] = ['ready', ICONS.check, 'Ready to eject',
      state.locks.length ? 'Only macOS services are left, and they let go by themselves.' : 'Nothing is using this disk.'];
  }
  el.statusCard.className = `status-card tone-${tone}`;
  el.statusIcon.innerHTML = icon;
  el.statusTitle.textContent = title;
  el.statusMessage.textContent = message;
}

function listEmpty(icon, title, message) {
  return `<div class="list-empty"><div class="empty-icon">${icon}</div><h3>${escapeHtml(title)}</h3><p>${escapeHtml(message)}</p></div>`;
}

function renderLocks() {
  if (!state.selectedVolume || state.lockStatus === 'error' || state.lockStatus === 'idle') {
    el.locksContainer.innerHTML = '';
    return;
  }
  if (state.lockStatus !== 'ready') {
    const skeleton = '<div class="skeleton"><div class="skeleton-icon"></div><div class="skeleton-lines"><div class="skeleton-line"></div><div class="skeleton-line short"></div></div></div>';
    el.locksContainer.innerHTML = skeleton.repeat(3);
    return;
  }
  const volumePath = state.selectedVolume.mount_point;
  const query = state.searchQuery.toLowerCase().trim();
  const filtered = state.locks.filter(lock => {
    if (state.activeCategory !== 'all' && lock.category_id !== state.activeCategory) {
      return false;
    }
    if (query) {
      const matchName = lock.name.toLowerCase().includes(query);
      const matchPid = String(lock.pid).includes(query);
      const matchUser = lock.user.toLowerCase().includes(query);
      const matchFile = lock.files && lock.files.some(f => f.path.toLowerCase().includes(query));
      return matchName || matchPid || matchUser || matchFile;
    }
    return true;
  });

  if (state.locks.length === 0) {
    el.locksContainer.innerHTML = listEmpty(ICONS.check, 'Nothing is using this disk', 'No process had files open on it in the last scan.');
    return;
  }
  if (filtered.length === 0) {
    el.locksContainer.innerHTML = listEmpty(ICONS.search, 'No matches', 'Nothing matches your search or filter.');
    return;
  }

  // Open the first row once per disk, so the reason and tip are visible right away
  if (!state.autoExpanded) {
    const first = state.locks.find(lock => !lock.is_system) || state.locks[0];
    state.expanded.add(first.pid);
    state.autoExpanded = true;
  }

  el.locksContainer.innerHTML = '';
  filtered.forEach(lock => {
    const card = document.createElement('div');
    const expanded = state.expanded.has(lock.pid);
    card.className = `proc${expanded ? ' expanded' : ''}`;

    const hint = lockHint(lock);
    const origin = originText(lock);
    const category = CATEGORY_ICONS[lock.category_id] ? lock.category_id : 'app';
    const sub = [origin, lock.category_label, `PID ${lock.pid}`, lock.user].filter(Boolean).join(' · ');
    const filesHtml = lock.files.map((f, index) => `
      <div class="file-row">
        <span class="file-kind kind-${escapeHtml(f.kind)}">${escapeHtml(KIND_SHORT[f.kind] || f.kind)}</span>
        <span class="file-path" title="${escapeHtml(f.path)}">${pathHtml(f.path, volumePath)}</span>
        <button class="icon-btn small btn-reveal" data-index="${index}" title="Show in Finder" aria-label="Show in Finder">${ICONS.folder}</button>
      </div>`).join('');
    const extraCount = lock.file_count - lock.files.length;
    const extraText = extraCount > 0 ? `<div class="file-more">…and ${extraCount} more</div>` : '';

    card.innerHTML = `
      <div class="proc-row">
        <button class="proc-disclosure" aria-expanded="${expanded}" aria-label="Show details">${ICONS.chevron}</button>
        <div class="proc-icon cat-${category}">${CATEGORY_ICONS[category]}</div>
        <div class="proc-main">
          <div class="proc-title">
            <span class="proc-name" title="${escapeHtml(lock.executable || lock.name)}">${escapeHtml(lock.name)}</span>
            <span class="reason-tag kind-${hint.kind}">${KIND_LABELS[hint.kind]}</span>
          </div>
          <div class="proc-sub">${escapeHtml(sub)}</div>
        </div>
        <div class="proc-actions">
          ${lock.is_system ? '' : `<button class="btn btn-secondary btn-small btn-action-terminate" ${canAct() ? '' : 'disabled'}>Quit</button>`}
        </div>
      </div>
      <div class="proc-detail">
        <p class="proc-hint">${hint.html}</p>
        <div class="file-list">
          ${filesHtml}
          ${extraText}
        </div>
        <div class="detail-actions">
          <button class="link-btn process-pid">${ICONS.copy}Copy PID</button>
          ${lock.is_system
            // macOS services let go by themselves, so quitting them stays out of the way
            ? `<button class="link-btn danger btn-action-terminate" ${canAct() ? '' : 'disabled'}>${ICONS.stop}Quit Anyway…</button>`
            : `<button class="link-btn danger btn-action-force" ${canAct() ? '' : 'disabled'}>${ICONS.stop}Force Quit…</button>`}
        </div>
      </div>
    `;

    const row = card.querySelector('.proc-row');
    const disclosure = card.querySelector('.proc-disclosure');
    const toggle = () => {
      const open = !state.expanded.has(lock.pid);
      if (open) state.expanded.add(lock.pid);
      else state.expanded.delete(lock.pid);
      card.classList.toggle('expanded', open);
      disclosure.setAttribute?.('aria-expanded', String(open));
    };
    row.addEventListener('click', event => {
      if (event.target.closest?.('.proc-actions')) return;
      toggle();
    });

    card.querySelector('.file-list').addEventListener('click', event => {
      const button = event.target.closest?.('.btn-reveal');
      const file = button && lock.files[Number(button.dataset.index)];
      if (file) revealFile(file.path);
    });

    card.querySelector('.process-pid').addEventListener('click', async () => {
      try {
        await globalThis.navigator.clipboard.writeText(String(lock.pid));
        showToast(`Copied PID ${lock.pid}.`, 'info');
      } catch {
        showToast("Couldn't copy to the clipboard.", 'error');
      }
    });

    card.querySelector('.btn-action-terminate').addEventListener('click', () => {
      if (!canAct(volumePath)) return;
      const quit = async () => {
        if (await killProcess(lock.pid, false) || !state.locks.some(l => l.pid === lock.pid)) return;
        openModal({
          title: `${lock.name} didn't quit`,
          message: 'It ignored the normal quit request. Force quit it? Unsaved work in it will be lost.',
          details: `PID ${lock.pid}`,
          confirmText: 'Force Quit',
          onConfirm: () => canAct(volumePath) && killProcess(lock.pid, true),
        });
      };
      if (lock.is_system) {
        openModal({
          title: 'Quit a macOS service?',
          message: `'${lock.name}' is part of macOS. It lets go of the disk by itself when you eject, and quitting it can cause brief glitches.`,
          details: `PID ${lock.pid}`,
          confirmText: 'Quit Anyway',
          onConfirm: () => canAct(volumePath) && killProcess(lock.pid, false),
        });
      } else {
        quit();
      }
    });

    card.querySelector('.btn-action-force')?.addEventListener('click', () => {
      if (!canAct(volumePath)) return;
      openModal({
        title: `Force quit ${lock.name}?`,
        message: 'It will be stopped immediately (kill -9). Unsaved work in it will be lost.',
        details: `PID ${lock.pid}`,
        confirmText: 'Force Quit',
        onConfirm: () => canAct(volumePath) && killProcess(lock.pid, true),
      });
    });

    el.locksContainer.appendChild(card);
  });
}

// --- ACTIONS ---

function handleSafeEjectClick() {
  if (!canAct() || !canEject(state.selectedVolume)) return;
  const vol = state.selectedVolume;
  const blocking = blockingLocks();

  if (blocking.length === 0) return ejectVolume(vol.mount_point);
  openModal({
    title: `Quit ${plural(blocking.length, 'process')} and eject?`,
    message: `These are still using '${vol.name}'. SafeEject will ask them to quit, offer a force quit for any that refuse, then eject the disk.`,
    details: blocking.map(lock => `${lock.name} (PID ${lock.pid})`).join('\n'),
    confirmText: 'Quit & Eject',
    isDanger: false,
    onConfirm: () => quitAndEject(vol),
  });
}

el.btnSafeEject.addEventListener('click', handleSafeEjectClick);

el.btnKillAll.addEventListener('click', () => {
  const blocking = blockingLocks();
  if (!canAct() || state.selectedVolume.mount_point === '/' || blocking.length === 0) return;
  const vol = state.selectedVolume;

  openModal({
    title: `Quit ${plural(blocking.length, 'process')}?`,
    message: `Everything using '${vol.name}' will be asked to quit. macOS services are left alone.`,
    details: blocking.map(lock => `${lock.name} (PID ${lock.pid})`).join('\n'),
    confirmText: 'Quit All',
    onConfirm: () => killAllProcesses(vol.mount_point, false),
  });
});

el.btnSpotlight.addEventListener('click', () => {
  const vol = state.selectedVolume;
  if (!vol || typeof state.spotlight !== 'boolean' || state.actionInProgress) return;
  const turnOff = state.spotlight;
  openModal({
    title: turnOff ? `Turn off Spotlight for '${vol.name}'?` : `Turn Spotlight back on for '${vol.name}'?`,
    message: turnOff
      ? "Spotlight indexing often keeps a disk busy. Turning it off stops macOS from indexing this disk, so its files won't show up in Spotlight search. macOS may ask for your password."
      : 'macOS will index this disk again so its files show up in Spotlight search. macOS may ask for your password.',
    confirmText: turnOff ? 'Turn Off' : 'Turn On',
    isDanger: false,
    onConfirm: () => setSpotlight(vol, !turnOff),
  });
});

el.btnRefreshVolumes.addEventListener('click', async () => {
  if (await fetchVolumes()) showToast('Disks reloaded.', 'info');
});

el.btnRefreshLocks.addEventListener('click', async () => {
  if (await fetchLocks()) showToast('Rescanned.', 'info');
});

el.filterSearch.addEventListener('input', (e) => {
  state.searchQuery = e.target.value;
  renderLocks();
});

el.categoryFilters.forEach(btn => {
  btn.addEventListener('click', () => {
    el.categoryFilters.forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.activeCategory = btn.dataset.cat;
    renderLocks();
  });
});

// Live mode
function renderLiveStatus() {
  const paused = !state.autoRefresh;
  el.statusText.textContent = paused ? 'Paused' : 'Live';
  el.btnAutoRefresh.classList.toggle('paused', paused);
  el.btnAutoRefresh.title = paused ? 'Resume rescanning every 3 seconds' : 'Rescanning every 3 seconds. Click to pause.';
}

el.btnAutoRefresh.addEventListener('click', () => {
  state.autoRefresh = !state.autoRefresh;
  renderLiveStatus();
  if (state.autoRefresh) {
    startAutoRefresh();
    showToast('Live mode on: rescanning every 3 seconds.', 'info');
  } else {
    stopAutoRefresh();
    showToast('Live mode paused.', 'info');
  }
});

function startAutoRefresh() {
  stopAutoRefresh();
  // Don't run lsof every few seconds for a tab nobody is looking at
  if (state.actionInProgress || document.hidden) return;
  state.refreshTimer = setInterval(() => {
    if (state.selectedVolume) {
      fetchLocks(true);
    }
  }, state.refreshIntervalMs);
}

function stopAutoRefresh() {
  if (state.refreshTimer) {
    clearInterval(state.refreshTimer);
    state.refreshTimer = null;
  }
}

document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    stopAutoRefresh();
  } else if (state.autoRefresh && !state.actionInProgress) {
    startAutoRefresh();
    fetchLocks(true);
  }
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && modalAction) closeModal();
});

el.logToggle.addEventListener('click', () => {
  el.logContent.classList.toggle('open');
  el.drawerArrow.classList.toggle('open');
});

el.modalConfirm.addEventListener('click', () => {
  if (modalAction) modalAction();
  closeModal();
});
el.modalCancel.addEventListener('click', closeModal);
el.modalClose.addEventListener('click', closeModal);
el.modal.addEventListener('click', event => {
  if (event.target === el.modal) closeModal();
});

// Static icons in the page markup
document.querySelectorAll('[data-icon]').forEach(node => {
  node.innerHTML = ICONS[node.dataset.icon] || '';
});

// --- START ---

applyTheme(state.theme);
fetchStatus();
fetchVolumes();
startAutoRefresh();
