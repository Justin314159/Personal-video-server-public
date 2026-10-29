// Per-profile colours: dark mode (Dark / Light / Match iPhone), one dark and
// one light preset, and custom colours on top of either.
import { api } from '../api.js';
import { h, put, icon, debounce, toast, seg, skeleton, pop } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state } from '../state.js';
import { applyProfileTheme, contrastWarnings, schemeFor, THEME_FIELDS } from '../theme.js';

const PRESET_NAMES = { midnight: 'Midnight', oled: 'OLED black', warm: 'Warm', daylight: 'Daylight', linen: 'Linen' };
const SHARED = ['accent', 'progress'];

export const appearanceScreen = {
  async render(root) {
    nav('settings');
    const body = h('div', skeleton('list', 3));
    root.append(screen(topbar('Appearance', { backTo: '#/settings' }), body));
    let ap = await api.appearance();
    let editing = schemeFor(ap);  // which palette the colour pickers change

    const remember = (next) => {
      ap = { ...ap, ...next };
      state.profile.theme = { mode: ap.mode, preset: ap.preset, colors: ap.colors, custom: ap.custom, dark: ap.dark, light: ap.light };
      applyProfileTheme(state.profile.theme);
    };
    const save = debounce(async (colors, scheme) => {
      try { remember(await api.setAppearance({ colors, scheme })); } catch (e) { toast(e.message); }
    }, 350);

    const modeEl = h('div');
    const presetsEl = h('div');
    const editEl = h('div');
    const pickers = h('div.group');
    const warnings = h('div');
    const preview = h('div');

    const renderMode = () => put(modeEl, 
      seg([['dark', 'Dark'], ['light', 'Light'], ['auto', 'Match iPhone']], ap.mode, async (m) => {
        try {
          remember(await api.setAppearance({ mode: m }));
          editing = schemeFor(ap);
          toast(m === 'auto' ? 'Follows your iPhone’s Light/Dark setting' : `${m === 'dark' ? 'Dark' : 'Light'} mode on`, 2200, 'check');
          renderAll();
        } catch (e) { toast(e.message); }
      }, 'accent'),
      h('p.muted.small.note', ap.mode === 'auto' ? 'Switches between your dark and light theme with the iPhone (Settings → Display & Brightness).' :
        'Each profile has its own look. Pick “Match iPhone” to switch with the time of day.'));

    const presetRow = (scheme) => {
      const keys = Object.entries(ap.presets).filter(([, c]) => c.scheme === scheme);
      const chosen = ap[scheme].preset;
      return h('div.presets', { role: 'radiogroup', 'aria-label': `${scheme === 'dark' ? 'Dark' : 'Light'} theme` }, keys.map(([key, c]) => h('button.preset', {
        class: chosen === key ? 'on' : '', role: 'radio', 'aria-checked': String(chosen === key),
        onclick: async (e) => {
          const btn = e.currentTarget;
          try {
            remember(await api.setAppearance({ preset: key }));
            editing = scheme;
            renderAll();
            pop(presetsEl.querySelector(`[data-key="${key}"]`));
          } catch (err) { toast(err.message); }
          return btn;
        }, 'data-key': key,
      }, h('div.sw', { style: { background: c.bg } },
        h('i', { style: { background: c.surface, width: '80%' } }), h('i', { style: { background: ap.colors.accent, width: '50%' } }), h('i', { style: { background: c.text, width: '65%', opacity: 0.8 } })),
      h('div.n', PRESET_NAMES[key] || key))));
    };
    const renderPresets = () => put(presetsEl, 
      h('div.section-title', icon('palette'), 'Dark theme'), presetRow('dark'),
      h('div.section-title', icon('sparkle'), 'Light theme'), presetRow('light'));

    const renderEdit = () => put(editEl, h('div.section-title', 'Custom colours', h('span.spacer'),
      seg([['dark', 'Dark'], ['light', 'Light']], editing, (v) => { editing = v; renderPickers(); renderWarnings(); renderPreview(); }, 'tiny')));

    const renderPickers = () => {
      const pal = ap[editing];
      put(pickers, ...THEME_FIELDS.map(([key, label]) => {
        const input = h('input', { type: 'color', value: (pal.colors[key] || '#000000').toLowerCase(), 'aria-label': `${label} (${SHARED.includes(key) ? 'both themes' : editing})` });
        const code = h('code', (pal.colors[key] || '').toUpperCase());
        input.addEventListener('input', () => {
          const val = input.value.toUpperCase();
          code.textContent = val;
          const scope = SHARED.includes(key) ? ['dark', 'light'] : [editing];
          for (const sc of scope) ap[sc] = { ...ap[sc], colors: { ...ap[sc].colors, [key]: val } };
          if (scope.includes(schemeFor(ap))) applyProfileTheme({ ...state.profile.theme, dark: ap.dark, light: ap.light, mode: ap.mode });
          renderWarnings();
          renderPreview();
          save({ [key]: val }, editing);
        });
        const custom = pal.custom && pal.custom[key];
        return h('label.color-row', h('span.grow', label, SHARED.includes(key) ? h('span.muted.small', ' · both themes') : null, custom ? h('span.muted.small', ' · custom') : null), code, input);
      }));
    };

    const renderWarnings = () => {
      const w = contrastWarnings(ap[editing].colors);
      put(warnings, w.length ? h('div.banner', icon('warn'), h('div', h('div', { style: { fontWeight: 600 } }, 'Low contrast'), w.map((x) => h('div.small', x)))) :
        h('div.banner.info', icon('check'), h('span', 'Contrast looks good.')));
    };

    const renderPreview = () => {
      const c = ap[editing].colors;
      const vars = Object.fromEntries(Object.entries(c).map(([k, v]) => [`--${k}`, v]));
      put(preview, h('div.theme-preview', { style: vars, 'aria-label': 'Preview' },
        h('div.row', h('div.pv-thumb', h('div.pv-bar')),
          h('div.grow', h('div', { style: { fontWeight: 600, color: 'var(--text)' } }, 'Sourdough from scratch'), h('div.small', { style: { color: 'var(--muted)' } }, 'Crumb & Crust · 12:04'),
            h('div.progress', { style: { marginTop: '8px' } }, h('div', { style: { width: '62%' } })))),
        h('div.row', { style: { marginTop: '12px' } }, h('span.btn.primary', 'Download'), h('span.btn', 'Cancel'))));
    };

    const renderAll = () => { renderMode(); renderPresets(); renderEdit(); renderPickers(); renderWarnings(); renderPreview(); };
    renderAll();

    put(body, 
      h('p.muted.small', `Colours for ${state.profile.name} only. Each profile has its own theme.`),
      h('div.section-title.first', icon('eye'), 'Dark mode'), modeEl,
      presetsEl,
      editEl, preview, h('div', { style: { height: '12px' } }), pickers,
      h('div', { style: { height: '12px' } }), warnings,
      h('div', { style: { height: '12px' } }),
      h('button.btn.block', { onclick: async () => {
        try {
          remember(await api.setAppearance({ preset: ap[editing].preset, mode: ap.mode }));
          renderAll();
          toast('Back to preset colours', 2200, 'check');
        } catch (e) { toast(e.message); }
      } }, icon('refresh'), 'Reset custom colours'));
  },
};
