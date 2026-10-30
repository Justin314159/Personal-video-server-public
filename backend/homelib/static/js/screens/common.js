// Sheets shared by several screens: download options, "Why am I seeing this?",
// add to playlist, delete after watching and the per-video menu.
import { api } from '../api.js';
import { h, put, icon, sheet, toast, fmtDuration, avatar, promptSheet, confirmSheet, skeleton, pop } from '../ui.js';
import { navigate } from '../router.js';
import { state, profileById } from '../state.js';

const QUALITY = [
  ['compat', 'Compatible 1080p', 'H.264 up to 1080p. Plays in Safari on every iPhone.'],
  ['best', 'Best quality', 'Up to 4K. Some files are VP9/AV1 and may need VLC on the phone.'],
];

// shared: opened from "Both of us", so it starts on "Share with both of us".
// Everywhere else a download is private to whoever downloads it.
export async function downloadSheet(video, { onDone, shared = false } = {}) {
  if (!state.settings) state.settings = await api.settings();
  if (video.local_status === 'ready' || video.status === 'ready') {
    navigate(`#/player/${encodeURIComponent(video.id)}`);
    return;
  }
  let quality = video.is_short ? 'compat' : state.settings.default_quality;
  let dest = shared ? 'shared' : 'personal';
  const other = state.profiles.find((p) => p.id !== state.profile.id);
  const DEST = [
    ['personal', 'My queue', `Only ${state.profile.name} sees it. Deleted once you've watched it.`],
    ['none', 'Just download', `Only ${state.profile.name} sees it, in the library, without queueing.`],
    ['shared', other ? 'Share with both of us' : 'Shared queue', `Shows up for ${other ? `${other.name} too` : 'everyone'}, in the Shared queue. Deleted after you've ${other ? 'both' : 'all'} watched.`],
  ];
  sheet((close) => {
    const qWrap = h('div.stack');
    const dWrap = h('div.stack');
    const renderOpts = (wrap, opts, get, set) => {
      put(wrap, ...opts.map(([v, t, d]) => h('button.option', {
        class: get() === v ? 'on' : '', onclick: () => { set(v); renderAll(); },
        disabled: video.is_short && wrap === qWrap && v === 'best',
      }, h('span.radio'), h('span', h('div.t', t), h('div.d', d)))));
    };
    const renderAll = () => {
      renderOpts(qWrap, QUALITY, () => quality, (v) => { quality = v; });
      renderOpts(dWrap, DEST, () => dest, (v) => { dest = v; });
    };
    renderAll();
    const go = h('button.btn.primary.block', {
      onclick: async () => {
        go.disabled = true;
        try {
          await api.download(video, quality, dest);
          toast(dest === 'none' ? 'Downloading (just for you)' : `Downloading → ${dest === 'shared' ? 'Shared' : 'My'} queue`);
          close();
          onDone && onDone();
        } catch (e) {
          toast(e.code === 'disk_full' ? `Not enough disk space. ${e.message}` : `Couldn't start: ${e.message}`, 4500);
          go.disabled = false;
        }
      },
    }, icon('download'), 'Download');
    return h('div.stack',
      h('div', h('h2.clamp2', video.title), h('div.muted.small', [video.channel, fmtDuration(video.duration)].filter(Boolean).join(' · '))),
      h('div.section-title', 'Quality'), qWrap,
      video.is_short ? h('div.muted.small', 'Shorts always download in Compatible quality so they autoplay in Safari.') : null,
      h('div.section-title', 'Add to'), dWrap,
      go,
    );
  });
}

// ---- feedback on feed cards ------------------------------------------------------

