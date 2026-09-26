// One thread (a Run): the sticky header, the transcript folded from its event log, the one
// composer, and the inspector beside it.
//
// The Run record arrives quickly; the event page takes longer, because the API verifies the hash
// chain and redacts every payload before it answers. The header and the prompt render as soon as
// the record arrives; everything folded from the log follows when it does.
//
// The console adds no authority here. Approve, Reject, Resume and Cancel call the API, which asks
// the Policy Engine; the transcript only shows what the log already recorded.

import { api, followEvents, readAllEvents } from "../api.js";
import { h, replace } from "../dom.js";
import { headline, integer, plainQuestion, timestamp } from "../format.js";
import { foldApprovals, foldInterview, latestRunState, waitingForInformation } from "../fold.js";
import { writePref } from "../prefs.js";
import { TERMINAL_STATUSES, isResumableState, stageStepIndex, stageSteps } from "../status.js";
import { foldThread, stageLabel } from "../thread-fold.js";
import { toast } from "../toast.js";
import { button, confirmDialog, copyable, errorNotice, iconButton, notice, skeleton, statusTag, stepper } from "../ui.js";
import { Composer } from "./composer.js";
import { Inspector } from "./inspector.js";
import { Transcript } from "./transcript.js";

const RUN_RECORD_EVENTS = new Set([
  "run.transitioned",
  "run.completed",
  "run.failed",
  "turn.ended",
  "human.approval",
  "human.approval_requested",
  "audit.completed",
  "audit.block",
  "artifact.created",
  "audit.finding",
]);
const UPDATE_DELAY_MS = 400;
const INTERVIEW_RETRY_MS = 3000;

// RunOutcome → stepper tone; undefined while the Run has not reached a terminal outcome.
function outcomeTone(state) {
  if (state?.condition !== "terminal") return undefined;
  if (state.outcome === "completed") return "ok";
  if (state.outcome === "blocked" || state.outcome === "failed") return "block";
  return "neutral";
}

// The interview status only changes when the interview or the Run's lifecycle records something.
function interviewKey(events) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const type = events[index].type;
    if (type.startsWith("activity_profile.") || type === "run.transitioned" || type === "human.approval") {
      return events[index].seq;
    }
  }
  return -1;
}

export class ThreadView {
  constructor(main, inspectorRoot, runId, panel, item, hooks = {}) {
    this.runId = runId;
    this.hooks = hooks;
    this.panel = panel ?? null;
    this.item = item ?? null;
    this.run = null;
    this.events = [];
    this.eventsLoaded = false;
    this.lastSeq = -1;
    this.disposed = false;
    this.catchingUp = false;
    this.catchUpAgain = false;
    this.runRecordStale = false;
    this.updateTimer = null;
    this.interviewTimer = null;
    this.headKey = null;
    this.stepKey = null;
    this.stepperNode = null;
    this.idNode = null;
    this.eventCount = h("span");
    this.interviewCache = null;
    this.composerToken = 0;
    this.controller = new AbortController();

    this.head = h("header", { class: "thread-head" }, skeleton(2));
    this.actionStatus = h("span", { class: "action-status", role: "status" });
    this.inspectorToggle = iconButton("panel", {
      label: "Toggle the inspector",
      kind: "ghost",
      onClick: () => this.toggleInspector(),
    });
    this.transcriptRoot = h("div", { class: "transcript" });
    this.composerHost = h("div");
    replace(main, h("section", { class: "thread" }, this.head, this.transcriptRoot, this.composerHost));

    this.transcript = new Transcript(this.transcriptRoot, {
      api,
      runId,
      onApprove: (decisionId, note) => this.decide(true, note),
      onReject: (decisionId, note) => this.decide(false, note),
      onRunAgain: () => this.runAgain(),
      loadPlan: () => api.plan(this.runId),
    });
    this.composer = new Composer(this.composerHost, {
      mode: "thread",
      onSend: (text, kind) => this.onSend(text, kind),
      onApprove: (note) => this.decide(true, note),
      onReject: (note) => this.decide(false, note),
      onResume: () => this.mutate(() => api.resume(this.runId), "Resume requested."),
      onRunAgain: () => this.runAgain(),
      onShowCall: () => this.showFocusedReview(),
    });
    this.inspector = new Inspector(inspectorRoot, {
      onClose: () => this.hooks.onInspectorChange?.(null),
      onPanelChange: (next) => this.hooks.onInspectorChange?.(next),
      onOverlay: (open) => this.hooks.onInspectorOverlay?.(open),
      onWidth: (px) => writePref("inspectorWidth", px),
    });
    if (this.panel) this.inspector.open(this.panel, this.item);
    this.renderTranscript();
    this.load();
  }

