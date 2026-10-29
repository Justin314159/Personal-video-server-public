// API client. In the static preview, window.HOMELIB_MOCK_API (js/mockapi.js)
// replaces the network with an in-browser fake backend.

const PROFILE_KEY = 'hl.profile';

export function currentProfileId() {
  try { return Number(localStorage.getItem(PROFILE_KEY)) || 0; } catch { return 0; }
}

export function setCurrentProfileId(id) {
  try { localStorage.setItem(PROFILE_KEY, String(id)); } catch { /* private mode */ }
}

// Short, human messages instead of "HTTP 500" or validation dumps. The
// server's own `detail` text is already written for people, so it wins when
// it's a plain sentence.
export function friendlyError(status, detail) {
  if (typeof detail === 'string' && detail && !/^not found: /.test(detail)) return detail;
  if (status === 404) return 'That isn’t on the server any more.';
  if (status === 422 || status === 400) return 'That didn’t look right. Please try again.';
  if (status === 507) return 'The server is out of space.';
  if (status >= 500) return 'The server hit a snag. Try again in a moment.';
  return 'Something went wrong. Please try again.';
}

async function call(method, path, body) {
  const opts = { method, headers: { 'X-Profile': String(currentProfileId() || 1) } };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  let r;
  try {
    r = await fetch(path, opts);
  } catch {
    const err = new Error('Can’t reach the home server. Is Tailscale on?');
    err.status = 0; err.code = 'offline';
    throw err;
  }
  const isJson = (r.headers.get('content-type') || '').includes('json');
  const data = isJson ? await r.json().catch(() => null) : await r.text();
  if (!r.ok) {
    const err = new Error(friendlyError(r.status, data && data.detail));
    err.status = r.status;
    err.code = data && data.code;
    throw err;
  }
  return data;
}

const enc = encodeURIComponent;

