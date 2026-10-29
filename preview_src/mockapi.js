// In-browser fake backend for the static preview (no server needed).
// Mirrors the HTTP API in js/api.js. State lives in localStorage so clicks
// persist between reloads; "Reset demo data" in Settings clears it.
// The catalog comes from preview/mockdata.js, generated from homelib/mock.py.

const CATALOG = window.HOMELIB_MOCK_DATA.catalog;
const SONGS = window.HOMELIB_MOCK_DATA.songs;
const BY_ID = Object.fromEntries([...CATALOG, ...SONGS].map((v) => [v.id, v]));
const AU = 'au_';
const ytid = (id) => (id.startsWith(AU) ? id.slice(AU.length) : id);
const KEY = 'hl.demo.v4';
const HOUR = 3600, DAY = 86400;
const WEIGHTS = { finish: 1, like: 1.5, dislike: -2, delete_unwatched: -1, skip: -1, search: 0.2 };
const LABELS = { finish: 'finished', like: 'liked', dislike: 'disliked', delete_unwatched: 'deleted unwatched', skip: 'skipped', search: 'searched' };
const STOP = new Set('a about after all also an and any are as at be been but by can could did do does for from get got had has have how i if in into is it its just me more my no not now of on one or our out over really so some than that the their them then there these they this those to too up us very was we were what when where which while who why will with you your video videos official full new best top part episode shorts short watch ft feat 4k hd make made making day days time way thing things like vs every'.split(' '));
const PRESETS = {
  midnight: { scheme: 'dark', bg: '#0F1115', surface: '#181B21', border: '#2A2F39', text: '#F2F3F5', muted: '#A3A9B5', progress: null, accent: null },
  oled: { scheme: 'dark', bg: '#000000', surface: '#0B0B0D', border: '#1F2228', text: '#F5F5F7', muted: '#9AA0AA', progress: null, accent: null },
  warm: { scheme: 'dark', bg: '#17120E', surface: '#221A14', border: '#3A2D22', text: '#F7EFE6', muted: '#BFAE9C', progress: null, accent: null },
  daylight: { scheme: 'light', bg: '#F6F7F9', surface: '#FFFFFF', border: '#D9DDE3', text: '#14171C', muted: '#5A6270', progress: null, accent: null },
  linen: { scheme: 'light', bg: '#F7F2EB', surface: '#FFFCF7', border: '#E2D8CA', text: '#2A2018', muted: '#6E5E4E', progress: null, accent: null },
};
const SHARED_KEYS = ['accent', 'progress'];
const DEFAULT_ALGO = { half_life_days: 30, exploration: 0.2, channel_loyalty: 0.4, boosts: {}, mutes: [], forgotten: [],
  starter_topics: ['home cooking', 'woodworking', 'japan travel', 'space science', 'guitar lessons'], include_shorts_in_feed: false };

const now = () => Date.now() / 1000;
const clone = (x) => JSON.parse(JSON.stringify(x));
const delay = (v, ms = 120) => new Promise((r) => setTimeout(() => r(clone(v)), ms));
function fail(msg, status = 400, code) { const e = new Error(msg); e.status = status; e.code = code; return Promise.reject(e); }

