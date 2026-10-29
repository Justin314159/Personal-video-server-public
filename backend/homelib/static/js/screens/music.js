// Music: YouTube Music search, your songs on the server, play in the app or
// save the mp3 onto the phone.
import { api } from '../api.js';
import { h, icon, seg, toast, sheet, fmtDuration, emptyState, confirmSheet, skeleton } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state } from '../state.js';
import { navigate } from '../router.js';
import { playTracks, currentTrack, onTrackChange } from '../musicplayer.js';

const last = { q: '', results: [] };
let timer;
let offTrack;

export const musicScreen = {
  async render(root) {
    nav('search');
    if (!state.settings) state.settings = await api.settings();
    const input = h('input', { type: 'search', placeholder: 'Search songs or paste a link', enterkeyhint: 'search', autocapitalize: 'off', autocomplete: 'off', 'aria-label': 'Search music' });
    const results = h('div');
    const mine = h('div');

    const run = async (q) => {
      q = q.trim();
      if (!q) return;
      input.blur();
      results.replaceChildren(skeleton('list', 4));
      try {
        const r = await api.musicSearch(q);
        last.q = q; last.results = r.results;
        renderResults();
      } catch (e) {
        results.replaceChildren(emptyState('warn', 'Search failed', e.message));
      }
    };

    const renderResults = () => {
      if (!last.q) { results.replaceChildren(); return; }
      results.replaceChildren(
        h('div.section-title', `Results for “${last.q}”`, h('span.spacer'),
          h('button.chip', { onclick: () => { last.q = ''; last.results = []; input.value = ''; renderResults(); } }, 'Clear')),
        last.results.length ? h('div.card', { style: { padding: '0 12px' } }, last.results.map((s) => {
          const st = s.local_status;
          const right = st === 'ready' ? h('span.pill.accent', 'Saved')
            : st === 'queued' || st === 'downloading' ? h('span.pill', 'Downloading')
            : h('button.icon-btn.filled', { 'aria-label': `Download ${s.title}`, onclick: () => downloadSheet(s, () => { s.local_status = 'queued'; renderResults(); loadMine(); }) }, icon('download'));
          return h('div.track', h('img', { src: s.thumb, alt: '', loading: 'lazy' }),
            h('div.grow', h('div.title.clamp2', s.title), h('div.sub', [s.channel, fmtDuration(s.duration)].filter(Boolean).join(' · '))), right);
        })) : emptyState('music', 'No songs found', 'Try the artist and song name.'));
    };

    const loadMine = async () => {
      const { tracks } = await api.music();
      const ready = tracks.filter((t) => t.status === 'ready');
      const busy = tracks.filter((t) => t.status !== 'ready');
      const now = currentTrack();
      // Keep the search results' "Downloading/Saved" badges in sync.
      const byId = Object.fromEntries(tracks.map((t) => [t.id, t.status]));
      let changed = false;
      for (const r of last.results) {
        const st = byId[`au_${r.id}`] || null;
        if (st !== r.local_status) { r.local_status = st; changed = true; }
      }
      if (changed) renderResults();
      mine.replaceChildren(...[
        h('div.section-title', `Your music (${ready.length})`, h('span.spacer'),
          ready.length ? h('button.chip', { onclick: () => playTracks(ready, 0) }, icon('play'), 'Play all') : null),
        busy.length ? h('div.stack', { style: { marginBottom: '12px' } }, busy.map((t) => h('div.card.row', { style: { padding: '10px 12px' } },
          h('div.grow', h('div.ellipsis', { style: { fontWeight: 600 } }, t.title),
            h('div.progress', { style: { marginTop: '8px' } }, h('div', { style: { width: `${t.download ? Math.round(t.download.progress) : 0}%` } }))),
          h('span.small.muted', t.status === 'error' ? 'Failed' : t.download && t.download.phase === 'converting' ? 'Converting…' : `${t.download ? Math.round(t.download.progress) : 0}%`)))) : null,
        ready.length ? h('div.card', { style: { padding: '0 12px' } }, ready.map((t, i) => h('div.track', { class: now && now.id === t.id ? 'playing' : '' },
          h('button.track-main', { onclick: () => playTracks(ready, i), 'aria-label': `Play ${t.title}` },
            h('img', { src: t.thumb, alt: '' }),
            h('div.grow', h('div.title.clamp2', t.title),
              h('div.sub', [t.channel, fmtDuration(t.duration), (t.format || '').toUpperCase()].filter(Boolean).join(' · ')))),
          h('a.icon-btn', { href: t.file, download: '', 'aria-label': `Save ${t.title} to iPhone`, onclick: saveHint }, icon('phone')),
          h('button.icon-btn', { 'aria-label': `Delete ${t.title}`, onclick: async () => {
            if (await confirmSheet('Delete this song?', `“${t.title}” will be removed from the server. Copies already saved on a phone stay there.`, 'Delete')) {
              await api.deleteVideo(t.id); toast('Deleted'); loadMine();
            }
          } }, icon('trash')))))
          : busy.length ? null : emptyState('music', 'No music yet', 'Search for a song and tap download. Songs stay on the server until you delete them.')].filter(Boolean));
      clearTimeout(timer);
      if (busy.some((t) => t.status !== 'error')) timer = setTimeout(() => loadMine().catch(() => {}), 1200);
    };

    root.append(screen(
      topbar('Music'),
      seg([['videos', 'Videos'], ['music', 'Music']], 'music', (v) => { if (v === 'videos') navigate('#/search'); }),
      h('div', { style: { height: '12px' } }),
      h('form', { role: 'search', onsubmit: (e) => { e.preventDefault(); run(input.value); } }, h('label.field', icon('search'), input)),
      results, mine));
    if (last.q) { input.value = last.q; renderResults(); }
    offTrack = onTrackChange(() => loadMine().catch(() => {}));
    await loadMine();
  },
  unmount() { clearTimeout(timer); if (offTrack) offTrack(); },
};

