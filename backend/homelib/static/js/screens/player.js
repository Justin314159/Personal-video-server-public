// Video player with our own controls:
//   * drag-and-release scrubbing with a time bubble
//   * YouTube-style double-tap skip (keep tapping to add 10 s each time)
//   * speed (remembered per video and person), captions, transcript
//   * audio-only mode, picture-in-picture, lock-screen controls (Media Session)
//   * keeps playing with the screen locked: when Safari pauses the <video>, the
//     same stream carries on through an <audio> element (see docs/IOS_PLAYBACK.md)
//   * end card: delete after watching + up next / playlist autoplay
// Playlist videos load in place (same <video> element), so the next one can
// start even while the phone is locked.
import { api } from '../api.js';
import { h, put, icon, thumbEl, fmtDuration, toast, sheet, emptyState, avatar, pop, watchInfo, skeleton, errorState } from '../ui.js';
import { screen, nav } from '../layout.js';
import { state } from '../state.js';
import { navigate, back } from '../router.js';
import { pauseMusic, claimMediaSession } from '../musicplayer.js';
import { addToPlaylistSheet, deleteSection, deleteSheet } from './common.js';

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 1.75, 2];
const SKIP = 10;
const TAP_WINDOW = 300;   // ms between taps that make a double-tap
const SEEK_WINDOW = 800;  // ms after the last skip tap during which single taps keep adding
const HIDE_AFTER = 3000;

let teardown = null;

const prefKey = (k) => `hl.${k}.${state.profile.id}`;
const getPref = (k, d) => { try { const v = localStorage.getItem(prefKey(k)); return v === null ? d : JSON.parse(v); } catch { return d; } };
const setPref = (k, v) => { try { localStorage.setItem(prefKey(k), JSON.stringify(v)); } catch { /* private mode */ } };
const fmtSpeed = (r) => `${r}×`;

export const playerScreen = {
  async render(root, arg) {
    nav(null);
    const [firstId, , listArg] = (arg || '').split('/');
    const listId = listArg ? Number(listArg) : null;
    const holder = h('div', skeleton('grid', 1));
    root.append(holder);
    let v;
    try { v = await api.video(firstId); } catch (e) { put(holder, screen(errorState(e, () => location.reload()))); return; }
    if (v.status !== 'ready') {
      put(holder, screen(emptyState('film', 'Not available', v.status === 'deleted' ? 'This video was deleted from the server.' : 'Still downloading. Check the Downloads tab.'),
        h('button.btn.block', { onclick: () => back('#/library') }, 'Back')));
      return;
    }
    holder.remove();
    teardown = buildPlayer(root, v, listId);
  },
  unmount() { if (teardown) teardown(); teardown = null; },
};

