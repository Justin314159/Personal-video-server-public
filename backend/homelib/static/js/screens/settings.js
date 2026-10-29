import { api, isPreview } from '../api.js';
import { h, icon, seg, toast, avatar, fmtBytes, confirmSheet, sheet } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state, loadProfiles } from '../state.js';

// ---- profile photo ---------------------------------------------------------------
const PHOTO_PX = 256;

// Shrink and centre-crop a picked photo to a small square JPEG on the phone,
// so the server never has to process images.
async function squarePhoto(file) {
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.src = url;
    await img.decode();
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    if (!side) throw new Error('empty image');
    const c = document.createElement('canvas');
    c.width = c.height = PHOTO_PX;
    c.getContext('2d').drawImage(img, (img.naturalWidth - side) / 2, (img.naturalHeight - side) / 2, side, side, 0, 0, PHOTO_PX, PHOTO_PX);
    return c.toDataURL('image/jpeg', 0.85);
  } finally {
    URL.revokeObjectURL(url);
  }
}

function photoButton(p) {
  const input = h('input', { type: 'file', accept: 'image/*', class: 'sr-only', tabindex: '-1', 'aria-hidden': 'true' });
  const btn = h('button.avatar-edit', { 'aria-label': 'Profile photo' },
    avatar(p), h('span.cam', { 'aria-hidden': 'true' }, icon('camera')));
  const refresh = async () => {
    await loadProfiles();
    // Swap the avatars on this page (row + top bar) for the new one.
    for (const el of document.querySelectorAll('.avatar-edit > .avatar, .topbar .avatar-btn > .avatar')) el.replaceWith(avatar(state.profile));
  };
  input.addEventListener('change', async () => {
    const file = input.files && input.files[0];
    input.value = '';
    if (!file) return;
    try {
      await api.setAvatar(p.id, await squarePhoto(file));
      await refresh();
      toast('Photo saved', 2200, 'check');
    } catch (e) {
      toast(e.status ? e.message : 'That photo couldn’t be opened. Try another one.');
    }
  });
  btn.addEventListener('click', () => {
    if (!state.profile.avatar) { input.click(); return; }
    sheet((close) => h('div',
      h('h2', { style: { marginBottom: '8px' } }, 'Profile photo'),
      h('button.menu-item', { onclick: () => { close(); input.click(); } }, icon('camera'), 'Choose a new photo'),
      h('button.menu-item.danger', { onclick: async () => {
        close();
        try { await api.removeAvatar(p.id); await refresh(); toast('Photo removed'); } catch (err) { toast(err.message); }
      } }, icon('trash'), 'Remove photo')));
  });
  return [btn, input];
}