  dispose() {
    this.disposed = true;
    this.controller.abort();
    clearTimeout(this.updateTimer);
    clearTimeout(this.interviewTimer);
    this.inspector.close();
  }

  // ---------------------------------------------------------------- loading

  async load() {
    const pendingEvents = readAllEvents(this.runId, { signal: this.controller.signal });
    pendingEvents.catch(() => {});
    try {
      this.run = await api.getRun(this.runId);
      if (this.disposed) return;
      this.hooks.onRunChanged?.(this.run);
      this.renderAll();
      const events = await pendingEvents;
      if (this.disposed) return;
      this.events = events;
      this.eventsLoaded = true;
      this.lastSeq = events.length ? events[events.length - 1].seq : -1;
      this.renderAll();
      followEvents(this.runId, {
        since: this.lastSeq,
        signal: this.controller.signal,
        keepGoing: () => !this.disposed && !TERMINAL_STATUSES.has(this.run?.status),
        onEvent: (event) => this.accept(event),
      });
    } catch (error) {
      if (this.disposed || error?.name === "AbortError") return;
      if (this.run) {
        this.composer.setState({ kind: "ended" });
        this.composer.setError(`${error?.code ?? "error"}: ${error?.message ?? error}`);
        return;
      }
      replace(this.head, h("h1", null, this.runId), errorNotice(error));
    }
  }

  accept(event) {
    if (!event || event.seq <= this.lastSeq) return;
    if (event.seq !== this.lastSeq + 1) {
      this.catchUp();
      return;
    }
    this.events.push(event);
    this.lastSeq = event.seq;
    if (RUN_RECORD_EVENTS.has(event.type)) this.runRecordStale = true;
    clearTimeout(this.updateTimer);
    this.updateTimer = setTimeout(() => this.applyUpdate(), UPDATE_DELAY_MS);
  }

  async catchUp() {
    // A gap reported while a read is in flight cannot be dropped: the stream already moved its
    // cursor past that event and will not send it again. Remember the request and repeat the read.
    if (this.catchingUp) {
      this.catchUpAgain = true;
      return;
    }
    this.catchingUp = true;
    this.catchUpAgain = false;
    const before = this.lastSeq;
    try {
      const events = await readAllEvents(this.runId, { afterSeq: this.lastSeq, signal: this.controller.signal });
      for (const event of events) this.accept(event);
    } catch {
      // The live stream reconnects and repairs again.
    } finally {
      this.catchingUp = false;
    }
    const again = this.catchUpAgain;
    this.catchUpAgain = false;
    // Only while the log is still making progress: a read that returned nothing new would repeat
    // for ever against an event the API cannot yet serve.
    if (again && !this.disposed && this.lastSeq > before) await this.catchUp();
  }

  async applyUpdate() {
    if (this.disposed || !this.run) return;
    if (this.runRecordStale) {
      this.runRecordStale = false;
      try {
        const previous = this.run.status;
        this.run = await api.getRun(this.runId);
        if (previous !== this.run.status) this.hooks.onRunChanged?.(this.run);
      } catch {
        // Keep the last snapshot.
      }
    }
    if (this.disposed) return;
    this.renderAll();
  }

