// Library tab: your videos on the server (shared ones plus your private
// downloads), the two queues, and playlists.
import { api } from '../api.js';
import { h, put, icon, thumbEl, seg, emptyState, skeleton, errorState, pullToRefresh, fmtDuration, watchInfo } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state } from '../state.js';
import { navigate } from '../router.js';
import { videoMenu, newPlaylistSheet } from './common.js';
import { queueList } from './queue.js';

const SEGMENTS = [['videos', 'Videos'], ['queue', 'My queue'], ['shared', 'Shared'], ['playlists', 'Playlists']];
let filter = 'all';
let stopPtr = null, stopCur = null;

export const libraryScreen = {
  async render(root, arg) {
    nav('library');
    const tab = SEGMENTS.some(([k]) => k === arg) ? arg : 'videos';
    const body = h('div');
    root.append(screen(
      topbar('Library'),
      seg(SEGMENTS, tab, (k) => navigate(k === 'videos' ? '#/library' : `#/library/${k}`, true), 'compact'),
      h('div', { style: { height: '14px' } }),
      body));
    let reload;
    if (tab === 'queue' || tab === 'shared') {
      const q = queueList(tab === 'queue' ? 'personal' : 'shared');
      body.append(q.el);
      stopCur = q.stop;
      reload = q.load;
    } else if (tab === 'playlists') {
      reload = () => renderPlaylists(body);
    } else {
      reload = () => renderVideos(body);
    }
    stopPtr = pullToRefresh(reload);
    await reload();
  },
  unmount() { if (stopPtr) stopPtr(); if (stopCur) stopCur(); stopPtr = stopCur = null; },
};

// ---- all videos ---------------------------------------------------------------------
const FILTERS = [['all', 'All'], ['new', 'Not started'], ['progress', 'In progress'], ['watched', 'Watched']];

async function renderVideos(el) {
  if (!el.firstChild) el.append(skeleton('grid', 4));
  let lib;
  try { lib = await api.library(); } catch (e) { put(el, errorState(e, () => renderVideos(el))); return; }
  const me = state.profile.id;
  const all = lib.videos;
  const paint = () => {
    const shown = all.filter((v) => {
      const w = watchInfo(v, me);
      if (filter === 'new') return !w.watched && !w.pos;
      if (filter === 'progress') return !w.watched && w.pos > 0;
      if (filter === 'watched') return w.watched;
      return true;
    });
    const chips = h('div.chips', { role: 'group', 'aria-label': 'Filter' }, FILTERS.map(([k, label]) => h(`button.chip${filter === k ? '.on' : ''}`, {
      'aria-pressed': String(filter === k), onclick: () => { filter = k; paint(); },
    }, label)));
    let list;
    if (!all.length) {
      list = emptyState('library', 'Nothing on the server yet', 'Find something in Search or For you. Downloads land here.',
        h('a.btn.primary', { href: '#/search' }, icon('search'), 'Search'));
    } else if (!shown.length) {
      list = emptyState('checkcircle', { new: 'You’ve started everything', progress: 'Nothing in progress', watched: 'Nothing watched yet' }[filter],
        'Try another filter.');
    } else {
      list = h('div.vgrid', shown.map((v) => card(v, () => renderVideos(el))));
    }
    put(el, chips, h('div', { style: { height: '10px' } }), list,
      all.length ? h('p.muted.small.center', { style: { marginTop: '18px' } }, `${all.length} video${all.length === 1 ? '' : 's'} · auto-delete after ${lib.auto_delete_hours}h unless kept or in a playlist`) : null);
  };
  paint();
}

export function card(v, reload) {
  const me = state.profile.id;
  const { watched, pos } = watchInfo(v, me);
  const ready = v.status === 'ready';
  const d = v.download;
  const shade = ready ? null : d && d.status === 'running' ? `${Math.round(d.progress)}%` : 'Queued';
  return h('article.vcard',
    h('button.vitem', { onclick: () => navigate(ready ? `#/player/${encodeURIComponent(v.id)}` : '#/downloads'), 'aria-label': `${ready ? 'Play' : 'Downloading'}: ${v.title}${watched ? ', watched' : pos ? `, ${Math.round(pos * 100)}% watched` : ''}` },
      thumbEl(v, { bar: pos, watched, shade, badge: v.keep ? 'Kept' : null })),
    h('div.meta',
      h('div.grow', h('div.title.clamp2', v.title),
        h('div.sub.ellipsis', [v.shared === false && state.profiles.length > 1 ? 'Just you' : null, v.channel].filter(Boolean).join(' · '))),
      h('button.icon-btn.more', { 'aria-label': `More options for ${v.title}`, onclick: () => videoMenu(v, { onChange: reload }) }, icon('more'))));
}

// ---- playlists ------------------------------------------------------------------------
async function renderPlaylists(el) {
  if (!el.firstChild) el.append(skeleton('list', 3));
  let lists;
  try { ({ playlists: lists } = await api.playlists()); } catch (e) { put(el, errorState(e, () => renderPlaylists(el))); return; }
  const newBtn = h('button.btn.block.dashed', { onclick: async () => { const pl = await newPlaylistSheet(); if (pl) navigate(`#/playlist/${pl.id}`); } }, icon('plus'), 'New playlist');
  if (!lists.length) {
    put(el, emptyState('listadd', 'No playlists yet', 'Group videos for a series, a cooking project or movie night. Shared playlists show up for both of you.'), newBtn);
    return;
  }
  const section = (title, items) => (items.length ? [h('div.section-title', title), h('div.pl-grid', items.map(plCard))] : []);
  put(el, newBtn,
    ...section('Shared', lists.filter((p) => p.scope === 'shared')),
    ...section(`Just ${state.profile.name}`, lists.filter((p) => p.scope === 'personal')));
}

function plCard(pl) {
  const covers = pl.thumbs.length ? pl.thumbs : [];
  return h('a.pl-card', { href: `#/playlist/${pl.id}`, 'aria-label': `${pl.name}, ${pl.count} videos` },
    h(`div.pl-cover.n${Math.min(4, covers.length)}`, covers.length ? covers.map((src) => h('img', { src, alt: '', loading: 'lazy' })) : icon('listadd')),
    h('div.pl-name.clamp2', pl.name),
    h('div.sub', `${pl.count} video${pl.count === 1 ? '' : 's'}${pl.duration ? ` · ${fmtDuration(pl.duration)}` : ''}`));
}
