// "Your algorithm": everything the recommender knows, editable. The interest
// editor (topics learned from what you watch, plus your own), blocks, "not
// interested", subscriptions, the sliders, keywords/channels and history.
import { api } from '../api.js';
import { h, put, icon, seg, toast, debounce, confirmSheet, promptSheet, fmtAgo, emptyState, toggle } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state } from '../state.js';

let tab = 'kw';
const LABEL = { finish: 'Finished', like: 'Liked', dislike: 'Disliked', delete_unwatched: 'Deleted unwatched', skip: 'Skipped', search: 'Searched',
  rewatch: 'Rewatched', more: 'More like this', less: 'Less like this', not_interested: 'Not interested', subscribe: 'Subscribed',
  watched_mostly: 'Watched most of', abandoned: 'Stopped early' };
let blockKind = 'kw';

export const algorithmScreen = {
  async render(root) {
    nav('settings');
    let a = await api.algorithm();
    const sliders = h('div.group');
    const terms = h('div');
    const starters = h('div');
    const editor = h('div');
    const reload = async () => { a = await api.algorithm(); renderTerms(); renderStarters(); renderEditor(); };
    const saveSettings = debounce((patch) => api.setAlgorithm(patch).then(() => toast('Saved')), 400);

    const slider = (key, label, min, max, step, fmt, ends, help) => {
      const val = h('span.muted', fmt(a.settings[key]));
      const input = h('input', { type: 'range', min, max, step, value: a.settings[key], 'aria-label': label });
      input.addEventListener('input', () => { val.textContent = fmt(Number(input.value)); saveSettings({ [key]: Number(input.value) }); });
      return h('div.slider', h('div.row', h('label', label), val), input, h('div.ends', h('span', ends[0]), h('span', ends[1])), h('div.muted.small', { style: { marginTop: '6px' } }, help));
    };
    sliders.append(
      slider('half_life_days', 'Memory', 7, 90, 1, (v) => `${v}-day half-life`, ['Recent only', 'Long memory'],
        'How fast old signals fade. At 30 days, something you watched a month ago counts half.'),
      slider('exploration', 'Exploration', 0, 1, 0.05, (v) => `${Math.round(v * 100)}%`, ['Familiar', 'Something new'],
        'Higher mixes in topics you have never watched.'),
      slider('channel_loyalty', 'Channel loyalty', 0, 1, 0.05, (v) => `${Math.round(v * 100)}%`, ['Topics', 'Channels'],
        'Whether the channel or the subject matters more when matching.'),
      h('div.slider.row', h('div.grow', h('label', 'Include Shorts in For you'), h('div.muted.small', 'Shorts always have their own feed too.')),
        toggle(a.settings.include_shorts_in_feed, (v) => api.setAlgorithm({ include_shorts_in_feed: v }).then(() => toast('Saved')), 'Include Shorts in For you')),
    );

    const addInput = h('input', { placeholder: 'Add a topic to boost, e.g. pottery', 'aria-label': 'Topic to boost', enterkeyhint: 'done' });
    const addForm = h('form', { onsubmit: async (e) => { e.preventDefault(); const t = addInput.value.trim(); if (!t) return; await api.boost(tab === 'ch' ? 'ch' : 'kw', t, 2); addInput.value = ''; toast(`Boosted ${t}`); reload(); } },
      h('label.field', icon('plus'), addInput));

    function termRow(t, kind) {
      const key = `${kind}:${kind === 'kw' ? t.term.toLowerCase() : t.term}`;
      const muted = a.settings.mutes.includes(key);
      const boosted = !!a.settings.boosts[key];
      const w = t.weight;
      const counts = Object.entries(t.counts || {}).map(([k, n]) => `${(LABEL[k] || k).toLowerCase()} ${n}`).join(' · ');
      return h('div.term',
        h('div.row', h('span.name.grow', kind === 'kw' ? `“${t.term}”` : t.term),
          muted ? h('span.pill.danger', 'Muted') : null, boosted ? h('span.pill.accent', `Boost +${a.settings.boosts[key]}`) : null,
          h('span.muted.small', (w >= 0 ? '+' : '') + w.toFixed(2))),
        h('div.wbar', h(`div.${w >= 0 ? 'pos' : 'neg'}`, { style: { width: `${Math.min(50, (Math.abs(w) / maxW) * 50)}%` } })),
        h('div.muted.small', { style: { marginTop: '6px' } }, counts || (boosted ? 'Added by you' : '')),
        h('div.actions',
          h(`button.chip${boosted ? '.on' : ''}`, { onclick: async () => { await api.boost(kind, t.term, boosted ? 0 : 2); reload(); } }, icon('bolt'), boosted ? 'Unboost' : 'Boost'),
          h(`button.chip${muted ? '.on' : ''}`, { onclick: async () => { await api.mute(kind, t.term, !muted); reload(); } }, icon('mute'), muted ? 'Unmute' : 'Mute'),
          h('button.chip', { onclick: async () => { await api.forget(kind, t.term, true); toast(`Forgot ${t.term}`); reload(); } }, icon('trash'), 'Forget')));
    }

    let maxW = 1;
    function renderTerms() {
      const list = tab === 'kw' ? a.keywords : tab === 'ch' ? a.channels : null;
      maxW = Math.max(1, ...[...a.keywords, ...a.channels].map((t) => Math.abs(t.weight)));
      let body;
      if (tab === 'events') {
        body = a.events.length ? h('div.group', a.events.map((e) => h('div.item',
          h('span.grow', h('div', { style: { fontWeight: 600 } }, `${LABEL[e.type] || e.type} `, h('span.muted.small', `${e.weight > 0 ? '+' : ''}${e.weight}`)),
            h('div.muted.small.clamp2', e.type === 'search' ? `“${e.data.query || e.keywords.join(' ')}”` : (e.data.title || e.keywords.join(', '))),
            h('div.muted.small', fmtAgo(e.ts))),
          h('button.icon-btn', { 'aria-label': 'Delete this event', onclick: async () => { await api.deleteEvent(e.id); reload(); } }, icon('x')))))
          : emptyState('eye', 'No history yet', 'Watching, liking, skipping and searching all show up here.');
      } else {
        body = list.length ? h('div.group', list.map((t) => termRow(t, tab)))
          : emptyState('sparkle', 'Nothing learned yet', 'Watch and rate a few videos, or boost a topic above.');
      }
      terms.replaceChildren(...[
        seg([['kw', `Topics (${a.keywords.length})`], ['ch', `Channels (${a.channels.length})`], ['events', 'History']], tab, (v) => { tab = v; renderTerms(); }),
        h('div', { style: { height: '10px' } }),
        tab !== 'events' ? addForm : null,
        h('div', { style: { height: '10px' } }),
        body].filter(Boolean));
      addInput.placeholder = tab === 'ch' ? 'Add a channel to boost' : 'Add a topic to boost, e.g. pottery';
    }

    function renderStarters() {
      const input = h('input', { placeholder: 'Add a starter topic', 'aria-label': 'Starter topic' });
      starters.replaceChildren(
        h('div.chips', { style: { flexWrap: 'wrap' } }, a.settings.starter_topics.map((t) => h('button.chip', {
          'aria-label': `Remove ${t}`,
          onclick: async () => { await api.setAlgorithm({ starter_topics: a.settings.starter_topics.filter((x) => x !== t) }); reload(); },
        }, t, h('span.x', '×')))),
        h('form', { onsubmit: async (e) => { e.preventDefault(); const t = input.value.trim(); if (!t) return; await api.setAlgorithm({ starter_topics: [...a.settings.starter_topics, t] }); reload(); } },
          h('label.field', { style: { marginTop: '8px' } }, icon('plus'), input)));
    }

    // ---- the interest editor --------------------------------------------------------
    const counts = (c) => Object.entries(c || {}).map(([k, n]) => (k === 'subscribed' ? 'from a channel you subscribe to' : `${(LABEL[k] || k).toLowerCase()} ${n}`)).join(' · ');
    const act = async (fn, msg) => { try { await fn(); if (msg) toast(msg, 2200, 'check'); await reload(); } catch (e) { toast(e.message); } };

    function interestRow(it, maxIW) {
      const w = it.weight;
      const step = (d) => act(() => api.updateInterest(it.id, { weight: Math.max(-5, Math.min(5, Math.round((w + d) * 2) / 2)) }));
      const badge = it.pinned ? h('span.pill.accent', 'Pinned') : null;
      const kind = it.kind === 'manual' ? h('span.pill', it.migrated ? 'From your keywords' : 'Added by you') : h('span.pill', 'Learned');
      const why = it.kind === 'manual'
        ? (it.videos ? `${it.videos} video${it.videos === 1 ? '' : 's'} match · ${counts(it.counts)}` : 'Searched for even without history')
        : `From ${it.videos} video${it.videos === 1 ? '' : 's'}${it.counts ? ` · ${counts(it.counts)}` : ''}`;
      return h('div.term.interest',
        h('div.row', h('span.name.grow', it.label), badge, kind),
        h('div.wbar', h(`div.${w >= 0 ? 'pos' : 'neg'}`, { style: { width: `${Math.min(50, (Math.abs(w) / maxIW) * 50)}%` } })),
        h('div.muted.small', { style: { marginTop: '6px' } }, why),
        it.examples.length ? h('div.muted.small.clamp2', `e.g. ${it.examples.slice(0, 2).join(' · ')}`) : null,
        h('div.row.weight-row',
          h('button.icon-btn.small', { 'aria-label': `Less ${it.label}`, onclick: () => step(-0.5) }, icon('minus')),
          h('span.weight-val', { title: 'Weight' }, (w >= 0 ? '' : '−') + Math.abs(w).toFixed(1)),
          h('button.icon-btn.small', { 'aria-label': `More ${it.label}`, onclick: () => step(0.5) }, icon('plus')),
          it.manual_weight != null && it.kind === 'learned'
            ? h('button.chip', { onclick: () => act(() => api.updateInterest(it.id, { auto: true }), 'Back to the learned weight') }, 'Auto')
            : h('span.muted.small', it.manual_weight != null ? 'set by you' : 'learned')),
        h('div.actions',
          h(`button.chip${it.pinned ? '.on' : ''}`, { onclick: () => act(() => api.updateInterest(it.id, { pinned: !it.pinned }), it.pinned ? 'Unpinned' : 'Pinned: always in your feed') }, icon('pin'), it.pinned ? 'Unpin' : 'Pin'),
          h('button.chip', { onclick: async () => {
            const name = await promptSheet('Rename interest', { value: it.label, okLabel: 'Rename' });
            if (name) act(() => api.updateInterest(it.id, { label: name }), 'Renamed');
          } }, icon('edit'), 'Rename'),
          h('button.chip', { onclick: async () => {
            if (await confirmSheet(`Block “${it.label}”?`, 'Videos about it won’t be suggested at all. Undo below under Blocked.', 'Block')) act(() => api.blockInterest(it.id), `Blocked “${it.label}”`);
          } }, icon('ban'), 'Block'),
          h('button.chip', { onclick: async () => {
            const learned = it.kind === 'learned';
            if (await confirmSheet(`Delete “${it.label}”?`, learned ? 'The feed forgets this interest. It won’t come back from the videos it was learned from (restore it below).' : 'Your topic is removed.', 'Delete')) act(() => api.deleteInterest(it.id), 'Deleted');
          } }, icon('trash'), 'Delete')));
    }

    function renderEditor() {
      const maxIW = Math.max(1, ...a.interests.map((i) => Math.abs(i.weight)));
      const topicInput = h('input', { placeholder: 'Add a topic, e.g. pottery or Roman history', 'aria-label': 'New topic', enterkeyhint: 'done', maxlength: 60 });
      const blockInput = h('input', { placeholder: blockKind === 'ch' ? 'Channel name to block' : 'Topic to block, e.g. cooking', 'aria-label': 'Block', enterkeyhint: 'done' });
      const blocked = [...a.blocked.topics.map((t) => ['kw', t]), ...a.blocked.channels.map((c) => ['ch', c])];
      put(editor,
        h('p.muted.small', 'Grouped from what you watch, like, rewatch and subscribe to. Weights grow slowly, so a couple of videos only nudge the feed, and fade over time (Memory slider). Your changes always win.'),
        h('form', { onsubmit: (e) => { e.preventDefault(); const t = topicInput.value.trim(); if (t) act(() => api.addInterest(t), `Added “${t}”`); } },
          h('label.field', icon('plus'), topicInput)),
        h('div', { style: { height: '10px' } }),
        a.interests.length ? h('div.group', a.interests.map((it) => interestRow(it, maxIW)))
          : emptyState('sparkle', 'No interests yet', 'Watch, like or subscribe to a few things, or add a topic above.'),
        a.hidden_interests.length ? h('details.more', h('summary.muted.small', `Deleted interests (${a.hidden_interests.length})`),
          h('div.group', a.hidden_interests.map((it) => h('div.item', h('span.grow', it.label),
            h('button.chip', { onclick: () => act(() => api.restoreInterest(it.id), `Restored “${it.label}”`) }, 'Restore'))))) : null,

        h('div.section-title', icon('ban'), 'Blocked'),
        h('p.muted.small', 'Never suggested, also not in “Both of us”. A blocked topic also covers videos about the same thing.'),
        seg([['kw', 'Topic'], ['ch', 'Channel']], blockKind, (v) => { blockKind = v; renderEditor(); }),
        h('form', { style: { marginTop: '8px' }, onsubmit: (e) => { e.preventDefault(); const t = blockInput.value.trim(); if (t) act(() => api.mute(blockKind, t, true), `Blocked ${t}`); } },
          h('label.field', icon('ban'), blockInput)),
        blocked.length ? h('div.group', { style: { marginTop: '10px' } }, blocked.map(([k, t]) => h('div.item',
          h('span.grow', h('div', { style: { fontWeight: 600 } }, k === 'ch' ? t : `“${t}”`), h('div.muted.small', k === 'ch' ? 'Channel' : 'Topic')),
          h('button.chip', { onclick: () => act(() => api.mute(k, t, false), `Unblocked ${t}`) }, 'Unblock'))))
          : h('p.muted.small', 'Nothing blocked.'),

        h('div.section-title', icon('x'), 'Not interested'),
        a.not_interested.length ? h('div.group', a.not_interested.map((n) => h('div.item',
          h('span.grow', h('div.clamp2', { style: { fontWeight: 600 } }, n.title || n.video_id), h('div.muted.small', [n.channel, fmtAgo(n.ts)].filter(Boolean).join(' · '))),
          h('button.chip', { onclick: () => act(() => api.undoNotInterested(n.video_id), 'Undone') }, 'Undo'))))
          : h('p.muted.small', 'Videos you mark “Not interested” in For you are listed here.'),

        h('div.section-title', icon('bell'), 'Subscriptions', h('span.spacer'), h('a.small', { href: '#/subs', style: { textTransform: 'none', letterSpacing: 0 } }, 'Open')),
        a.subscriptions.length ? h('div.group', a.subscriptions.map((c) => h('div.item',
          h('a.grow', { href: `#/channel/${encodeURIComponent(c.channel_id)}/${encodeURIComponent(c.channel)}`, style: { fontWeight: 600, color: 'inherit', textDecoration: 'none' } }, c.channel),
          h('button.chip', { onclick: () => act(() => api.unsubscribe(c.channel_id), `Unsubscribed from ${c.channel}`) }, 'Unsubscribe'))))
          : h('p.muted.small', 'Subscribe from a video or a channel page.'),
        h('p.muted.small', { style: { marginTop: '10px' } }, `Similarity model: ${a.embeddings.label}.`),
      );
    }

    renderTerms();
    renderStarters();
    renderEditor();

    const w = a.signal_strengths;
    root.append(screen(
      topbar('Your algorithm', { backTo: '#/settings' }),
      h('p.muted.small', `This is everything “For you” knows about ${state.profile.name}. It lives on your server and nowhere else. “Both of us” mode blends both profiles equally.`),
      h('div.section-title', 'Controls'), sliders,
      h('div.section-title', icon('sparkle'), 'Your interests'), editor,
      h('div.section-title', 'Keywords & channels'),
      h('p.muted.small', 'The finer, older view: single words and channels. Boosting a word adds it to your interests.'), terms,
      h('div.section-title', 'Starter topics'),
      h('p.muted.small', 'Used for exploration and when there isn’t enough history yet.'), starters,
      h('div.section-title', 'How signals count'),
      h('div.group', h('div.block.small',
        Object.entries(w).map(([k, v]) => h('div.kv', h('span', LABEL[k] || k), h('span', { style: { fontWeight: 600 } }, `${v > 0 ? '+' : ''}${v}`))),
        h('div.muted', { style: { marginTop: '8px' } }, 'Explicit feedback counts more than watching. Skip = left within 2 seconds; stopped early = under 15% after two days. Subscribing counts for the channel and its topics. Suggestions are fetched every morning and when you reload; the Exploration slider sets the share of new topics in both the searches and the feed, and no interest gets more than 30% of the rest.'))),
      h('div.section-title', 'Your data'),
      h('div.stack',
        h('button.btn.block', { onclick: exportJson }, icon('export'), 'Export as JSON'),
        h('button.btn.block.danger', { onclick: async () => {
          if (await confirmSheet('Reset your algorithm?', `Deletes ${state.profile.name}’s history, boosts, mutes and settings. Downloads are not affected.`, 'Reset')) {
            await api.resetAlgorithm(); toast('Algorithm reset'); reload();
          }
        } }, icon('refresh'), 'Reset algorithm')),
    ));
  },
};

async function exportJson() {
  const data = await api.exportAlgorithm();
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
  const a = h('a', { href: URL.createObjectURL(blob), download: `homelib-algorithm-${state.profile.name.replace(/[^A-Za-z0-9]+/g, '-')}.json` });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  toast('Exported');
}
