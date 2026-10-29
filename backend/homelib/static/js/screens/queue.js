// The shared and personal queues (shown as segments of the Library tab).
import { api } from '../api.js';
import { h, put, icon, thumbEl, emptyState, toast, avatar, skeleton, errorState, watchInfo } from '../ui.js';
import { navigate } from '../router.js';
import { state } from '../state.js';
import { videoMenu } from './common.js';

// Returns { el, load, stop }.
export function queueList(scope) {
  const el = h('div', skeleton('list', 4));
  let timer;
  const load = async () => {
    let items;
    try { ({ items } = await api.queue(scope)); } catch (e) { put(el, errorState(e, load)); return; }
    if (!items.length) {
      put(el, emptyState('queue', scope === 'personal' ? 'Your queue is empty' : 'Shared queue is empty',
        scope === 'personal' ? 'Download something from Search or For you and choose “My queue”.' : 'Anything either of you adds to “Shared” shows up here.',
        h('a.btn', { href: '#/search' }, icon('search'), 'Find something')));
      return;
    }
    put(el, h('div.card.list-card', items.map((v, i) => row(v, i, items.length, scope, load))));
    clearTimeout(timer);
    if (items.some((v) => v.status !== 'ready')) timer = setTimeout(() => load().catch(() => {}), 2000);
  };
  return { el, load, stop: () => clearTimeout(timer) };
}

function row(v, i, n, scope, reload) {
  const me = state.profile.id;
  const { watched, pos } = watchInfo(v, me);
  const ready = v.status === 'ready';
  const d = v.download;
  let status;
  if (ready) status = watched ? 'Watched' : pos > 0.02 ? 'Resume' : 'Ready';
  else if (d && d.status === 'running') status = `Downloading ${Math.round(d.progress)}%`;
  else if (d && d.status === 'paused') status = 'Paused';
  else if (d && d.status === 'error') status = 'Failed';
  else status = 'Queued';
  const watchers = scope === 'shared' ? h('span.row', { style: { gap: '4px' }, 'aria-label': state.profiles.map((p) => `${p.name}: ${(v.watch[p.id] || {}).watched ? 'watched' : 'not yet'}`).join(', ') },
    state.profiles.map((p) => avatar(p, `sm${(v.watch[p.id] || {}).watched ? '' : '.off'}`))) : null;
  const move = (dir) => async (e) => { e.stopPropagation(); try { await api.queueMove(v.item_id, dir); } catch (err) { toast(err.message); } reload(); };
  return h('div.hitem',
    h('button.vitem', { style: { width: '112px', flex: 'none' }, onclick: () => ready ? navigate(`#/player/${encodeURIComponent(v.id)}`) : navigate('#/downloads'), 'aria-label': `${ready ? 'Play' : 'Download status for'} ${v.title}` },
      thumbEl(v, { bar: pos, watched, shade: ready ? null : status.replace('Downloading ', '') })),
    h('div.grow',
      h('div.title.clamp2', v.title),
      h('div.sub.row', { style: { gap: '6px', flexWrap: 'wrap' } },
        h(`span.pill${ready && !watched ? '.accent' : ''}`, status), v.needs_vlc ? h('span.pill.warn', 'VLC') : null, watchers)),
    h('div.actions',
      h('button.icon-btn', { 'aria-label': 'Move up', disabled: i === 0, onclick: move(-1) }, icon('up')),
      h('button.icon-btn', { 'aria-label': 'Move down', disabled: i === n - 1, onclick: move(1) }, icon('down'))),
    h('button.icon-btn', { 'aria-label': `More options for ${v.title}`, onclick: () => videoMenu(v, { onChange: reload, extra: [
      ['Remove from queue', 'x', async () => { await api.queueRemove(v.item_id); toast('Removed from queue'); reload(); }],
    ] }) }, icon('more')),
  );
}