// Thumbs up / down and "Not interested" under a For you card. The rating is
// remembered on the card object so re-renders keep it.
export function feedActions(v, { onRemove } = {}) {
  const row = h('div.card-actions');
  const paint = () => {
    const r = v.my_rating || 0;
    const btn = (val, ic, label) => h(`button.fb-btn${r === val ? '.on' : ''}`, {
      'aria-label': label, 'aria-pressed': String(r === val),
      onclick: async (e) => {
        const next = r === val ? 0 : val;
        try {
          await api.rateCandidate(v, next);
          v.my_rating = next;
          paint();
          pop(row.querySelector(`[aria-label="${label}"]`));
          if (next) toast(next > 0 ? 'Thanks: more like this' : 'Got it: fewer like this', 1800);
        } catch (err) { toast(err.message); }
        return e;
      },
    }, icon(ic));
    put(row,
      btn(1, 'like', 'Thumbs up'), btn(-1, 'dislike', 'Thumbs down'),
      h('span.spacer'),
      h('button.fb-btn.text', { 'aria-label': `Not interested in ${v.title}`, onclick: () => notInterested(v, onRemove) }, icon('x'), 'Not interested'));
  };
  paint();
  return row;
}

export async function notInterested(v, onRemove) {
  try {
    await api.notInterested(v);
    onRemove && onRemove();
    toast('Removed. You’ll see less like this.', 2600, 'check');
  } catch (e) { toast(e.message); }
}

// The channel name as a link to its channel page (plain text when the id is unknown).
export function channelLink(v, cls = 'chan-link') {
  if (!v.channel) return '';
  if (!v.channel_id) return v.channel;
  return h(`a.${cls}`, { href: `#/channel/${encodeURIComponent(v.channel_id)}/${encodeURIComponent(v.channel)}`, onclick: (e) => e.stopPropagation() }, v.channel);
}

export async function toggleSubscribe(v, subscribed) {
  if (subscribed) {
    await api.unsubscribe(v.channel_id);
    toast(`Unsubscribed from ${v.channel}`, 2200);
    return false;
  }
  await api.subscribe(v.channel_id, v.channel, v.id);
  toast(`Subscribed to ${v.channel}. New uploads show up in Subs and For you.`, 3000, 'check');
  return true;
}

const BREAKDOWN = [
  ['interest_part', 'Your interests'], ['keyword_part', 'Topics & channels you boosted or watch'],
  ['similar_part', 'Like videos you liked'], ['channel_part', 'The channel'], ['fresh_part', 'New upload'],
  ['avoid_part', 'Like things you asked for less of'], ['jitter_part', 'Daily shuffle'],
  // 1.4 names (still used by the Shorts feed)
  ['familiar_part', 'Familiar (topics + channel)'], ['novelty_part', 'New to you (exploration)'],
];

export async function whySheet(item, mode) {
  const s = sheet(h('div.stack', h('h2', 'Why am I seeing this?'), skeleton('list', 2)));
  try {
    const w = await api.why(item.id, mode);
    const r = w.result;
    const b = r ? r.breakdown : null;
    const bar = (label, val, max = 1) => h('div', { style: { marginTop: '10px' } },
      h('div.kv', h('span', label), h('span.muted', (val >= 0 ? '+' : '') + val.toFixed(2))),
      h('div.wbar', h(`div.${val >= 0 ? 'pos' : 'neg'}`, { style: { width: `${Math.min(50, Math.abs(val) / max * 50)}%` } })));
    put(s.el, h('div.grab'), h('div.stack',
      h('h2', 'Why am I seeing this?'),
      h('div.muted.small.clamp2', w.video.title),
      r ? h('div.card', { style: { padding: '12px 14px' } }, r.reasons.map((t) => h('div.reason', icon('sparkle'), h('span', t)))) :
        h('div.banner', icon('warn'), h('span', `This video is blocked (${w.blocked || 'one of your blocks'}).`)),
      w.interest ? h('div.muted.small', `Interest “${w.interest.label}”: weight ${w.interest.weight.toFixed(2)}${w.interest.manual_weight != null ? ' (set by you)' : ''}, from ${w.interest.videos} video${w.interest.videos === 1 ? '' : 's'}.`) : null,
      b ? h('div.card', { style: { padding: '4px 14px 14px' } },
        BREAKDOWN.filter(([k]) => b[k] !== undefined && (b[k] !== 0 || ['interest_part', 'familiar_part'].includes(k))).map(([k, label]) => bar(label, b[k])),
        h('div.kv', { style: { marginTop: '12px', fontWeight: 700 } }, h('span', 'Score'), h('span', r.score.toFixed(3)))) : null,
      h('div.section-title', 'What the algorithm knows about these'),
      h('div.group', w.terms.map((t) => h('div.item',
        h('span.grow', h('div', { style: { fontWeight: 600 } }, t.kind === 'ch' ? `Channel: ${t.term}` : `“${t.term}”`),
          h('div.muted.small', Object.entries(t.counts || {}).map(([k, n]) => `${k} ×${n}`).join(', ') || 'never seen')),
        h('span.val', (t.weight >= 0 ? '+' : '') + Number(t.weight || 0).toFixed(2))))),
      h('div.muted.small', `Memory half-life ${Math.round(w.settings.half_life_days)} days · exploration ${Math.round(w.settings.exploration * 100)}% · channel loyalty ${Math.round(w.settings.channel_loyalty * 100)}%${mode === 'both' ? ' (blended for both of you)' : ''}`),
      w.embedder ? h('div.muted.small', `Similarity model: ${w.embedder.label}.`) : null,
      h('a.btn.block', { href: '#/algorithm' }, icon('sliders'), 'Edit your interests'),
    ));
  } catch (e) {
    put(s.el, h('div.grab'), h('p', `Couldn't explain: ${e.message}`));
  }
}

