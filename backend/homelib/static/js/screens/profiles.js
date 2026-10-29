import { h, icon, avatar } from '../ui.js';
import { state, loadProfiles, selectProfile } from '../state.js';
import { navigate } from '../router.js';
import { nav } from '../layout.js';

export const profilesScreen = {
  async render(root) {
    nav(null);
    await loadProfiles();
    root.append(h('main.profiles',
      h('div.logo', icon('logo'), 'Home Video Library'),
      h('h1', "Who's watching?"),
      h('div.who', state.profiles.map((p) => h('button', {
        class: state.profile && state.profile.id === p.id ? 'current' : '',
        style: { '--a': p.accent },
        onclick: async () => { await selectProfile(p.id); navigate('#/foryou'); },
      }, avatar(p), h('span', p.name)))),
      h('p.muted.small', 'Tap to switch. No passwords: this only works on your private network.'),
    ));
  },
};