  interviewStatus() {
    const key = interviewKey(this.events);
    if (!this.interviewCache || this.interviewCache.key !== key) {
      const promise = api.riskInterview(this.runId);
      this.interviewCache = { key, promise };
      promise.catch(() => {
        if (this.interviewCache?.promise === promise) this.interviewCache = null;
      });
    }
    return this.interviewCache.promise;
  }

  context() {
    return {
      api,
      run: this.run,
      events: this.events,
      runId: this.runId,
      item: this.item,
      onRunMutated: (run) => this.onRunMutated(run),
      interviewStatus: () => this.interviewStatus(),
      runAgain: () => this.runAgain(),
    };
  }

  async onRunMutated(run) {
    if (this.disposed) return;
    if (run?.id === this.runId) this.run = run;
    try {
      const events = await readAllEvents(this.runId, { afterSeq: this.lastSeq, signal: this.controller.signal });
      for (const event of events) {
        if (event.seq !== this.lastSeq + 1) continue;
        this.events.push(event);
        this.lastSeq = event.seq;
      }
      this.run = await api.getRun(this.runId);
    } catch {
      // The live stream catches up.
    }
    this.hooks.onRunChanged?.(this.run);
    if (!this.disposed) this.renderAll();
  }

  // ---------------------------------------------------------------- actions

  // Resolves to null when the action succeeded and to the error message when it did not, so a
  // surface that disabled its own controls (the transcript's review card) can put them back
  // instead of sitting on "Recording the approval…" for ever.
  async mutate(action, done) {
    this.actionStatus.textContent = "Working…";
    this.actionStatus.className = "action-status";
    this.composer.setError(null);
    try {
      const run = await action();
      this.actionStatus.textContent = done;
      toast(done, { tone: "ok" });
      await this.onRunMutated(run);
      return null;
    } catch (error) {
      const message = `${error?.code ?? "error"}: ${error?.message ?? error}`;
      this.actionStatus.textContent = message;
      this.actionStatus.className = "action-status tone-block";
      this.composer.setError(message);
      return message;
    } finally {
      this.composer.setBusy(false);
    }
  }

  decide(approved, note) {
    const text = String(note ?? "").trim();
    return this.mutate(
      () => (approved ? api.approve(this.runId, text) : api.reject(this.runId, text)),
      approved ? "Approved." : "Rejected.",
    );
  }

  onSend(text, kind) {
    if (kind === "answer") {
      this.mutate(async () => {
        const result = await api.answerRiskInterview(this.runId, text);
        this.composer.clear();
        return result?.run ?? result;
      }, "Answer recorded.");
      return;
    }
    if (kind === "ended") {
      this.createFollowUp(text);
      return;
    }
    this.composer.setBusy(false);
  }

  async createFollowUp(prompt) {
    this.composer.setError(null);
    try {
      const body = await api.createRun(prompt);
      this.composer.clear();
      this.hooks.onRunCreated?.(body?.run ?? body);
    } catch (error) {
      this.composer.setError(`${error?.code ?? "error"}: ${error?.message ?? error}`);
      this.composer.setBusy(false);
    }
  }

  async runAgain() {
    this.actionStatus.textContent = "Creating a new thread…";
    this.actionStatus.className = "action-status";
    try {
      const body = await api.createRun(this.run.prompt);
      this.hooks.onRunCreated?.(body?.run ?? body);
    } catch (error) {
      this.actionStatus.textContent = `${error?.code ?? "error"}: ${error?.message ?? error}`;
      this.actionStatus.className = "action-status tone-block";
    }
  }

  async cancel() {
    const confirmed = await confirmDialog({
      title: "Cancel this thread?",
      body: "Its pending reviews stop being answerable.",
      confirmLabel: "Cancel thread",
      cancelLabel: "Keep it running",
      danger: true,
    });
    if (!confirmed) return;
    this.mutate(() => api.cancel(this.runId, "Cancelled from the web console."), "Thread cancelled.");
  }