export function candidateMenu(item, mode, onChange, onRemove) {
  sheet((close) => {
    const act = (label, ic, fn, cls = '', reload = true) => h(`button.menu-item${cls}`, {
      onclick: async () => { close(); try { await fn(); if (reload && onChange) onChange(); } catch (e) { toast(e.message); } },
    }, icon(ic), label);
    const topic = item.interest || (item.keywords || [])[0];
    return h('div',
      h('h2.clamp2', { style: { marginBottom: '8px' } }, item.title),
      act('Download…', 'download', () => downloadSheet(item, { onDone: onChange, shared: mode === 'both' }), '', false),
      act('Why am I seeing this?', 'info', () => whySheet(item, mode), '', false),
      act('Not interested', 'x', () => notInterested(item, onRemove), '', !onRemove),
      item.channel_id ? act(`Go to ${item.channel}`, 'chev', () => navigate(`#/channel/${encodeURIComponent(item.channel_id)}/${encodeURIComponent(item.channel)}`), '', false) : null,
      item.channel_id ? act(`Subscribe to ${item.channel}`, 'bell', () => toggleSubscribe(item, false), '', false) : null,
      !item.interest && topic ? act(`More about “${topic}”`, 'bolt', async () => { await api.boost('kw', topic, 2); toast(`Added “${topic}” to your interests`); }) : null,
      item.interest && mode !== 'both' ? act(`Block the topic “${item.interest}”`, 'ban', async () => {
        if (await confirmSheet(`Block “${item.interest}”?`, 'Videos about it won’t be suggested any more. You can undo this in Your interests.', 'Block')) {
          await api.blockInterest(item.interest_id); toast(`Blocked “${item.interest}”`);
        }
      }, '.danger') : null,
      act(`Block ${item.channel}`, 'mute', async () => { await api.mute('ch', item.channel, true); toast(`Blocked ${item.channel}`); }, '.danger'),
    );
  });
}

// ---- playlists ------------------------------------------------------------------

export async function newPlaylistSheet() {
  let scope = 'personal';
  const other = state.profiles.find((p) => p.id !== state.profile.id);
  const opts = h('div.stack');
  const paint = () => put(opts, ...[
    ['personal', 'Just for me', `Only ${state.profile.name} sees it. You can share it later.`],
    ['shared', other ? 'Share with both of us' : 'Shared', `${other ? `${other.name} sees it too` : 'Everyone sees it'} and can add videos. Its videos become shared too.`],
  ].map(([v, t, d]) => h('button.option', { type: 'button', class: scope === v ? 'on' : '', 'aria-pressed': String(scope === v), onclick: () => { scope = v; paint(); } },
    h('span.radio'), h('span', h('div.t', t), h('div.d', d)))));
  paint();
  const name = await promptSheet('New playlist', { placeholder: 'e.g. Sunday cooking', okLabel: 'Create', extra: opts });
  if (!name) return null;
  try {
    const pl = await api.createPlaylist(name, scope);
    toast(`Created “${pl.name}”`, 2200, 'check');
    return pl;
  } catch (e) { toast(e.message); return null; }
}

