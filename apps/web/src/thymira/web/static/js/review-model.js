// Read-only descriptions of the exact policy decision parked for human review. These helpers
// consume the API's verified, redacted event projection; summaries never supply authority.

const DECISION_ID_FIELDS = ["decision_id", "policy_decision_id"];

function decisionId(payload) {
  for (const field of DECISION_ID_FIELDS) {
    if (typeof payload?.[field] === "string" && payload[field]) return payload[field];
  }
  return typeof payload?.policy_decision?.id === "string" ? payload.policy_decision.id : null;
}

function policyDecisionFor(events, id) {
  return events.find((event) => event.type === "policy.decision" && event.payload?.id === id)?.payload ?? null;
}

function parkedTransitionFor(events, id) {
  return [...events].reverse().find((event) => {
    if (event.type !== "run.transitioned" || event.payload?.command !== "wait_for_approval") return false;
    return decisionId(event.payload) === id;
  }) ?? null;
}

function riskProfileBefore(events, seq) {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (typeof seq === "number" && typeof event.seq === "number" && event.seq >= seq) continue;
    const profile = event.type === "risk.classified" ? event.payload?.risk_profile : null;
    if (profile && typeof profile === "object") return profile;
  }
  return null;
}

function eventsBeforeRequest(events, request) {
  const index = events.findIndex((event) => event.seq === request.seq);
  if (index >= 0) return events.slice(0, index);
  return typeof request.seq === "number"
    ? events.filter((event) => typeof event.seq !== "number" || event.seq < request.seq)
    : events;
}

function integerOrNull(value) {
  return Number.isInteger(value) && value >= 0 ? value : null;
}

function questionFromEvent(event) {
  if (!event) return null;
  return {
    event_id: event.event_id ?? null,
    field: event.payload?.field ?? null,
    question: event.payload?.question ?? null,
  };
}

function answerFromEvent(event) {
  if (!event) return null;
  return {
    event_id: event.event_id ?? null,
    answer: event.payload?.answer ?? null,
    sufficiency: event.payload?.sufficiency ?? null,
  };
}

function interviewLimitContext(events, request, requestPayload) {
  const earlier = eventsBeforeRequest(events, request);
  const questions = earlier.filter((event) => event.type === "activity_profile.questioned");
  const answers = earlier.filter((event) => event.type === "activity_profile.answered");
  const lastQuestion = requestPayload.last_question ?? questionFromEvent(questions.at(-1));
  const linkedAnswer = lastQuestion?.event_id
    ? answers.find((event) => event.payload?.question_event_id === lastQuestion.event_id)
    : null;
  const lastAnswer = requestPayload.last_answer ?? answerFromEvent(linkedAnswer ?? answers.at(-1));
  const field = requestPayload.field ?? lastQuestion?.field ?? null;
  const fieldQuestionCount = field
    ? questions.filter((event) => event.payload?.field === field).length
    : null;
  const counts = requestPayload.questions_asked ?? {};
  return {
    reason: requestPayload.reason ?? request.reason ?? null,
    reviewKind: requestPayload.review_kind ?? null,
    field,
    questionsAsked: {
      total: integerOrNull(counts.total) ?? questions.length,
      maxTotal: integerOrNull(counts.max_total),
      fieldTotal: integerOrNull(counts.field_total) ?? fieldQuestionCount,
      maxPerField: integerOrNull(counts.max_per_field),
    },
    lastQuestion,
    lastAnswer,
    remainingFields: Array.isArray(requestPayload.remaining_fields)
      ? requestPayload.remaining_fields.filter((value) => typeof value === "string")
      : [],
    decisionEffects: requestPayload.decision_effects && typeof requestPayload.decision_effects === "object"
      ? requestPayload.decision_effects
      : null,
  };
}

function activityProfileForAssessment(events, assessment, request) {
  if (!assessment) return null;
  const earlier = eventsBeforeRequest(events, request);
  return [...earlier].reverse().map((event) => event.payload?.activity_profile)
    .find((profile) => profile?.id === assessment.activity_profile_id && profile?.version === assessment.activity_profile_version) ?? null;
}

function knownProfileFacts(profile, assessment) {
  const facts = [];
  if (profile) {
    for (const field of ["purpose", "affected_population", "decision_effect", "autonomy", "human_oversight", "jurisdiction", "data_categories", "sensitive_attributes", "potential_consequences"]) {
      const value = profile[field];
      if (Array.isArray(value) && value.length) facts.push({ field, value: value.join(", ") });
      else if (typeof value === "string" && value.trim() && !["unknown", "unspecified", "not provided", "not stated", "n/a"].includes(value.trim().toLowerCase())) {
        facts.push({ field, value: value.trim() });
      }
    }
  }
  if (facts.length) return facts;
  return (Array.isArray(assessment?.evidence) ? assessment.evidence : [])
    .filter((evidence) => typeof evidence?.ref === "string" && evidence.ref)
    .map((evidence) => ({
      field: evidence.kind ?? "evidence",
      value: [evidence.ref, evidence.note].filter((part) => typeof part === "string" && part).join(" · "),
    }));
}