function downloadSheet(song, onDone) {
  let fmt = state.settings.music_format || 'mp3';
  const OPTS = [
    ['mp3', 'MP3', 'Plays everywhere. Converted on the server (192 kbit/s).'],
    ['m4a', 'M4A (AAC)', 'Original audio, no conversion. Plays on iPhone and most apps.'],
  ];
  sheet((close) => {
    const wrap = h('div.stack');
    const paint = () => wrap.replaceChildren(...OPTS.map(([v, t, d]) => h('button.option', { class: fmt === v ? 'on' : '', onclick: () => { fmt = v; paint(); } },
      h('span.radio'), h('span', h('div.t', t), h('div.d', d)))));
    paint();
    const go = h('button.btn.primary.block', { onclick: async () => {
      go.disabled = true;
      try { await api.musicDownload(song, fmt); toast('Downloading song'); close(); onDone(); }
      catch (e) { toast(e.code === 'disk_full' ? `Not enough disk space. ${e.message}` : e.message, 4500); go.disabled = false; }
    } }, icon('download'), 'Download');
    return h('div.stack', h('div', h('h2.clamp2', song.title), h('div.muted.small', [song.channel, fmtDuration(song.duration)].filter(Boolean).join(' · '))),
      h('div.section-title', 'Format'), wrap,
      h('div.muted.small', 'The song is saved on the home server. Use the phone button next to it to put a copy on this iPhone.'), go);
  });
}

// First time only: explain where the file ends up on iOS.
function saveHint() {
  let seen = false;
  try { seen = localStorage.getItem('hl.saveHint') === '1'; localStorage.setItem('hl.saveHint', '1'); } catch { /* ignore */ }
  if (seen) return;
  setTimeout(() => sheet((close) => h('div.stack',
    h('h2', 'Saving to your iPhone'),
    h('p', 'Safari asks “Do you want to download…?”. Tap Download. The song goes to the Files app → Downloads, and plays from there or in VLC.'),
    h('p.muted.small', 'If a player opens instead, tap the Share button → “Save to Files”. Apple’s Music app can’t import songs on the iPhone itself; that needs a computer.'),
    h('button.btn.block', { onclick: close }, 'Got it'))), 400);
}