export function keywords(...texts) {
  const out = [];
  for (const t of texts) for (let w of (t || '').toLowerCase().match(/[\p{L}\p{N}]+(?:'[\p{L}\p{N}]+)?/gu) || []) {
    w = w.replace(/'s$/, '');
    if (w.length < 3 || /^\d+$/.test(w) || STOP.has(w) || out.includes(w)) continue;
    out.push(w);
    if (out.length >= 12) return out;
  }
  return out;
}

function hashNum(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) >>> 0; return h; }
function mediaFor(v) {
  const n = (hashNum(v.id) % 3) + 1;
  if (v.media === 'audio') return `media/song-${n}.mp3`;
  return v.is_short ? `media/vertical-${n}.mp4` : `media/landscape-${n}.mp4`;
}
function pub(v) { return { id: v.id, title: v.title, channel: v.channel, channel_id: v.channel_id, duration: v.duration, url: v.url, thumb_url: v.thumb_url, is_short: v.is_short, view_count: v.view_count, keywords: v.keywords }; }

// ---------------------------------------------------------------- state
let S = load();

function load() {
  try { const s = JSON.parse(localStorage.getItem(KEY)); if (s && s.v === 4) return s; } catch { /* ignore */ }
  return seed();
}
function save() { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch { /* ignore */ } }

function seed() {
  const t = now();
  const s = {
    v: 4, ids: 100, playlists: [], subs: [], votes: {}, seen: {}, shown: {},
    profiles: [{ id: 1, name: 'Alex', accent: '#46D3B8' }, { id: 2, name: 'Sam', accent: '#F2B84B' }],
    themes: {}, videos: {}, queue: [], watch: { 1: {}, 2: {} }, downloads: [], events: [],
    algo: { 1: clone(DEFAULT_ALGO), 2: clone(DEFAULT_ALGO) }, settings: { default_quality: 'compat', auto_delete_hours: 24, music_format: 'mp3' },
    reco: {}, last_fetch: t - 5 * HOUR,
  };
  const find = (re) => CATALOG.find((v) => re.test(v.title));
  const ready = (v, hoursAgo, audience = 'all', extra = {}) => {
    s.videos[v.id] = { ...pub(v), status: 'ready', quality: 'compat', vcodec: 'avc1.64001f', width: v.is_short ? 360 : 640, height: v.is_short ? 640 : 360,
      file_size: v.is_short ? 6e6 : 2.1e8 + (hashNum(v.id) % 9) * 3e7, downloaded_at: t - hoursAgo * HOUR, keep: 0, audience, ...extra };
    s.downloads.push({ id: s.ids++, video_id: v.id, quality: 'compat', kind: v.is_short ? 'background' : 'user', status: 'done', progress: 100, created_at: t - hoursAgo * HOUR, finished_at: t - hoursAgo * HOUR });
  };
  const q = (v, scope, pid) => s.queue.push({ item_id: s.ids++, video_id: v.id, scope, profile_id: scope === 'personal' ? pid : null, position: s.queue.length + 1, added_by: pid, added_at: t });
  const ev = (pid, type, v, daysAgo, extra = {}) => s.events.push({ id: s.ids++, profile_id: pid, video_id: v ? v.id : null, type, weight: WEIGHTS[type],
    keywords: v ? v.keywords : extra.keywords, channel: v && type !== 'search' ? v.channel : '', data: v ? { title: v.title } : { query: extra.query }, ts: t - daysAgo * DAY });

  const sour = find(/Sourdough starter/), knife = find(/Knife skills/), table = find(/walnut coffee/), kyoto = find(/48 hours in Kyoto/);
  const yoga = find(/20 minute morning yoga/), webb = find(/James Webb/), server = find(/old mini PC/), ramen = find(/Weeknight ramen/);
  ready(sour, 3, 'p:1'); q(sour, 'personal', 1); s.watch[1][sour.id] = { position: 412, duration: sour.duration, watched: 0, rating: 0, updated_at: t - 2 * HOUR };
  ready(table, 6, 'p:1'); q(table, 'personal', 1);
  ready(kyoto, 2); q(kyoto, 'shared', 2); s.watch[2][kyoto.id] = { position: kyoto.duration, duration: kyoto.duration, watched: 1, rating: 1, updated_at: t - HOUR };
  ready(webb, 9); q(webb, 'shared', 1);
  ready(yoga, 4, 'p:2'); q(yoga, 'personal', 2);
  ready(server, 30, 'all', { keep: 1, quality: 'best', vcodec: 'vp09.00.40.08', width: 3840, height: 2160 });
  // a download in progress
  s.videos[ramen.id] = { ...pub(ramen), status: 'downloading', quality: 'compat', audience: 'p:1' };
  q(ramen, 'personal', 1);
  s.downloads.push({ id: s.ids++, video_id: ramen.id, quality: 'compat', kind: 'user', status: 'running', progress: 0, started_ms: Date.now(), done_ms: 0, total: 3.1e8, created_at: t });
  // shorts ready to swipe
  CATALOG.filter((v) => v.is_short).slice(0, 12).forEach((v, i) => ready(v, 1 + i * 0.5));

  // a few songs already saved
  SONGS.filter((x) => /Harbour Lights|Paper Moons|Late Train|Acoustic Mornings/.test(x.title)).forEach((x, i) => {
    s.videos[AU + x.id] = { ...pub(x), id: AU + x.id, media: 'audio', status: 'ready', quality: 'mp3', vcodec: 'none',
      file_size: 4.6e6 + i * 7e5, downloaded_at: t - (i + 1) * DAY, keep: 0, audience: 'all' };
  });

  // History: Alex likes baking/woodworking, the other profile travel/yoga.
  const C = (re) => CATALOG.find((v) => re.test(v.title));
  ev(1, 'finish', C(/Open crumb/), 2); ev(1, 'like', C(/Open crumb/), 2); ev(1, 'finish', C(/focaccia you will/), 5);
  ev(1, 'finish', C(/Hand tool woodworking/), 9); ev(1, 'like', C(/Dovetails by hand/), 12); ev(1, 'finish', C(/Smash burgers/), 20);
  ev(1, 'dislike', C(/Cat tries every toy/), 3); ev(1, 'skip', C(/Kitten's first week/), 4);
  ev(1, 'search', null, 1, { query: 'sourdough focaccia', keywords: ['sourdough', 'focaccia'] });
  ev(2, 'finish', C(/slowest train/), 1); ev(2, 'like', C(/slowest train/), 1); ev(2, 'finish', C(/Hokkaido/), 6);
  ev(2, 'finish', C(/Yoga for back pain/), 3); ev(2, 'like', C(/Hip opening/), 8); ev(2, 'finish', C(/Growing tomatoes/), 15);
  ev(2, 'dislike', C(/Rocket engines/), 6);
  ev(2, 'search', null, 2, { query: 'japan rail pass', keywords: ['japan', 'rail', 'pass'] });
  s.algo[1].boosts['kw:knife'] = 2;
  // A shared playlist and auto-captions for the downloaded videos.
  s.playlists.push({ id: s.ids++, name: 'Weekend projects', scope: 'shared', profile_id: null, created_at: t, updated_at: t,
    items: [table, server, webb].map((v, i) => ({ item_id: s.ids++, video_id: v.id, position: i + 1 })) });
  s.playlists.push({ id: s.ids++, name: 'Japan trip ideas', scope: 'personal', profile_id: 2, created_at: t, updated_at: t,
    items: [{ item_id: s.ids++, video_id: kyoto.id, position: 1 }] });
  s.watch[1][webb.id] = { position: 300, duration: webb.duration, watched: 0, rating: 0, updated_at: t - 4 * HOUR };
  for (const v of [sour, table, kyoto, webb, yoga, server]) s.subs.push({ id: s.ids++, video_id: v.id, lang: 'en', label: 'English (auto)', kind: 'auto', cues: fakeCues(v) });
  s.algo[2].mutes.push('ch:Homelab Hour');
  return s;
}

// ---------------------------------------------------------------- downloads
function advance() {
  let changed = false;
  for (const d of S.downloads) {
    if (d.status !== 'running') continue;
    const secs = 14; // simulated download time
    const elapsed = d.done_ms + (Date.now() - d.started_ms);
    d.progress = Math.min(100, (elapsed / (secs * 1000)) * 100);
    d.speed = d.total / secs; d.eta = Math.max(0, secs - elapsed / 1000);
    d.downloaded_bytes = d.total * d.progress / 100; d.total_bytes = d.total;
    d.phase = d.progress > 96 ? 'merging' : 'video';
    if (d.progress >= 100) {
      d.status = 'done'; d.finished_at = now(); d.speed = null; d.eta = 0;
      const v = S.videos[d.video_id];
      Object.assign(v, { status: 'ready', downloaded_at: now(), vcodec: d.quality === 'best' ? 'vp09.00.40.08' : 'avc1.64001f',
        width: v.is_short ? 360 : 640, height: v.is_short ? 640 : 360, file_size: d.total, ...(v.media === 'audio' ? { vcodec: 'none', width: null, height: null } : {}) });
    }
    changed = true;
  }
  // start the next queued one if nothing is running (one foreground slot)
  if (!S.downloads.some((d) => d.status === 'running')) {
    const next = S.downloads.find((d) => d.status === 'queued');
    if (next) { next.status = 'running'; next.started_ms = Date.now(); next.done_ms = next.done_ms || 0; S.videos[next.video_id].status = 'downloading'; changed = true; }
  }
  if (changed) save();
}
setInterval(advance, 1000);

function dlView(d) {
  const v = S.videos[d.video_id] || BY_ID[d.video_id];
  return { ...d, title: v.title, channel: v.channel, is_short: v.is_short, duration: v.duration, thumb: `thumbs/${ytid(d.video_id)}.svg` };
}

// ---------------------------------------------------------------- library helpers
const pids = () => S.profiles.map((p) => p.id);
function relevant(v) {
  if (v.audience === 'all' || S.queue.some((q) => q.video_id === v.id && q.scope === 'shared')) return pids();
  const owners = new Set(S.queue.filter((q) => q.video_id === v.id && q.scope === 'personal').map((q) => q.profile_id));
  if (v.audience.startsWith('p:')) owners.add(Number(v.audience.slice(2)));
  const out = [...owners].filter((p) => pids().includes(p)).sort();
  return out.length ? out : pids();
}
// Private downloads only show up for the people they are for.
const visible = (v, pid = P()) => relevant(v).includes(pid);
const onServer = (v) => ['ready', 'downloading', 'queued'].includes(v.status);
function fakeCues(v) {
  const words = `welcome back to ${v.channel} today we are looking at ${v.title.toLowerCase()} let's get started first a quick overview of what you will need then we go step by step if you enjoyed this one there is more on the channel thanks for watching`.split(' ');
  const out = [];
  for (let i = 0, t = 0; i < words.length; i += 6, t += 2.4) out.push({ start: t, end: t + 2.39, text: words.slice(i, i + 6).join(' ') });
  return out;
}
function parseSubs(text) {
  const cues = [];
  const ts = (h, m, s, ms) => (+(h || 0)) * 3600 + (+m) * 60 + (+s) + (+(ms.padEnd(3, '0'))) / 1000;
  for (const block of text.replace(/\r/g, '').split(/\n\n+/)) {
    const lines = block.split('\n');
    const i = lines.findIndex((l) => /-->/.test(l));
    if (i < 0) continue;
    const m = lines[i].match(/(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})/);
    if (!m) continue;
    const body = lines.slice(i + 1).map((l) => l.replace(/<[^>]*>/g, '').trim()).filter(Boolean).join('\n');
    if (body) cues.push({ start: ts(m[1], m[2], m[3], m[4]), end: ts(m[5], m[6], m[7], m[8]), text: body });
  }
  return cues;
}
const vttUrls = {};
function subView(x) {
  if (!vttUrls[x.id]) {
    const f = (t) => new Date(t * 1000).toISOString().slice(11, 23);
    const vtt = 'WEBVTT\n\n' + x.cues.map((c) => `${f(c.start)} --> ${f(c.end)}\n${c.text}\n`).join('\n');
    vttUrls[x.id] = URL.createObjectURL(new Blob([vtt], { type: 'text/vtt' }));
  }
  return { id: x.id, video_id: x.video_id, lang: x.lang, label: x.label, kind: x.kind, url: vttUrls[x.id] };
}
const inPlaylist = (id) => S.playlists.some((p) => p.items.some((i) => i.video_id === id));
function deleteStatus(id) {
  const rel = relevant(S.videos[id]);
  const votes = S.votes[id] || [];
  const st = {};
  for (const p of rel) st[p] = votes.includes(p) ? 'voted' : (S.watch[p][id] && S.watch[p][id].watched) ? 'watched' : 'pending';
  return { video_id: id, relevant: rel, state: st, pending: rel.filter((p) => st[p] === 'pending'), votes: votes.filter((p) => rel.includes(p)), solo: rel.length === 1 };
}
function playlistView(pl) {
  const items = [...pl.items].sort((a, b) => a.position - b.position).filter((i) => S.videos[i.video_id] && S.videos[i.video_id].status !== 'deleted');
  return { id: pl.id, name: pl.name, scope: pl.scope, profile_id: pl.profile_id, count: items.length,
    duration: items.reduce((a, i) => a + (S.videos[i.video_id].duration || 0), 0),
    thumbs: items.slice(0, 4).map((i) => `thumbs/${ytid(i.video_id)}.svg`),
    items: items.map((i) => ({ ...decorate(S.videos[i.video_id]), item_id: i.item_id, position: i.position })) };
}
const visiblePl = (id) => { const p = S.playlists.find((x) => x.id === Number(id)); if (!p || (p.scope === 'personal' && p.profile_id !== P())) throw Object.assign(new Error('That isn’t on the server any more.'), { status: 404 }); return p; };

function decorate(v) {
  const watch = {};
  for (const pid of pids()) if (S.watch[pid][v.id]) watch[pid] = S.watch[pid][v.id];
  const d = [...S.downloads].reverse().find((x) => x.video_id === v.id) || null;
  const needsVlc = !!v.vcodec && !/^(avc1|h264|hvc1|hev1|hevc)/i.test(v.vcodec);
  const audio = v.media === 'audio';
  const forWho = relevant(v);
  return { ...v, watch, download: d, needs_vlc: needsVlc, delete_votes: S.votes[v.id] || [], in_playlist: inPlaylist(v.id), relevant: v.status === 'ready' && !audio ? forWho : [], thumb: `thumbs/${ytid(v.id)}.svg`,
    for: forWho, shared: pids().every((p) => forWho.includes(p)),
    stream: v.status === 'ready' ? mediaFor(v) : null,
    file: audio && v.status === 'ready' ? mediaFor(v) : undefined, format: audio ? v.quality : undefined,
    expires_at: v.keep || inPlaylist(v.id) || !v.downloaded_at ? null : v.downloaded_at + S.settings.auto_delete_hours * HOUR };
}
function cleaner() {
  const t = now();
  for (const v of Object.values(S.videos)) {
    if (v.status !== 'ready' || v.keep || v.media === 'audio' || inPlaylist(v.id)) continue;
    const rel = relevant(v);
    const recent = pids().some((p) => S.watch[p][v.id] && t - S.watch[p][v.id].updated_at < 600);
    if (recent) continue;
    const all = rel.every((p) => (S.watch[p][v.id] && S.watch[p][v.id].watched) || (S.votes[v.id] || []).includes(p));
    if (all || t - v.downloaded_at > S.settings.auto_delete_hours * HOUR) removeVideo(v.id, all ? 'auto: watched by everyone' : 'auto: too old', null);
  }
}
function removeVideo(id, reason, by) {
  const v = S.videos[id];
  v.status = 'deleted'; v.deleted_at = now();
  S.queue = S.queue.filter((q) => q.video_id !== id);
  S.playlists.forEach((p) => { p.items = p.items.filter((i) => i.video_id !== id); });
  S.subs = S.subs.filter((x) => x.video_id !== id);
  delete S.votes[id];
  S.events.push({ id: S.ids++, profile_id: by, video_id: id, type: 'deleted', weight: 0, keywords: v.keywords, channel: v.channel, data: { reason, title: v.title }, ts: now() });
  if (by && v.media !== 'audio' && !(S.watch[by][id] && S.watch[by][id].watched)) addEvent(by, 'delete_unwatched', v);
}
function addEvent(pid, type, v, extra = {}) {
  S.events.push({ id: S.ids++, profile_id: pid, video_id: v ? v.id : null, type, weight: WEIGHTS[type] || 0, keywords: v ? v.keywords : extra.keywords,
    channel: v && type !== 'search' ? v.channel : '', data: v ? { title: v.title } : { query: extra.query }, ts: now() });
}
function queueAdd(id, scope, pid) {
  if (!S.queue.some((q) => q.video_id === id && q.scope === scope && (scope === 'shared' || q.profile_id === pid))) {
    const pos = Math.max(0, ...S.queue.filter((q) => q.scope === scope).map((q) => q.position)) + 1;
    S.queue.push({ item_id: S.ids++, video_id: id, scope, profile_id: scope === 'personal' ? pid : null, position: pos, added_by: pid, added_at: now() });
  }
  const v = S.videos[id];
  if (scope === 'shared' || (v.audience !== 'all' && v.audience !== `p:${pid}`)) v.audience = 'all';
}
function progress(pid, id, position, duration, ended) {
  const v = S.videos[id];
  const prev = S.watch[pid][id] || { position: 0, watched: 0, rating: 0 };
  const dur = duration || v.duration;
  const watched = prev.watched || ended || (dur && position >= 0.9 * dur) ? 1 : 0;
  S.watch[pid][id] = { ...prev, position, duration: dur, watched, updated_at: now() };
  if (watched && !prev.watched) {
    addEvent(pid, 'finish', v);
    S.queue = S.queue.filter((q) => !(q.video_id === id && q.scope === 'personal' && q.profile_id === pid));
  }
  return S.watch[pid][id];
}
function rate(pid, id, value, meta) {
  if (!S.videos[id]) S.videos[id] = { ...pub(meta || BY_ID[id]), status: 'new', audience: 'all' };
  const prev = S.watch[pid][id] || { position: 0, watched: 0, rating: 0, updated_at: now() };
  S.watch[pid][id] = { ...prev, rating: value };
  S.events = S.events.filter((e) => !(e.profile_id === pid && e.video_id === id && (e.type === 'like' || e.type === 'dislike')));
  if (value) addEvent(pid, value > 0 ? 'like' : 'dislike', S.videos[id]);
  return S.watch[pid][id];
}

// ---------------------------------------------------------------- recommender (port of recommender.py)
const algo = (pid) => ({ ...clone(DEFAULT_ALGO), ...S.algo[pid] });
function buildProfile(pid, s) {
  const t = now(), terms = {};
  const add = (key, w, type) => { const e = terms[key] || (terms[key] = { learned: 0, counts: {} }); e.learned += w; e.counts[type] = (e.counts[type] || 0) + 1; };
  for (const e of S.events) {
    if (e.profile_id !== pid || !e.weight) continue;
    const d = 0.5 ** ((t - e.ts) / DAY / s.half_life_days);
    const kws = e.keywords || [];
    for (const k of kws) add(`kw:${k}`, e.weight * d / Math.sqrt(kws.length), e.type);
    if (e.channel) add(`ch:${e.channel}`, e.weight * d, e.type);
  }
  for (const k of Object.keys(s.boosts)) if (!terms[k]) terms[k] = { learned: 0, counts: {} };
  const out = { kw: {}, ch: {} };
  for (const [key, e] of Object.entries(terms)) {
    if (s.forgotten.includes(key)) continue;
    const [kind, ...rest] = key.split(':');
    const boost = s.boosts[key] || 0;
    out[kind][rest.join(':')] = { weight: e.learned + boost, learned: e.learned, boost, counts: e.counts, muted: s.mutes.includes(key) };
  }
  return out;
}
function blend(profiles) {
  const out = { kw: {}, ch: {} };
  for (const kind of ['kw', 'ch']) {
    const names = new Set(profiles.flatMap((p) => Object.keys(p[kind])));
    for (const n of names) {
      const es = profiles.map((p) => p[kind][n] || { weight: 0, learned: 0, boost: 0, counts: {} });
      const counts = {};
      es.forEach((e) => Object.entries(e.counts).forEach(([k, c]) => { counts[k] = (counts[k] || 0) + c; }));
      out[kind][n] = { weight: es.reduce((a, e) => a + e.weight, 0) / es.length, learned: es.reduce((a, e) => a + e.learned, 0) / es.length,
        boost: es.reduce((a, e) => a + e.boost, 0) / es.length, counts };
    }
  }
  return out;
}
function blendSettings(ss) {
  const avg = (k) => ss.reduce((a, s) => a + s[k], 0) / ss.length;
  return { ...clone(DEFAULT_ALGO), half_life_days: avg('half_life_days'), exploration: avg('exploration'), channel_loyalty: avg('channel_loyalty'), mutes: [...new Set(ss.flatMap((s) => s.mutes))] };
}
const phrase = (counts) => Object.entries(counts).filter(([k]) => LABELS[k]).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, n]) => `${LABELS[k]} ${n}`).join(', ');
function jitter(id) { const day = new Date().toISOString().slice(0, 10); return (hashNum(day + id) % 10000) / 10000; }

