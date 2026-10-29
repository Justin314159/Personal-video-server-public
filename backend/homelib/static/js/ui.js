// Small DOM + formatting helpers shared by every screen.

// h('div.card.row', {onclick}, child, 'text', [children]) -> Element
export function h(tag, attrs, ...children) {
  const [name, ...classes] = tag.split('.');
  const el = document.createElement(name || 'div');
  if (classes.length) el.className = classes.join(' ');
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className += (el.className ? ' ' : '') + v;
    else if (k === 'style' && typeof v === 'object') {
      for (const [prop, val] of Object.entries(v)) {
        if (prop.startsWith('--')) el.style.setProperty(prop, val); else el.style[prop] = val;
      }
    }
    else if (k === 'html') el.innerHTML = v;
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}

// Like el.replaceChildren(), but skips null/false and flattens arrays (as h() does).
export function put(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children) {
    if (c === null || c === undefined || c === false) continue;
    if (Array.isArray(c)) append(el, c);
    else el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

const ICONS = {
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
  sparkle: '<path d="M12 3l1.8 4.9L19 9.7l-5.2 1.8L12 16.4l-1.8-4.9L5 9.7l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
  queue: '<path d="M4 6h16M4 12h10M4 18h10"/><path d="m17 14 4 3-4 3z"/>',
  download: '<path d="M12 4v11"/><path d="m7 10 5 5 5-5"/><path d="M5 20h14"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  back: '<path d="m15 18-6-6 6-6"/>',
  chev: '<path d="m9 18 6-6-6-6"/>',
  play: '<path d="M7 4v16l13-8z" fill="currentColor"/>',
  pause: '<path d="M7 4h3v16H7zM14 4h3v16h-3z" fill="currentColor"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  up: '<path d="m18 15-6-6-6 6"/>',
  down: '<path d="m6 9 6 6 6-6"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
  like: '<path d="M7 10v11H3V10zM7 10l4-8a3 3 0 0 1 3 3v4h5.5a2 2 0 0 1 2 2.3l-1.4 8A2 2 0 0 1 18.1 21H7"/>',
  dislike: '<path d="M17 14V3h4v11zM17 14l-4 8a3 3 0 0 1-3-3v-4H4.5a2 2 0 0 1-2-2.3l1.4-8A2 2 0 0 1 5.9 3H17"/>',
  pin: '<path d="M12 17v5M5 17h14l-2-4V5H7v8z"/>',
  more: '<circle cx="5" cy="12" r="1.6" fill="currentColor"/><circle cx="12" cy="12" r="1.6" fill="currentColor"/><circle cx="19" cy="12" r="1.6" fill="currentColor"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
  warn: '<path d="M12 3 2 21h20z"/><path d="M12 10v5M12 18v.5"/>',
  mute: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="m23 9-6 6M17 9l6 6"/>',
  sound: '<path d="M11 5 6 9H2v6h4l5 4z"/><path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/>',
  palette: '<circle cx="12" cy="12" r="9"/><circle cx="8" cy="10" r="1.3" fill="currentColor"/><circle cx="12" cy="7.5" r="1.3" fill="currentColor"/><circle cx="16" cy="10" r="1.3" fill="currentColor"/><path d="M12 21a3 3 0 0 1 0-6h2a3 3 0 0 0 3-3"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/>',
  users: '<circle cx="9" cy="8" r="4"/><path d="M1 21a8 8 0 0 1 16 0"/><path d="M17 4a4 4 0 0 1 0 8M23 21a8 8 0 0 0-5-7.4"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  trashclock: '<circle cx="12" cy="13" r="8"/><path d="M12 9v4l3 2M9 2h6"/>',
  film: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4"/>',
  bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  export: '<path d="M12 15V3M7 8l5-5 5 5"/><path d="M5 14v6h14v-6"/>',
  shorts: '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="m10.5 9 4 3-4 3z" fill="currentColor"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  eye: '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
  logo: '<rect x="2.5" y="5" width="19" height="14" rx="4" fill="currentColor" opacity=".18"/><rect x="2.5" y="5" width="19" height="14" rx="4"/><path d="m10 9 5 3-5 3z" fill="currentColor"/>',
  music: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  prev: '<path d="M19 20 9 12l10-8z" fill="currentColor"/><path d="M5 19V5"/>',
  next: '<path d="m5 4 10 8-10 8z" fill="currentColor"/><path d="M19 5v14"/>',
  phone: '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M12 8v7M9 12l3 3 3-3"/>',
  wifi: '<path d="M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M2 9a15 15 0 0 1 20 0"/><circle cx="12" cy="19.5" r="1" fill="currentColor"/>',
  cc: '<rect x="2.5" y="5" width="19" height="14" rx="3"/><path d="M10.5 10.2a2.4 2.4 0 1 0 0 3.6M17.5 10.2a2.4 2.4 0 1 0 0 3.6"/>',
  speed: '<path d="M12 14l4-5"/><path d="M3.5 18a9.5 9.5 0 1 1 17 0"/>',
  pip: '<rect x="2.5" y="4.5" width="19" height="15" rx="2.5"/><rect x="12" y="11.5" width="7" height="5" rx="1" fill="currentColor"/>',
  headphones: '<path d="M3 18v-5a9 9 0 0 1 18 0v5"/><rect x="3" y="14" width="4" height="7" rx="1.5"/><rect x="17" y="14" width="4" height="7" rx="1.5"/>',
  fullscreen: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  listadd: '<path d="M3 6h12M3 12h12M3 18h7"/><path d="M18 14v7M14.5 17.5h7"/>',
  checkcircle: '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/>',
  text: '<path d="M4 6h16M4 10h16M4 14h10M4 18h13"/>',
  copy: '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h3"/>',
  camera: '<path d="M3 8a2 2 0 0 1 2-2h2.5l1.5-2h6l1.5 2H19a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><circle cx="12" cy="13" r="3.5"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13.5 6.5 4 4"/>',
  library: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  replay: '<path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5"/>',
  upload: '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M5 20h14"/>',
  airplay: '<path d="M6 17H4a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-2"/><path d="m12 15 5 6H7z" fill="currentColor"/>',
};

export function icon(name, cls = '') {
  const span = document.createElement('span');
  span.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="${cls}">${ICONS[name] || ''}</svg>`;
  return span.firstChild;
}

export function fmtDuration(s) {
  if (s == null || isNaN(s)) return '';
  s = Math.round(s);
  const hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60), ss = s % 60;
  const p = (n) => String(n).padStart(2, '0');
  return hh ? `${hh}:${p(mm)}:${p(ss)}` : `${mm}:${p(ss)}`;
}

export function fmtBytes(b) {
  if (b == null) return '';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
}

export function fmtViews(n) {
  if (!n) return '';
  if (n >= 1e6) return `${(n / 1e6).toFixed(n >= 1e7 ? 0 : 1)}M views`;
  if (n >= 1e3) return `${Math.round(n / 1e3)}K views`;
  return `${n} views`;
}

export function fmtAgo(ts) {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return 'just now';
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export function fmtLeft(ts) {
  const d = ts - Date.now() / 1000;
  if (d <= 0) return 'soon';
  if (d < 3600) return `${Math.max(1, Math.round(d / 60))}m`;
  return `${Math.round(d / 3600)}h`;
}

export function initials(name) {
  const clean = (name || '?').replace(/[\[\]]/g, '').trim();
  return (clean[0] || '?').toUpperCase();
}

// The profile photo if there is one, otherwise the initial on the accent colour.
export function avatar(profile, cls = '') {
  const el = h(`span.avatar${cls ? '.' + cls : ''}`, { style: { '--a': profile.accent }, 'aria-hidden': 'true' }, initials(profile.name));
  if (profile.avatar) {
    const img = h('img', { src: profile.avatar, alt: '', decoding: 'async' });
    img.onerror = () => img.remove();  // fall back to the initial
    el.classList.add('photo');
    el.append(img);
  }
  return el;
}

// Copy text to the clipboard. Older iOS needs the hidden-textarea fallback.
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const ta = h('textarea', { readonly: '', style: { position: 'fixed', top: '0', left: '0', opacity: '0' } });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    let ok = false;
    try { ok = document.execCommand('copy'); } catch { /* not supported */ }
    ta.remove();
    return ok;
  }
}

let toastEl, toastTimer;
// toast('Saved', 2600, 'check') shows a small check mark for completed actions.
export function toast(msg, ms = 2600, iconName = null) {
  if (!toastEl) {
    toastEl = h('div.toast', { role: 'status', 'aria-live': 'polite' });
    document.body.append(toastEl);
  }
  toastEl.replaceChildren(...(iconName ? [icon(iconName)] : []), h('span', msg));
  toastEl.classList.remove('show');
  void toastEl.offsetWidth; // restart the pop animation
  toastEl.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toastEl.classList.remove('show'), ms);
}

// Bottom sheet. Returns { el, close }. content may be a function(close) -> Node.
export function sheet(content, { onClose } = {}) {
  const backdrop = h('div.sheet-backdrop');
  const el = h('div.sheet', { role: 'dialog', 'aria-modal': 'true' }, h('div.grab'));
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const close = () => {
    document.removeEventListener('keydown', onKey);
    el.classList.remove('open');
    backdrop.classList.remove('open');
    setTimeout(() => { el.remove(); backdrop.remove(); }, 260);
    window.removeEventListener('hashchange', close);
    onClose && onClose();
  };
  el.append(typeof content === 'function' ? content(close) : content);
  backdrop.addEventListener('click', close);
  window.addEventListener('hashchange', close);
  document.addEventListener('keydown', onKey);
  document.body.append(backdrop, el);
  requestAnimationFrame(() => { backdrop.classList.add('open'); el.classList.add('open'); });
  return { el, close };
}

export function confirmSheet(title, body, okLabel, danger = true) {
  return new Promise((resolve) => {
    let answered = false;
    sheet((close) => h('div.stack',
      h('h2', title), h('p.muted', body),
      h(`button.btn.block${danger ? '.danger' : '.primary'}`, { onclick: () => { answered = true; resolve(true); close(); } }, okLabel),
      h('button.btn.block', { onclick: () => close() }, 'Cancel'),
    ), { onClose: () => { if (!answered) resolve(false); } });
  });
}

export function thumbEl(v, { bar, badge, shade, watched } = {}) {
  const img = h('img', { src: v.thumb, alt: '', loading: 'lazy', decoding: 'async' });
  const t = h('div.thumb', img);
  img.addEventListener('load', () => t.classList.add('loaded'), { once: true });
  img.addEventListener('error', () => t.classList.add('noimg'), { once: true });
  if (v.duration && !v.is_short) t.append(h('span.dur', fmtDuration(v.duration)));
  if (v.is_short && !badge) t.append(h('span.badge', 'Short'));
  if (badge) t.append(h('span.badge', badge));
  if (watched) t.append(h('span.watched', icon('check'), 'Watched'));
  if (bar || watched) t.append(h('div.bar-track', h('div.bar', { style: { width: `${watched ? 100 : Math.min(100, bar * 100)}%` } })));
  if (shade) t.append(h('div.shade', shade));
  return t;
}

// Progress (0..1) and watched flag of a decorated video for the current profile.
export function watchInfo(v, profileId) {
  const ws = (v.watch || {})[profileId];
  return { ws, watched: !!(ws && ws.watched), pos: ws && v.duration && !ws.watched ? ws.position / v.duration : 0 };
}

// ---- loading placeholders --------------------------------------------------------
export function skeleton(kind = 'grid', n = 4) {
  const one = {
    grid: () => h('div.sk-card', h('div.sk.sk-thumb'), h('div.sk.sk-line'), h('div.sk.sk-line.short')),
    list: () => h('div.sk-row', h('div.sk.sk-thumb.small'), h('div.grow', h('div.sk.sk-line'), h('div.sk.sk-line.short'))),
    row: () => h('div.sk-card.narrow', h('div.sk.sk-thumb'), h('div.sk.sk-line')),
  }[kind];
  return h(`div.skeleton.${kind}`, { 'aria-busy': 'true', 'aria-label': 'Loading' }, Array.from({ length: n }, one));
}

export function errorState(e, retry) {
  return h('div.empty', icon('warn'), h('h3', e && e.code === 'offline' ? 'Can’t reach the server' : 'That didn’t load'),
    h('p', (e && e.message) || 'Something went wrong.'),
    retry ? h('button.btn', { onclick: retry }, icon('refresh'), 'Try again') : null);
}

// ---- pull to refresh -------------------------------------------------------------
// Drag down at the top of the page to reload. Returns a function that detaches it.
export function pullToRefresh(onRefresh) {
  const spin = h('div.ptr-spin', icon('refresh'));
  const ind = h('div.ptr', { 'aria-hidden': 'true' }, spin);
  document.body.append(ind);
  const TH = 64;
  let startY = null, d = 0, busy = false;
  const reset = () => { ind.classList.remove('armed', 'busy'); ind.style.transform = ''; ind.style.opacity = ''; d = 0; };
  const start = (e) => {
    startY = (busy || window.scrollY > 0 || e.touches.length !== 1 || document.querySelector('.sheet.open')) ? null : e.touches[0].clientY;
  };
  const move = (e) => {
    if (startY === null) return;
    if (window.scrollY > 0) { startY = null; reset(); return; }
    d = Math.max(0, Math.min(110, (e.touches[0].clientY - startY) * 0.5));
    ind.style.transform = `translate(-50%, ${d}px)`;
    ind.style.opacity = String(Math.min(1, d / 40));
    spin.style.transform = `rotate(${d * 4}deg)`;
    ind.classList.toggle('armed', d >= TH);
  };
  const end = async () => {
    if (startY === null) return;
    startY = null;
    if (d < TH) { reset(); return; }
    busy = true;
    ind.classList.add('busy');
    ind.style.transform = `translate(-50%, ${TH}px)`;
    try { await onRefresh(); } catch (e) { toast(e.message || 'Couldn’t refresh'); }
    busy = false;
    reset();
  };
  window.addEventListener('touchstart', start, { passive: true });
  window.addEventListener('touchmove', move, { passive: true });
  window.addEventListener('touchend', end);
  window.addEventListener('touchcancel', end);
  return () => {
    window.removeEventListener('touchstart', start);
    window.removeEventListener('touchmove', move);
    window.removeEventListener('touchend', end);
    window.removeEventListener('touchcancel', end);
    ind.remove();
  };
}

// Ask for a short text (playlist names). Resolves to the text or null.
export function promptSheet(title, { value = '', placeholder = '', okLabel = 'Save', extra = null } = {}) {
  return new Promise((resolve) => {
    let done = false;
    sheet((close) => {
      const input = h('input', { value, placeholder, maxlength: 60, 'aria-label': title, enterkeyhint: 'done' });
      const submit = (e) => {
        e.preventDefault();
        const v = input.value.trim();
        if (!v) { input.focus(); return; }
        done = true; resolve(v); close();
      };
      setTimeout(() => input.focus(), 280);
      return h('form.stack', { onsubmit: submit }, h('h2', title), h('label.field', input), extra,
        h('button.btn.primary.block', { type: 'submit' }, okLabel),
        h('button.btn.block', { type: 'button', onclick: () => close() }, 'Cancel'));
    }, { onClose: () => { if (!done) resolve(null); } });
  });
}

// Little "pop" on a control after a completed action.
export function pop(el) {
  if (!el) return;
  el.classList.remove('pop');
  void el.offsetWidth;
  el.classList.add('pop');
}

export function seg(options, value, onChange, cls = '') {
  const el = h(`div.seg${cls ? '.' + cls : ''}`, { role: 'tablist' });
  const render = (val) => {
    el.replaceChildren(...options.map(([v, label]) => h('button', {
      class: v === val ? 'on' : '', role: 'tab', 'aria-selected': String(v === val),
      onclick: () => { render(v); onChange(v); },
    }, label)));
  };
  render(value);
  return el;
}

export function emptyState(iconName, title, text, ...extra) {
  return h('div.empty', icon(iconName), h('h3', title), h('p', text), ...extra);
}

export function debounce(fn, ms = 400) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

export function toggle(checked, onChange, label) {
  const input = h('input', { type: 'checkbox', role: 'switch', 'aria-label': label });
  input.checked = !!checked;
  input.addEventListener('change', () => onChange(input.checked));
  return h('label.toggle', input, h('span'));
}
