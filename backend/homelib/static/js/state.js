// App-wide state: profiles and the active profile.
import { api, currentProfileId, setCurrentProfileId } from './api.js';
import { applyProfileTheme } from './theme.js';

export const state = { profiles: [], profile: null, settings: null };

export async function loadProfiles() {
  state.profiles = await api.profiles();
  const id = currentProfileId();
  state.profile = state.profiles.find((p) => p.id === id) || null;
  if (state.profile) applyProfileTheme(state.profile.theme);
  return state.profiles;
}

export async function selectProfile(id) {
  const p = await api.selectProfile(id);
  setCurrentProfileId(id);
  state.profile = { ...state.profiles.find((x) => x.id === id), ...p };
  state.profiles = state.profiles.map((x) => (x.id === id ? state.profile : x));
  applyProfileTheme(state.profile.theme);
  return state.profile;
}

export function otherProfiles() {
  return state.profiles.filter((p) => !state.profile || p.id !== state.profile.id);
}

export function profileById(id) {
  return state.profiles.find((p) => p.id === Number(id));
}
