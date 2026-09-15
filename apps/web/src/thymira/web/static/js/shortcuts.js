// Keyboard bindings for the shell. Nothing here mutates a Run: a binding only moves the console.
// While the operator is typing, only the bindings that opt in (Ctrl+K, Escape) fire, so a prompt
// can contain any character without triggering a view change.

const TYPING_SELECTOR = "input, textarea, select, [contenteditable=''], [contenteditable='true']";

export function isTyping(target) {
  return Boolean(target?.matches?.(TYPING_SELECTOR));
}

// "ctrl+k" matches Ctrl or Meta (macOS Command); "?" is whatever key produces it on this layout.
function parse(keys) {
  const parts = String(keys ?? "").toLowerCase().split("+").filter(Boolean);
  const key = parts.pop() ?? "";
  return { key, ctrl: parts.includes("ctrl"), shift: parts.includes("shift"), alt: parts.includes("alt") };
}

function matches(binding, event) {
  const key = String(event.key ?? "").toLowerCase();
  if (key !== binding.key) return false;
  if (binding.ctrl !== (event.ctrlKey || event.metaKey)) return false;
  if (binding.alt !== event.altKey) return false;
  // Shift is only required when the binding asks for it: "?" already implies it on most layouts.
  return !binding.shift || event.shiftKey;
}

export function installShortcuts(bindings) {
  const parsed = bindings.map((binding) => ({ ...binding, combo: parse(binding.keys) }));
  const onKeyDown = (event) => {
    if (event.defaultPrevented || event.isComposing) return;
    const typing = isTyping(event.target);
    for (const binding of parsed) {
      if (typing && !binding.whenTyping) continue;
      if (!matches(binding.combo, event)) continue;
      event.preventDefault();
      binding.run(event);
      return;
    }
  };
  window.addEventListener("keydown", onKeyDown);
  return () => window.removeEventListener("keydown", onKeyDown);
}
