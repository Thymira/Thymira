// The console's appearance setting. "system" leaves the choice to prefers-color-scheme; the two
// explicit themes stamp data-theme on <html>, which is what css/tokens.css keys its palette on.

import { readPref, writePref } from "./prefs.js";

export const THEMES = ["system", "light", "dark"];

export function currentTheme() {
  const name = readPref("theme", "system");
  return THEMES.includes(name) ? name : "system";
}

export function applyTheme(name) {
  const theme = THEMES.includes(name) ? name : "system";
  document.documentElement.dataset.theme = theme;
  writePref("theme", theme);
  return theme;
}

export function cycleTheme() {
  const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
  return applyTheme(next);
}

// The icon that names the *current* setting in the footer and the settings page.
export function themeIcon(name = currentTheme()) {
  if (name === "light") return "sun";
  if (name === "dark") return "moon";
  return "monitor";
}
