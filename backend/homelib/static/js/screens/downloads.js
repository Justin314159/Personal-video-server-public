import { api } from '../api.js';
import { h, icon, thumbEl, fmtBytes, fmtDuration, fmtLeft, emptyState, toast, confirmSheet, avatar, skeleton, pullToRefresh, watchInfo } from '../ui.js';
import { screen, topbar, nav, setBadge } from '../layout.js';
import { state } from '../state.js';
import { navigate } from '../router.js';

let timer;
let showBackground = false;
let stopPtr = null;

export const downloadsScreen = {
  async render(root) {
    nav('downloads');
    const disk = h('div');
    const active = h('div');
    const lib = h('div', skeleton('list', 3));
    root.append(screen(topbar('Downloads'), disk, active, lib));

    const tick = async () => {
      try {
        const [{ downloads }, st] = await Promise.all([api.downloads(), api.status()]);
        renderDisk(disk, st.disk);
        renderActive(active, downloads, tick);
        const running = downloads.filter((d) => ['running', 'queued', 'paused'].includes(d.status));
        setBadge('downloads', running.filter((d) => d.kind === 'user').length);
        clearTimeout(timer);
        timer = setTimeout(tick, running.length ? 1000 : 5000);
        if (running.length !== lastRunning) { lastRunning = running.length; await loadLib(); }
      } catch (e) {
        clearTimeout(timer);
        timer = setTimeout(tick, 5000);
      }
    };
    let lastRunning = -1;
    const loadLib = async () => renderLibrary(lib, await api.library(), loadLib);
    stopPtr = pullToRefresh(async () => { await tick(); await loadLib(); });
    await tick();
    await loadLib();
  },
  unmount() { clearTimeout(timer); if (stopPtr) stopPtr(); stopPtr = null; },
};

function renderDisk(el, d) {
  const usedPct = d.total ? (1 - d.free / d.total) * 100 : 0;
  el.replaceChildren(h('div.card', { style: { padding: '12px 14px' } },
    h('div.kv', h('span', icon('server'), ' Server disk'), h('span', { style: { fontWeight: 600 } }, `${fmtBytes(d.free)} free`)),
    h('div.progress.disk', h('div', { style: { width: `${usedPct}%`, background: d.ok ? 'var(--progress)' : 'var(--danger)' } })),
    h('div.kv.small.muted', { style: { marginTop: '6px' } }, h('span', `Videos: ${fmtBytes(d.library_bytes)}`),
      h('span', d.ok ? `Pauses below ${fmtBytes(d.min_free)}` : 'Low space: new downloads refused'))));
}

function renderActive(el, downloads, refresh) {
  const user = downloads.filter((d) => d.kind === 'user');
  const bg = downloads.filter((d) => d.kind === 'background' && ['running', 'queued', 'paused'].includes(d.status));
  const live = user.filter((d) => ['running', 'queued', 'paused', 'error'].includes(d.status));
  const done = user.filter((d) => ['done', 'cancelled'].includes(d.status));
  const items = [];
  items.push(h('div.section-title', 'In progress', h('span.spacer'),
    done.length ? h('button.chip', { onclick: async () => { await api.clearDownloads(); refresh(); } }, 'Clear finished') : null));
  if (!live.length) items.push(h('div.card.hint', icon('checkcircle'), h('span', 'All caught up. Pick something from Search or For you.')));
  else items.push(h('div.stack', live.map((d) => dlCard(d, refresh))));
  if (bg.length) {
    items.push(h('button.section-title', { style: { background: 'none', border: 0, width: '100%', padding: 0, minHeight: '44px' }, onclick: () => { showBackground = !showBackground; refresh(); } },
      icon('shorts'), `Shorts prefetch (${bg.length})`, h('span.spacer'), icon(showBackground ? 'up' : 'down')));
    if (showBackground) items.push(h('div.stack', bg.map((d) => dlCard(d, refresh))));
  }
  el.replaceChildren(...items);
}

