import { api } from "../api.js";
import { h, replace } from "../dom.js";
import { headline, shortId } from "../format.js";
import { activeTurnLabel, isUncertainSendError, newClientMessageId, runSectionForTurn, sessionTimelineEntries, sessionTitle, turnBadge, turnStateLabel } from "../session-model.js";
import { TERMINAL_STATUSES, statusTone } from "../status.js";
import { errorNotice, loading, notice, tag } from "../ui.js";
import { ArtifactInspectorView } from "./artifact-inspector.js";
import { ProjectExplorerView } from "./explorer.js";
import { renderProjectDatasets } from "./inputs.js";
import { SessionDatasetStatus } from "./session-datasets.js";
import { SessionReviewView } from "./session-review.js";
import { SessionInterviewView } from "./session-interview.js";
import { SessionRuntimeStatus, terminalDiagnostic } from "./session-diagnostics.js";
import { SessionPlotsView } from "./session-plots.js";
import { conversationOutput } from "./conversation-output.js";

const MAX_MESSAGE_LENGTH = 16000;
const POLL_MS = 1500;
const PENDING_PREFIX = "thymira.console.pending-message.";

function pendingStorageKey(sessionId) {
  return `${PENDING_PREFIX}${sessionId}`;
}

function turnIsTerminal(turn) {
  if (turn?.state) return turn.state.condition === "terminal";
  return TERMINAL_STATUSES.has(String(turn?.run?.status ?? "").toUpperCase());
}

function readPending(sessionId) {
  try {
    const value = sessionStorage.getItem(pendingStorageKey(sessionId));
    if (!value) return null;
    const parsed = JSON.parse(value);
    if (typeof parsed?.message !== "string" || typeof parsed?.clientMessageId !== "string") return null;
    return { ...parsed, mode: parsed.mode ?? "auto", uncertain: true };
  } catch {
    return null;
  }
}

function writePending(sessionId, pending) {
  try {
    sessionStorage.setItem(pendingStorageKey(sessionId), JSON.stringify(pending));
  } catch {
    // The live view still retains the id when tab storage is unavailable.
  }
}

function clearPending(sessionId) {
  try {
    sessionStorage.removeItem(pendingStorageKey(sessionId));
  } catch {
    // Nothing persisted.
  }
}

function linkToRun(runId, turn) {
  const tab = runSectionForTurn(turn);
  return `#/runs/${encodeURIComponent(runId)}/${tab}`;
}

function turnTone(turn) {
  const state = turn?.state;
  if (state) {
    if (state.condition === "waiting") return state.wait_reason === "approval" ? "review" : "warn";
    if (state.condition === "terminal") {
      return state.outcome === "completed" ? "ok" : state.outcome === "cancelled" ? "neutral" : "block";
    }
    if (state.condition === "paused") return "warn";
    return "active";
  }
  return statusTone(turn?.run?.status);
}

function turnNodes(turn, activeRunId, sessionId, onDiscuss, plots) {
  const runId = turn.id;
  const completed = turn.state?.condition === "terminal" && turn.state?.outcome === "completed";
  const response = completed && typeof turn.assistant_message === "string"
    ? h("section", { class: "conversation-assistant" },
      h("div", { class: "conversation-speaker" }, "THYMIRA"),
      conversationOutput(turn.assistant_message),
    )
    : turnIsTerminal(turn)
      ? h("p", { class: "conversation-response-unavailable muted" }, "No approved response is available for this run. Open the run to inspect its status and evidence.")
      : null;
  const activeNotice = activeRunId === runId
      ? h("p", { class: "conversation-active", role: "status" }, turnStateLabel(turn))
    : null;
  return h(
    "article",
    { class: "conversation-turn", dataset: { runId } },
    h("header", { class: "conversation-turn-head" },
      h("span", { class: "conversation-speaker" }, "You"),
      h("span", { class: "conversation-turn-index" }, `Work request ${turn.ordinal}`),
      h("span", { class: `tag tone-${turnTone(turn)}` }, turnBadge(turn)),
      h("span", { class: "conversation-turn-actions" },
        h("a", { class: "text-link", href: linkToRun(runId, turn) }, "Open run"),
        h("a", { class: "text-link", href: `#/sessions/${encodeURIComponent(sessionId)}/artifacts/${encodeURIComponent(runId)}` }, "Artifacts"),
      ),
    ),
    h("pre", { class: "conversation-message conversation-message-user" }, turn.user_message ?? ""),
    response,
    plots,
    terminalDiagnostic(turn, onDiscuss),
    activeNotice,
  );
}