function score(c, prof, s, who = 'you') {
  const kws = c.keywords || keywords(c.title);
  if (s.mutes.includes(`ch:${c.channel}`) || kws.some((k) => s.mutes.includes(`kw:${k}`))) return null;
  const contrib = kws.filter((k) => prof.kw[k] && prof.kw[k].weight).map((k) => [k, prof.kw[k].weight / Math.sqrt(kws.length), prof.kw[k]]);
  const topic = contrib.reduce((a, c2) => a + c2[1], 0);
  const ch = prof.ch[c.channel];
  const chW = ch ? ch.weight : 0;
  const familiar = (1 - s.channel_loyalty) * topic + s.channel_loyalty * chW;
  const known = kws.filter((k) => prof.kw[k] && Math.abs(prof.kw[k].weight) >= 0.05).length;
  let novelty = kws.length ? 1 - known / kws.length : 0;
  if (ch && Math.abs(chW) >= 0.05) novelty *= 0.5;
  const famPart = (1 - s.exploration) * Math.tanh(familiar / 3), novPart = s.exploration * novelty, jitPart = s.exploration * 0.3 * jitter(c.id);
  const reasons = [];
  for (const [k, w, e] of [...contrib].sort((a, b) => b[1] - a[1])) {
    if (w <= 0 || reasons.length >= 2) continue;
    if (e.boost > 0 && e.learned <= 0.05) reasons.push(`You boosted the topic “${k}”`);
    else { const p = phrase(e.counts); reasons.push(`Topic “${k}”${e.boost > 0 ? ' (boosted)' : ''}${p ? `: ${who} ${p}` : ''}`); }
  }
  if (ch && chW > 0.05 && s.channel_loyalty > 0) { const p = phrase(ch.counts); reasons.push(`From ${c.channel}${p ? `: ${who} ${p}` : ''}`); }
  const neg = contrib.filter((x) => x[1] < 0).sort((a, b) => a[1] - b[1])[0];
  if (neg) reasons.push(`Down-weighted: “${neg[0]}” (${phrase(neg[2].counts) || 'muted'})`);
  if (novPart > famPart && novPart > 0.05) reasons.unshift(`Exploration pick: mostly new topics for ${who === 'you' ? 'you' : 'you both'}`);
  if (!reasons.length) reasons.push('Starter topic');
  return { score: famPart + novPart + jitPart, reasons,
    breakdown: { topic, channel: chW, familiar, novelty, familiar_part: famPart, novelty_part: novPart, jitter_part: jitPart } };
}
function context(pid, mode) {
  if (mode === 'both') {
    const ss = pids().map(algo);
    return { prof: blend(pids().map((p, i) => buildProfile(p, ss[i]))), s: blendSettings(ss), who: 'you both', ids: pids() };
  }
  const s = algo(pid);
  return { prof: buildProfile(pid, s), s, who: 'you', ids: [pid] };
}
function rank(pid, mode, shorts = false) {
  const { prof, s, who, ids } = context(pid, mode);
  const excluded = new Set(Object.values(S.videos).filter((v) => ['ready', 'downloading', 'queued'].includes(v.status)).map((v) => v.id));
  for (const p of ids) for (const [id, w] of Object.entries(S.watch[p])) if (w.watched || w.rating < 0) excluded.add(id);
  for (const id of seenFor(pid, mode)) excluded.add(id);
  const out = [];
  for (const c of CATALOG) {
    if (excluded.has(c.id) || (shorts !== null && c.is_short !== shorts)) continue;
    const sc = score(c, prof, s, who);
    if (sc) out.push({ ...pub(c), ...sc, thumb: `thumbs/${c.id}.svg` });
  }
  return out.sort((a, b) => b.score - a.score).slice(0, 30);
}

