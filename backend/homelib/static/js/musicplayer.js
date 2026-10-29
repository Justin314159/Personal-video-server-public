// One <audio> element for the whole app, so music keeps playing while you
// switch tabs. A mini player sits above the tab bar; the lock screen gets
// title/artist/artwork and prev/next through the Media Session API.
import { h, icon } from './ui.js';

const audio = new Audio();
audio.preload = 'metadata';
let tracks = [];
let index = -1;
let bar = null;
const listeners = new Set();

export function onTrackChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
export function currentTrack() { return index >= 0 ? tracks[index] : null; }
export function isPlaying() { return !audio.paused; }

export function playTracks(list, start = 0) {
  tracks = list.filter((t) => t.stream);
  const want = list[start];
  index = Math.max(0, tracks.findIndex((t) => want && t.id === want.id));
  load(true);
}

export function pauseMusic() { audio.pause(); }

function setMetadata(t) {
  if (!('mediaSession' in navigator)) return;
  navigator.mediaSession.metadata = t ? new MediaMetadata({
    title: t.title, artist: t.channel || '', album: 'Home Video Library',
    artwork: [{ src: new URL(t.thumb, location.href).href, sizes: '320x320' }],
  }) : null;
}

function load(autoplay) {
  const t = tracks[index];
  if (!t) return;
  audio.src = t.stream;
  if (autoplay) audio.play().catch(() => {});
  setMetadata(t);
  render();
  listeners.forEach((fn) => fn(t));
}

function step(d) {
  if (!tracks.length) return;
  index = (index + d + tracks.length) % tracks.length;
  load(true);
}

audio.addEventListener('ended', () => { if (index < tracks.length - 1) step(1); else render(); });
audio.addEventListener('play', render);
audio.addEventListener('pause', render);
audio.addEventListener('timeupdate', () => {
  const fill = bar && bar.querySelector('.mini-fill');
  if (fill && audio.duration) fill.style.width = `${(audio.currentTime / audio.duration) * 100}%`;
});
// The video player borrows the lock-screen controls while it's open and hands
// them back with claimMediaSession() when it closes.
export function claimMediaSession() {
  if (!('mediaSession' in navigator)) return;
  const ms = navigator.mediaSession;
  const set = (action, fn) => { try { ms.setActionHandler(action, fn); } catch { /* unsupported action */ } };
  set('previoustrack', () => step(-1));
  set('nexttrack', () => step(1));
  set('play', () => audio.play());
  set('pause', () => audio.pause());
  for (const a of ['seekbackward', 'seekforward', 'seekto']) set(a, null);
  setMetadata(currentTrack());
}
claimMediaSession();

function render() {
  const t = tracks[index];
  if (!t) { if (bar) bar.remove(); bar = null; document.body.classList.remove('has-mini'); return; }
  if (!bar) { bar = h('div.mini', { role: 'region', 'aria-label': 'Now playing' }); document.body.append(bar); }
  document.body.classList.add('has-mini');
  bar.replaceChildren(
    h('div.mini-progress', h('div.mini-fill')),
    h('img', { src: t.thumb, alt: '' }),
    h('div.grow', h('div.ellipsis', { style: { fontWeight: 600 } }, t.title), h('div.ellipsis.muted.small', t.channel || '')),
    h('button.icon-btn', { 'aria-label': 'Previous', onclick: () => step(-1) }, icon('prev')),
    h('button.icon-btn', { 'aria-label': audio.paused ? 'Play' : 'Pause', onclick: () => (audio.paused ? audio.play() : audio.pause()) }, icon(audio.paused ? 'play' : 'pause')),
    h('button.icon-btn', { 'aria-label': 'Next', onclick: () => step(1) }, icon('next')),
    h('button.icon-btn', { 'aria-label': 'Close player', onclick: () => { audio.pause(); audio.removeAttribute('src'); tracks = []; index = -1; render(); } }, icon('x')),
  );
}