function matchedFindings(events, findingIds) {
  const expected = new Set(findingIds);
  if (!expected.size) return [];
  return events
    .filter((event) => event.type === "audit.finding")
    .map((event) => event.payload?.finding)
    .filter((finding) => finding && expected.has(finding.id));
}

function recordedPlan(events, request, decision) {
  const attached = request.payload?.plan ?? decision?.plan;
  if (attached && (Array.isArray(attached) || typeof attached === "object")) return attached;
  const planEvent = [...events].reverse().find((event) =>
    event.type === "plan.proposed" && event.payload?.plan &&
    (typeof request.seq !== "number" || typeof event.seq !== "number" || event.seq < request.seq),
  );
  return planEvent?.payload?.plan ?? null;
}

function toolScope(toolCall, sideEffects, sandboxMode, decision) {
  const tool = toolCall?.tool ?? "unknown tool";
  const args = toolCall?.arguments && typeof toolCall.arguments === "object" ? toolCall.arguments : {};
  const outputs = [];
  const targets = [];
  const effects = [];
  let action = `Run the recorded ${tool} request.`;

  if (typeof args.description === "string" && args.description.trim()) targets.push(args.description.trim());
  if (tool === "write_file") {
    action = "Write the requested file into this Run's workspace.";
    if (typeof args.path === "string" && args.path) {
      targets.push(`Target file: ${args.path}`);
      outputs.push(args.path);
      effects.push(`Writes the supplied content to ${args.path}.`);
    } else {
      effects.push("The request is to write a file, but its path is not recorded.");
    }
  } else if (tool === "run_python") {
    action = "Run the recorded Python code in this Run's workspace.";
    if (Array.isArray(args.output_artifacts)) {
      for (const artifact of args.output_artifacts) {
        if (typeof artifact?.path === "string" && artifact.path) outputs.push(artifact.path);
      }
    }
  } else if (typeof args.path === "string" && args.path) {
    targets.push(`Target path: ${args.path}`);
    outputs.push(args.path);
  }

  if (typeof args.timeout_s === "number") targets.push(`Time limit: ${args.timeout_s} seconds`);
  if (Array.isArray(sideEffects) && sideEffects.length) effects.push(...sideEffects.map(String));
  else if (typeof sideEffects === "string" && sideEffects.trim()) effects.push(sideEffects.trim());
  else if (tool === "run_python") {
    effects.push(sandboxMode
      ? `Runs with ${String(sandboxMode).replaceAll("_", " ")} access.`
      : "The policy reason says this tool has local side effects; this request does not list them individually.");
  } else if (tool !== "write_file" && decision?.reason?.toLowerCase().includes("side effect")) {
    effects.push("The policy says this tool has side effects; this request does not list them individually.");
  }

  return { action, targets, outputs, effects };
}

