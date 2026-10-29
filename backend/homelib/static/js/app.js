// Entry point: routes, profile gate, service worker.
import { api, isPreview } from './api.js';
import { route, render, navigate } from './router.js';
import { state, loadProfiles } from './state.js';
import { setBadge } from './layout.js';
import { h, icon } from './ui.js';
import { profilesScreen } from './screens/profiles.js';
import { searchScreen } from './screens/search.js';
import { foryouScreen } from './screens/foryou.js';
import { libraryScreen } from './screens/library.js';
import { playlistScreen } from './screens/playlist.js';
import { downloadsScreen } from './screens/downloads.js';
import { settingsScreen } from './screens/settings.js';
import { playerScreen } from './screens/player.js';
import { shortsScreen } from './screens/shorts.js';
import { appearanceScreen } from './screens/appearance.js';
import { algorithmScreen } from './screens/algorithm.js';
import { musicScreen } from './screens/music.js';
import { shortcutScreen } from './screens/shortcut.js';

route('profiles', profilesScreen);
route('search', searchScreen);
route('foryou', foryouScreen);
route('library', libraryScreen);
route('playlist', playlistScreen);
route('queue', { render() { navigate('#/library/queue', true); } });  // old links
route('downloads', downloadsScreen);
route('settings', settingsScreen);
route('player', playerScreen);
route('shorts', shortsScreen);
route('appearance', appearanceScreen);
route('algorithm', algorithmScreen);
route('music', musicScreen);
route('shortcut', shortcutScreen);

const root = document.getElementById('app');

// The profile picker is the only gate (private network, no logins).
function guard(name) {
  if (!state.profile && name !== 'profiles') return '#/profiles';
  if (!name) return '#/foryou';
  return null;
}

async function boot() {
  try {
    await loadProfiles();
  } catch (e) {
    root.replaceChildren(h('main.screen', h('div.empty', icon('wifi'), h('h3', 'Can’t reach the server'),
      h('p', 'Is Tailscale connected on this phone? Is the home server on?'),
      h('button.btn', { onclick: () => location.reload() }, icon('refresh'), 'Try again'))));
    return;
  }
  window.addEventListener('hashchange', () => render(root, guard));
  await render(root, guard);
  if (isPreview) document.body.append(h('div.preview-ribbon', 'PREVIEW'));
  pollBadge();
  if ('serviceWorker' in navigator && !isPreview && (location.protocol === 'https:' || location.hostname === 'localhost')) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }
}

async function pollBadge() {
  try {
    if (state.profile) {
      const { downloads } = await api.downloads();
      setBadge('downloads', downloads.filter((d) => d.kind === 'user' && ['running', 'queued', 'paused'].includes(d.status)).length);
    }
  } catch { /* offline */ }
  setTimeout(pollBadge, document.hidden ? 30000 : 8000);
}

boot();