export async function addToPlaylistSheet(video) {
  const s = sheet(h('div.stack', h('h2', 'Save to playlist'), skeleton('list', 2)));
  const render = async () => {
    let lists, inIds;
    try {
      [{ playlists: lists }, { playlist_ids: inIds }] = await Promise.all([api.playlists(), api.videoPlaylists(video.id)]);
    } catch (e) {
      put(s.el, h('div.grab'), h('p', e.message));
      return;
    }
    const row = (pl) => {
      const on = inIds.includes(pl.id);
      return h('button.menu-item', { 'aria-pressed': String(on), onclick: async (ev) => {
        const btn = ev.currentTarget;
        btn.disabled = true;
        try {
          if (on) {
            const full = await api.playlist(pl.id);
            const it = full.items.find((i) => i.id === video.id);
            if (it) await api.playlistRemove(pl.id, it.item_id);
            toast(`Removed from “${pl.name}”`);
          } else {
            await api.playlistAdd(pl.id, video.id);
            toast(`Saved to “${pl.name}”`, 2200, 'check');
          }
        } catch (e) { toast(e.message); }
        render();
      } }, icon(on ? 'checkcircle' : 'plus', on ? 'on' : ''), h('span.grow', pl.name),
      h('span.pill', pl.scope === 'shared' ? 'Shared' : 'Mine'));
    };
    put(s.el, h('div.grab'), h('div',
      h('h2', { style: { marginBottom: '8px' } }, 'Save to playlist'),
      lists.length ? lists.map(row) : h('p.muted.small', 'No playlists yet. Make one for a series, a cooking project or movie night.'),
      h('button.menu-item.accent', { onclick: async () => {
        const pl = await newPlaylistSheet();
        if (pl) { try { await api.playlistAdd(pl.id, video.id); toast(`Saved to “${pl.name}”`, 2200, 'check'); } catch (e) { toast(e.message); } }
      } }, icon('plus'), 'New playlist…')));
  };
  render();
}

// ---- delete after watching ----------------------------------------------------------

const STATE_LABEL = { voted: 'Tapped delete', watched: 'Finished', pending: 'Still to watch' };

// The "who's still pending" block: avatars with each person's status.
export function deleteStatusRow(st) {
  return h('div.who-row', st.relevant.map((pid) => {
    const p = profileById(pid);
    const sv = st.state[pid];
    return p ? h(`div.who-chip.${sv}`, avatar(p, 'sm'), h('span', p.id === state.profile.id ? 'You' : p.name),
      h('span.muted', STATE_LABEL[sv])) : null;
  }));
}