function decisionScope({ kind, request, riskProfile, riskAssessment, knownFacts, interviewLimit, executionConstraints, toolCall, sideEffects, sandboxMode, decision, findings, plan }) {
  if (kind === "risk_uncertainty") {
    const uncertainty = riskAssessment?.justification ?? request?.summary ?? "MIRA could not classify inherent risk with sufficient confidence.";
    return {
      action: "Continue the run from this risk-classification checkpoint.",
      targets: riskAssessment ? [
        `Specific uncertainty: ${uncertainty}`,
        typeof riskAssessment.confidence === "number" ? `Assessment confidence: ${Math.round(riskAssessment.confidence * 100)}%.` : null,
        riskAssessment.missing_information?.length ? `Unresolved: ${riskAssessment.missing_information.map(humanizeField).join(", ")}.` : null,
      ].filter(Boolean) : [request?.summary ?? "MIRA could not classify inherent risk with sufficient confidence."],
      outputs: [],
      effects: [
        knownFacts?.length ? `Known facts from the assessment-linked profile: ${knownFacts.map((fact) => `${humanizeField(fact.field)}: ${fact.value}`).join("; ")}.` : "No known profile facts were attached to this assessment.",
        "Accepting this uncertainty does not waive the execution-start Gate or later policy and tool reviews.",
      ],
      approveOutcome: "Continue with the recorded uncertainty. The execution-start Gate and any later required policy or tool reviews still apply.",
      rejectOutcome: "The run stops at this checkpoint.",
    };
  }

  if (kind === "interview_limit") {
    const field = interviewLimit?.field ? humanizeField(interviewLimit.field) : "the unresolved activity-profile facts";
    const count = interviewLimit?.questionsAsked?.fieldTotal;
    const max = interviewLimit?.questionsAsked?.maxPerField;
    const reason = interviewLimit?.reason;
    const limited = reason === "per_field_question_limit" && count !== null
      ? `The interview reached ${count}${max !== null ? ` of ${max}` : ""} questions for ${field}.`
      : reason === "total_question_limit" && interviewLimit?.questionsAsked?.total !== null
        ? `The interview reached ${interviewLimit.questionsAsked.total}${interviewLimit.questionsAsked.maxTotal !== null ? ` of ${interviewLimit.questionsAsked.maxTotal}` : ""} questions in total.`
        : `The interview ended with ${field} unresolved.`;
    const effects = [limited];
    if (interviewLimit?.lastAnswer?.sufficiency?.reason) {
      const label = interviewLimit.lastAnswer.sufficiency.sufficient === false ? "The last answer was judged insufficient" : "The last answer was judged sufficient";
      effects.push(`${label}: ${interviewLimit.lastAnswer.sufficiency.reason}`);
    }
    effects.push("An approval applies only to this checkpoint. It does not make the missing fact known; later policy gates and any required tool reviews still apply.");
    return {
      action: "Answer the human review raised by the bounded risk interview.",
      targets: [field === "the unresolved activity-profile facts" ? null : `Field still under review: ${field}.`, interviewLimit?.lastQuestion?.question ? `Last recorded question: ${interviewLimit.lastQuestion.question}` : null].filter(Boolean),
      outputs: [],
      effects,
      approveOutcome: interviewLimit?.decisionEffects?.approve ?? "Continue without resolving the missing fact. Later policy gates and any required tool reviews still apply.",
      rejectOutcome: interviewLimit?.decisionEffects?.reject ?? "Rejecting this review stops the Run at this checkpoint.",
    };
  }

  if (kind === "execution_start") {
    const targets = [];
    if (riskProfile) {
      const classification = [
        riskProfile.risk_level ? String(riskProfile.risk_level).replaceAll("_", " ") : null,
        riskProfile.activity_category ? humanizeField(riskProfile.activity_category) : null,
        typeof riskProfile.confidence === "number" ? `${Math.round(riskProfile.confidence * 100)}% confidence` : null,
      ].filter(Boolean).join(" · ");
      if (classification) targets.push(`Recorded risk assessment: ${classification}.`);
      if (Array.isArray(riskProfile.risk_factors) && riskProfile.risk_factors.length) {
        targets.push(`Risk factors: ${riskProfile.risk_factors.map(humanizeField).join(", ")}.`);
      }
      if (Array.isArray(riskProfile.missing_information) && riskProfile.missing_information.length) {
        targets.push(`Still missing: ${riskProfile.missing_information.map(humanizeField).join(", ")}.`);
      }
    }
    if (!targets.length) targets.push("No risk details were attached to this review request.");
    const effects = [];
    if (executionConstraints?.local_execution_only === true) effects.push("Execution is restricted to the local workspace.");
    for (const [field, label] of [["allowed_tools", "Allowed tools"], ["prohibited_tools", "Prohibited tools"], ["prohibited_actions", "Prohibited actions"], ["required_evidence", "Required evidence"]]) {
      if (executionConstraints?.[field]?.length) effects.push(`${label}: ${executionConstraints[field].map(humanizeField).join(", ")}.`);
    }
    if (typeof executionConstraints?.max_tool_calls === "number") effects.push(`At most ${executionConstraints.max_tool_calls} tool calls.`);
    effects.push(executionConstraints?.requires_human_review
      ? "Every tool call requires its own human approval under these constraints. Approving the start does not waive those reviews."
      : "Each tool call still passes its own policy checks; some calls may need a separate review.");
    return {
      action: "Start this run under the recorded execution constraints.",
      targets,
      outputs: [],
      effects,
      approveOutcome: "The run may start under these constraints.",
      rejectOutcome: "The run will not start.",
    };
  }

  if (kind === "tool") {
    const scope = toolScope(toolCall, sideEffects, sandboxMode, decision);
    return {
      ...scope,
      approveOutcome: "Only this exact tool request is allowed once. A changed or repeated call needs a separate review.",
      rejectOutcome: "This exact call will not run under this review.",
    };
  }

  if (kind === "findings") {
    const targets = findings.map((finding) => `${finding.title ?? finding.control_id ?? "Recorded finding"}${finding.severity ? ` (${String(finding.severity).toLowerCase()})` : ""}`);
    if (!targets.length && request?.payload?.summary) targets.push(request.payload.summary);
    return {
      action: "Record an answer to this findings decision.",
      targets,
      outputs: [],
      effects: ["Your answer applies to the finding IDs listed in the policy record."],
      approveOutcome: "Record approval for this findings decision.",
      rejectOutcome: "Record rejection for this findings decision.",
    };
  }

  if (kind === "plan") {
    const targets = Array.isArray(plan) ? plan.map(String) : [];
    return {
      action: "Advance the recorded plan under the execution constraints.",
      targets,
      outputs: [],
      effects: ["Only the plan attached to this decision is covered."],
      approveOutcome: "The recorded plan may advance.",
      rejectOutcome: "The run will not advance through this plan review.",
    };
  }

  return {
    action: "Answer the policy review shown here.",
    targets: request?.payload?.summary ? [request.payload.summary] : [],
    outputs: [],
    effects: ["The event record does not describe a more specific effect for this review."],
    approveOutcome: "Your answer applies to this policy decision only.",
    rejectOutcome: "This policy decision will remain unapproved.",
  };
}

