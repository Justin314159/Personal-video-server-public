// "Your algorithm": everything the recommender knows, editable.
import { api } from '../api.js';
import { h, icon, seg, toast, debounce, confirmSheet, fmtAgo, emptyState, toggle } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state } from '../state.js';

let tab = 'kw';
const LABEL = { finish: 'Finished', like: 'Liked', dislike: 'Disliked', delete_unwatched: 'Deleted unwatched', skip: 'Skipped', search: 'Searched' };

export const algorithmScreen = {
  async render(root) {
    nav('settings');
    let a = await api.algorithm();
    const sliders = h('div.group');
    const terms = h('div');
    const starters = h('div');
    const reload = async () => { a = await api.algorithm(); renderTerms(); renderStarters(); };
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

    renderTerms();
    renderStarters();

    const w = a.event_weights;
    root.append(screen(
      topbar('Your algorithm', { backTo: '#/settings' }),
      h('p.muted.small', `This is everything “For you” knows about ${state.profile.name}. It lives on your server and nowhere else. “Both of us” mode blends both profiles equally.`),
      h('div.section-title', 'Controls'), sliders,
      h('div.section-title', 'What it knows'), terms,
      h('div.section-title', 'Starter topics'),
      h('p.muted.small', 'Used for exploration and when there isn’t enough history yet.'), starters,
      h('div.section-title', 'How signals count'),
      h('div.group', h('div.block.small',
        Object.entries(w).map(([k, v]) => h('div.kv', h('span', LABEL[k] || k), h('span', { style: { fontWeight: 600 } }, `${v > 0 ? '+' : ''}${v}`))),
        h('div.muted', { style: { marginTop: '8px' } }, 'Skip = left within 2 seconds. Suggestions are fetched nightly at 03:30. Score = familiar × (1 − exploration) + newness × exploration + a small daily shuffle.'))),
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