export const settingsScreen = {
  async render(root) {
    nav('settings');
    const [settings, status] = await Promise.all([api.settings(), api.status().catch(() => null)]);
    state.settings = settings;
    const p = state.profile;
    const link = (href, ic, label, val) => h('a.item', { href }, h('span.lead', icon(ic)), h('span.grow', label), val ? h('span.val', val) : null, icon('chev', 'chev'));

    const nameInput = h('input', { value: p.name, maxlength: 40, 'aria-label': 'Profile name', style: { flex: 1, minWidth: 0, background: 'transparent', border: 0, outline: 0, fontSize: '1rem', fontWeight: 600, height: '44px' } });
    nameInput.addEventListener('change', async () => {
      const name = nameInput.value.trim();
      if (!name) { nameInput.value = p.name; return; }
      await api.renameProfile(p.id, name);
      await loadProfiles();
      toast('Name saved');
    });

    const qualityNote = h('div.muted.small', { style: { marginTop: '8px' } });
    const setQNote = (q) => { qualityNote.textContent = q === 'best' ? 'Up to 4K, stored as-is. Some videos come as VP9/AV1, which may need VLC on the phone.' : 'H.264 up to 1080p. Plays natively in Safari on every iPhone.'; };
    setQNote(settings.default_quality);

    root.append(screen(
      topbar('Settings'),
      h('div.group',
        h('div.item', photoButton(p), nameInput, h('span.val', 'Rename')),
        link('#/profiles', 'users', 'Switch profile', state.profiles.filter((x) => x.id !== p.id).map((x) => x.name).join(', '))),

      h('div.section-title', 'Download quality'),
      h('div.group', h('div.block',
        seg([['compat', 'Compatible 1080p'], ['best', 'Best quality']], settings.default_quality, async (q) => { setQNote(q); await api.setSettings({ default_quality: q }); toast('Default quality saved'); }, 'accent'),
        qualityNote)),

      h('div.section-title', 'Music format'),
      h('div.group', h('div.block',
        seg([['mp3', 'MP3'], ['m4a', 'M4A (AAC)']], settings.music_format || 'mp3', async (f) => { await api.setSettings({ music_format: f }); toast('Music format saved'); }, 'accent'),
        h('div.muted.small', { style: { marginTop: '8px' } }, 'MP3 plays everywhere. M4A keeps the original audio without converting. Music is never auto-deleted.'))),

      h('div.section-title', 'Auto-delete'),
      h('div.group', h('div.block',
        seg([[12, '12 hours'], [24, '24 hours'], [48, '48 hours']], settings.auto_delete_hours, async (v) => { await api.setSettings({ auto_delete_hours: v }); toast(`Auto-delete after ${v}h`); }),
        h('div.muted.small', { style: { marginTop: '8px' } },
          'Videos are deleted once everyone they were for has watched them, or when they’re older than this, whichever comes first. Tap the pin (“Keep”) on a video to keep it.'))),

      h('div.section-title', 'Server'),
      serverBox(status),

      h('div.section-title', 'Personalize'),
      h('div.group',
        link('#/algorithm', 'sliders', 'Your algorithm'),
        link('#/appearance', 'palette', 'Appearance & dark mode', themeLabel(state.profile.theme))),

      h('div.section-title', 'iPhone'),
      h('div.group',
        h('div.block.small.muted',
          'In Safari, open this page, tap Share → “Add to Home Screen”. It opens full-screen like an app. Keep Tailscale connected on the phone.'),
        link('#/shortcut', 'phone', 'Share-sheet shortcut', 'Set up')),

      isPreview ? h('div', { style: { marginTop: '20px' } }, h('button.btn.block.danger', { onclick: async () => {
        if (await confirmSheet('Reset the demo?', 'Clears everything you did in this preview.', 'Reset')) { window.HOMELIB_MOCK_API.resetDemo(); location.hash = '#/profiles'; location.reload(); }
      } }, 'Reset demo data')) : null,
      h('p.muted.small', { style: { textAlign: 'center', marginTop: '24px' } }, `Home Video Library ${status ? status.version : ''}${status && status.mock ? ' · mock mode' : ''}`),
    ));
  },
};

function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : ''; }

function themeLabel(t) {
  if (!t) return '';
  if (t.mode === 'auto') return 'Match iPhone';
  return t.mode ? `${cap(t.mode)} · ${cap(t.preset)}` : cap(t.preset);
}

function serverBox(st) {
  if (!st) return h('div.group', h('div.item', h('span.status-dot.off'), h('span.grow', 'Server unreachable'), h('span.val', 'Check Tailscale')));
  const d = st.disk;
  const pct = d.total ? (1 - d.free / d.total) * 100 : 0;
  return h('div.group',
    h('div.item', h('span.status-dot'), h('span.grow', 'Online'), h('span.val', st.via_tailscale ? 'via Tailscale' : 'local connection')),
    h('div.block',
      h('div.kv', h('span', 'Disk free'), h('span', { style: { fontWeight: 600 } }, `${fmtBytes(d.free)} of ${fmtBytes(d.total)}`)),
      h('div.progress.disk', h('div', { style: { width: `${pct}%`, background: d.ok ? 'var(--progress)' : 'var(--danger)' } })),
      h('div.muted.small', { style: { marginTop: '8px' } }, d.ok ? `New downloads pause below ${fmtBytes(d.min_free)} free.` : 'Below the free-space limit: new downloads are refused.')),
    h('div.block.small',
      h('div.kv', h('span.muted', 'Videos on disk'), h('span', fmtBytes(d.library_bytes))),
      h('div.kv', h('span.muted', 'Active downloads'), h('span', String((st.downloads.running || 0) + (st.downloads.queued || 0)))),
      h('div.kv', h('span.muted', 'yt-dlp'), h('span', st.yt_dlp || '—')),
      h('div.kv', h('span.muted', 'ffmpeg'), h('span', st.ffmpeg.found ? st.ffmpeg.source : 'missing')),
      h('div.kv', h('span.muted', 'JS runtime'), h('span', st.js_runtime || 'none')),
      h('div.kv', h('span.muted', 'Last suggestions fetch'), h('span', st.last_candidate_fetch ? new Date(st.last_candidate_fetch * 1000).toLocaleString() : 'never'))));
}