export function buildApprovalReview(events, request) {
  const decision = policyDecisionFor(events, request.decisionId);
  const parked = parkedTransitionFor(events, request.decisionId);
  const resumeFrom = parked?.payload?.resume_from ?? null;
  const subjectKind = decision?.subject_kind ?? null;
  const payload = request.payload ?? {};
  const toolCall = request.toolCall ?? payload.tool_call ?? (payload.tool
    ? { tool: payload.tool, arguments: payload.arguments ?? {} }
    : null);
  const actionType = payload.action_type ?? payload.action_kind ?? decision?.action_type ?? null;
  const requestPayload = payload.request_payload && typeof payload.request_payload === "object"
    ? payload.request_payload
    : {};
  const interviewLimitReason = requestPayload.reason ?? payload.reason ?? request.reason ?? null;
  let kind = "unknown";
  if (subjectKind === "tool_call" || toolCall) kind = "tool";
  else if (resumeFrom === "interview_limit" || ["missing_activity_profile", "per_field_question_limit", "total_question_limit"].includes(interviewLimitReason)) kind = "interview_limit";
  else if (resumeFrom === "risk_uncertainty") kind = "risk_uncertainty";
  else if (resumeFrom === "execution_start") kind = "execution_start";
  else if (subjectKind === "findings") kind = "findings";
  else if (actionType === "plan.proposed" || payload.summary === "plan.proposed") kind = "plan";

  const findingIds = Array.isArray(decision?.finding_ids)
    ? decision.finding_ids.filter((value) => typeof value === "string")
    : [];
  const riskProfile = ["execution_start", "risk_uncertainty", "interview_limit"].includes(kind)
    ? riskProfileBefore(events, request.seq)
    : null;
  const assessmentId = payload.request_payload?.risk_assessment_id;
  const riskAssessment = assessmentId ? events.find((event) =>
    event.type === "risk_assessment.recorded" && event.payload?.risk_assessment?.id === assessmentId,
  )?.payload?.risk_assessment ?? null : null;
  const interviewLimit = kind === "interview_limit" ? interviewLimitContext(events, request, requestPayload) : null;
  const assessedProfile = kind === "risk_uncertainty"
    ? activityProfileForAssessment(events, riskAssessment, request)
    : null;
  const knownFacts = kind === "risk_uncertainty" ? knownProfileFacts(assessedProfile, riskAssessment) : [];
  const review = {
    kind,
    decisionId: request.decisionId,
    decision,
    request,
    parked,
    resumeFrom,
    subjectKind,
    actionType,
    requestedWork: events.find((event) => event.payload?.run)?.payload?.run?.conversation?.message
      ?? events.find((event) => event.payload?.run)?.payload?.run?.prompt ?? null,
    riskProfile,
    riskAssessment,
    knownFacts,
    interviewLimit,
    executionConstraints: decision?.execution_constraints ?? null,
    toolCall,
    findings: kind === "findings" ? matchedFindings(events, findingIds) : [],
    findingIds,
    plan: kind === "plan" ? recordedPlan(events, request, decision) : null,
    sideEffects: payload.side_effects ?? payload.external_effects ?? null,
    sandboxMode: payload.sandbox_mode ?? request.sandboxMode ?? null,
    expiresAt: payload.approval_expires_at ?? request.expiresAt ?? null,
  };
  review.scope = decisionScope({ ...review, request, decision });
  return review;
}

export function humanizeField(value) {
  const names = {
    affected_population: "Affected population",
    decision_role: "Decision role",
    decision_effect: "Decision effect",
    autonomy: "Autonomy",
    deployment_context: "Deployment context",
    human_oversight: "Human oversight",
    intended_use: "Intended use",
    potential_consequences: "Potential consequences",
  };
  const key = String(value ?? "");
  return names[key] ?? key.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
