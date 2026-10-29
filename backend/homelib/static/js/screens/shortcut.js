// How to build the iOS "Send to Home Videos" share-sheet shortcut.
// Instructions are filled in with this server's address and the current profile.
import { h, icon, toast } from '../ui.js';
import { screen, topbar, nav } from '../layout.js';
import { state } from '../state.js';

export const shortcutScreen = {
  async render(root) {
    nav('settings');
    const real = location.protocol.startsWith('http');
    const endpoint = real ? `${location.origin}/api/share` : 'https://YOUR_SERVER_HOSTNAME.YOUR_TAILNET.ts.net/api/share';
    const name = state.profile.name;

    const copy = (text) => h('button.chip', {
      onclick: async () => {
        try { await navigator.clipboard.writeText(text); toast('Copied'); } catch { toast('Long-press the text to copy it'); }
      },
    }, 'Copy');
    const code = (text) => h('div.row', { style: { marginTop: '6px' } },
      h('code.grow', { style: { wordBreak: 'break-all', background: 'var(--bg)', padding: '8px 10px', borderRadius: '8px', fontSize: '0.765rem' } }, text),
      copy(text));
    const step = (n, title, ...body) => h('div.item', { style: { alignItems: 'flex-start' } },
      h('span.lead', { style: { fontWeight: 700 } }, String(n)),
      h('div.grow', h('div', { style: { fontWeight: 600 } }, title), ...body.map((b) => (typeof b === 'string' ? h('div.muted.small', { style: { marginTop: '4px' } }, b) : b))));

    root.append(screen(
      topbar('Share shortcut', { backTo: '#/settings' }),
      h('p.muted.small', `Send a video or song from the YouTube app straight to the server: Share → “Send to Home Videos”. You get a notification; nothing opens. Set it up once on each iPhone (about 3 minutes), signed in as ${name}.`),
      !real ? h('div.banner', icon('info'), h('span', 'Preview: on the real server this shows your own address.')) : null,

      h('div.section-title', 'Build it in the Shortcuts app'),
      h('div.group',
        step(1, 'New shortcut', 'Open Shortcuts → + . Tap the name at the top and call it “Send to Home Videos”.'),
        step(2, 'Accept shared links', 'Tap the (i) info button → turn on “Show in Share Sheet”. Back in the editor, tap “Receive Any input” and pick only URLs and Text. Set “If there’s no input” to “Get Clipboard”, so it also works when you run it with a copied link.'),
        step(3, 'Add “Choose from Menu”', 'Prompt: “Send as…”. Make three items, and in each one add a Text action with exactly:',
          h('div.small', { style: { marginTop: '6px' } },
            h('div', 'Video – My queue → ', h('code', 'video-personal')),
            h('div', 'Video – Shared → ', h('code', 'video-shared')),
            h('div', 'Music (MP3) → ', h('code', 'mp3')))),
        step(4, 'Add “Get Contents of URL” (after End Menu)', 'URL:', code(endpoint),
          'Tap “Show More”: Method POST, Request Body JSON, and add three Text fields:',
          h('div.small', { style: { marginTop: '6px' } },
            h('div', h('code', 'url'), ' → the “Shortcut Input” variable'),
            h('div', h('code', 'profile'), ' → ', h('code', name)),
            h('div', h('code', 'action'), ' → the “Menu Result” variable'))),
        step(5, 'Add “Get Dictionary Value”', h('div.muted.small', { style: { marginTop: '4px' } }, 'Get Value for key ', h('code', 'message'), ' in “Contents of URL”.')),
        step(6, 'Add “Show Notification”', 'Text: the “Dictionary Value” variable. Done.')),

      h('div.section-title', 'Use it'),
      h('div.group', h('div.block.small',
        h('p', { style: { marginTop: 0 } }, 'In the YouTube or YouTube Music app: Share → More (…) → “Send to Home Videos” → pick where it goes. Tip: Edit Actions in the share sheet to add it to your favourites.'),
        h('p.muted', { style: { marginBottom: 0 } }, 'Tailscale must be connected on the phone. The download uses your default quality (Settings). The other person builds their own copy with their name in “profile”.'))),
    ));
  },
};