function runEventsHref(runId) {
  return `#/runs/${encodeURIComponent(runId)}/events`;
}

function projectCitationTarget(sessionId, citation) {
  const path = String(citation?.path ?? "");
  const projectRoot = path === "project";
  if (!projectRoot && (!path.startsWith("project/") || path.slice("project/".length).split("/").some((part) => !part || part === "." || part === ".."))) return null;
  const directory = projectRoot || String(citation?.label ?? "").startsWith("Directory: ");
  return `#/sessions/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(path)}${directory ? "?directory=1" : ""}`;
}

function projectPathFromHash(sessionId) {
  const parts = String(window.location.hash ?? "").replace(/^#\/?/, "").split("/");
  if (parts[0] !== "sessions" || parts[1] !== encodeURIComponent(sessionId) || parts[2] !== "files" || !parts[3]) return null;
  const queryAt = parts[3].indexOf("?");
  const encodedPath = queryAt < 0 ? parts[3] : parts[3].slice(0, queryAt);
  let path;
  try { path = decodeURIComponent(encodedPath); } catch { return null; }
  if (path === "project") return { path: "", directory: true };
  if (!path.startsWith("project/")) return null;
  const relativePath = path.slice("project/".length);
  if (!relativePath || relativePath.split("/").some((part) => !part || part === "." || part === "..")) return null;
  return { path: relativePath, directory: queryAt >= 0 && parts[3].slice(queryAt + 1) === "directory=1" };
}

function discussionNodes(discussion, { sessionId, onResend, onRetryPending, activeRun, retrying, retryError }) {
  const kind = discussion?.kind ?? "answer";
  const status = discussion?.status ?? "failed";
  const label = kind === "action" ? "Work request" : kind === "clarification" ? "Session clarification" : "Session question";
  const response = status === "completed" && typeof discussion?.assistant_message === "string"
    ? h("section", { class: "conversation-assistant" },
      h("div", { class: "conversation-speaker" }, kind === "answer" ? "Explanation of recorded evidence" : "THYMIRA"),
      conversationOutput(discussion.assistant_message),
      kind === "action" ? null : h("p", { class: "conversation-evidence-note" },
        kind === "answer"
          ? "This explains evidence already recorded in the session. It does not create a new result, approval, or audit."
          : "This clarification refers to recorded session evidence. It does not create a new result, approval, or audit."),
      Array.isArray(discussion?.citations) && discussion.citations.length
        ? h("nav", { class: "conversation-citations", "aria-label": "Cited session evidence" },
          h("span", { class: "muted small" }, "Recorded evidence:"),
          discussion.citations.map((citation) => citation?.run_id
            ? h("a", { class: "text-link", href: runEventsHref(citation.run_id) },
              `${citation.label || "Run events"}${Number.isInteger(citation.seq) ? ` · event ${citation.seq}` : ""}`)
            : projectCitationTarget(sessionId, citation)
              ? h("a", { class: "text-link", href: projectCitationTarget(sessionId, citation) },
                citation.label || citation.path)
              : null),
        )
        : null,
    )
    : status === "pending"
      ? h("div", { class: "conversation-discussion-failure" },
        h("p", { class: "conversation-discussion-status", role: "status" }, retrying
          ? "Retrying this saved request…"
          : "This request is still pending. No answer has been recorded yet. Retry it to continue from the same saved request."),
        retryError ? notice(`The saved request could not be retried: ${retryError}`, "warn") : null,
        h("button", {
          class: "btn btn-small",
          type: "button",
          disabled: retrying || (activeRun && kind === "action"),
          onClick: () => onRetryPending(discussion),
        }, "Retry pending request"),
      )
      : status === "failed"
        ? h("div", { class: "conversation-discussion-failure" },
          h("p", { class: "conversation-discussion-status", role: "status" }, `This request failed: ${discussion.error || "The service did not produce a response."}`),
          h("button", {
            class: "btn btn-small",
            type: "button",
            disabled: activeRun && kind === "action",
            onClick: () => onResend(discussion),
          }, "Resend as new message"),
        )
        : h("p", { class: "conversation-discussion-status", role: "status" }, "No response was recorded for this request.");
  const runLink = discussion?.run_id
    ? h("a", { class: "text-link", href: runEventsHref(discussion.run_id) }, "Open run events")
    : null;
  return h(
    "article",
    { class: ["conversation-turn", "conversation-discussion"], dataset: { discussionId: discussion?.id ?? "" } },
    h("header", { class: "conversation-turn-head" },
      h("span", { class: "conversation-speaker" }, "You"),
      h("span", { class: "conversation-turn-index" }, label),
      h("span", { class: "tag tone-neutral" }, status.toUpperCase()),
      runLink,
    ),
    h("pre", { class: "conversation-message conversation-message-user" }, discussion?.user_message ?? ""),
    response,
  );
}

export class SessionView {
  constructor(mainRoot, inspectorRoot, sessionId, { onSessionChanged } = {}) {
    this.mainRoot = mainRoot;
    this.inspectorRoot = inspectorRoot;
    this.sessionId = sessionId;
    this.onSessionChanged = onSessionChanged;
    this.detail = null;
    this.detailLoaded = false;
    this.refreshStale = false;
    this.loadGeneration = 0;
    this.disposed = false;
    this.controller = new AbortController();
    this.pollTimer = null;
    this.posting = false;
    this.sendError = null;
    this.pending = readPending(sessionId);
    this.panel = null;
    this.panelRunId = null;
    this.panelFileTarget = null;
    this.panelView = null;
    this.lastTimelineKey = null;
    this.retryingDiscussionId = null;
    this.discussionRetryErrors = new Map();
    this.plotViews = new Map();

    this.title = h("h1", { class: "conversation-title" }, loading("Loading session…"));
    this.sessionIdNode = h("span", { class: "conversation-id mono" }, shortId(sessionId));
    this.runStatus = h("span", { class: "conversation-status", role: "status" });
    this.transcript = h("div", { class: "conversation-transcript", "aria-live": "off" });
    this.reviewRoot = h("div", { class: "conversation-review" });
    this.interviewRoot = h("div", { class: "conversation-interview" });
    this.timeline = h("div", { class: "conversation-timeline" }, this.transcript, this.interviewRoot, this.reviewRoot);
    this.interviewView = new SessionInterviewView(this.interviewRoot, {
      onUpdated: () => this.load({ silent: true }),
    });
    this.reviewView = new SessionReviewView(this.reviewRoot, {
      onResolved: () => this.load({ silent: true }),
      onClarifyRisk: (review) => this.interviewView.openClarification(review),
    });
    this.modeField = h("select", {
      class: "input input-small conversation-mode",
      "aria-label": "Message mode",
      onChange: () => this.onModeChange(),
    },
    h("option", { value: "auto" }, "Auto"),
    h("option", { value: "ask" }, "Ask about session"),
    h("option", { value: "action" }, "Request work"));
    this.modeField.value = this.pending?.mode ?? "auto";
    this.modeHelp = h("p", { class: "conversation-mode-help" });
    this.messageField = h("textarea", {
      class: "input conversation-input",
      rows: 3,
      maxlength: MAX_MESSAGE_LENGTH,
      placeholder: "Describe the data-science work or follow up on this conversation…",
      "aria-label": "Message this session",
      onInput: () => this.onDraftChange(),
      onKeydown: (event) => this.onKeydown(event),
    });
    this.sendButton = h("button", { class: "btn btn-primary", type: "submit" }, "Send");
    this.composerStatus = h("span", { class: "conversation-composer-status", role: "status" });
    this.retryButton = h("button", {
      class: "btn btn-small",
      type: "button",
      hidden: true,
      onClick: () => this.send(),
    }, "Retry same message");
    this.composer = h(
      "form",
      { class: "conversation-composer", onSubmit: (event) => this.onSubmit(event) },
      h("div", { class: "conversation-mode-row" },
        h("label", { class: "field-label", for: "session-mode" }, "Mode"),
        this.modeField,
        this.modeHelp,
      ),
      h("label", { class: "field-label", for: "session-message" }, "Message"),
      this.messageField,
      h("div", { class: "conversation-composer-actions" },
        this.composerStatus,
        h("span", { class: "muted small" }, "Enter to send · Shift+Enter for a new line"),
        this.retryButton,
        this.sendButton,
      ),
    );
    this.modeField.id = "session-mode";
    this.messageField.id = "session-message";
    this.datasetRoot = h("div", { class: "conversation-datasets" });
    this.composer.append(this.datasetRoot);
    this.datasetStatus = new SessionDatasetStatus(this.datasetRoot, {
      onChange: () => this.renderComposerState(),
      onManage: () => { window.location.hash = `#/sessions/${encodeURIComponent(this.sessionId)}/datasets`; },
    });
    this.runtimeRoot = h("div", { class: "conversation-runtime" });
    this.composer.append(this.runtimeRoot);
    this.runtimeStatus = new SessionRuntimeStatus(this.runtimeRoot);
    if (this.pending) this.messageField.value = this.pending.message;
    replace(
      mainRoot,
      h("section", { class: "conversation-page" },
        h("header", { class: "conversation-header" },
          h("div", { class: "conversation-title-block" },
            h("p", { class: "eyebrow" }, "Session"),
            this.title,
            h("span", { class: "conversation-header-meta" }, this.sessionIdNode, this.runStatus),
          ),
          h("div", { class: "conversation-header-actions" },
            h("button", { class: "btn btn-small", onClick: () => this.load({ silent: true }) }, "Refresh session"),
            h("a", { class: "btn btn-small", href: "#/runs" }, "Runs"),
            h("a", { class: "btn btn-small", href: "#/project/files" }, "Project files"),
          ),
        ),
        this.timeline,
        this.composer,
      ),
    );
    this.messageField.readOnly = Boolean(this.pending?.uncertain);
    this.renderComposerState();
    this.setInspector("files");
    this.load();
  }

  dispose() {
    this.disposed = true;
    this.controller.abort();
    clearTimeout(this.pollTimer);
    this.panelView?.dispose?.();
    this.datasetStatus.dispose();
    this.runtimeStatus.dispose();
    this.reviewView.dispose();
    this.interviewView.dispose();
    for (const view of this.plotViews.values()) view.dispose();
  }

  updateRoute(panel, runId) {
    this.setInspector(panel, runId);
  }

  setInspector(panel = "files", runId = null, fileTarget = projectPathFromHash(this.sessionId)) {
    const nextPanel = ["artifacts", "datasets"].includes(panel) ? panel : "files";
    const resolvedRunId = nextPanel === "artifacts" ? runId ?? this.latestRunId() : null;
    const resolvedFileTarget = nextPanel === "files" ? fileTarget : null;
    const fileKey = resolvedFileTarget ? `${resolvedFileTarget.directory ? "dir" : "file"}:${resolvedFileTarget.path}` : null;
    if (this.panel === nextPanel && this.panelRunId === resolvedRunId && this.panelFileTarget === fileKey) return;
    this.panelView?.dispose?.();
    this.panelView = null;
    this.panel = nextPanel;
    this.panelRunId = resolvedRunId;
    this.panelFileTarget = fileKey;
    this.inspectorRoot.hidden = false;
    const panelBody = h("div", { class: "inspector-body" });
    const artifactsRunId = nextPanel === "artifacts" ? resolvedRunId : this.latestRunId();
    const artifactsHref = artifactsRunId
      ? `#/sessions/${encodeURIComponent(this.sessionId)}/artifacts/${encodeURIComponent(artifactsRunId)}`
      : null;
    replace(
      this.inspectorRoot,
      h("section", { class: "session-inspector" },
        h("header", { class: "inspector-head" },
          h("span", { class: "inspector-title" }, { files: "Explorer", datasets: "Datasets", artifacts: "Artifacts" }[nextPanel]),
          h("nav", { class: "inspector-nav", "aria-label": "Inspector sections" },
            h("a", { class: ["inspector-tab", nextPanel === "files" ? "is-active" : null], href: `#/sessions/${encodeURIComponent(this.sessionId)}/files` }, "Files"),
            h("a", { class: ["inspector-tab", nextPanel === "datasets" ? "is-active" : null], href: `#/sessions/${encodeURIComponent(this.sessionId)}/datasets` }, "Datasets"),
            artifactsHref ? h("a", { class: ["inspector-tab", nextPanel === "artifacts" ? "is-active" : null], href: artifactsHref }, "Artifacts") : null,
          ),
        ),
        panelBody,
      ),
    );
    if (nextPanel === "files") {
      this.panelView = new ProjectExplorerView(
        panelBody,
        { initialPath: resolvedFileTarget?.directory ? resolvedFileTarget.path : "" },
      );
      if (resolvedFileTarget && !resolvedFileTarget.directory) {
        this.panelView.openFile(resolvedFileTarget.path);
      }
    }
    else if (nextPanel === "datasets") replace(panelBody, renderProjectDatasets({
      onDatasetsChanged: () => this.datasetStatus.refresh(),
      onUploadStateChanged: (busy) => this.datasetStatus.mutationPending(busy),
      onUseDataset: (dataset) => {
        if (this.disposed || this.messageField.disabled || this.messageField.readOnly) return;
        this.messageField.value = `${this.messageField.value}${this.messageField.value ? " " : ""}${dataset.name}`;
        this.onDraftChange();
        this.messageField.focus?.();
      },
    }));
    else if (resolvedRunId) this.panelView = new ArtifactInspectorView(panelBody, resolvedRunId);
    else replace(panelBody, h("p", { class: "empty" }, "A run has not produced artifacts yet."));
  }

  latestRunId() {
    return this.detail?.turns?.at(-1)?.id ?? null;
  }

  async load({ silent = false } = {}) {
    if (this.disposed) return;
    const generation = ++this.loadGeneration;
    try {
      const detail = await api.getSession(this.sessionId, { signal: this.controller.signal });
      if (this.disposed || generation !== this.loadGeneration) return;
      this.detail = detail;
      this.detailLoaded = true;
      this.refreshStale = false;
      this.onSessionChanged?.(detail.session, detail.turns);
      this.renderDetail();
      this.schedulePoll();
    } catch (error) {
      if (this.disposed || generation !== this.loadGeneration || error?.name === "AbortError") return;
      this.refreshStale = true;
      this.runStatus.textContent = this.detailLoaded
        ? "Could not refresh; showing the last saved conversation"
        : "This session could not be loaded";
      if (!this.detailLoaded) replace(this.title, "Session unavailable");
      if (!silent || !this.detail) replace(this.transcript, errorNotice(error));
      this.updateReview();
      this.renderComposerState();
      if (this.detail?.active_run_id) this.schedulePoll();
    }
  }

  schedulePoll() {
    clearTimeout(this.pollTimer);
    if (this.disposed || !this.detail?.active_run_id) return;
    this.pollTimer = setTimeout(() => this.load({ silent: true }), POLL_MS);
  }

  renderDetail() {
    const detail = this.detail;
    const title = sessionTitle(detail?.session, detail?.turns);
    replace(this.title, headline(title, 120));
    this.runStatus.textContent = detail?.active_run_id ? activeTurnLabel(detail) : "Ready for your next message";
    const turns = Array.isArray(detail?.turns) ? detail.turns : [];
    const discussions = Array.isArray(detail?.discussions) ? detail.discussions : [];
    const entries = sessionTimelineEntries(turns, discussions);
    const timelineKey = JSON.stringify(entries);
    if (timelineKey !== this.lastTimelineKey) {
      const stayAtBottom = this.timeline.scrollHeight - this.timeline.scrollTop - this.timeline.clientHeight < 80;
      const previousScrollTop = this.timeline.scrollTop;
      replace(
        this.transcript,
        entries.length
          ? entries.map((entry) => entry.type === "turn"
            ? turnNodes(entry.turn, detail.active_run_id, this.sessionId, (runId) => this.discussDiagnostic(runId), this.plotsFor(entry.turn))
            : discussionNodes(entry.discussion, {
              sessionId: this.sessionId,
              activeRun: Boolean(detail.active_run_id),
              retrying: this.retryingDiscussionId === entry.discussion.id,
              retryError: this.discussionRetryErrors.get(entry.discussion.id),
              onResend: (discussion) => this.resendDiscussion(discussion),
              onRetryPending: (discussion) => this.retryPendingDiscussion(discussion),
            }))
          : h("div", { class: "conversation-empty" },
            h("p", { class: "eyebrow" }, "New conversation"),
            h("h2", null, "Start with the work you want done."),
            h("p", { class: "muted" }, "Ask about evidence already recorded in this session, or request new work to start a governed run."),
          ),
      );
      this.lastTimelineKey = timelineKey;
      this.timeline.scrollTop = stayAtBottom ? this.timeline.scrollHeight : previousScrollTop;
    }
    this.updateReview();
    this.renderComposerState();
  }

  updateReview() {
    const active = this.detail?.turns?.find((turn) => turn.id === this.detail.active_run_id);
    this.reviewView.update(active ? { ...active, stale: this.refreshStale } : null);
    this.interviewView.update(active ? { ...active, stale: this.refreshStale } : null);
  }

  plotsFor(turn) {
    if (turn.state?.condition !== "terminal" || turn.state.outcome !== "completed" || !turn.assistant_message) return null;
    if (!this.plotViews.has(turn.id)) {
      this.plotViews.set(turn.id, new SessionPlotsView(h("div"), turn.id));
    }
    return this.plotViews.get(turn.id).root;
  }

  renderComposerState() {
    const active = Boolean(this.detail?.active_run_id);
    const uncertain = Boolean(this.pending?.uncertain);
    const unavailable = !this.detailLoaded || this.refreshStale;
    const mode = this.modeField.value || "auto";
    const actionUnavailable = active && mode === "action" && !uncertain;
    const inputsUnavailable = mode !== "ask" && !uncertain && !this.datasetStatus?.ready;
    this.messageField.readOnly = uncertain;
    this.messageField.disabled = unavailable || this.posting || actionUnavailable;
    this.modeField.disabled = unavailable || this.posting || uncertain;
    this.sendButton.disabled = unavailable || inputsUnavailable || this.posting || actionUnavailable;
    this.sendButton.textContent = this.posting ? "Sending…" : uncertain ? "Retry same message" : mode === "ask" ? "Ask" : mode === "action" ? "Request work" : "Send";
    this.retryButton.hidden = !uncertain;
    this.retryButton.disabled = unavailable || this.posting;
    this.modeHelp.textContent = mode === "ask"
      ? "Questions inspect evidence recorded in this session and do not start a Run."
      : mode === "action"
        ? "A work request starts a governed Run with the session's project inputs."
        : "Auto can answer from recorded session evidence or start governed work, based on your message.";
    if (unavailable) {
      const refreshMessage = this.detailLoaded
        ? "Refresh the session before sending another message."
        : "Waiting for the current session state before sending.";
      this.composerStatus.textContent = this.sendError ? `${this.sendError}. ${refreshMessage}` : refreshMessage;
    } else if (uncertain) {
      this.composerStatus.textContent = `${this.sendError ? `${this.sendError}. ` : "The response was uncertain. "}Retry this exact message, mode, and request id.`;
    } else if (this.sendError) {
      this.composerStatus.textContent = active && mode === "action" ? `${this.sendError}. ${activeTurnLabel(this.detail)}` : this.sendError;
    } else if (active) {
      this.composerStatus.textContent = mode === "action"
        ? `Work requests are unavailable while ${activeTurnLabel(this.detail).toLowerCase()}.`
        : mode === "ask"
          ? "You can ask about evidence recorded in this session while the Run continues."
          : `You can ask about this session while ${activeTurnLabel(this.detail).toLowerCase()}. Auto may choose a request that the API rejects while busy.`;
    } else if (inputsUnavailable) {
      this.composerStatus.textContent = this.datasetStatus?.message ?? "Checking project datasets…";
    } else if (this.pending) {
      this.composerStatus.textContent = "This message was not accepted yet. Send again when ready.";
    } else {
      this.composerStatus.textContent = mode === "ask"
        ? "Your question will inspect recorded session evidence."
        : mode === "action"
          ? "Your work request starts a governed Run."
          : "Auto chooses whether to answer from session evidence or start a governed Run.";
    }
  }

  onDraftChange() {
    this.sendError = null;
    if (this.pending && this.messageField.value !== this.pending.message && !this.pending.uncertain) {
      this.pending = null;
      clearPending(this.sessionId);
    }
  }

  discussDiagnostic(runId) {
    if (this.disposed || this.posting || this.pending || !this.detailLoaded || this.refreshStale) return;
    if (this.messageField.value.trim()) {
      this.composerStatus.textContent = "Your draft is preserved. Send or clear it before preparing an audit question.";
      this.messageField.focus?.();
      return;
    }
    this.modeField.value = "ask";
    this.messageField.value = `Explain the recorded terminal outcome of ${runId} and the corrective steps within my requested scope. Distinguish technical failures from policy blocks and recorded findings, and missing evidence from confirmed violations. This run is closed; any corrective execution requires a new governed request. Do not execute new work.`;
    this.onModeChange();
    this.messageField.focus?.();
  }

  onModeChange() {
    this.sendError = null;
    if (this.pending && !this.pending.uncertain && this.pending.mode !== this.modeField.value) {
      this.pending = null;
      clearPending(this.sessionId);
    }
    this.renderComposerState();
  }

  onKeydown(event) {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      this.send();
    }
  }

  onSubmit(event) {
    event.preventDefault();
    this.send();
  }

  async send() {
    const mode = this.pending?.uncertain ? this.pending.mode : this.modeField.value || "auto";
    const active = Boolean(this.detail?.active_run_id);
    if (!this.detailLoaded || this.refreshStale || this.posting || (active && mode === "action" && !this.pending?.uncertain)) return;
    if (mode !== "ask" && !this.pending?.uncertain && !this.datasetStatus.ready) return;
    const message = this.pending?.message ?? this.messageField.value.trim();
    if (!message) return;
    if (message.length > MAX_MESSAGE_LENGTH) {
      this.composerStatus.textContent = `Messages are limited to ${MAX_MESSAGE_LENGTH} characters.`;
      return;
    }
    if (!this.pending || this.pending.message !== message || this.pending.mode !== mode) {
      this.pending = { message, mode, clientMessageId: newClientMessageId(), uncertain: false };
    }
    writePending(this.sessionId, this.pending);
    this.posting = true;
    this.sendError = null;
    this.renderComposerState();
    this.composerStatus.textContent = "Sending…";
    try {
      const result = await api.sendSessionChat(
        this.sessionId,
        this.pending.message,
        this.pending.clientMessageId,
        this.pending.mode,
      );
      const discussion = result?.discussion ?? result;
      if (this.disposed) {
        clearPending(this.sessionId);
        return;
      }
      if (discussion?.id) this.acceptDiscussion(discussion);
      this.pending = null;
      clearPending(this.sessionId);
      this.messageField.value = "";
      this.posting = false;
      this.renderComposerState();
      await this.load({ silent: true });
    } catch (error) {
      if (this.disposed) return;
      this.posting = false;
      if (isUncertainSendError(error)) {
        this.pending = { ...this.pending, mode, uncertain: true };
        writePending(this.sessionId, this.pending);
      } else if (error?.status === 409 && ["session_busy", "session_inputs_missing"].includes(error?.code)) {
        this.pending = { ...this.pending, mode, uncertain: false };
        writePending(this.sessionId, this.pending);
      } else {
        clearPending(this.sessionId);
      }
      this.sendError = error?.message ?? "The message could not be sent.";
      if (error?.code === "session_inputs_missing") {
        this.datasetStatus.refresh();
        this.setInspector("datasets");
      }
      this.renderComposerState();
      if (error?.status === 409 && error?.code === "session_busy") await this.load({ silent: true });
    }
  }

  acceptDiscussion(discussion) {
    const discussions = [...(this.detail?.discussions ?? []).filter((item) => item.id !== discussion.id), discussion]
      .sort((left, right) => left.ordinal - right.ordinal);
    this.detail = { ...this.detail, discussions, session: this.detail?.session };
    this.onSessionChanged?.(this.detail.session, this.detail.turns ?? []);
    this.lastTimelineKey = null;
    this.renderDetail();
  }

  resendDiscussion(discussion) {
    this.pending = null;
    clearPending(this.sessionId);
    this.sendError = null;
    this.modeField.value = ["auto", "ask", "action"].includes(discussion.mode) ? discussion.mode : "auto";
    this.messageField.value = discussion.user_message ?? "";
    this.onDraftChange();
    this.renderComposerState();
    this.messageField.focus?.();
  }

  async retryPendingDiscussion(discussion) {
    if (this.disposed || this.retryingDiscussionId || (this.detail?.active_run_id && discussion.kind === "action")) return;
    this.retryingDiscussionId = discussion.id;
    this.discussionRetryErrors.delete(discussion.id);
    this.lastTimelineKey = null;
    this.renderDetail();
    try {
      const result = await api.retrySessionDiscussion(this.sessionId, discussion.id);
      if (this.disposed) return;
      const savedDiscussion = result?.discussion ?? result;
      if (savedDiscussion?.id) {
        this.acceptDiscussion(savedDiscussion);
        if (savedDiscussion.status !== "pending" && this.pending?.message === savedDiscussion.user_message && this.pending?.mode === savedDiscussion.mode) {
          this.pending = null;
          clearPending(this.sessionId);
          if (this.messageField.value === savedDiscussion.user_message) this.messageField.value = "";
        }
      }
      await this.load({ silent: true });
    } catch (error) {
      if (this.disposed) return;
      this.discussionRetryErrors.set(discussion.id, `${error?.code ?? "error"}: ${error?.message ?? error}`);
      if (error?.status === 409 && error?.code === "session_busy") await this.load({ silent: true });
    } finally {
      if (this.disposed) return;
      this.retryingDiscussionId = null;
      this.lastTimelineKey = null;
      this.renderDetail();
    }
  }
}