const http = {
  profiles: () => call('GET', '/api/profiles'),
  selectProfile: (id) => call('POST', `/api/profiles/${id}/select`),
  renameProfile: (id, name) => call('PATCH', `/api/profiles/${id}`, { name }),
  search: (q) => call('GET', `/api/search?q=${enc(q)}`),
  download: (video, quality, queue) => call('POST', '/api/downloads', { ...pick(video), quality, queue }),
  downloads: () => call('GET', '/api/downloads'),
  dlControl: (id, action) => call('POST', `/api/downloads/${id}/${action}`),
  clearDownloads: () => call('POST', '/api/downloads/clear'),
  library: () => call('GET', '/api/library'),
  video: (id) => call('GET', `/api/videos/${enc(id)}`),
  deleteVideo: (id) => call('DELETE', `/api/videos/${enc(id)}`),
  keep: (id, keep) => call('POST', `/api/videos/${enc(id)}/keep`, { keep }),
  rate: (id, value) => call('POST', `/api/videos/${enc(id)}/rate`, { value }),
  rateCandidate: (video, value) => call('POST', '/api/candidates/rate', { video: pick(video, true), value }),
  queue: (scope) => call('GET', `/api/queue?scope=${scope}`),
  queueAdd: (videoId, scope) => call('POST', '/api/queue', { video_id: videoId, scope }),
  queueMove: (itemId, direction) => call('POST', `/api/queue/${itemId}/move`, { direction }),
  queueRemove: (itemId) => call('DELETE', `/api/queue/${itemId}`),
  watch: (id) => call('GET', `/api/watch/${enc(id)}`),
  progress: (id, body) => call('POST', `/api/watch/${enc(id)}`, body),
  foryou: (mode) => call('GET', `/api/foryou?mode=${mode}`),
  why: (id, mode) => call('GET', `/api/foryou/why/${enc(id)}?mode=${mode}`),
  refreshForyou: () => call('POST', '/api/foryou/refresh'),
  shorts: () => call('GET', '/api/shorts'),
  shortSeen: (id, seconds, duration) => call('POST', `/api/shorts/${enc(id)}/seen`, { seconds, duration }),
  prefetchShorts: () => call('POST', '/api/shorts/prefetch'),
  algorithm: () => call('GET', '/api/algorithm'),
  setAlgorithm: (patch) => call('PUT', '/api/algorithm', patch),
  boost: (kind, term, amount) => call('POST', '/api/algorithm/boost', { kind, term, amount }),
  mute: (kind, term, muted = true) => call('POST', '/api/algorithm/mute', { kind, term, muted }),
  forget: (kind, term, forgotten = true) => call('POST', '/api/algorithm/forget', { kind, term, forgotten }),
  deleteEvent: (id) => call('DELETE', `/api/algorithm/events/${id}`),
  exportAlgorithm: () => call('GET', '/api/algorithm/export'),
  resetAlgorithm: () => call('POST', '/api/algorithm/reset'),
  musicSearch: (q) => call('GET', `/api/music/search?q=${enc(q)}`),
  musicDownload: (song, format) => call('POST', '/api/music/download', { ...pick(song), format }),
  music: () => call('GET', '/api/music'),
  appearance: () => call('GET', '/api/appearance'),
  setAppearance: (body) => call('PUT', '/api/appearance', body),
  settings: () => call('GET', '/api/settings'),
  setSettings: (body) => call('PUT', '/api/settings', body),
  status: () => call('GET', '/api/status'),
  continueWatching: () => call('GET', '/api/continue'),
  setSpeed: (id, speed) => call('PUT', `/api/watch/${enc(id)}/speed`, { speed }),
  setWatched: (id, watched) => call('POST', `/api/watch/${enc(id)}/watched`, { watched }),
  playlists: () => call('GET', '/api/playlists'),
  playlist: (id) => call('GET', `/api/playlists/${id}`),
  createPlaylist: (name, scope) => call('POST', '/api/playlists', { name, scope }),
  renamePlaylist: (id, name) => call('PATCH', `/api/playlists/${id}`, { name }),
  deletePlaylist: (id) => call('DELETE', `/api/playlists/${id}`),
  playlistAdd: (id, videoId) => call('POST', `/api/playlists/${id}/items`, { video_id: videoId }),
  playlistRemove: (id, itemId) => call('DELETE', `/api/playlists/${id}/items/${itemId}`),
  playlistReorder: (id, itemIds) => call('POST', `/api/playlists/${id}/reorder`, { item_ids: itemIds }),
  videoPlaylists: (videoId) => call('GET', `/api/videos/${enc(videoId)}/playlists`),
  deleteStatus: (id) => call('GET', `/api/videos/${enc(id)}/delete-status`),
  voteDelete: (id) => call('POST', `/api/videos/${enc(id)}/delete-vote`),
  unvoteDelete: (id) => call('DELETE', `/api/videos/${enc(id)}/delete-vote`),
  subtitles: (id) => call('GET', `/api/videos/${enc(id)}/subtitles`),
  uploadSubtitle: (id, body) => call('POST', `/api/videos/${enc(id)}/subtitles`, body),
  fetchSubtitles: (id) => call('POST', `/api/videos/${enc(id)}/subtitles/fetch`),
  deleteSubtitle: (sid) => call('DELETE', `/api/subtitles/${sid}`),
  transcript: (sid) => call('GET', `/api/subtitles/${sid}/transcript`),
};

// Only send the metadata fields the server accepts.
function pick(v, keepKeywords = false) {
  const out = {};
  for (const k of ['id', 'url', 'title', 'channel', 'channel_id', 'duration', 'thumb_url', 'is_short']) {
    if (v[k] !== undefined && v[k] !== null) out[k] = v[k];
  }
  if (keepKeywords && v.keywords) out.keywords = v.keywords;
  return out;
}

export const api = window.HOMELIB_MOCK_API || http;
export const isPreview = !!window.HOMELIB_MOCK_API;
