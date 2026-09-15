// Per-browser console preferences (sidebar width, last inspector panel, theme). Never Run state
// and never a credential: the operator's token stays in sessionStorage, written only by api.js.
// Storage can be unavailable (private windows, blocked site data), so every access is guarded and
// the caller's fallback is returned instead of throwing.

const KEY = "thymira.console.prefs";

function readAll() {
  try {
    const raw = localStorage.getItem(KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function readPref(key, fallback = null) {
  const value = readAll()[key];
  return value === undefined ? fallback : value;
}

export function writePref(key, value) {
  try {
    const all = readAll();
    if (value === null || value === undefined) delete all[key];
    else all[key] = value;
    localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    // The preference lasts only as long as this page.
  }
}
