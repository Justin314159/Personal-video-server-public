// One playlist: play in order, rename, reorder (drag the handle or use the
// menu), add and remove videos.
import { api } from '../api.js';
import { h, put, icon, thumbEl, emptyState, toast, sheet, skeleton, errorState, promptSheet, confirmSheet, fmtDuration, watchInfo, pullToRefresh } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state, profileById } from '../state.js';
import { navigate } from '../router.js';
import { videoMenu } from './common.js';

let stopPtr = null;

export const playlistScreen = {
  async render(root, arg) {
    nav('library');
    const id = Number(arg);
    const body = h('div', skeleton('list', 4));
    const bar = topbar('Playlist', { backTo: '#/library/playlists' });
    root.append(screen(bar, body));
    const load = async () => {
      let pl;
      try { pl = await api.playlist(id); } catch (e) { put(body, errorState(e, load)); return; }
      bar.querySelector('h1').textContent = pl.name;
      paint(body, pl, load);
    };
    stopPtr = pullToRefresh(load);
    await load();
  },
  unmount() { if (stopPtr) stopPtr(); stopPtr = null; },
};

function paint(body, pl, reload) {
  const me = state.profile.id;
  const ready = pl.items.filter((v) => v.status === 'ready');
  const firstUnwatched = ready.find((v) => !watchInfo(v, me).watched) || ready[0];
  const owner = pl.scope === 'shared' ? 'Shared with both of you' : `Just ${(profileById(pl.profile_id) || state.profile).name}`;
  const other = state.profiles.find((p) => p.id !== me);
  const header = h('div.pl-head',
    h('div.muted.small', `${owner} · ${pl.count} video${pl.count === 1 ? '' : 's'}${pl.duration ? ` · ${fmtDuration(pl.duration)}` : ''}`),
    h('div.row.wrap', { style: { marginTop: '12px', gap: '8px' } },
      firstUnwatched ? h('a.btn.primary', { href: `#/player/${encodeURIComponent(firstUnwatched.id)}/list/${pl.id}` }, icon('play'), firstUnwatched === ready[0] ? 'Play all' : 'Continue') : null,
      h('button.btn', { onclick: () => addVideosSheet(pl, reload) }, icon('plus'), 'Add'),
      pl.scope === 'personal' && other ? h('button.btn', { onclick: async () => {
        if (await confirmSheet('Share with both of us?', `${other.name} will see “${pl.name}” and its videos, and can add to it. Shared videos are deleted once you’ve both watched them (videos in a playlist are kept).`, 'Share', false)) {
          try { await api.sharePlaylist(pl.id); toast(`Shared with ${other.name}`, 2200, 'check'); reload(); } catch (e) { toast(e.message); }
        }
      } }, icon('users'), 'Share with both of us') : null,
      h('button.icon-btn.filled', { 'aria-label': 'Rename playlist', onclick: async () => {
        const name = await promptSheet('Rename playlist', { value: pl.name });
        if (name && name !== pl.name) { try { await api.renamePlaylist(pl.id, name); toast('Renamed', 2000, 'check'); reload(); } catch (e) { toast(e.message); } }
      } }, icon('edit')),
      h('button.icon-btn.filled', { 'aria-label': 'Delete playlist', onclick: async () => {
        if (await confirmSheet(`Delete “${pl.name}”?`, 'Only the playlist goes. The videos stay on the server (and can be auto-deleted again).', 'Delete playlist')) {
          try { await api.deletePlaylist(pl.id); toast('Playlist deleted', 2200, 'check'); navigate('#/library/playlists', true); } catch (e) { toast(e.message); }
        }
      } }, icon('trash'))));
  if (!pl.items.length) {
    put(body, header, emptyState('listadd', 'This playlist is empty', 'Tap “Add videos”, or use “Save” on any video.'));
    return;
  }
  const list = h('ol.card.list-card.sortable', { 'aria-label': 'Videos in this playlist' });
  const move = async (from, to) => {
    const ids = pl.items.map((x) => x.item_id);
    const [it] = ids.splice(from, 1);
    ids.splice(to, 0, it);
    try { await api.playlistReorder(pl.id, ids); } catch (e) { toast(e.message); }
    reload();
  };
  pl.items.forEach((v, i) => {
    const { watched, pos } = watchInfo(v, me);
    const ok = v.status === 'ready';
    const li = h('li.hitem',
      h('button.drag', { 'aria-label': `Reorder ${v.title}. Use the menu to move it with a screen reader.` }, h('span.grip')),
      h('button.vitem', { style: { width: '112px', flex: 'none' }, 'aria-label': `Play ${v.title}`, onclick: () => navigate(ok ? `#/player/${encodeURIComponent(v.id)}/list/${pl.id}` : '#/downloads') },
        thumbEl(v, { bar: pos, watched, shade: ok ? null : 'Downloading' })),
      h('div.grow', h('div.title.clamp2', v.title), h('div.sub', v.channel)),
      h('button.icon-btn', { 'aria-label': `More options for ${v.title}`, onclick: () => videoMenu(v, { onChange: reload, extra: [
        i > 0 ? ['Move up', 'up', () => move(i, i - 1)] : null,
        i < pl.items.length - 1 ? ['Move down', 'down', () => move(i, i + 1)] : null,
        ['Remove from playlist', 'x', async () => { await api.playlistRemove(pl.id, v.item_id); toast('Removed from playlist'); reload(); }],
      ].filter(Boolean) }) }, icon('more')));
    list.append(li);
  });
  sortable(list, move);
  put(body, header, list);
}

