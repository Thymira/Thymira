// Transient confirmations in the shell's aria-live region. A toast reports what the console just
// did; it never carries authority and never replaces the inline error a failed action shows.

import { h } from "./dom.js";

const HOST_ID = "toasts";
const LEAVE_MS = 200;
const DEFAULT_MS = 3500;

function host() {
  return document.getElementById(HOST_ID);
}

export function toast(text, { tone = "neutral", duration = DEFAULT_MS } = {}) {
  const container = host();
  if (!container) return null;
  const node = h("div", { class: `toast tone-${tone}`, role: "status" }, text);
  let timer = null;
  const dismiss = () => {
    clearTimeout(timer);
    if (!node.isConnected) return;
    node.dataset.leaving = "true";
    setTimeout(() => node.remove(), LEAVE_MS);
  };
  node.addEventListener("click", dismiss);
  container.append(node);
  timer = setTimeout(dismiss, duration);
  return node;
}
