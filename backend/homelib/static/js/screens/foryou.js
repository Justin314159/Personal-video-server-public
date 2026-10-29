import { api } from '../api.js';
import { h, put, icon, thumbEl, seg, fmtViews, emptyState, toast, skeleton, errorState, pullToRefresh, fmtDuration, watchInfo } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state } from '../state.js';
import { downloadSheet, whySheet, candidateMenu, videoMenu } from './common.js';

let mode = 'me';
let stopPtr = null;

// Reload: set aside everything the feed is showing and fetch a fresh batch.
async function reloadFeed(btn, load) {
  btn.disabled = true;
  btn.classList.add('spin');
  try {
    const r = await api.refreshForyou(mode);
    toast(r.suggestions ? `${r.suggestions} new suggestion${r.suggestions === 1 ? '' : 's'}` : 'No new suggestions right now. Try again later.', 2600, r.suggestions ? 'check' : null);
  } catch (err) { toast(err.message); }
  btn.disabled = false;
  btn.classList.remove('spin');
  await load();
}

export const foryouScreen = {
  async render(root) {
    nav('foryou');
    const list = h('div.vlist.grid', skeleton('grid', 3));
    const strip = h('div');
    const cont = h('div');
    const load = async () => {
      api.continueWatching().then((c) => renderContinue(cont, c.items, load)).catch(() => cont.replaceChildren());
      try {
        const [fy, sh] = await Promise.all([api.foryou(mode), api.shorts()]);
        renderShorts(strip, sh);
        if (!fy.items.length) {
          put(list, emptyState('sparkle', 'Nothing here yet',
            fy.fetching ? 'Fetching suggestions now…' : 'Search and watch a few videos. The feed reloads every night at 03:30, or tap reload.',
            h('button.btn', { onclick: (e) => reloadFeed(e.currentTarget, load) }, icon('refresh'), 'Fetch suggestions now')));
          return;
        }
        put(list, ...fy.items.map((v) => h('article.vitem',
          h('button.vitem', { onclick: () => downloadSheet(v, { onDone: load, shared: mode === 'both' }), 'aria-label': `Download ${v.title}` }, thumbEl(v)),
          h('div.meta',
            h('div.grow',
              h('div.title.clamp2', v.title),
              h('div.sub', [v.channel, fmtViews(v.view_count)].filter(Boolean).join(' · ')),
              h('button.reason', { style: { background: 'none', border: 0, padding: 0, textAlign: 'left', minHeight: '28px' }, onclick: () => whySheet(v, mode) },
                icon('sparkle'), h('span', v.reasons[0])),
            ),
            h('button.icon-btn', { 'aria-label': 'More options', onclick: () => candidateMenu(v, mode, load) }, icon('more'))))));
      } catch (e) {
        put(list, errorState(e, load));
      }
    };
    const reloadBtn = h('button.icon-btn.reload', { 'aria-label': 'Reload suggestions', title: 'Reload suggestions', onclick: () => reloadFeed(reloadBtn, load) }, icon('refresh'));
    root.append(screen(
      topbar('For you', { right: reloadBtn }),
      cont,
      seg([['me', 'Just me'], ['both', 'Both of us']], mode, (v) => { mode = v; put(list, skeleton('grid', 3)); load(); }),
      strip,
      h('div.section-title', 'Suggested', h('span.spacer'), h('a.small', { href: '#/algorithm', style: { textTransform: 'none', letterSpacing: 0 } }, 'Tune')),
      list,
    ));
    stopPtr = pullToRefresh(load);
    await load();
  },
  unmount() { if (stopPtr) stopPtr(); stopPtr = null; },
};

// "Continue watching": this profile's half-watched videos, newest first.
function renderContinue(el, items, reload) {
  if (!items.length) { el.replaceChildren(); return; }
  const me = state.profile.id;
  put(el, 
    h('div.section-title.first', icon('play'), 'Continue watching'),
    h('div.hscroll', { role: 'list' }, items.map((v) => {
      const { ws, pos } = watchInfo(v, me);
      const left = v.duration && ws ? Math.max(0, v.duration - ws.position) : null;
      return h('div.cw', { role: 'listitem' },
        h('a.vitem', { href: `#/player/${encodeURIComponent(v.id)}`, 'aria-label': `Resume ${v.title}${left ? `, ${fmtDuration(left)} left` : ''}` },
          thumbEl(v, { bar: pos }),
          h('div.cw-meta', h('div.title.clamp2', v.title), h('div.sub', left ? `${fmtDuration(left)} left` : v.channel))),
        h('button.icon-btn.more', { 'aria-label': `More options for ${v.title}`, onclick: () => videoMenu(v, { onChange: reload }) }, icon('more')));
    })));
}

function renderShorts(el, sh) {
  const items = sh.items.slice(0, 12);
  put(el, 
    h('div.section-title', icon('shorts'), 'Shorts', h('span.spacer'),
      h('a.small', { href: '#/shorts', style: { textTransform: 'none', letterSpacing: 0 } }, items.length ? 'Watch all' : '')),
    items.length
      ? h('div.shorts-strip', items.map((s, i) => h('a', { href: `#/shorts/${encodeURIComponent(s.id)}`, 'aria-label': s.title },
        thumbEl(s, { badge: i === 0 ? '▶' : ' ' }), h('div.t.clamp2', s.title))))
      : h('div.card.muted.small', { style: { padding: '14px' } },
        sh.downloading ? `Downloading ${sh.downloading} Shorts in the background…` : 'No Shorts downloaded yet. The server keeps at least 10 ready.'));
}
