// Settings: model routing, appearance and the connection to the API.
//
// Model routing is live: which model THY, MIRA and each sub-agent tier actually call. It is backed
// by the durable settings API's "models" namespace (compare-and-set, one mutable user layer). A
// saved value reaches the very next routed model call in the running API process -- no restart --
// but never bypasses that run's own frozen model-route allowlist: a model this deployment does not
// permit is still refused the moment it is used, exactly as an environment-configured one would be.
//
// Appearance and the token are per browser: the theme is a preference, the token lives in this tab
// only. Neither is Run state and neither reaches the API.

import { api } from "../api.js";
import { h, replace } from "../dom.js";
import { icon } from "../icons.js";
import { THEMES, applyTheme, currentTheme } from "../theme.js";
import { button, errorNotice, loading } from "../ui.js";

const NAMESPACE = "models";

const FIELDS = [
  ["THYMIRA_THY_MODEL", "THY (orchestrator)", "e.g. anthropic/claude-opus-5"],
  ["THYMIRA_MIRA_MODEL", "MIRA (auditor)", "e.g. anthropic/claude-opus-5"],
  ["THYMIRA_MODEL_FRONTIER", "Frontier tier", "e.g. anthropic/claude-opus-5"],
  ["THYMIRA_MODEL_STANDARD", "Standard tier", "e.g. anthropic/claude-sonnet-5"],
  ["THYMIRA_MODEL_FAST", "Fast tier", "e.g. anthropic/claude-haiku-4-5-20251001"],
];

// Common LiteLLM-routed ids (`thymira.agents.llm.litellm_provider`, "anthropic/<model>"). The API
// exposes no canonical model list, so this suggests rather than restricts: a datalist keeps the
// field free-text, since a deployment may legitimately route to another provider or a newer id.
const MODEL_SUGGESTIONS = [
  "anthropic/claude-opus-5",
  "anthropic/claude-sonnet-5",
  "anthropic/claude-haiku-4-5-20251001",
  "anthropic/claude-fable-5-1",
];
const MODEL_LIST_ID = "model-suggestions";

const THEME_LABELS = { system: "System", light: "Light", dark: "Dark" };

async function readSnapshot() {
  try {
    return await api.getSettings(NAMESPACE);
  } catch (error) {
    if (error?.status === 404) return { namespace: NAMESPACE, revision: 0, values: {} };
    throw error;
  }
}

function section(title, description, body) {
  return h(
    "section",
    { class: "settings-section" },
    h("h2", null, title),
    description ? h("p", { class: "lede" }, description) : null,
    body,
  );
}

export class SettingsView {
  constructor(root, { onConnect, onDisconnect, hasToken = () => false } = {}) {
    this.onConnect = onConnect;
    this.onDisconnect = onDisconnect;
    this.hasToken = hasToken;
    this.body = h("div", { class: "stack-tight" }, loading("Reading the live model configuration…"));
    this.themePills = THEMES.map((name) =>
      h(
        "button",
        {
          class: "pill",
          type: "button",
          "aria-pressed": name === currentTheme() ? "true" : "false",
          onClick: () => {
            applyTheme(name);
            this.syncTheme();
          },
        },
        THEME_LABELS[name] ?? name,
      ),
    );
    this.connectionState = h("p", { class: "lede" });
    this.connectButton = button("Connect", { kind: "primary", onClick: () => this.onConnect?.() });
    this.disconnectButton = button("Disconnect", { onClick: () => this.onDisconnect?.() });

    replace(
      root,
      h(
        "section",
        { class: "settings" },
        h("p", { class: "eyebrow" }, "Thymira console"),
        h("h1", null, "Settings"),
        section(
          "Model routing",
          "Which model THY, MIRA and each sub-agent tier call for the running API process. Leave a field blank to fall back to the server's own environment configuration; a saved value takes effect on the next routed call, with no restart.",
          this.body,
        ),
        section(
          "Appearance",
          "System follows the operating system. Motion follows it too: with reduced motion on, every animation here stops.",
          h("div", { class: "filter-pills", role: "group", "aria-label": "Theme" }, this.themePills),
        ),
        section(
          "Connection",
          null,
          h(
            "div",
            { class: "stack-tight" },
            this.connectionState,
            h("div", { class: "form-actions" }, this.connectButton, this.disconnectButton),
          ),
        ),
      ),
    );
    this.syncConnection();
    this.load();
  }

  syncTheme() {
    const current = currentTheme();
    this.themePills.forEach((pill, index) => {
      pill.setAttribute("aria-pressed", THEMES[index] === current ? "true" : "false");
    });
  }

  syncConnection() {
    const connected = this.hasToken();
    replace(
      this.connectionState,
      icon(connected ? "check" : "alert", { size: 14 }),
      connected
        ? " This tab holds an API token. It is kept in sessionStorage for this tab only and is gone when the tab closes."
        : " This tab holds no API token. The API prints where it wrote its token when it starts.",
    );
    this.connectButton.disabled = connected;
    this.disconnectButton.disabled = !connected;
  }

  async load() {
    try {
      replace(this.body, this.form(await readSnapshot()));
    } catch (error) {
      replace(this.body, errorNotice(error));
    }
  }

  form(snapshot, message = "") {
    const inputs = new Map(
      FIELDS.map(([key, label, placeholder]) => [
        key,
        h("input", {
          class: "input",
          type: "text",
          list: MODEL_LIST_ID,
          value: snapshot.values[key] ?? "",
          placeholder,
          autocomplete: "off",
          spellcheck: "false",
          "aria-label": label,
        }),
      ]),
    );
    const status = h("span", { class: "form-status", role: "status" }, message);
    const save = button("Save", {
      kind: "primary",
      onClick: async () => {
        const values = {};
        for (const [key, input] of inputs) {
          const value = input.value.trim();
          if (value) values[key] = value;
        }
        save.disabled = true;
        status.textContent = "Saving…";
        try {
          const next = await api.putSettings(NAMESPACE, values, snapshot.revision);
          replace(this.body, this.form(next, "Saved. The next routed model call uses this."));
        } catch (error) {
          save.disabled = false;
          status.textContent =
            error?.code === "settings_conflict"
              ? "Someone else changed this since you opened it. Reload the page to see the current values."
              : `${error.code}: ${error.message}`;
        }
      },
    });
    return h(
      "div",
      { class: "stack-tight" },
      h("datalist", { id: MODEL_LIST_ID }, MODEL_SUGGESTIONS.map((model) => h("option", { value: model }))),
      FIELDS.map(([key, label]) => h("label", { class: "field" }, h("span", { class: "field-label" }, label), inputs.get(key))),
      h("div", { class: "form-actions" }, status, save),
    );
  }

  dispose() {}
}