  showFocusedReview() {
    const focus = this.eventsLoaded ? foldApprovals(this.events).focus : null;
    if (focus) this.transcript.scrollToKey(`review:${focus.decisionId}`);
  }

  // ---------------------------------------------------------------- inspector

  setRoute(panel, item) {
    this.panel = panel ?? null;
    this.item = item ?? null;
    this.inspector.update(this.context());
    if (this.panel) {
      this.inspector.open(this.panel, this.item);
      writePref("inspectorPanel", this.panel);
    } else {
      this.inspector.close();
    }
    if (this.run) this.renderHead();
  }

  toggleInspector() {
    this.inspector.toggle();
  }

  // ---------------------------------------------------------------- rendering

  renderAll() {
    if (!this.run) return;
    this.renderHead();
    this.renderTranscript();
    this.renderComposer();
    this.inspector.update(this.context());
    this.renderBadges();
  }

  renderHead() {
    const run = this.run;
    if (!run) return;
    const state = this.eventsLoaded ? latestRunState(this.events) : null;
    const active = !TERMINAL_STATUSES.has(run.status);
    const resumable = isResumableState(state);
    const prompt = String(run.prompt ?? "");
    const attention = this.needsAttention();
    this.inspectorToggle.setAttribute("aria-pressed", String(this.inspector.isOpen));
    if (attention) this.inspectorToggle.dataset.attention = "true";
    else delete this.inspectorToggle.dataset.attention;
    // The event count is the only part of the header that changes on every batch, so it lives in
    // its own node and the rest is rebuilt only when it actually says something different.
    replace(this.eventCount, this.eventsLoaded ? `${integer(this.events.length)} events` : "reading the log…");
    const stepKey = `${this.eventsLoaded}|${stageStepIndex(state)}|${outcomeTone(state) ?? ""}`;
    if (stepKey !== this.stepKey) {
      this.stepKey = stepKey;
      this.stepperNode = this.eventsLoaded
        ? stepper(stageSteps(), stageStepIndex(state), outcomeTone(state))
        : null;
    }
    if (!this.idNode) this.idNode = copyable(run.id);
    // Rebuilding the header on every batch restarts the current step's pulse, throws away the
    // "Copied" confirmation and takes the focus off whatever action the operator is on.
    const headKey = [stepKey, run.status, active, resumable, prompt, run.error ?? "", run.created_at].join("|");
    if (headKey === this.headKey) return;
    this.headKey = headKey;
    replace(
      this.head,
      h(
        "div",
        { class: "thread-head-title" },
        h("h1", { title: prompt }, headline(prompt, 120)),
        statusTag(run.status),
        h(
          "div",
          { class: "thread-head-actions" },
          this.actionStatus,
          resumable
            ? button("Resume", {
                kind: "small",
                title: "Continue from the last durable checkpoint",
                onClick: () => this.mutate(() => api.resume(this.runId), "Resume requested."),
              })
            : null,
          active ? button("Cancel", { kind: ["danger", "small"], onClick: () => this.cancel() }) : null,
          active
            ? null
            : button("Run again", {
                kind: "small",
                title: "Start a new thread with this prompt and the current project inputs",
                onClick: () => this.runAgain(),
              }),
          this.inspectorToggle,
        ),
      ),
      h(
        "div",
        { class: "thread-head-meta" },
        this.stepperNode,
        this.idNode,
        this.eventCount,
        h("span", null, timestamp(run.created_at)),
      ),
      run.error ? notice(run.error, "block") : null,
    );
  }

  renderTranscript() {
    const state = this.eventsLoaded ? latestRunState(this.events) : null;
    const approvals = this.eventsLoaded ? foldApprovals(this.events) : { focus: null };
    this.transcript.render(foldThread(this.run, this.events), {
      run: this.run,
      state,
      focusDecisionId: approvals.focus?.decisionId ?? null,
      answerable: this.run?.status === "WAITING_FOR_APPROVAL",
    });
    this.transcript.setWorking(this.workingLabel(state));
  }

