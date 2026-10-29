// Vertical Shorts feed: scroll-snap, muted autoplay, tap to unmute.
import { api } from '../api.js';
import { h, icon, toast } from '../ui.js';
import { nav } from '../layout.js';
import { back } from '../router.js';
import { whySheet } from './common.js';
import { pauseMusic } from '../musicplayer.js';

let teardown = null;
let unmuted = false; // once the user taps for sound, keep it on for the session

export const shortsScreen = {
  async render(root, startId) {
    nav(null);
    pauseMusic();
    const feed = h('div.shorts', { role: 'feed', 'aria-label': 'Shorts' });
    const top = h('div.shorts-top', h('button.icon-btn', { 'aria-label': 'Back', onclick: () => back('#/foryou') }, icon('back')), h('h2', 'Shorts'));
    root.append(feed, top);
    const data = await api.shorts();
    const items = data.items;
    if (startId) {
      const i = items.findIndex((s) => s.id === startId);
      if (i > 0) items.unshift(...items.splice(i, 1));
    }
    const stats = new Map(); // id -> { seconds, last, reported }
    const cards = items.map((s) => shortCard(s, stats));
    const endCard = h('section.short', h('div', { style: { textAlign: 'center', padding: '24px' } },
      icon('check'), h('h2', { style: { margin: '10px 0 6px' } }, 'You’re all caught up'),
      h('p', { style: { opacity: 0.8 } }, data.downloading ? `${data.downloading} more downloading in the background.` : 'New Shorts are downloaded automatically.'),
      h('button.btn', { onclick: async () => { const r = await api.prefetchShorts(); toast(r.queued ? `Fetching ${r.queued} more` : 'Already have enough queued'); } }, icon('refresh'), 'Fetch more')));
    feed.append(...cards.map((c) => c.el), endCard);

    let active = -1;
    const activate = (idx) => {
      if (idx === active) return;
      if (active >= 0 && cards[active]) cards[active].leave();
      active = idx;
      // Keep only the neighbours loaded: iPhones run out of video decoders fast.
      cards.forEach((c, i) => (Math.abs(i - idx) <= 1 ? c.load() : c.unload()));
      if (cards[idx]) cards[idx].enter();
    };
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting && e.intersectionRatio >= 0.6) {
          const idx = cards.findIndex((c) => c.el === e.target);
          activate(idx);
        }
      }
    }, { root: feed, threshold: [0.6] });
    cards.forEach((c) => io.observe(c.el));
    io.observe(endCard);
    if (cards.length) activate(0);

    teardown = () => {
      io.disconnect();
      if (active >= 0 && cards[active]) cards[active].leave();
      cards.forEach((c) => c.unload());
    };

    function shortCard(s, stats) {
      const video = h('video', { playsinline: true, 'webkit-playsinline': true, muted: true, loop: true, preload: 'none', 'aria-label': s.title });
      video.muted = true;
      const hint = h('div.muted-hint', icon('mute'), 'Tap for sound');
      const bar = h('div');
      const st = { seconds: 0, last: null, reported: false };
      stats.set(s.id, st);
      let rating = 0;
      const likeBtn = h('button.icon-btn', { 'aria-label': 'Like', onclick: async () => { rating = rating === 1 ? 0 : 1; await api.rate(s.id, rating); paint(); } }, icon('like'));
      const disBtn = h('button.icon-btn', { 'aria-label': 'Dislike', onclick: async () => { rating = rating === -1 ? 0 : -1; await api.rate(s.id, rating); paint(); } }, icon('dislike'));
      const paint = () => { likeBtn.classList.toggle('on', rating === 1); disBtn.classList.toggle('on', rating === -1); };
      const el = h('section.short', video,
        h('div.tapzone', { onclick: () => {
          unmuted = video.muted; // toggles
          video.muted = !video.muted;
          hint.style.opacity = video.muted ? 1 : 0;
          if (video.paused) video.play().catch(() => {});
        } }),
        hint,
        h('div.overlay', h('div.t', s.title), h('div.c', s.channel)),
        h('div.rail',
          h('div', likeBtn), h('div', disBtn),
          h('div', h('button.icon-btn', { 'aria-label': 'Why am I seeing this?', onclick: () => whySheet(s, 'me') }, icon('info')))),
        h('div.sbar', bar));
      video.addEventListener('timeupdate', () => {
        const now = video.currentTime;
        if (st.last !== null && now >= st.last && now - st.last < 1.5) st.seconds += now - st.last;
        st.last = now;
        if (video.duration) bar.style.width = `${(now / video.duration) * 100}%`;
      });
      return {
        el,
        load() { if (!video.getAttribute('src')) { video.src = s.stream; video.preload = 'auto'; } },
        unload() { if (video.getAttribute('src')) { video.pause(); video.removeAttribute('src'); video.load(); } },
        enter() {
          video.muted = !unmuted;
          hint.style.opacity = video.muted ? 1 : 0;
          st.last = null;
          video.play().catch(() => { video.muted = true; hint.style.opacity = 1; video.play().catch(() => {}); });
        },
        leave() {
          video.pause();
          if (!st.reported) {
            st.reported = true;
            api.shortSeen(s.id, st.seconds, video.duration || s.duration).catch(() => {});
          }
        },
      };
    }
  },
  unmount() { if (teardown) teardown(); teardown = null; },
};
