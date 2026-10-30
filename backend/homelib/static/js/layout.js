// Shared page chrome: top bar with the profile avatar, bottom tab bar.
import { h, icon, avatar } from './ui.js';
import { state } from './state.js';
import { back } from './router.js';

export const TABS = [
  ['search', 'Search', 'search'],
  ['foryou', 'For you', 'sparkle'],
  ['library', 'Library', 'library'],
  ['subs', 'Subs', 'bell'],
  ['downloads', 'Downloads', 'download'],
  ['settings', 'Settings', 'settings'],
];

export function screen(...children) {
  return h('main.screen', { id: 'main' }, ...children);
}

export function topbar(title, { backTo, right } = {}) {
  return h('header.topbar',
    backTo ? h('button.icon-btn.back', { 'aria-label': 'Back', onclick: () => back(backTo) }, icon('back')) : null,
    h('h1', title),
    right || null,
    state.profile ? h('a.avatar-btn', { href: '#/profiles', 'aria-label': `Profile: ${state.profile.name}. Tap to switch.` }, avatar(state.profile)) : null,
  );
}

let navEl;
export function nav(active) {
  if (!navEl) {
    navEl = h('nav.nav', { 'aria-label': 'Main' },
      TABS.map(([id, label, ic]) => h('a', { href: `#/${id}`, 'data-tab': id }, icon(ic), h('span', label))));
    document.body.append(navEl);
  }
  navEl.style.display = active === null ? 'none' : '';
  document.body.classList.toggle('no-nav', active === null);
  for (const a of navEl.querySelectorAll('a')) {
    const on = a.dataset.tab === active;
    a.classList.toggle('active', on);
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  }
}

export function setBadge(tab, n) {
  if (!navEl) return;
  const a = navEl.querySelector(`[data-tab="${tab}"]`);
  let dot = a.querySelector('.dot');
  if (!n) { if (dot) dot.remove(); return; }
  if (!dot) { dot = h('span.dot'); a.append(dot); }
  dot.textContent = n > 9 ? '9+' : String(n);
}
