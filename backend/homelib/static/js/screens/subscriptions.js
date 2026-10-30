// Subscriptions: a section per subscribed channel with its latest uploads.
// Only metadata and thumbnails are on the server; tapping a video opens the
// usual download sheet. Uploads you haven't seen yet carry a "NEW" marker.
import { api } from '../api.js';
import { h, put, icon, thumbEl, emptyState, toast, skeleton, errorState, pullToRefresh, fmtAgo, confirmSheet } from '../ui.js';
import { screen, topbar, nav, setBadge } from '../layout.js';
import { downloadSheet } from './common.js';

let stopPtr = null;
let seenTimer = null;

export function uploadCard(u, onDone) {
  return h('button.vitem.sub-item', { onclick: () => { api.markUploadsSeen([u.id]).catch(() => {}); downloadSheet(u, { onDone }); }, 'aria-label': `${u.new ? 'New: ' : ''}${u.title}` },
    thumbEl(u, { badge: u.new ? 'NEW' : undefined, watched: u.watched }),
    h('div.title.clamp2', u.title),
    h('div.sub', [u.upload_ts ? fmtAgo(u.upload_ts) : '', u.local_status === 'ready' ? 'Downloaded' : ''].filter(Boolean).join(' · ')));
}

export const subscriptionsScreen = {
  async render(root) {
    nav('subs');
    const body = h('div', skeleton('row', 3));
    const load = async () => {
      let data;
      try { data = await api.subscriptions(); } catch (e) { put(body, errorState(e, load)); return; }
      setBadge('subs', data.new_count);
      if (!data.channels.length) {
        put(body, emptyState('bell', 'No subscriptions yet',
          'Tap Subscribe on a video or a channel page. New uploads show up here and in For you. Nothing is downloaded until you tap it.'));
        return;
      }
      put(body, ...data.channels.map((c) => h('section.sub-channel',
        h('div.section-title',
          h('a', { href: `#/channel/${encodeURIComponent(c.channel_id)}/${encodeURIComponent(c.channel)}` }, c.channel),
          c.new_count ? h('span.pill.accent', `${c.new_count} new`) : null,
          h('span.spacer'),
          h('button.icon-btn', { 'aria-label': `Unsubscribe from ${c.channel}`, onclick: async () => {
            if (await confirmSheet(`Unsubscribe from ${c.channel}?`, 'Its uploads leave this tab and your feed. Downloaded videos stay.', 'Unsubscribe')) {
              try { await api.unsubscribe(c.channel_id); toast(`Unsubscribed from ${c.channel}`); load(); } catch (e) { toast(e.message); }
            }
          } }, icon('belloff'))),
        c.uploads.length
          ? h('div.hscroll', { role: 'list' }, c.uploads.map((u) => h('div.sub-cell', { role: 'listitem' }, uploadCard(u, load))))
          : h('div.card.muted.small', { style: { padding: '14px' } },
            c.check_ok === false ? 'Couldn’t check this channel last time. It’s retried on the next reload.' : 'Checking for uploads on the next reload…'))));
      // The markers stay for this visit; next time they're gone.
      const ids = data.channels.flatMap((c) => c.uploads.filter((u) => u.new).map((u) => u.id));
      clearTimeout(seenTimer);
      if (ids.length) seenTimer = setTimeout(() => api.markUploadsSeen(ids).then(() => setBadge('subs', 0)).catch(() => {}), 2500);
    };
    root.append(screen(
      topbar('Subscriptions'),
      h('p.muted.small', 'Checked every morning and whenever you reload For you. Only titles and thumbnails are stored; videos download when you tap them.'),
      body));
    stopPtr = pullToRefresh(load);
    await load();
  },
  unmount() { if (stopPtr) stopPtr(); stopPtr = null; },
};

export const channelScreen = {
  async render(root, arg) {
    nav('subs');
    const [cid, ...rest] = (arg || '').split('/');
    let name = rest.join('/');
    const head = h('div');
    const list = h('div.vlist.grid', skeleton('grid', 3));
    root.append(screen(topbar(name || 'Channel', { backTo: '#/subs' }), head, list));
    let ch;
    try { ch = await api.channel(cid, name); } catch (e) { put(list, errorState(e, () => channelScreen.render(root, arg))); return; }
    name = ch.channel;
    root.querySelector('.topbar h1').textContent = name;
    const paintHead = () => put(head, h('div.row', { style: { gap: '10px', margin: '4px 0 12px' } },
      h(`button.btn${ch.subscribed ? '' : '.primary'}`, { onclick: async () => {
        try {
          if (ch.subscribed) { await api.unsubscribe(cid); toast(`Unsubscribed from ${name}`); } else {
            await api.subscribe(cid, name); toast(`Subscribed to ${name}`, 2400, 'check');
          }
          ch.subscribed = !ch.subscribed;
          paintHead();
        } catch (e) { toast(e.message); }
      } }, icon(ch.subscribed ? 'belloff' : 'bell'), ch.subscribed ? 'Unsubscribe' : 'Subscribe'),
      h('span.muted.small', ch.subscribed ? 'New uploads appear in Subs and For you.' : 'Latest uploads')));
    paintHead();
    put(list, ...(ch.videos.length ? ch.videos.map((u) => h('article.vitem', uploadCard(u, null))) : [emptyState('film', 'No uploads found', 'YouTube didn’t list any videos for this channel.')]));
  },
};