// "Reload": what the feed shows is set aside, so the next batch is all new.
// (The demo catalog is small, so it starts over once it runs out.)
const bucket = (pid, mode) => (mode === 'both' ? 0 : pid);
function seenFor(pid, mode) { S.seen = S.seen || {}; return S.seen[bucket(pid, mode)] || []; }

// ---------------------------------------------------------------- the API
const P = () => { try { return Number(localStorage.getItem('hl.profile')) || 1; } catch { return 1; } };
function themeState(pid) {
  const t = S.themes[pid] || {};
  if (!t.mode) Object.assign(t, { mode: 'dark', dark: { preset: 'midnight', colors: {} }, light: { preset: 'daylight', colors: {} }, accent: {} });
  S.themes[pid] = t;
  return t;
}
function theme(pid) {
  const t = themeState(pid);
  const accent = S.profiles.find((p) => p.id === pid).accent;
  const pal = (scheme) => {
    const preset = t[scheme].preset;
    const { scheme: _s, ...base } = PRESETS[preset];
    base.accent = accent; base.progress = accent;
    const custom = { ...(t[scheme].colors || {}), ...t.accent };
    Object.assign(base, Object.fromEntries(Object.entries(custom).filter(([, v]) => v)));
    return { preset, colors: base, custom };
  };
  const dark = pal('dark'), light = pal('light');
  const active = t.mode === 'light' ? light : dark;
  return { mode: t.mode, preset: active.preset, colors: active.colors, custom: active.custom, dark, light };
}