// Renders the delete section (used by the end-of-video card and the Delete
// button). onDeleted() runs once the video is actually gone.
export function deleteSection(video, { onDeleted, finished = false, allowForceDelete = false } = {}) {
  const box = h('div.stack.delete-box', h('div.sk.sk-line'));
  const me = state.profile.id;
  const paint = async () => {
    let st;
    try { st = await api.deleteStatus(video.id); } catch (e) { put(box, h('p.muted.small', e.message)); return; }
    if (!st.relevant.includes(me)) {
      const owner = st.relevant.map((p) => (profileById(p) || {}).name).filter(Boolean).join(' & ');
      put(box, h('p.muted.small', `This is ${owner}’s video, so it’s theirs to delete.`));
      return;
    }
    const voted = st.votes.includes(me);
    const vote = h('button.btn.block.danger', { onclick: async () => {
      vote.disabled = true;
      try {
        const r = await api.voteDelete(video.id);
        if (r.deleted) { toast('Deleted. Space freed up.', 2600, 'check'); onDeleted && onDeleted(); return; }
        const names = r.pending.map((p) => (profileById(p) || {}).name).join(' & ');
        toast(`Got it. Deletes once ${names} is done too.`, 3200, 'check');
        paint();
      } catch (e) { toast(e.message); vote.disabled = false; }
    } }, icon('trash'), st.solo ? 'Delete now' : 'Delete when we’re both done');
    if (st.solo) {
      put(box, 
        h('p', finished ? 'All done with this one? Delete it to free up space.' : 'Delete this video from the server?'),
        vote);
      return;
    }
    const pendingNames = st.pending.filter((p) => p !== me).map((p) => (profileById(p) || {}).name);
    put(box, 
      h('p', voted ? `You tapped delete. Waiting on ${pendingNames.join(' & ') || 'nobody'}.`
        : 'Shared video: it’s deleted once you’ve both finished it or tapped delete.'),
      deleteStatusRow(st),
      voted ? h('button.btn.block', { onclick: async () => { await api.unvoteDelete(video.id); toast('Delete vote taken back'); paint(); } }, 'Undo my delete vote') : vote,
      allowForceDelete ? h('button.btn.block.subtle', { onclick: async () => {
        if (await confirmSheet('Delete for both of you now?', 'It will be removed from the server right away, even if not everyone has watched it.', 'Delete now')) {
          try { await api.deleteVideo(video.id); toast('Deleted', 2400, 'check'); onDeleted && onDeleted(); } catch (e) { toast(e.message); }
        }
      } }, 'Delete for both now') : null);
  };
  paint();
  return box;
}

export function deleteSheet(video, { onDeleted } = {}) {
  sheet((close) => h('div.stack',
    h('h2.clamp2', video.title),
    deleteSection(video, { allowForceDelete: true, onDeleted: () => { close(); onDeleted && onDeleted(); } }),
    h('button.btn.block', { onclick: () => close() }, 'Cancel')));
}

// ---- per-video menu (library, playlists, continue watching) ----------------------------

// "Share with both of us" for a private video (null when it's already shared).
function shareAction(video, act, onChange) {
  const other = state.profiles.find((p) => p.id !== state.profile.id);
  if (!other || video.shared !== false || !['ready', 'queued', 'downloading'].includes(video.status)) return null;
  return act('Share with both of us', 'users', async () => {
    await api.shareVideo(video.id);
    toast(`Shared with ${other.name}: it’s in the Shared queue`, 2600, 'check');
    onChange && onChange();
  });
}

export function videoMenu(video, { onChange, extra = [] } = {}) {
  const me = state.profile.id;
  const ws = (video.watch || {})[me];
  const watched = !!(ws && ws.watched);
  sheet((close) => {
    const act = (label, ic, fn, cls = '') => h(`button.menu-item${cls}`, {
      onclick: async () => { close(); try { await fn(); } catch (e) { toast(e.message); } },
    }, icon(ic), label);
    return h('div',
      h('h2.clamp2', { style: { marginBottom: '8px' } }, video.title),
      video.status === 'ready' ? act('Play', 'play', () => navigate(`#/player/${encodeURIComponent(video.id)}`)) : null,
      video.status === 'ready' ? act(watched ? 'Mark as unwatched' : 'Mark as watched', watched ? 'eye' : 'checkcircle', async () => {
        await api.setWatched(video.id, !watched);
        toast(watched ? 'Marked as unwatched' : 'Marked as watched', 2200, 'check');
        onChange && onChange();
      }) : null,
      act('Save to playlist…', 'listadd', () => addToPlaylistSheet(video)),
      shareAction(video, act, onChange),
      ...extra.map(([label, ic, fn, cls]) => act(label, ic, fn, cls || '')),
      video.status === 'ready' ? act(video.keep ? 'Allow auto-delete' : 'Keep (never auto-delete)', 'pin', async () => {
        await api.keep(video.id, !video.keep);
        toast(video.keep ? 'Auto-delete back on' : 'Kept: won’t be auto-deleted', 2400, 'check');
        onChange && onChange();
      }) : null,
      video.status === 'ready' ? act('Delete…', 'trash', () => deleteSheet(video, { onDeleted: onChange }), '.danger') : null);
  });
}