  workingLabel(state) {
    if (!this.run) return "Loading the thread…";
    if (!this.eventsLoaded) return "Reading the event log…";
    if (TERMINAL_STATUSES.has(this.run.status)) return null;
    if (this.run.status === "WAITING_FOR_APPROVAL") return null;
    if (state?.condition === "waiting" || state?.condition === "paused") return null;
    const stage = String(state?.stage ?? "").toLowerCase();
    if (stage === "auditing") return "MIRA is auditing";
    return stage ? `THY is working · ${stageLabel(stage)}` : "THY is working";
  }

  renderComposer() {
    const token = (this.composerToken += 1);
    if (!this.run || !this.eventsLoaded) {
      this.composer.setState({ kind: "loading" });
      return;
    }
    const state = latestRunState(this.events);
    const stage = state?.stage ?? null;
    if (TERMINAL_STATUSES.has(this.run.status)) {
      this.composer.setState({ kind: "ended" });
      return;
    }
    if (waitingForInformation(this.events)) {
      // The question itself comes from the API, not from the log: show the working surface until
      // it answers, so the composer never claims a question that is no longer pending. A composer
      // already on a question keeps it, rather than flashing back through "working" on every batch.
      if (this.composer.state.kind !== "answer") this.composer.setState({ kind: "working", stage });
      this.interviewStatus()
        .then((data) => {
          const question = data?.pending_question;
          if (this.disposed || token !== this.composerToken || !question) return;
          this.composer.setState({
            kind: "answer",
            question: {
              question_number: question.question_number,
              field: question.field,
              text: plainQuestion(question.question),
            },
          });
        })
        .catch((error) => {
          // No event arrives while the Run waits for information, so nothing would call this
          // again: a disabled "THY is working" composer would be the operator's dead end. Say the
          // Run is waiting, name the failure, and read the question again shortly.
          if (this.disposed || token !== this.composerToken) return;
          this.composer.setState({ kind: "answer", question: null });
          this.composer.setError(
            `${error?.code ?? "error"}: ${error?.message ?? error} · the thread is waiting for you; retrying`,
          );
          clearTimeout(this.interviewTimer);
          this.interviewTimer = setTimeout(() => {
            if (!this.disposed) this.renderComposer();
          }, INTERVIEW_RETRY_MS);
        });
      return;
    }
    const approvals = foldApprovals(this.events);
    if (this.run.status === "WAITING_FOR_APPROVAL" && approvals.focus) {
      this.composer.setState({ kind: "review", review: approvals.focus });
      return;
    }
    if (isResumableState(state)) {
      this.composer.setState({ kind: "paused" });
      return;
    }
    this.composer.setState({ kind: "working", stage });
  }

  needsAttention() {
    if (!this.eventsLoaded || !this.run) return false;
    if (this.run.status === "WAITING_FOR_APPROVAL") return true;
    return waitingForInformation(this.events);
  }

  renderBadges() {
    const pending = this.eventsLoaded ? foldApprovals(this.events).pending.length : 0;
    const asking = this.eventsLoaded && waitingForInformation(this.events);
    this.inspector.setBadges({
      agents: { count: this.run.agent_ids?.length ?? 0 },
      reviews: { count: pending, attention: Boolean(pending) && this.run.status === "WAITING_FOR_APPROVAL" },
      tools: { count: this.run.tool_call_ids?.length ?? 0 },
      artifacts: { count: this.run.artifact_ids?.length ?? 0 },
      audit: { count: this.run.finding_ids?.length ?? 0 },
      events: { count: this.eventsLoaded ? this.events.length : 0 },
      interview: { count: this.eventsLoaded ? foldInterview(this.events).length : 0, attention: asking },
    });
  }
}
