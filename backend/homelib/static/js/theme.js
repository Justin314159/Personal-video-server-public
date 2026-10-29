// Applying per-profile colours and checking contrast (WCAG 2.x).

export const THEME_FIELDS = [
  ['bg', 'Background'],
  ['surface', 'Cards & sheets'],
  ['border', 'Borders'],
  ['text', 'Text'],
  ['muted', 'Secondary text'],
  ['accent', 'Accent'],
  ['progress', 'Progress bar'],
];

export function applyTheme(colors) {
  if (!colors) return;
  const root = document.documentElement.style;
  for (const [k] of THEME_FIELDS) if (colors[k]) root.setProperty(`--${k}`, colors[k]);
  if (colors.accent) root.setProperty('--on-accent', contrast(colors.accent, '#0F1115') >= 4.5 ? '#0F1115' : '#FFFFFF');
  const light = luminance(colors.bg || '#000000') > 0.4;
  document.documentElement.style.colorScheme = light ? 'light' : 'dark';
  document.documentElement.dataset.scheme = light ? 'light' : 'dark';
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta && colors.bg) meta.setAttribute('content', colors.bg);
}

// ---- dark mode -----------------------------------------------------------------
// A profile theme has a dark and a light palette plus a mode: 'dark', 'light'
// or 'auto' (follow the iPhone's Light/Dark setting, live).
const systemLight = window.matchMedia ? window.matchMedia('(prefers-color-scheme: light)') : { matches: false };
let activeTheme = null;

export function schemeFor(theme) {
  if (!theme || !theme.dark) return 'dark';
  if (theme.mode === 'auto') return systemLight.matches ? 'light' : 'dark';
  return theme.mode === 'light' ? 'light' : 'dark';
}

export function paletteFor(theme) {
  if (!theme) return null;
  return theme.dark ? theme[schemeFor(theme)].colors : theme.colors;
}

export function applyProfileTheme(theme) {
  activeTheme = theme;
  applyTheme(paletteFor(theme));
}

if (systemLight.addEventListener) {
  systemLight.addEventListener('change', () => {
    if (activeTheme && activeTheme.mode === 'auto') applyTheme(paletteFor(activeTheme));
  });
}

function channel(c) {
  c /= 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return 0;
  const n = parseInt(m[1], 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

export function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

// Returns human-readable warnings for low-contrast combinations.
export function contrastWarnings(c) {
  const out = [];
  const check = (fg, bg, min, label) => {
    const r = contrast(c[fg], c[bg]);
    if (r < min) out.push(`${label}: ${r.toFixed(1)}:1 (aim for ${min}:1)`);
  };
  check('text', 'bg', 4.5, 'Text on background');
  check('text', 'surface', 4.5, 'Text on cards');
  check('muted', 'bg', 3, 'Secondary text on background');
  check('accent', 'bg', 3, 'Accent on background');
  check('progress', 'surface', 2, 'Progress bar on cards');
  return out;
}