function dlCard(d, refresh) {
  const ctl = (action, ic, label) => h('button.icon-btn.filled', {
    'aria-label': label, onclick: async () => { try { await api.dlControl(d.id, action); } catch (e) { toast(e.message); } refresh(); },
  }, icon(ic));
  const pct = Math.round(d.progress || 0);
  const info = d.status === 'running'
    ? [d.phase === 'merging' ? 'Merging…' : `${pct}%`, d.speed ? `${fmtBytes(d.speed)}/s` : null, d.eta != null && d.phase !== 'merging' ? `${fmtDuration(d.eta)} left` : null].filter(Boolean).join(' · ')
    : d.status === 'queued' ? 'Waiting…' : d.status === 'paused' ? `Paused at ${pct}%` : d.status === 'error' ? (d.error || 'Failed') : d.status;
  return h('div.card', { style: { padding: '12px' } },
    h('div.row',
      h('div', { style: { width: '96px', flex: 'none' } }, thumbEl({ ...d, duration: null })),
      h('div.grow',
        h('div.title.clamp2', { style: { fontWeight: 600, fontSize: '0.882rem' } }, d.title),
        h('div.small.muted', { style: { marginTop: '4px' } }, h(`span.pill${d.quality === 'best' ? '.warn' : ''}`, { best: 'Best', compat: '1080p', mp3: 'MP3', m4a: 'M4A' }[d.quality] || d.quality), ' ', info))),
    h('div.progress', { style: { marginTop: '10px' } }, h('div', { style: { width: `${pct}%` } })),
    h('div.row', { style: { justifyContent: 'flex-end', marginTop: '8px', gap: '8px' } },
      d.status === 'running' || d.status === 'queued' ? ctl('pause', 'pause', 'Pause') : null,
      d.status === 'paused' || d.status === 'error' ? ctl('resume', 'play', d.status === 'error' ? 'Retry' : 'Resume') : null,
      ctl('cancel', 'x', 'Cancel')));
}

function renderLibrary(el, lib, reload) {
  const vids = lib.videos.filter((v) => v.status === 'ready');
  const me = state.profile.id;
  el.replaceChildren(
    h('div.section-title', `On the server (${vids.length})`, h('span.spacer'), h('span.small', { style: { textTransform: 'none', letterSpacing: 0 } }, `Auto-delete: ${lib.auto_delete_hours}h`)),
    vids.length ? h('div.card', { style: { padding: '0 12px' } }, vids.map((v) => {
      const { watched, pos } = watchInfo(v, me);
      const left = v.keep ? 'Kept' : v.in_playlist ? 'In a playlist' : v.expires_at ? `Deletes in ${fmtLeft(v.expires_at)}` : '';
      return h('div.hitem',
        h('button.vitem', { style: { width: '120px', flex: 'none' }, onclick: () => navigate(`#/player/${encodeURIComponent(v.id)}`), 'aria-label': `Play ${v.title}` },
          thumbEl(v, { bar: pos, watched })),
        h('div.grow',
          h('div.title.clamp2', v.title),
          h('div.sub', fmtBytes(v.file_size), ' · ', left),
          h('div.row', { style: { gap: '4px', marginTop: '6px' } },
            v.relevant.map((pid) => { const p = state.profiles.find((x) => x.id === pid); return p ? avatar(p, `sm${(v.watch[pid] || {}).watched ? '' : '.off'}`) : null; }),
            v.needs_vlc ? h('span.pill.warn', 'May need VLC') : null)),
        h('div.actions',
          h(`button.icon-btn${v.keep ? '.on' : ''}`, { 'aria-label': v.keep ? 'Unpin (allow auto-delete)' : 'Keep (never auto-delete)', 'aria-pressed': String(!!v.keep),
            onclick: async () => { await api.keep(v.id, !v.keep); toast(v.keep ? 'Will auto-delete again' : 'Kept: never auto-deleted'); reload(); } }, icon('pin')),
          h('button.icon-btn', { 'aria-label': 'Delete now', onclick: async () => {
            if (await confirmSheet('Delete this video?', `“${v.title}” will be removed from the server.`, 'Delete')) {
              await api.deleteVideo(v.id); toast('Deleted'); reload();
            }
          } }, icon('trash'))));
    })) : emptyState('film', 'No videos on the server', 'Downloads appear here until they are watched or expire.', h('a.btn', { href: '#/search' }, icon('search'), 'Find something')));
}