const api = {
  async setAvatar(id, dataUrl) { const p = S.profiles.find((x) => x.id === id); p.avatar = dataUrl; save(); return delay(p, 150); },
  async removeAvatar(id) { const p = S.profiles.find((x) => x.id === id); p.avatar = null; save(); return delay(p); },
  async profiles() { return delay(S.profiles.map((p) => ({ ...p, theme: theme(p.id) })), 30); },
  async selectProfile(id) { const p = S.profiles.find((x) => x.id === id); return delay({ ...p, theme: theme(id) }, 30); },
  async renameProfile(id, name) { S.profiles.find((p) => p.id === id).name = name; save(); return delay(S.profiles.find((p) => p.id === id)); },
  async search(q) {
    const id = (q.match(/(?:v=|youtu\.be\/|\/shorts\/)([A-Za-z0-9_-]{11})/) || [])[1];
    let results;
    if (id) results = BY_ID[id] ? [BY_ID[id]] : [];
    else {
      const qk = new Set([...keywords(q), ...q.toLowerCase().split(/\s+/).filter((w) => w.length >= 3)]);
      results = CATALOG.map((v) => [v, v.keywords.filter((k) => qk.has(k)).length * 2 + [...qk].filter((w) => (v.title + ' ' + v.channel).toLowerCase().includes(w)).length])
        .sort((a, b) => b[1] - a[1] || a[0].id.localeCompare(b[0].id)).slice(0, 15).map((x) => x[0]);
      const kws = keywords(q);
      if (kws.length) { addEvent(P(), 'search', null, { query: q, keywords: kws }); save(); }
    }
    return delay({ results: results.map((v) => ({ ...pub(v), thumb: `thumbs/${v.id}.svg`, local_status: S.videos[v.id] && (!onServer(S.videos[v.id]) || visible(S.videos[v.id])) ? S.videos[v.id].status : null })) }, 350);
  },
  async download(video, quality, queue) {
    const pid = P();
    const meta = BY_ID[video.id] || video;
    const existing = S.videos[video.id];
    if (!existing || ['new', 'deleted', 'error'].includes(existing.status)) {
      S.videos[video.id] = { ...pub(meta), status: 'queued', quality, audience: queue === 'shared' ? 'all' : `p:${pid}`, keep: 0 };
      S.downloads.push({ id: S.ids++, video_id: video.id, quality, kind: 'user', status: 'queued', progress: 0, done_ms: 0, total: meta.is_short ? 6e6 : 2.4e8 + (hashNum(meta.id) % 9) * 3e7, created_at: now() });
    }
    else if (!visible(existing, pid)) existing.audience = 'all';  // both picked it
    if (queue === 'personal' || queue === 'shared') queueAdd(video.id, queue, pid);
    advance(); save();
    return delay(dlView([...S.downloads].reverse().find((d) => d.video_id === video.id)));
  },
  async downloads() { advance(); const order = { running: 0, queued: 1, paused: 2 }; return delay({ downloads: [...S.downloads].filter((d) => d.kind === 'background' || !S.videos[d.video_id] || visible(S.videos[d.video_id])).sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3) || b.id - a.id).map(dlView), counts: {} }, 40); },
  async dlControl(id, action) {
    const d = S.downloads.find((x) => x.id === id);
    if (action === 'pause' && d.status === 'running') { d.done_ms += Date.now() - d.started_ms; d.status = 'paused'; d.speed = null; d.eta = null; S.videos[d.video_id].status = 'queued'; }
    else if (action === 'pause' && d.status === 'queued') d.status = 'paused';
    else if (action === 'resume' && ['paused', 'error'].includes(d.status)) d.status = 'queued';
    else if (action === 'cancel' && d.status !== 'done') { d.status = 'cancelled'; d.speed = null; S.videos[d.video_id].status = 'new'; S.queue = S.queue.filter((q) => q.video_id !== d.video_id); }
    advance(); save();
    return delay(dlView(d));
  },
  async clearDownloads() { const n = S.downloads.length; S.downloads = S.downloads.filter((d) => !['done', 'cancelled', 'error'].includes(d.status)); save(); return delay({ cleared: n - S.downloads.length }); },
  async musicSearch(q) {
    const id = (q.match(/(?:v=|youtu\.be\/)([A-Za-z0-9_-]{11})/) || [])[1];
    const words = q.toLowerCase().split(/\s+/).filter((w) => w.length >= 2);
    let hits = id ? SONGS.filter((x) => x.id === id) : SONGS.map((x) => [x, words.filter((w) => `${x.title} ${x.channel}`.toLowerCase().includes(w)).length])
      .sort((a, b) => b[1] - a[1] || a[0].id.localeCompare(b[0].id)).filter((x, _, arr) => x[1] > 0 || !arr.some((y) => y[1] > 0)).map((x) => x[0]);
    hits = hits.slice(0, 15);
    return delay({ results: hits.map((x) => ({ ...pub(x), thumb: `thumbs/${x.id}.svg`, local_status: S.videos[AU + x.id] ? S.videos[AU + x.id].status : null })) }, 300);
  },
  async musicDownload(song, format) {
    const id = AU + ytid(song.id);
    const cur = S.videos[id];
    if (!cur || ['new', 'deleted', 'error'].includes(cur.status)) {
      const meta = BY_ID[ytid(song.id)] || song;
      S.videos[id] = { ...pub(meta), id, media: 'audio', status: 'queued', quality: format || S.settings.music_format, audience: 'all', keep: 0 };
      S.downloads.push({ id: S.ids++, video_id: id, quality: format || S.settings.music_format, kind: 'user', status: 'queued', progress: 0, done_ms: 0, total: 5.2e6, created_at: now() });
    }
    advance(); save();
    return delay(dlView([...S.downloads].reverse().find((d) => d.video_id === id)));
  },
  async music() {
    advance();
    const tracks = Object.values(S.videos).filter((v) => v.media === 'audio' && ['ready', 'queued', 'downloading', 'error'].includes(v.status))
      .sort((a, b) => (b.downloaded_at || b.created_at || now()) - (a.downloaded_at || a.created_at || now())).map(decorate);
    return delay({ tracks }, 60);
  },
  async library() { advance(); cleaner(); save(); return delay({ videos: Object.values(S.videos).filter((v) => v.status === 'ready' && !v.is_short && v.media !== 'audio' && visible(v)).sort((a, b) => b.downloaded_at - a.downloaded_at).map(decorate), auto_delete_hours: S.settings.auto_delete_hours }); },
  async video(id) { return delay(decorate(S.videos[id] || { ...pub(BY_ID[id]), status: 'new' })); },
  async deleteVideo(id) { removeVideo(id, 'manual', P()); save(); return delay({ id, reason: 'manual' }); },
  async shareVideo(id) { const v = S.videos[id]; if (!v || !visible(v)) return fail('That isn’t on the server any more.', 404); queueAdd(id, 'shared', P()); v.audience = 'all'; save(); return delay(decorate(v)); },
  async keep(id, keep) { S.videos[id].keep = keep ? 1 : 0; save(); return delay(decorate(S.videos[id])); },
  async rate(id, value) { const r = rate(P(), id, value); save(); return delay(r); },
  async rateCandidate(video, value) { rate(P(), video.id, value, video); save(); return delay({ ok: true }); },
  async queue(scope) {
    advance();
    const pid = P();
    const items = S.queue.filter((q) => q.scope === scope && (scope === 'shared' || q.profile_id === pid)).sort((a, b) => a.position - b.position)
      .filter((q) => S.videos[q.video_id]).map((q) => ({ ...decorate(S.videos[q.video_id]), item_id: q.item_id, position: q.position }));
    return delay({ scope, items }, 60);
  },
  async queueAdd(id, scope) { queueAdd(id, scope, P()); save(); return delay({ ok: true }); },
  async queueMove(itemId, dir) {
    const it = S.queue.find((q) => q.item_id === itemId);
    const list = S.queue.filter((q) => q.scope === it.scope && q.profile_id === it.profile_id).sort((a, b) => a.position - b.position);
    const i = list.indexOf(it), j = Math.max(0, Math.min(list.length - 1, i + dir));
    [list[i], list[j]] = [list[j], list[i]];
    list.forEach((q, k) => { q.position = k + 1; });
    save(); return delay({ ok: true }, 30);
  },
  async queueRemove(itemId) { S.queue = S.queue.filter((q) => q.item_id !== itemId); save(); return delay({ ok: true }, 30); },
  async watch(id) { return delay(S.watch[P()][id] || { position: 0, watched: 0, rating: 0, speed: null }, 20); },
  async continueWatching() {
    const pid = P();
    const items = Object.entries(S.watch[pid]).filter(([id, w]) => { const v = S.videos[id]; return v && v.status === 'ready' && !v.is_short && v.media !== 'audio' && !w.watched && w.position >= 5 && (!v.duration || w.position < v.duration - 5); })
      .sort((a, b) => b[1].updated_at - a[1].updated_at).map(([id]) => decorate(S.videos[id]));
    return delay({ items }, 60);
  },
  async setSpeed(id, speed) { const pid = P(); const w = S.watch[pid][id] || { position: 0, watched: 0, rating: 0, updated_at: now() }; S.watch[pid][id] = { ...w, speed: speed === 1 ? null : speed }; save(); return delay(S.watch[pid][id], 20); },
  async setWatched(id, watched) {
    const pid = P();
    if (watched) { const v = S.videos[id]; const r = progress(pid, id, v.duration || 0, v.duration, true); save(); return delay(r); }
    const w = S.watch[pid][id];
    if (w) { w.watched = 0; w.position = 0; w.updated_at = now(); }
    if (S.votes[id]) S.votes[id] = S.votes[id].filter((p) => p !== pid);
    save(); return delay(w || { position: 0, watched: 0 });
  },
  async playlists() { const pid = P(); return delay({ playlists: S.playlists.filter((p) => p.scope === 'shared' || p.profile_id === pid).sort((a, b) => (a.scope === b.scope ? b.updated_at - a.updated_at : a.scope === 'shared' ? -1 : 1)).map(playlistView) }, 80); },
  async playlist(id) { try { return delay(playlistView(visiblePl(id)), 60); } catch (e) { return Promise.reject(e); } },
  async createPlaylist(name, scope) { const pl = { id: S.ids++, name: name.trim().slice(0, 60), scope, profile_id: scope === 'personal' ? P() : null, created_at: now(), updated_at: now(), items: [] }; S.playlists.push(pl); save(); return delay(playlistView(pl)); },
  async sharePlaylist(id) {
    const pl = visiblePl(id);
    if (pl.scope === 'personal') { pl.scope = 'shared'; pl.profile_id = null; pl.updated_at = now(); pl.items.forEach((i) => { if (S.videos[i.video_id]) S.videos[i.video_id].audience = 'all'; }); }
    save(); return delay(playlistView(pl));
  },
  async renamePlaylist(id, name) { const pl = visiblePl(id); pl.name = name.trim().slice(0, 60); pl.updated_at = now(); save(); return delay(playlistView(pl)); },
  async deletePlaylist(id) { visiblePl(id); S.playlists = S.playlists.filter((p) => p.id !== Number(id)); save(); return delay({ ok: true }); },
  async playlistAdd(id, videoId) {
    const pl = visiblePl(id);
    if (!S.videos[videoId] || !visible(S.videos[videoId])) return fail('That isn’t on the server any more.', 404);
    if (!pl.items.some((i) => i.video_id === videoId)) pl.items.push({ item_id: S.ids++, video_id: videoId, position: Math.max(0, ...pl.items.map((i) => i.position)) + 1 });
    pl.updated_at = now();
    if (pl.scope === 'shared') S.videos[videoId].audience = 'all';
    save(); return delay(playlistView(pl));
  },
  async playlistRemove(id, itemId) { const pl = visiblePl(id); pl.items = pl.items.filter((i) => i.item_id !== itemId); save(); return delay(playlistView(pl)); },
  async playlistReorder(id, itemIds) {
    const pl = visiblePl(id);
    const cur = [...pl.items].sort((a, b) => a.position - b.position).map((i) => i.item_id);
    const order = [...itemIds.filter((i) => cur.includes(i)), ...cur.filter((i) => !itemIds.includes(i))];
    order.forEach((iid, k) => { pl.items.find((i) => i.item_id === iid).position = k + 1; });
    save(); return delay(playlistView(pl));
  },
  async videoPlaylists(videoId) { const pid = P(); return delay({ playlist_ids: S.playlists.filter((p) => (p.scope === 'shared' || p.profile_id === pid) && p.items.some((i) => i.video_id === videoId)).map((p) => p.id) }, 30); },
  async deleteStatus(id) { return delay(deleteStatus(id), 40); },
  async voteDelete(id) {
    const pid = P();
    const st0 = deleteStatus(id);
    if (!st0.relevant.includes(pid)) return fail(`This one is ${st0.relevant.map((p) => S.profiles.find((x) => x.id === p).name).join(', ')}'s video, so it's theirs to delete.`);
    S.votes[id] = [...new Set([...(S.votes[id] || []), pid])];
    const st = deleteStatus(id);
    if (!st.pending.length) { removeVideo(id, st.solo ? 'deleted after watching' : 'both done with it', pid); save(); return delay({ ...st, deleted: true }); }
    save(); return delay({ ...st, deleted: false });
  },
  async unvoteDelete(id) { const pid = P(); S.votes[id] = (S.votes[id] || []).filter((p) => p !== pid); save(); return delay({ ...deleteStatus(id), deleted: false }); },
  async subtitles(id) { return delay({ subtitles: S.subs.filter((x) => x.video_id === id).sort((a, b) => (a.kind === 'uploaded' ? -1 : 0) - (b.kind === 'uploaded' ? -1 : 0)).map(subView) }, 30); },
  async uploadSubtitle(id, body) {
    const cues = parseSubs(body.content);
    if (!cues.length) return fail('That file doesn\'t contain any subtitles we could read (SRT or VTT).');
    const stem = (body.filename || '').replace(/\.[^.]+$/, '');
    const names = { en: 'English', es: 'Spanish', de: 'German', fr: 'French', it: 'Italian', pt: 'Portuguese', ja: 'Japanese' };
    const x = { id: S.ids++, video_id: id, lang: body.lang || 'en', label: body.label || (stem.length > 3 ? stem : names[body.lang] || (body.lang || 'und').toUpperCase()), kind: 'uploaded', cues };
    S.subs.push(x); save(); return delay(subView(x));
  },
  async fetchSubtitles(id) {
    S.subs = S.subs.filter((x) => !(x.video_id === id && x.kind === 'auto'));
    const v = S.videos[id];
    S.subs.push({ id: S.ids++, video_id: id, lang: 'en', label: 'English (auto)', kind: 'auto', cues: fakeCues(v) });
    save(); return delay({ added: 1, subtitles: S.subs.filter((x) => x.video_id === id).map(subView) }, 700);
  },
  async deleteSubtitle(sid) { S.subs = S.subs.filter((x) => x.id !== sid); save(); return delay({ ok: true }); },
  async transcript(sid) { const x = S.subs.find((y) => y.id === sid); return x ? delay({ ...subView(x), cues: x.cues }) : fail('That isn’t on the server any more.', 404); },
  async progress(id, body) {
    const pid = P();
    if (body.skipped) { addEvent(pid, 'skip', S.videos[id]); save(); return delay({}); }
    const r = progress(pid, id, body.position, body.duration, body.ended); save(); return delay(r, 20);
  },
  async foryou(mode) {
    const s = algo(P());
    const items = rank(P(), mode, s.include_shorts_in_feed ? null : false);
    S.shown = S.shown || {};
    S.shown[bucket(P(), mode)] = items.map((x) => x.id); save();
    return delay({ mode, items, last_fetch: S.last_fetch, fetching: false }, 250);
  },
  async why(id, mode) {
    const { prof, s, who } = context(P(), mode);
    const v = BY_ID[id] || S.videos[id];
    const r = score(v, prof, s, who);
    const terms = v.keywords.map((k) => ({ kind: 'kw', term: k, ...(prof.kw[k] || { weight: 0, counts: {} }) }));
    terms.push({ kind: 'ch', term: v.channel, ...(prof.ch[v.channel] || { weight: 0, counts: {} }) });
    return delay({ video: pub(v), result: r, terms, settings: s, source: '' });
  },
  async refreshForyou(mode = 'me') {
    const b = bucket(P(), mode);
    S.seen = S.seen || {}; S.shown = S.shown || {};
    S.seen[b] = [...new Set([...(S.seen[b] || []), ...(S.shown[b] || [])])];
    S.shown[b] = [];
    if (rank(P(), mode).length < 6) S.seen[b] = [];  // demo catalog ran out: start over
    S.last_fetch = now(); save();
    return delay({ fetched: 40, suggestions: rank(P(), mode).length }, 900);
  },
  async shorts() {
    const pid = P(), { prof, s } = context(pid, 'me');
    const items = Object.values(S.videos).filter((v) => v.is_short && v.media !== 'audio' && v.status === 'ready' && !(S.watch[pid][v.id] && S.watch[pid][v.id].watched))
      .map((v) => { const sc = score(v, prof, s); return sc ? { ...decorate(v), score: sc.score, reasons: sc.reasons } : null; }).filter(Boolean)
      .sort((a, b) => b.score - a.score);
    return delay({ items, downloading: 0, target: 10 }, 80);
  },
  async shortSeen(id, seconds, duration) {
    const pid = P();
    if (seconds < 2) { addEvent(pid, 'skip', S.videos[id]); const w = S.watch[pid][id] || { rating: 0 }; S.watch[pid][id] = { ...w, position: 0, watched: 1, updated_at: now() }; }
    else progress(pid, id, seconds, duration, true);
    save(); return delay({ ok: true }, 20);
  },
  async prefetchShorts() {
    const fresh = CATALOG.filter((v) => v.is_short && !S.videos[v.id]).slice(0, 4);
    fresh.forEach((v) => { S.videos[v.id] = { ...pub(v), status: 'ready', quality: 'compat', vcodec: 'avc1', width: 360, height: 640, file_size: 6e6, downloaded_at: now(), keep: 0, audience: 'all' }; });
    save(); return delay({ queued: fresh.length });
  },
  async algorithm() {
    const pid = P(), s = algo(pid), prof = buildProfile(pid, s);
    const list = (d) => Object.entries(d).map(([term, e]) => ({ term, ...e })).sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
    const events = S.events.filter((e) => e.profile_id === pid && e.type !== 'deleted').sort((a, b) => b.ts - a.ts);
    return delay({ settings: s, keywords: list(prof.kw), channels: list(prof.ch), events, event_weights: WEIGHTS, embeddings: { enabled: false } });
  },
  async setAlgorithm(patch) { S.algo[P()] = { ...algo(P()), ...patch }; save(); return delay(S.algo[P()]); },
  async boost(kind, term, amount) {
    const s = algo(P()); const key = `${kind}:${kind === 'kw' ? term.toLowerCase() : term}`;
    if (amount) { s.boosts[key] = amount; s.mutes = s.mutes.filter((m) => m !== key); } else delete s.boosts[key];
    S.algo[P()] = s; save(); return delay(s);
  },
  async mute(kind, term, muted = true) {
    const s = algo(P()); const key = `${kind}:${kind === 'kw' ? term.toLowerCase() : term}`;
    s.mutes = s.mutes.filter((m) => m !== key).concat(muted ? [key] : []); if (muted) delete s.boosts[key];
    S.algo[P()] = s; save(); return delay(s);
  },
  async forget(kind, term, forgotten = true) {
    const s = algo(P()); const key = `${kind}:${kind === 'kw' ? term.toLowerCase() : term}`;
    s.forgotten = s.forgotten.filter((m) => m !== key).concat(forgotten ? [key] : []);
    S.algo[P()] = s; save(); return delay(s);
  },
  async deleteEvent(id) { S.events = S.events.filter((e) => e.id !== id); save(); return delay({ ok: true }); },
  async exportAlgorithm() {
    const a = await api.algorithm();
    return { format: 'homelib-algorithm-v1', exported_at: now(), profile_id: P(), settings: a.settings, event_weights: WEIGHTS,
      keywords: Object.fromEntries(a.keywords.map((k) => [k.term, k])), channels: Object.fromEntries(a.channels.map((k) => [k.term, k])), events: a.events };
  },
  async resetAlgorithm() { const pid = P(); S.events = S.events.filter((e) => e.profile_id !== pid || e.type === 'deleted'); S.algo[pid] = clone(DEFAULT_ALGO); save(); return delay({ ok: true }); },
  async appearance() { return delay({ ...theme(P()), presets: PRESETS }); },
  async setAppearance(body) {
    const pid = P(); const cur = themeState(pid);
    if (body.mode) cur.mode = body.mode;
    if (body.preset) {
      const sc = PRESETS[body.preset].scheme;
      cur[sc] = { preset: body.preset, colors: {} };
      cur.accent = cur.accent.accent ? { accent: cur.accent.accent } : {};
      if (cur.mode !== 'auto' && !body.mode) cur.mode = sc;
    }
    const target = body.scheme || (cur.mode === 'light' ? 'light' : 'dark');
    for (const [k, v] of Object.entries(body.colors || {})) {
      const bucket = SHARED_KEYS.includes(k) ? cur.accent : cur[target].colors;
      if (v === null) delete bucket[k]; else bucket[k] = v.toUpperCase();
    }
    if (cur.accent.accent) S.profiles.find((p) => p.id === pid).accent = cur.accent.accent;
    save(); return delay(theme(pid), 40);
  },
  async settings() { return delay({ music_format: 'mp3', ...S.settings, min_free_gb: 10, shorts_prefetch: 10 }, 30); },
  async setSettings(body) { Object.assign(S.settings, body); save(); return delay(S.settings); },
  async status() {
    const lib = Object.values(S.videos).filter((v) => v.status === 'ready').reduce((a, v) => a + (v.file_size || 0), 0);
    const total = 476 * 1024 ** 3, free = 401 * 1024 ** 3 - lib;
    return delay({ ok: true, version: '1.3.0 (preview)', mock: true, via_tailscale: true, disk: { total, free, used: total - free, library_bytes: lib, min_free: 10 * 1024 ** 3, ok: true },
      downloads: { running: S.downloads.filter((d) => d.status === 'running').length }, ffmpeg: { found: true, source: 'system' }, js_runtime: 'deno', yt_dlp: '2026.10.26 (mock)', python: '3.12', last_candidate_fetch: S.last_fetch }, 40);
  },
  resetDemo() { localStorage.removeItem(KEY); S = seed(); save(); },
};

window.HOMELIB_MOCK_API = api;