function buildPlayer(root, first, listId) {
  const me = state.profile.id;
  const off = [];  // cleanup callbacks
  const on = (target, ev, fn, opts) => { target.addEventListener(ev, fn, opts); off.push(() => target.removeEventListener(ev, fn, opts)); };

  // ---- media elements ------------------------------------------------------------
  const video = h('video', { playsinline: true, 'webkit-playsinline': true, preload: 'metadata', 'x-webkit-airplay': 'allow', autopictureinpicture: true });
  const audio = new Audio();
  audio.preload = 'none';
  let media = video;                // the element currently producing sound
  let audioOnly = getPref('audioOnly', false);
  let handedOff = false;            // true while the <audio> element covers for a locked screen
  let wantPlaying = false;          // what the person asked for (system pauses don't change it)
  let audioPrimed = false;

  let v = first, ws = null, subs = [], playlist = null, upNext = [];
  let lastSent = 0, played = false, maxTime = 0, resumedFrom = 0, sentEnded = false;

  // ---- DOM -------------------------------------------------------------------------
  const art = h('div.audio-art', { 'aria-hidden': 'true' });
  const gesture = h('div.gesture');
  const skipL = h('div.skip-ind.left', { 'aria-hidden': 'true' }, h('div.chev', icon('prev'), icon('prev'), icon('prev')), h('span'));
  const skipR = h('div.skip-ind.right', { 'aria-hidden': 'true' }, h('div.chev', icon('next'), icon('next'), icon('next')), h('span'));
  const live = h('div.sr-only', { role: 'status', 'aria-live': 'polite' });
  const bigPlay = h('button.big-play', { 'aria-label': 'Play' }, icon('play'));
  const timeEl = h('span.time', '0:00 / 0:00');
  const fill = h('div.scrub-fill'), buf = h('div.scrub-buf'), thumbDot = h('div.scrub-thumb');
  const bubble = h('div.scrub-bubble', { 'aria-hidden': 'true' });
  const scrub = h('div.scrub', { role: 'slider', tabindex: '0', 'aria-label': 'Seek', 'aria-valuemin': '0', 'aria-valuenow': '0' },
    h('div.scrub-track', buf, fill), thumbDot, bubble);
  const ccBtn = h('button.ctl', { 'aria-label': 'Captions', 'aria-pressed': 'false' }, icon('cc'));
  const speedBtn = h('button.ctl.speed-btn', { 'aria-label': 'Playback speed' }, h('span', '1×'));
  const audioBtn = h('button.ctl', { 'aria-label': 'Audio only', 'aria-pressed': 'false' }, icon('headphones'));
  const pipBtn = h('button.ctl', { 'aria-label': 'Picture in picture' }, icon('pip'));
  const airBtn = h('button.ctl', { 'aria-label': 'AirPlay' }, icon('airplay'));
  const fsBtn = h('button.ctl', { 'aria-label': 'Full screen' }, icon('fullscreen'));
  const titleTop = h('div.ptitle.ellipsis');
  const controls = h('div.pctl',
    h('div.pctl-top', h('button.ctl', { 'aria-label': 'Back', onclick: () => back('#/library') }, icon('back')), titleTop, h('span.spacer'), ccBtn, speedBtn),
    bigPlay,
    h('div.pctl-bottom', h('div.row.pctl-row', timeEl, h('span.spacer'), audioBtn, airBtn, pipBtn, fsBtn), scrub));
  const wrap = h('div.player-wrap', video, art, gesture, skipL, skipR, controls, live);

  const infoBox = h('div');
  const resumeNote = h('div');
  const actions = h('div.actions-row');
  const extraBox = h('div');     // transcript panel
  const nextBox = h('div');      // playlist or up next
  root.append(screen(wrap, h('div.stack.player-info', resumeNote, infoBox, actions, extraBox, nextBox)));
  root.firstChild.classList.add('no-nav', 'player-screen');

  // ---- helpers -------------------------------------------------------------------------
  const dur = () => (media.duration && isFinite(media.duration) ? media.duration : v.duration || 0);
  const announce = (t) => { live.textContent = t; };
  const inPip = () => document.pictureInPictureElement === video || video.webkitPresentationMode === 'picture-in-picture';
  const whenReady = (el, fn) => (el.readyState >= 1 ? fn() : el.addEventListener('loadedmetadata', fn, { once: true }));
  const seekTo = (t) => { media.currentTime = Math.max(0, Math.min(dur() - 0.2, t)); paintTime(); syncPosition(); };
  const speed = () => (ws && ws.speed) || 1;
  const applySpeed = () => { for (const el of [video, audio]) { el.defaultPlaybackRate = speed(); el.playbackRate = speed(); } speedBtn.firstChild.textContent = fmtSpeed(speed()); };

  // Give the <audio> element permission to play later without a tap (iOS needs
  // one user-started play() per element).
  function primeAudio() {
    if (audioPrimed) return;
    audioPrimed = true;
    if (!audio.src) audio.src = v.stream;
    audio.muted = true;
    audio.play().then(() => { if (media !== audio || !wantPlaying) audio.pause(); audio.muted = false; })
      .catch(() => { audio.muted = false; });
  }

  function userPlay() {
    wantPlaying = true;
    primeAudio();
    pauseMusic();
    media.play().catch(() => { wantPlaying = false; paintPlay(); });
  }
  function userPause() { wantPlaying = false; media.pause(); }
  const togglePlay = () => (media.paused ? userPlay() : userPause());

  // ---- progress reporting (same rules as before: every 5 s, pause, hide, end) ----------
  const send = (extra = {}) => {
    if (!dur()) return;
    lastSent = Date.now();
    api.progress(v.id, { position: media.currentTime, duration: dur(), ...extra }).catch(() => {});
  };
  const flush = () => {
    if (played && maxTime < 2 && !resumedFrom && !sentEnded) api.progress(v.id, { position: 0, skipped: true }).catch(() => {});
    else if (played && !sentEnded) send();
  };

  // ---- loading a video (first one and playlist neighbours) ------------------------------
  async function load(next, { autoplay = false } = {}) {
    if (v && v.id !== next.id) flush();
    v = next;
    played = false; maxTime = 0; resumedFrom = 0; sentEnded = false;
    const [w, s] = await Promise.all([api.watch(v.id).catch(() => ({ position: 0, watched: 0 })), api.subtitles(v.id).catch(() => ({ subtitles: [] }))]);
    ws = w; subs = s.subtitles;
    video.querySelectorAll('track').forEach((t) => t.remove());
    for (const sub of subs) video.append(h('track', { kind: 'subtitles', src: sub.url, srclang: sub.lang, label: sub.label }));
    const vertical = v.is_short || (v.width && v.height && v.height > v.width);
    wrap.classList.toggle('vertical', !!vertical);
    art.style.backgroundImage = `url("${v.thumb}")`;
    titleTop.textContent = v.title;
    video.src = v.stream;
    audio.src = v.stream;
    // While the phone is locked the <audio> element carries playback (see handOff).
    if (audioOnly || handedOff) { media = audio; video.pause(); } else media = video;
    wrap.classList.toggle('audio-only', audioOnly);
    audioBtn.setAttribute('aria-pressed', String(audioOnly));
    applySpeed();
    whenReady(media, () => {
      applySpeed();
      if (ws.position > 5 && !ws.watched && ws.position < dur() - 10) {
        media.currentTime = ws.position;
        resumedFrom = ws.position;
        put(resumeNote, h('div.banner.info', icon('play'), h('span.grow', `Resumed at ${fmtDuration(ws.position)}`),
          h('button.chip', { onclick: () => { seekTo(0); resumeNote.replaceChildren(); } }, 'Start over')));
      } else resumeNote.replaceChildren();
      paintTime();
    });
    if (media === audio) audio.load();
    applyCaptions();
    setupMediaSession();
    renderInfo();
    renderActions();
    renderNext();
    if (extraBox.firstChild) openTranscript();
    if (autoplay) { wantPlaying = true; media.play().catch(() => { wantPlaying = false; paintPlay(); }); }
    else media.play().then(() => { wantPlaying = true; }).catch(() => paintPlay());
    showControls();
  }

  // ---- media events (bound to both elements, only the active one counts) ----------------
  for (const el of [video, audio]) {
    on(el, 'play', () => {
      if (el !== media) return;
      played = true;
      if (el.playbackRate !== speed()) el.playbackRate = speed();  // Safari resets it on new sources
      pauseMusic(); paintPlay(); scheduleHide(); syncPosition();
    });
    on(el, 'pause', () => {
      if (el !== media) return;
      paintPlay(); showControls();
      if (!document.hidden && !handedOff && !sentEnded) send();
    });
    on(el, 'timeupdate', () => {
      if (el !== media) return;
      maxTime = Math.max(maxTime, media.currentTime - resumedFrom);
      if (Date.now() - lastSent > 5000 && !media.paused) send();
      paintTime();
    });
    on(el, 'progress', () => { if (el === media) paintBuffered(); });
    on(el, 'loadedmetadata', () => { if (el === media) { paintTime(); syncPosition(); } });
    on(el, 'ratechange', () => { if (el === media) syncPosition(); });
    on(el, 'ended', () => { if (el === media) onEnded(); });
    on(el, 'error', () => {
      if (el !== media) return;
      toast(v.needs_vlc ? 'Safari can’t play this codec. Try “Open in VLC” below.' : 'This video won’t play right now.', 5000);
    });
  }
  on(video, 'enterpictureinpicture', () => pipBtn.classList.add('on'));
  on(video, 'leavepictureinpicture', () => pipBtn.classList.remove('on'));
  on(video, 'webkitpresentationmodechanged', () => pipBtn.classList.toggle('on', inPip()));

  // ---- keep playing with the screen locked -----------------------------------------------
  // iOS pauses a <video> when Safari goes to the background (unless it's in
  // picture-in-picture), but an <audio> element may keep going. So we hand the
  // same stream over to the audio element and take it back when the page shows.
  function handOff() {
    const t = video.currentTime;
    audio.playbackRate = speed();
    whenReady(audio, () => { audio.currentTime = t; });
    if (audio.readyState >= 1) audio.currentTime = t;
    media = audio;
    handedOff = true;
    video.pause();
    audio.play().catch(() => { /* blocked: the lock-screen play button resumes it */ });
    setupMediaSession();
  }
  function handBack() {
    const t = audio.currentTime;
    const playing = !audio.paused;
    audio.pause();
    media = video;
    handedOff = false;
    video.currentTime = t;
    if (playing) video.play().catch(() => paintPlay());
    setupMediaSession();
  }
  on(document, 'visibilitychange', () => {
    if (document.hidden) {
      send();
      if (media === video && wantPlaying && !inPip() && !sentEnded) handOff();
    } else if (handedOff) handBack();
  });
  on(window, 'pagehide', () => send());

  // ---- audio-only mode -----------------------------------------------------------------
  function setAudioOnly(onOff) {
    audioOnly = onOff;
    setPref('audioOnly', onOff);
    const from = media, t = from.currentTime, playing = !from.paused;
    const to = onOff ? audio : video;
    if (from !== to) {
      from.pause();
      media = to;
      whenReady(to, () => { to.currentTime = t; });
      if (to.readyState >= 1) to.currentTime = t;
      if (playing) to.play().catch(() => paintPlay());
    }
    wrap.classList.toggle('audio-only', onOff);
    audioBtn.setAttribute('aria-pressed', String(onOff));
    pop(audioBtn);
    toast(onOff ? 'Audio only: keeps playing with the screen locked' : 'Video back on', 2400);
    setupMediaSession();
    paintPlay();
  }

  // ---- lock screen / Control Center (Media Session API) ----------------------------------
  function setupMediaSession() {
    if (!('mediaSession' in navigator)) return;
    const ms = navigator.mediaSession;
    try {
      ms.metadata = new MediaMetadata({
        title: v.title, artist: v.channel || '', album: playlist ? playlist.name : 'Home Video Library',
        artwork: [{ src: new URL(v.thumb, location.href).href, sizes: '320x180', type: v.thumb.endsWith('.svg') ? 'image/svg+xml' : 'image/jpeg' }],
      });
    } catch { /* old Safari */ }
    const set = (a, fn) => { try { ms.setActionHandler(a, fn); } catch { /* unsupported */ } };
    set('play', () => userPlay());
    set('pause', () => userPause());
    set('seekbackward', (d) => seekTo(media.currentTime - ((d && d.seekOffset) || SKIP)));
    set('seekforward', (d) => seekTo(media.currentTime + ((d && d.seekOffset) || SKIP)));
    set('seekto', (d) => { if (d && d.seekTime != null) seekTo(d.seekTime); });
    // iOS shows either ±10 s or previous/next on the lock screen, not both; skipping wins.
    set('previoustrack', null);
    set('nexttrack', null);
    syncPosition();
  }
  let lastPos = 0;
  function syncPosition() {
    if (!('mediaSession' in navigator) || !navigator.mediaSession.setPositionState) return;
    const d = dur();
    if (!d) return;
    const now = Date.now();
    if (now - lastPos < 400) return;
    lastPos = now;
    try { navigator.mediaSession.setPositionState({ duration: d, playbackRate: media.playbackRate || 1, position: Math.min(d, media.currentTime || 0) }); } catch { /* ignore */ }
  }

  // ---- painting ----------------------------------------------------------------------------
  let dragging = false, preview = 0;
  function paintTime() {
    const d = dur();
    const t = dragging ? preview : media.currentTime || 0;
    const frac = d ? Math.min(1, t / d) : 0;
    fill.style.width = `${frac * 100}%`;
    thumbDot.style.left = `${frac * 100}%`;
    timeEl.textContent = `${fmtDuration(t) || '0:00'} / ${fmtDuration(d) || '0:00'}`;
    scrub.setAttribute('aria-valuemax', String(Math.round(d)));
    scrub.setAttribute('aria-valuenow', String(Math.round(t)));
    scrub.setAttribute('aria-valuetext', `${fmtDuration(t)} of ${fmtDuration(d)}`);
  }
  function paintBuffered() {
    const d = dur();
    const b = media.buffered;
    if (!d || !b || !b.length) return;
    buf.style.width = `${Math.min(100, (b.end(b.length - 1) / d) * 100)}%`;
  }
  function paintPlay() {
    const paused = media.paused;
    put(bigPlay, icon(paused ? 'play' : 'pause'));
    bigPlay.setAttribute('aria-label', paused ? 'Play' : 'Pause');
    wrap.classList.toggle('paused', paused);
  }

  // ---- controls visibility --------------------------------------------------------------
  let hideTimer;
  function showControls() { wrap.classList.add('show-ctl'); scheduleHide(); }
  function hideControls() { if (!media.paused && !dragging) wrap.classList.remove('show-ctl'); }
  function scheduleHide() { clearTimeout(hideTimer); hideTimer = setTimeout(hideControls, HIDE_AFTER); }
  const toggleControls = () => (wrap.classList.contains('show-ctl') && !media.paused ? (clearTimeout(hideTimer), wrap.classList.remove('show-ctl')) : showControls());
  on(controls, 'pointerdown', () => scheduleHide());

  // ---- double-tap skip, YouTube style ---------------------------------------------------------
  // Double-tap a side to skip 10 s; while the indicator is up, every further
  // single tap on that side adds another 10 s (20, 30, ...).
  let lastTap = { t: 0, side: null }, seek = { side: null, total: 0, last: 0 }, singleTimer, skipHide;
  function skip(side) {
    const now = performance.now();
    if (seek.side !== side || now - seek.last > SEEK_WINDOW) seek = { side, total: 0, last: now };
    seek.total += SKIP;
    seek.last = now;
    seekTo(media.currentTime + (side === 'left' ? -SKIP : SKIP));
    const ind = side === 'left' ? skipL : skipR;
    (side === 'left' ? skipR : skipL).classList.remove('show');
    ind.lastChild.textContent = `${seek.total} seconds`;
    ind.classList.remove('show', 'tap');
    void ind.offsetWidth;
    ind.classList.add('show', 'tap');
    announce(`${side === 'left' ? 'Back' : 'Forward'} ${seek.total} seconds`);
    clearTimeout(skipHide);
    skipHide = setTimeout(() => { ind.classList.remove('show'); seek.side = null; }, SEEK_WINDOW + 150);
  }
  on(gesture, 'pointerup', (e) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    const r = gesture.getBoundingClientRect();
    const x = (e.clientX - r.left) / r.width;
    const side = x < 0.4 ? 'left' : x > 0.6 ? 'right' : 'center';
    const now = performance.now();
    if (side !== 'center' && seek.side && now - seek.last < SEEK_WINDOW) {  // keep tapping
      clearTimeout(singleTimer);
      skip(side);
      return;
    }
    if (side !== 'center' && lastTap.side === side && now - lastTap.t < TAP_WINDOW) {  // double-tap
      clearTimeout(singleTimer);
      lastTap = { t: 0, side: null };
      skip(side);
      return;
    }
    lastTap = { t: now, side };
    clearTimeout(singleTimer);
    singleTimer = setTimeout(toggleControls, side === 'center' ? 0 : TAP_WINDOW);
  });
  on(gesture, 'dblclick', (e) => e.preventDefault());

  // ---- scrubbing: drag, see the time, release to seek ------------------------------------
  const fracAt = (x) => { const r = scrub.getBoundingClientRect(); return Math.max(0, Math.min(1, (x - r.left) / r.width)); };
  function dragTo(x) {
    const f = fracAt(x);
    preview = f * dur();
    const delta = preview - media.currentTime;
    put(bubble, h('b', fmtDuration(preview) || '0:00'), h('span', `${delta >= 0 ? '+' : '−'}${fmtDuration(Math.abs(delta))}`));
    const r = scrub.getBoundingClientRect();
    const half = 44;
    bubble.style.left = `${Math.max(half, Math.min(r.width - half, f * r.width))}px`;
    paintTime();
  }
  on(scrub, 'pointerdown', (e) => {
    if (!dur()) return;
    dragging = true;
    scrub.setPointerCapture(e.pointerId);
    wrap.classList.add('scrubbing', 'show-ctl');
    clearTimeout(hideTimer);
    dragTo(e.clientX);
  });
  on(scrub, 'pointermove', (e) => { if (dragging) dragTo(e.clientX); });
  const endDrag = (commit) => {
    if (!dragging) return;
    dragging = false;
    wrap.classList.remove('scrubbing');
    if (commit) { seekTo(preview); announce(`Jumped to ${fmtDuration(preview)}`); }
    paintTime();
    scheduleHide();
  };
  on(scrub, 'pointerup', () => endDrag(true));
  on(scrub, 'pointercancel', () => endDrag(false));
  on(scrub, 'keydown', (e) => {
    const step = { ArrowLeft: -SKIP, ArrowRight: SKIP, PageDown: -60, PageUp: 60 }[e.key];
    if (step) { e.preventDefault(); seekTo(media.currentTime + step); }
    if (e.key === 'Home') { e.preventDefault(); seekTo(0); }
    if (e.key === 'End') { e.preventDefault(); seekTo(dur()); }
  });

  // ---- buttons ----------------------------------------------------------------------------
  on(bigPlay, 'click', (e) => { e.stopPropagation(); togglePlay(); });
  on(audioBtn, 'click', () => setAudioOnly(!audioOnly));
  on(speedBtn, 'click', () => speedSheet());
  on(ccBtn, 'click', () => {
    if (!subs.length) { captionsSheet(); return; }
    if (subs.length > 1 && !captionsOn()) { captionsSheet(); return; }
    setCaption(captionsOn() ? '' : (getPref('ccLang', '') || subs[0].lang));
  });
  const pipSupported = !!(document.pictureInPictureEnabled || (video.webkitSupportsPresentationMode && video.webkitSupportsPresentationMode('picture-in-picture')));
  pipBtn.hidden = !pipSupported;
  on(pipBtn, 'click', async () => {
    try {
      if (audioOnly) setAudioOnly(false);
      if (inPip()) {
        if (document.exitPictureInPicture && document.pictureInPictureElement) await document.exitPictureInPicture();
        else video.webkitSetPresentationMode('inline');
      } else if (video.requestPictureInPicture && document.pictureInPictureEnabled) await video.requestPictureInPicture();
      else video.webkitSetPresentationMode('picture-in-picture');
    } catch { toast('Picture in picture isn’t available right now.'); }
  });
  airBtn.hidden = !video.webkitShowPlaybackTargetPicker;
  on(airBtn, 'click', () => { try { video.webkitShowPlaybackTargetPicker(); } catch { /* ignore */ } });
  on(fsBtn, 'click', () => {
    if (audioOnly) setAudioOnly(false);
    // iPhone only allows native full screen on the <video> itself; iPad and
    // desktop can full-screen the whole player and keep our controls.
    if (document.fullscreenElement) document.exitFullscreen();
    else if (document.fullscreenEnabled && wrap.requestFullscreen) wrap.requestFullscreen().catch(() => video.webkitEnterFullscreen && video.webkitEnterFullscreen());
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
  });
  // Any tap on the player is a user gesture: use it to allow the lock-screen handoff.
  on(wrap, 'touchend', primeAudio, { passive: true });
  on(wrap, 'click', primeAudio);
  on(document, 'keydown', (e) => {
    if (e.target.closest('input, textarea, select, .sheet')) return;
    if (e.key === ' ' || e.key === 'k') { e.preventDefault(); togglePlay(); }
    else if (e.key === 'ArrowLeft' && e.target !== scrub) skip('left');
    else if (e.key === 'ArrowRight' && e.target !== scrub) skip('right');
    else if (e.key === 'c') ccBtn.click();
    else if (e.key === 'f') fsBtn.click();
  });

  // ---- speed --------------------------------------------------------------------------------
  function speedSheet() {
    sheet((close) => h('div',
      h('h2', { style: { marginBottom: '8px' } }, 'Playback speed'),
      h('p.muted.small', 'Remembered for this video.'),
      SPEEDS.map((r) => h('button.menu-item', { 'aria-pressed': String(r === speed()), onclick: async () => {
        close();
        ws = { ...ws, speed: r === 1 ? null : r };
        applySpeed();
        syncPosition();
        pop(speedBtn);
        toast(r === 1 ? 'Normal speed' : `${fmtSpeed(r)} speed`, 1600);
        api.setSpeed(v.id, r).catch(() => {});
      } }, icon(r === speed() ? 'checkcircle' : 'speed', r === speed() ? 'on' : ''), h('span.grow', r === 1 ? 'Normal (1×)' : fmtSpeed(r))))));
  }

  // ---- captions -----------------------------------------------------------------------------
  const captionsOn = () => [...video.textTracks].some((t) => t.mode === 'showing');
  function setCaption(lang) {
    [...video.textTracks].forEach((t) => { t.mode = lang && t.language === lang ? 'showing' : 'disabled'; });
    if (lang && ![...video.textTracks].some((t) => t.mode === 'showing') && video.textTracks[0]) video.textTracks[0].mode = 'showing';
    setPref('ccLang', lang);
    setPref('cc', !!lang);
    ccBtn.setAttribute('aria-pressed', String(!!lang));
    ccBtn.classList.toggle('on', !!lang);
    pop(ccBtn);
  }
  function applyCaptions() {
    ccBtn.classList.toggle('dim', !subs.length);
    const want = getPref('cc', false) ? getPref('ccLang', '') : '';
    // <track> elements need a tick before textTracks reflects them.
    setTimeout(() => {
      [...video.textTracks].forEach((t) => { t.mode = 'disabled'; });
      if (want && subs.length) {
        const t = [...video.textTracks].find((x) => x.language === want) || video.textTracks[0];
        if (t) t.mode = 'showing';
      }
      ccBtn.setAttribute('aria-pressed', String(captionsOn()));
      ccBtn.classList.toggle('on', captionsOn());
    }, 0);
  }

  function captionsSheet() {
    const s = sheet((close) => {
      const body = h('div');
      const fileIn = h('input', { type: 'file', class: 'sr-only', 'aria-label': 'Subtitle file' });
      const langSel = h('select.select', { 'aria-label': 'Subtitle language' },
        [['en', 'English'], ['es', 'Spanish'], ['de', 'German'], ['fr', 'French'], ['it', 'Italian'], ['pt', 'Portuguese'], ['ja', 'Japanese'], ['und', 'Other']]
          .map(([c, n]) => h('option', { value: c }, n)));
      on(fileIn, 'change', async () => {
        const f = fileIn.files && fileIn.files[0];
        if (!f) return;
        if (f.size > 2 * 1024 * 1024) { toast('That file is too big (2 MB max).'); return; }
        try {
          const content = await f.text();
          const sub = await api.uploadSubtitle(v.id, { content, filename: f.name, lang: langSel.value });
          subs = (await api.subtitles(v.id)).subtitles;
          video.append(h('track', { kind: 'subtitles', src: sub.url, srclang: sub.lang, label: sub.label }));
          setTimeout(() => setCaption(sub.lang), 0);
          toast(`Added “${sub.label}”`, 2400, 'check');
          paint();
        } catch (e) { toast(e.message, 4000); }
        fileIn.value = '';
      });
      const fetchBtn = h('button.btn.block', { onclick: async () => {
        fetchBtn.disabled = true;
        put(fetchBtn, h('span.spinner.inline'), 'Asking YouTube…');
        try {
          const r = await api.fetchSubtitles(v.id);
          if (!r.added) toast('YouTube has no captions for this video.', 3200);
          else { toast('Captions added', 2200, 'check'); await reloadTracks(); }
        } catch (e) { toast(e.message, 4000); }
        paint();
      } }, icon('download'), 'Get captions from YouTube');
      const paint = () => {
        const cur = captionsOn() ? ([...video.textTracks].find((t) => t.mode === 'showing') || {}).language : '';
        put(body, 
          h('h2', { style: { marginBottom: '8px' } }, 'Captions'),
          h('button.menu-item', { 'aria-pressed': String(!cur), onclick: () => { setCaption(''); paint(); } }, icon(!cur ? 'checkcircle' : 'x', !cur ? 'on' : ''), h('span.grow', 'Off')),
          subs.map((sub) => h('div.row', h('button.menu-item.grow', { 'aria-pressed': String(cur === sub.lang), onclick: () => { setCaption(sub.lang); paint(); close(); } },
            icon(cur === sub.lang ? 'checkcircle' : 'cc', cur === sub.lang ? 'on' : ''), h('span.grow', sub.label), h('span.pill', { manual: 'YouTube', auto: 'Auto', uploaded: 'Added' }[sub.kind])),
          sub.kind === 'uploaded' ? h('button.icon-btn', { 'aria-label': `Remove ${sub.label}`, onclick: async () => {
            await api.deleteSubtitle(sub.id); await reloadTracks(); toast('Subtitle removed'); paint();
          } }, icon('trash')) : null)),
          !subs.length ? h('p.muted.small', 'No captions for this video yet.') : null,
          h('div.section-title', 'Add your own'),
          h('div.row', langSel, h('button.btn.grow', { onclick: () => fileIn.click() }, icon('upload'), 'Subtitle file (SRT/VTT)')), fileIn,
          !subs.some((x) => x.kind !== 'uploaded') ? h('div', { style: { marginTop: '10px' } }, fetchBtn) : null,
          h('button.btn.block', { style: { marginTop: '10px' }, onclick: () => { close(); openTranscript(true); } }, icon('text'), 'Show transcript'));
      };
      paint();
      return body;
    });
    return s;
  }
  async function reloadTracks() {
    subs = (await api.subtitles(v.id)).subtitles;
    video.querySelectorAll('track').forEach((t) => t.remove());
    for (const sub of subs) video.append(h('track', { kind: 'subtitles', src: sub.url, srclang: sub.lang, label: sub.label }));
    applyCaptions();
  }

  // ---- transcript (searchable, tap a line to jump there) ------------------------------
  let trOff = null;
  async function openTranscript(scroll = false) {
    if (trOff) { trOff(); trOff = null; }
    const panel = h('section.transcript', { 'aria-label': 'Transcript' });
    put(extraBox, panel);
    if (scroll) panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    const closeBtn = h('button.icon-btn', { 'aria-label': 'Close transcript', onclick: () => { if (trOff) trOff(); trOff = null; extraBox.replaceChildren(); renderActions(); } }, icon('x'));
    if (!subs.length) {
      put(panel, h('div.row', h('h3.grow', 'Transcript'), closeBtn),
        emptyState('text', 'No transcript yet', 'Get captions from YouTube or add a subtitle file to see a searchable transcript here.',
          h('button.btn', { onclick: () => captionsSheet() }, icon('cc'), 'Captions…')));
      return;
    }
    let pick = subs[0];
    const cur = [...video.textTracks].find((t) => t.mode === 'showing');
    if (cur) pick = subs.find((x) => x.lang === cur.language) || pick;
    put(panel, h('div.row', h('h3.grow', 'Transcript'), closeBtn), skeleton('list', 3));
    let tr;
    try { tr = await api.transcript(pick.id); } catch (e) { put(panel, errorState(e, () => openTranscript())); return; }
    const input = h('input', { type: 'search', placeholder: 'Search the transcript', 'aria-label': 'Search the transcript', enterkeyhint: 'search' });
    const count = h('span.small.muted');
    const list = h('ol.cues');
    let rows = [], activeIdx = -1, userScrolled = 0;
    const paint = () => {
      const q = input.value.trim().toLowerCase();
      list.replaceChildren();
      rows = [];
      let n = 0;
      tr.cues.forEach((c, i) => {
        const hit = !q || c.text.toLowerCase().includes(q);
        if (!hit) return;
        n++;
        const text = h('span.cue-text');
        if (q) {
          const low = c.text.toLowerCase();
          let at = 0, k;
          while ((k = low.indexOf(q, at)) >= 0) { text.append(c.text.slice(at, k), h('mark', c.text.slice(k, k + q.length))); at = k + q.length; }
          text.append(c.text.slice(at));
        } else text.textContent = c.text;
        const li = h('li', h('button.cue', { onclick: () => { seekTo(c.start); if (media.paused) userPlay(); } }, h('span.cue-time', fmtDuration(c.start)), text));
        li.dataset.i = i;
        rows.push(li);
        list.append(li);
      });
      count.textContent = q ? (n ? `${n} match${n === 1 ? '' : 'es'}` : 'No matches') : `${tr.cues.length} lines`;
      activeIdx = -1;
    };
    on(input, 'input', paint);
    on(list, 'scroll', () => { userScrolled = Date.now(); }, { passive: true });
    put(panel, h('div.row', h('h3.grow', 'Transcript', h('span.muted.small', ` · ${pick.label}`)), closeBtn),
      h('label.field', icon('search'), input), count, list);
    paint();
    const follow = () => {
      if (input.value.trim()) return;
      const t = media.currentTime;
      const i = tr.cues.findIndex((c, k) => t >= c.start && t < ((tr.cues[k + 1] || {}).start ?? c.end + 5));
      if (i === activeIdx || i < 0) return;
      const prev = rows[activeIdx];
      if (prev) prev.classList.remove('now');
      activeIdx = i;
      const row = rows[i];
      if (!row) return;
      row.classList.add('now');
      if (Date.now() - userScrolled > 4000) list.scrollTop = row.offsetTop - list.clientHeight / 2 + row.clientHeight / 2;
    };
    for (const el of [video, audio]) el.addEventListener('timeupdate', follow);
    trOff = () => { for (const el of [video, audio]) el.removeEventListener('timeupdate', follow); };
  }

  // ---- info & actions ------------------------------------------------------------------------
  const vlcUrl = () => `vlc-x-callback://x-callback-url/stream?url=${encodeURIComponent(new URL(v.stream, location.href).href)}`;
  function renderInfo() {
    put(infoBox, 
      h('div', h('h1.video-title', v.title),
        h('div.muted.small.meta', [v.channel, fmtDuration(v.duration), v.quality === 'best' ? 'Best quality' : 'Compatible 1080p'].filter(Boolean).join(' · '))),
      v.needs_vlc ? h('div.banner', { style: { marginTop: '12px' } }, icon('warn'), h('div.grow', h('div', `Encoded as ${(v.vcodec || '').split('.')[0].toUpperCase()}. If Safari won’t play it, use VLC.`),
        h('a.btn', { href: vlcUrl(), style: { marginTop: '8px' } }, 'Open in VLC'))) : null);
  }

  function renderActions() {
    const w = (v.watch || {})[me] || {};
    let rating = w.rating || 0;
    const watched = !!(ws && ws.watched);
    const chip = (label, ic, onclick, isOn = false, extra = {}) => h(`button.chip${isOn ? '.on' : ''}`, { 'aria-pressed': String(isOn), onclick, ...extra }, icon(ic), label);
    const setRating = async (e, val) => {
      const el = e.currentTarget;
      rating = rating === val ? 0 : val;
      try { await api.rate(v.id, rating); v.watch = { ...v.watch, [me]: { ...w, rating } }; renderActions(); pop(actions.querySelector(`[data-r="${val}"]`)); } catch (err) { toast(err.message); }
      return el;
    };
    put(actions, 
      chip('Like', 'like', (e) => setRating(e, 1), rating === 1, { 'data-r': '1' }),
      chip('Dislike', 'dislike', (e) => setRating(e, -1), rating === -1, { 'data-r': '-1' }),
      chip(watched ? 'Watched' : 'Mark watched', 'checkcircle', async () => {
        try {
          ws = await api.setWatched(v.id, !watched);
          toast(ws.watched ? 'Marked as watched' : 'Marked as unwatched', 2000, 'check');
          renderActions(); pop(actions.children[2]);
        } catch (e) { toast(e.message); }
      }, watched),
      chip('Save', 'listadd', () => addToPlaylistSheet(v)),
      chip('Captions', 'cc', () => captionsSheet()),
      chip('Transcript', 'text', () => openTranscript(true), !!extraBox.firstChild),
      chip(v.keep ? 'Kept' : 'Keep', 'pin', async () => {
        try { v = { ...v, keep: !v.keep ? 1 : 0 }; await api.keep(v.id, !!v.keep); toast(v.keep ? 'Kept: won’t be auto-deleted' : 'Auto-delete back on', 2200, 'check'); renderActions(); } catch (e) { toast(e.message); }
      }, !!v.keep),
      chip('Shared queue', 'plus', async () => { try { await api.queueAdd(v.id, 'shared'); toast('Added to Shared queue', 2000, 'check'); } catch (e) { toast(e.message); } }),
      chip('My queue', 'plus', async () => { try { await api.queueAdd(v.id, 'personal'); toast('Added to My queue', 2000, 'check'); } catch (e) { toast(e.message); } }),
      chip('Delete', 'trash', () => deleteSheet(v, { onDeleted: afterDeleted })));
  }

  // ---- playlist / up next ------------------------------------------------------------------
  async function loadLists() {
    if (listId) {
      try { playlist = await api.playlist(listId); } catch { playlist = null; }
    }
    if (!playlist) {
      const [mine, shared] = await Promise.all([api.queue('personal').catch(() => ({ items: [] })), api.queue('shared').catch(() => ({ items: [] }))]);
      upNext = [...mine.items, ...shared.items].filter((x, i, arr) => x.status === 'ready' && arr.findIndex((y) => y.id === x.id) === i);
    }
    renderNext();
  }
  const playable = () => (playlist ? playlist.items.filter((x) => x.status === 'ready') : []);
  function nextItem() {
    if (playlist) {
      const items = playable();
      const i = items.findIndex((x) => x.id === v.id);
      return i >= 0 ? items[i + 1] || null : items[0] || null;
    }
    return upNext.find((x) => x.id !== v.id) || null;
  }
  function goTo(item, { autoplay = true } = {}) {
    if (playlist) {
      history.replaceState(null, '', `#/player/${encodeURIComponent(item.id)}/list/${playlist.id}`);
      load(item, { autoplay });
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } else navigate(`#/player/${encodeURIComponent(item.id)}`, true);
  }
  function renderNext() {
    if (playlist) {
      const items = playable();
      const idx = items.findIndex((x) => x.id === v.id);
      put(nextBox, 
        h('div.section-title', icon('listadd'), playlist.name, h('span.spacer'), h('span.small.count', `${idx + 1} of ${items.length}`)),
        h('div.card.list-card', items.map((n) => h(`button.hitem.vitem${n.id === v.id ? '.current' : ''}`, { 'aria-current': n.id === v.id ? 'true' : null, onclick: () => n.id !== v.id && goTo(n) },
          h('div', { style: { width: '112px', flex: 'none' } }, thumbEl(n, watchInfoOpts(n))),
          h('div.grow', h('div.title.clamp2', n.title), h('div.sub', n.id === v.id ? 'Now playing' : n.channel))))));
      return;
    }
    const list = upNext.filter((x) => x.id !== v.id);
    if (!list.length) { nextBox.replaceChildren(); return; }
    put(nextBox, h('div.section-title', 'Up next from your queues'),
      h('div.card.list-card', list.slice(0, 6).map((n) => h('button.hitem.vitem', { onclick: () => goTo(n) },
        h('div', { style: { width: '112px', flex: 'none' } }, thumbEl(n, watchInfoOpts(n))),
        h('div.grow', h('div.title.clamp2', n.title), h('div.sub', n.channel))))));
  }
  const watchInfoOpts = (n) => { const w = watchInfo(n, me); return { bar: w.pos, watched: w.watched }; };

  // ---- the end: delete after watching + what's next -------------------------------------
  let endSheet = null, countdown = null;
  function onEnded() {
    sentEnded = true;
    send({ ended: true });
    ws = { ...ws, watched: 1 };
    renderActions();
    paintPlay();
    const nxt = nextItem();
    if (document.hidden) {  // locked phone: keep the playlist going, no prompts
      if (playlist && nxt) goTo(nxt);
      return;
    }
    showEndCard(nxt);
  }
  function showEndCard(nxt) {
    if (endSheet) endSheet.close();
    clearInterval(countdown);
    const auto = playlist && nxt;
    let left = 8;
    const ring = h('span.count-num', String(left));
    endSheet = sheet((close) => h('div.stack',
      h('div.row', icon('checkcircle', 'done-icon'), h('h2.grow', 'Finished')),
      deleteSection(v, { finished: true, onDeleted: () => { close(); afterDeleted(); } }),
      nxt ? h('div.next-card',
        h('div', { style: { width: '120px', flex: 'none' } }, thumbEl(nxt)),
        h('div.grow', h('div.small.muted', auto ? h('span', 'Up next in ', ring, 's') : 'Play next'), h('div.title.clamp2', nxt.title),
          h('div.row', { style: { marginTop: '8px', gap: '8px' } },
            h('button.btn.primary', { onclick: () => { close(); goTo(nxt); } }, icon('play'), 'Play now'),
            auto ? h('button.btn', { onclick: (e) => { clearInterval(countdown); e.currentTarget.remove(); ring.parentElement.textContent = 'Up next'; } }, 'Cancel') : null))) : null,
      h('button.btn.block', { onclick: () => { close(); seekTo(0); userPlay(); } }, icon('replay'), 'Watch again'),
    ), { onClose: () => { clearInterval(countdown); endSheet = null; } });
    if (auto) {
      countdown = setInterval(() => {
        left -= 1;
        ring.textContent = String(left);
        if (left <= 0) { clearInterval(countdown); if (endSheet) endSheet.close(); goTo(nxt); }
      }, 1000);
    }
  }
  function afterDeleted() {
    sentEnded = true;
    const nxt = nextItem();
    if (playlist) {
      playlist.items = playlist.items.filter((x) => x.id !== v.id);
      if (nxt) { goTo(nxt); return; }
    }
    navigate(playlist ? `#/playlist/${playlist.id}` : '#/library', true);
  }

  // ---- go ----------------------------------------------------------------------------------
  paintPlay();
  load(first);
  loadLists();

  return () => {
    flush();
    clearTimeout(hideTimer); clearTimeout(singleTimer); clearTimeout(skipHide); clearInterval(countdown);
    if (trOff) trOff();
    if (endSheet) endSheet.close();
    off.forEach((f) => f());
    for (const el of [video, audio]) { el.pause(); el.removeAttribute('src'); el.load(); }
    claimMediaSession();
  };
}
