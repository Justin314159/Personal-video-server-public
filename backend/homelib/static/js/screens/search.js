import { api } from '../api.js';
import { h, icon, thumbEl, fmtViews, toast, emptyState, seg, skeleton, errorState } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state } from '../state.js';
import { navigate } from '../router.js';
import { downloadSheet } from './common.js';

const RECENT_KEY = () => `hl.recent.${state.profile.id}`;
const lastResults = { q: '', results: [] };

function recent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY()) || '[]'); } catch { return []; }
}
function remember(q) {
  const r = [q, ...recent().filter((x) => x !== q)].slice(0, 8);
  try { localStorage.setItem(RECENT_KEY(), JSON.stringify(r)); } catch { /* ignore */ }
}

export const searchScreen = {
  async render(root) {
    nav('search');
    const input = h('input', { type: 'search', placeholder: 'Search YouTube or paste a link', enterkeyhint: 'search', autocapitalize: 'off', autocomplete: 'off', 'aria-label': 'Search' });
    const list = h('div.vlist.grid');
    const chips = h('div.chips');
    const form = h('form', { role: 'search', onsubmit: (e) => { e.preventDefault(); run(input.value); } },
      h('label.field', icon('search'), input));

    const renderRecent = () => {
      const r = recent();
      chips.replaceChildren(...r.map((q) => h('button.chip', { type: 'button', onclick: () => { input.value = q; run(q); } }, q)));
    };

    const renderResults = (results) => {
      if (!results.length) {
        list.replaceChildren(emptyState('search', 'No results', 'Try different words.'));
        return;
      }
      list.replaceChildren(...results.map((v) => {
        const status = v.local_status;
        const badge = status === 'ready' ? 'Downloaded' : (status === 'queued' || status === 'downloading') ? 'Downloading' : null;
        return h('button.vitem', { onclick: () => (status === 'ready' ? navigate(`#/player/${encodeURIComponent(v.id)}`) : downloadSheet(v, { onDone: () => { v.local_status = 'queued'; } })) },
          thumbEl(v, { badge }),
          h('div.meta', h('div.grow',
            h('div.title.clamp2', v.title),
            h('div.sub', [v.channel, fmtViews(v.view_count)].filter(Boolean).join(' · ')))));
      }));
    };

    async function run(q) {
      q = q.trim();
      if (!q) return;
      input.blur();
      remember(q);
      renderRecent();
      list.replaceChildren(skeleton('grid', 3));
      try {
        const { results } = await api.search(q);
        lastResults.q = q; lastResults.results = results;
        renderResults(results);
      } catch (e) {
        list.replaceChildren(errorState(e, () => run(q)));
      }
    }

    root.append(screen(topbar('Search'),
      seg([['videos', 'Videos'], ['music', 'Music']], 'videos', (v) => { if (v === 'music') navigate('#/music'); }),
      h('div', { style: { height: '12px' } }),
      form, chips, h('div', { style: { height: '12px' } }), list));
    renderRecent();
    if (lastResults.q) { input.value = lastResults.q; renderResults(lastResults.results); }
    else list.append(emptyState('search', 'Find something to watch', 'Search like you would on YouTube. Tap a result to download it as an mp4 to the home server.'));
  },
};