// Drag a row by its handle; other rows slide out of the way.
function sortable(list, onMove) {
  let drag = null;
  list.addEventListener('pointerdown', (e) => {
    const handle = e.target.closest('.drag');
    if (!handle) return;
    e.preventDefault();
    const li = handle.closest('li');
    const rows = [...list.children];
    drag = { li, rows, from: rows.indexOf(li), to: rows.indexOf(li), y0: e.clientY, h: li.getBoundingClientRect().height, id: e.pointerId };
    handle.setPointerCapture(e.pointerId);
    li.classList.add('dragging');
  });
  list.addEventListener('pointermove', (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dy = e.clientY - drag.y0;
    drag.li.style.transform = `translateY(${dy}px)`;
    const to = Math.max(0, Math.min(drag.rows.length - 1, drag.from + Math.round(dy / drag.h)));
    drag.to = to;
    drag.rows.forEach((r, i) => {
      if (r === drag.li) return;
      const shift = (i > drag.from && i <= to) ? -drag.h : (i < drag.from && i >= to) ? drag.h : 0;
      r.style.transform = shift ? `translateY(${shift}px)` : '';
    });
  });
  const end = () => {
    if (!drag) return;
    const { from, to, rows, li } = drag;
    drag = null;
    li.classList.remove('dragging');
    rows.forEach((r) => { r.style.transform = ''; });
    if (from !== to) onMove(from, to);
  };
  list.addEventListener('pointerup', end);
  list.addEventListener('pointercancel', end);
}

async function addVideosSheet(pl, reload) {
  const s = sheet(h('div.stack', h('h2', 'Add videos'), skeleton('list', 3)));
  let lib;
  try { lib = await api.library(); } catch (e) { put(s.el, h('div.grab'), errorState(e)); return; }
  const inIds = new Set(pl.items.map((x) => x.id));
  const cands = lib.videos.filter((v) => !inIds.has(v.id));
  let added = 0;
  put(s.el, h('div.grab'), h('div',
    h('div.row', h('h2.grow', 'Add videos'), h('button.btn.primary', { onclick: () => { s.close(); if (added) reload(); } }, 'Done')),
    cands.length ? h('div', { style: { marginTop: '8px' } }, cands.map((v) => {
      const b = h('button.hitem.pick', { 'aria-pressed': 'false', onclick: async () => {
        if (b.getAttribute('aria-pressed') === 'true') return;
        try {
          await api.playlistAdd(pl.id, v.id);
          added++;
          b.setAttribute('aria-pressed', 'true');
          b.querySelector('.pick-state').replaceChildren(icon('checkcircle', 'on'));
        } catch (e) { toast(e.message); }
      } }, h('div', { style: { width: '96px', flex: 'none' } }, thumbEl(v)), h('div.grow', h('div.title.clamp2', v.title), h('div.sub', v.channel)),
      h('span.pick-state', icon('plus')));
      return b;
    })) : emptyState('library', 'Everything’s already in here', 'Download more videos to add them.')));
}
