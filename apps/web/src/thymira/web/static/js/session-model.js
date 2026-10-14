// Small, deterministic helpers shared by the conversation list, transcript and its tests.

export function sessionTitle(session, turns = []) {
  const title = String(session?.title ?? "").trim();
  if (title) return title;
  const firstMessage = String(turns[0]?.user_message ?? "").trim().replace(/\s+/g, " ");
  if (!firstMessage) return "New session";
  return firstMessage.length > 76 ? `${firstMessage.slice(0, 75).trimEnd()}…` : firstMessage;
}

export function sessionTimelineEntries(turns = [], discussions = []) {
  const runIds = new Set(turns.map((turn) => turn?.id ?? turn?.run?.id).filter(Boolean));
  const entries = [
    ...turns.map((turn, index) => ({
      type: "turn",
      key: `run:${turn?.id ?? turn?.run?.id ?? index}`,
      createdAt: turn?.run?.created_at ?? turn?.created_at ?? null,
      ordinal: Number.isInteger(turn?.ordinal) ? turn.ordinal : index,
      turn,
    })),
    ...discussions
      .filter((discussion) => !(discussion?.kind === "action" && discussion?.run_id && runIds.has(discussion.run_id)))
      .map((discussion, index) => ({
        type: "discussion",
        key: `discussion:${discussion?.id ?? index}`,
        createdAt: discussion?.created_at ?? null,
        ordinal: Number.isInteger(discussion?.ordinal) ? discussion.ordinal : index,
        discussion,
      })),
  ];
  return entries.sort((left, right) => {
    const leftTime = Date.parse(left.createdAt ?? "");
    const rightTime = Date.parse(right.createdAt ?? "");
    if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) return leftTime - rightTime;
    if (Number.isFinite(leftTime) !== Number.isFinite(rightTime)) return Number.isFinite(leftTime) ? -1 : 1;
    return left.ordinal - right.ordinal;
  });
}

export function activeTurn(detail) {
  const activeRunId = detail?.active_run_id;
  if (!activeRunId || !Array.isArray(detail?.turns)) return null;
  return detail.turns.find((turn) => turn.id === activeRunId) ?? null;
}

export function turnStateLabel(turn) {
  const state = turn?.state;
  if (state) {
    if (state.condition === "waiting") {
      return state.wait_reason === "information" ? "Waiting for your answer" : "Waiting for your review";
    }
    if (state.condition === "terminal") {
      if (state.outcome === "cancelled") return "Run cancelled";
      if (state.outcome === "completed") return "Run completed";
      if (state.outcome === "failed") return "Run failed";
      if (state.outcome === "blocked") return "Run blocked";
    }
    if (state.condition === "paused") return "Run paused";
    if (state.stage === "auditing") return "MIRA is auditing this run";
    if (state.stage === "planning" || state.stage === "created") return "THY is planning this run";
    return "THY is working on this run";
  }

  const status = String(turn?.run?.status ?? "").toUpperCase();
  if (status === "WAITING_FOR_APPROVAL") return "Waiting for your review";
  if (status === "AUDITING") return "MIRA is auditing this run";
  if (status === "PLANNING") return "THY is planning this run";
  if (status) return `Run ${status.toLowerCase().replaceAll("_", " ")}`;
  return "Run status unavailable";
}

export function activeTurnLabel(detail) {
  const turn = activeTurn(detail);
  return turn ? turnStateLabel(turn) : "Working on this conversation";
}

export function runSectionForTurn(turn) {
  const state = turn?.state;
  if (state?.condition === "waiting" && state.wait_reason === "information") return "interview";
  if (state?.condition === "waiting" && state.wait_reason === "approval") return "approvals";
  if (state) return "overview";

  // Older responses only carried RunStatus, where this is the best available review destination.
  return String(turn?.run?.status ?? "").toUpperCase() === "WAITING_FOR_APPROVAL" ? "approvals" : "overview";
}

export function turnBadge(turn) {
  const state = turn?.state;
  if (state) {
    if (state.condition === "waiting") {
      return state.wait_reason === "information" ? "WAITING FOR ANSWER" : "WAITING FOR REVIEW";
    }
    if (state.condition === "terminal") return String(state.outcome ?? "terminal").toUpperCase();
    if (state.condition === "paused") return "PAUSED";
    return String(state.stage ?? state.condition ?? "active").toUpperCase();
  }
  return String(turn?.run?.status ?? "UNKNOWN").replaceAll("_", " ");
}

export function isUncertainSendError(error) {
  return error?.status === 0 || error?.status >= 500;
}

export function newClientMessageId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  globalThis.crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}
