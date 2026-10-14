"""Composed session acceptance for real credit-data profiling and scoped MIRA review.

Only model responses are scripted. The Session API, production Core/THY/MIRA/Gate graph,
credit-risk policy stack, dataset registration, ``profile_dataset`` capability, local artifact
store, event log, and public human-approval route remain real.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.thymira.api_support import TEST_CREDENTIAL, authenticated_client
from thymira.agents import RiskClassification
from thymira.agents.llm import LLMToolCall, ScriptedProvider, estimate_structured_input_tokens
from thymira.agents.llm.base import LLMMessage, LLMResponse
from thymira.api import RuntimeDeps, build_default_deps, create_app
from thymira.core import GraphFactory, build_runtime_graph_factory, default_activity_profile
from thymira.core.model_continuity import materialize_model_inputs
from thymira.core.session_chat_budget import session_usage_snapshot
from thymira.events import verify_events
from thymira.mira import AuditAgentSpec
from thymira.mira.checks import CONTROLS, ControlStatus
from thymira.mira.preflight import RiskJudgment
from thymira.mira.verification import DiscoveryLimits, FindingVerdict
from thymira.schemas import (
    ActivityProfile,
    EventType,
    Framework,
    RiskLevel,
    Run,
    SandboxMode,
)
from thymira.state.session_discussions import LocalDiscussionRepository, SessionProviderLog
from thymira.thy.agents import full_agent_catalog
from thymira.thy.agents.evaluation import EvaluationResult
from thymira.thy.agents.experiment import ExperimentResult
from thymira.thy.models import AgentTask, PlanOutput, SynthesisNarrative, ThyAgentKind, ThyPhase
from thymira.tools import ToolManager
from thymira.tools.builtins.run_python import builtins_registry
from thymira.tools.sandbox import ContainerSandbox, readiness

if TYPE_CHECKING:
    from collections.abc import Callable

    from langgraph.graph.state import CompiledStateGraph

pytestmark = [pytest.mark.integration, pytest.mark.slow]

_REPO = Path(__file__).resolve().parents[2]


def _credit_csv() -> bytes:
    """Create an 80-row credit fixture with a noisy target and no target-clone feature."""
    rng = random.Random(173)
    rows = ["age,duration_months,credit_amount,is_high_risk"]
    for _ in range(80):
        age = rng.randint(21, 70)
        duration = rng.randint(4, 72)
        amount = rng.randint(200, 8_000)
        latent_risk = (
            0.4 * (duration - 36) / 20
            + 0.25 * (amount - 4_000) / 3_000
            + 0.15 * (age - 45) / 15
            + rng.gauss(0, 0.8)
        )
        rows.append(f"{age},{duration},{amount},{int(latent_risk > 0)}")
    return ("\n".join(rows) + "\n").encode()


_CREDIT_CSV = _credit_csv()


def _risk_classification() -> RiskClassification:
    return RiskClassification(
        risk_level=RiskLevel.LOW,
        activity_category="data_analysis",
        confidence=0.9,
        needs_human_review=False,
        summary="The request is descriptive analysis of a registered local dataset.",
    )


def _mira_risk_judgment() -> RiskJudgment:
    return RiskJudgment(
        risk_level=RiskLevel.LOW,
        activity_category="data_analysis",
        confidence=0.9,
        justification="The declared activity describes a local credit-risk dataset.",
    )


def _risk_for_mode(mode: str) -> tuple[RiskJudgment, RiskClassification]:
    categories = {
        "eda": ("data_analysis", "Describe the registered credit data locally."),
        "correlation": ("data_analysis", "Compute a descriptive numeric correlation matrix."),
        "training": ("model_training", "Train a local synthetic research baseline."),
        "evaluation": ("model_evaluation", "Evaluate the prior local model on held-out rows."),
    }
    category, description = categories[mode]
    return (
        RiskJudgment(
            risk_level=RiskLevel.LOW,
            activity_category=category,
            confidence=0.9,
            justification=description,
        ),
        RiskClassification(
            risk_level=RiskLevel.LOW,
            activity_category=category,
            confidence=0.9,
            needs_human_review=False,
            summary=description,
        ),
    )


def _complete_sensitive_profile(run: Run, mode: list[str]) -> ActivityProfile:
    purpose, decision_effect, autonomy = {
        "eda": (
            "Academic description of registered credit data and its correlations.",
            "No application is ranked, approved, denied, or acted on.",
            "The tool computes descriptive statistics only.",
        ),
        "correlation": (
            "Describe numeric associations in a registered synthetic credit dataset.",
            "The matrix does not rank, approve, or deny an application.",
            "The tool computes descriptive correlations only.",
        ),
        "training": (
            "Train a small offline research classifier on synthetic credit records.",
            "No real applicant is scored and no operational decision follows.",
            "The local experiment trains one baseline model for review.",
        ),
        "evaluation": (
            "Evaluate the previously trained synthetic credit baseline on held-out rows.",
            "Evaluation does not retrain the model or affect any application.",
            "The local evaluator computes metrics for an existing model.",
        ),
    }[mode[0]]
    return default_activity_profile(run).model_copy(
        update={
            "purpose": purpose,
            "affected_population": "People represented by records in a synthetic credit dataset.",
            "decision_effect": decision_effect,
            "autonomy": autonomy,
            "human_oversight": "A researcher reviews each report before any separate use.",
            "jurisdiction": "European Union.",
            "data_categories": ("credit application", "age", "financial data"),
            "sensitive_attributes": ("age",),
            "potential_consequences": (
                "A later misuse of age correlations could contribute to unfair credit decisions.",
            ),
            "created_at": run.created_at,
        }
    )


class _EvidenceScriptedProvider(ScriptedProvider):
    """Resolve scripted applicability judgments from only the verifier-visible evidence frame."""

    def __init__(self, responses) -> None:
        super().__init__(responses, model="test-model")
        self.active_run: list[str] = []
        self.active_log = None

    def complete_structured(self, prompt, *, schema, system=""):
        if schema is not FindingVerdict:
            return super().complete_structured(prompt, schema=schema, system=system)
        item = self._next(prompt, system)
        assert callable(item), "finding-verdict fixture must be evidence-responsive"
        responder = cast("Callable[[str], dict[str, object]]", item)
        verdict_data = responder(prompt)
        verdict = schema.model_validate(verdict_data)
        response = LLMResponse(
            text=verdict.model_dump_json(),
            provider=self.provider_name,
            model=self.model,
            metadata={"api": "scripted.structured", "schema": schema.__name__},
        )
        return verdict, response


class _ClarifiedRepairProvider(ScriptedProvider):
    """Require a citation repair whose replacement source comes from the live schema."""

    def __init__(self, correlation_run_id: str) -> None:
        super().__init__(
            [
                {
                    "kind": "clarification",
                    "message": "¿Te refieres a la matriz de correlaciones?",
                    "clarification_scope": "evidence",
                },
                {
                    "kind": "answer",
                    "message": "La matriz resume asociaciones del conjunto sintético.",
                    "sources": ["r0e999999"],
                },
                {"kind": "answer", "message": "Repaired.", "sources": []},
            ],
            model="test-model",
        )
        self.schemas: list[type[Any]] = []
        self.correlation_run_id = correlation_run_id

    def complete_structured(self, prompt, *, schema, system=""):
        self.schemas.append(schema)
        if self._index < 2:
            return super().complete_structured(prompt, schema=schema, system=system)
        self._next(prompt, system)
        source_item = schema.model_json_schema()["properties"]["sources"]["items"]
        allowed = source_item.get("enum", [])
        assert allowed, "repair schema must retain a complete verified evidence allowlist"
        matrix_sources = [
            record
            for record in _framed_record_lines(prompt, "complete-verified-repair-sources")
            if record.get("run_id") == self.correlation_run_id and record.get("source") in allowed
        ]
        assert matrix_sources, (
            "citation repair must see a complete record for the correlation Run; "
            f"visible={_framed_record_lines(prompt, 'complete-verified-repair-sources')}"
        )
        selected = next(
            (record for record in matrix_sources if "e" not in str(record["source"])),
            matrix_sources[0],
        )
        answer = schema.model_validate(
            {
                "kind": "answer",
                "message": "La matriz resume asociaciones descriptivas; no decide solicitudes.",
                "sources": [selected["source"]],
            }
        )
        response = LLMResponse(
            text=answer.model_dump_json(),
            provider=self.provider_name,
            model=self.model,
            metadata={"api": "scripted.structured", "schema": schema.__name__},
        )
        return answer, response


def _framed_json(prompt: str, label: str) -> dict[str, Any]:
    """Parse only a complete, nonce-matched model-visible evidence frame."""
    match = re.search(
        rf"<<<THYMIRA_UNTRUSTED:(?P<nonce>[0-9a-f]{{32}}):{label}:START>>>\n"
        rf"The following[^\n]*\n(?P<payload>.*?)\n"
        rf"<<<THYMIRA_UNTRUSTED:(?P=nonce):{label}:END>>>",
        prompt,
        re.DOTALL,
    )
    assert match is not None, f"provider request must contain a complete {label} frame"
    value = json.loads(match.group("payload"))
    assert isinstance(value, dict)
    return value


def _framed_record_lines(prompt: str, label: str) -> list[dict[str, Any]]:
    """Parse complete JSON-line records from one delivered evidence frame."""
    match = re.search(
        rf"<<<THYMIRA_UNTRUSTED:(?P<nonce>[0-9a-f]{{32}}):{label}:START>>>\n"
        rf"The following[^\n]*\n(?P<payload>.*?)\n"
        rf"<<<THYMIRA_UNTRUSTED:(?P=nonce):{label}:END>>>",
        prompt,
        re.DOTALL,
    )
    assert match is not None, f"the provider request must contain a complete {label} frame"
    records = []
    for line in match.group("payload").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("source"), str):
            records.append(value)
    return records


def _audit_visible_evidence(messages: tuple[LLMMessage, ...]) -> list[dict[str, str]]:
    """Use only profile/action references present in the audit agent's actual prompt."""
    prompt = next(message.content for message in reversed(messages) if message.role == "user")
    projection = _framed_json(prompt, "mira-evidence")
    audit_context = projection["audit_context"]
    assert isinstance(audit_context, dict)
    profile_context = audit_context["activity_profile_is_declared_context"]
    assert isinstance(profile_context, dict)
    profile_seq = profile_context["event_seq"]
    profile_hash = profile_context["event_hash"]
    raw_events = projection["events"]
    assert isinstance(raw_events, list)
    actions = []
    for value in raw_events:
        if not isinstance(value, str) or '"tool":"profile_dataset"' not in value:
            continue
        try:
            event = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            actions.append(event)
    action = next(
        event
        for event in actions
        if event.get("type") == EventType.TOOL_COMPLETED.value
        and event.get("payload", {}).get("tool") == "profile_dataset"
    )
    return [
        {"kind": "event", "ref": f"seq:{profile_seq}", "sha256": profile_hash},
        {
            "kind": "event",
            "ref": f"seq:{action['seq']}",
            "sha256": action["hash"],
        },
    ]


def _verification_visible_evidence(prompt: str) -> list[dict[str, str]]:
    """Pin verifier applicability to the exact profile and action records in its framed view."""
    projection = _framed_json(prompt, "mira-verification")
    audit_context = projection["audit_context"]
    assert isinstance(audit_context, dict)
    profile_context = audit_context["activity_profile_is_declared_context"]
    assert isinstance(profile_context, dict)
    events = projection["audit_evidence_events"]
    assert isinstance(events, list)
    action = next(
        event
        for event in events
        if isinstance(event, dict)
        and event.get("type") == EventType.TOOL_COMPLETED.value
        and event.get("payload", {}).get("tool") == "profile_dataset"
    )
    return [
        {
            "kind": "event",
            "ref": f"seq:{profile_context['event_seq']}",
            "sha256": profile_context["event_hash"],
        },
        {"kind": "event", "ref": f"seq:{action['seq']}", "sha256": action["hash"]},
    ]


def _assert_profile_action_visible(provider: ScriptedProvider, events: list[Any]) -> None:
    """Require MIRA's delivered frame to preserve the exact profile and tool event identities."""
    frames = [
        call["prompt"]
        for call in reversed(provider.calls)
        if ":mira-evidence:START>>>" in call["prompt"]
    ]
    assert frames, (
        "the real audit provider must receive a MIRA evidence frame; "
        f"provider_index={getattr(provider, '_index', None)}, "
        f"events={[event.type.value for event in events[-12:]]}, "
        f"provider_call_count={len(provider.calls)}"
    )
    prompt = frames[0]
    assert "[truncated]" not in prompt, (
        "the complete MIRA audit evidence frame must fit before framing; whole-frame tail "
        "truncation makes its JSON unusable and can hide the executed profile action"
    )
    projection = _framed_json(prompt, "mira-evidence")
    audit_context = projection["audit_context"]
    assert isinstance(audit_context, dict)
    profile_context = audit_context["activity_profile_is_declared_context"]
    assert isinstance(profile_context, dict)
    profile_event = next(
        event for event in reversed(events) if event.type is EventType.ACTIVITY_PROFILE_RECORDED
    )
    assert profile_context["event_seq"] == profile_event.seq
    projected_events = projection["events"]
    assert isinstance(projected_events, list)
    projected = []
    for value in projected_events:
        if not isinstance(value, str):
            continue
        try:
            event = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            projected.append(event)
    profile_projection = next(event for event in projected if event.get("seq") == profile_event.seq)
    assert profile_projection["type"] == EventType.ACTIVITY_PROFILE_RECORDED.value
    assert profile_projection["hash"] == profile_event.hash

    action = next(
        event
        for event in reversed(events)
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "profile_dataset"
    )
    action_projection = next(event for event in projected if event.get("seq") == action.seq)
    assert action_projection["type"] == EventType.TOOL_COMPLETED.value
    assert action_projection["hash"] == action.hash
    assert action_projection["payload"]["tool"] == "profile_dataset"
    assert action_projection["payload"]["status"] == "COMPLETED"
    assert action_projection["payload_truncated"] is True
    assert len(json.dumps(action_projection, separators=(",", ":"))) <= 2_000


def _audit_candidate(mode: list[str]):
    """Return candidates grounded in the actual records shown to the audit agent."""

    def respond(messages: tuple[LLMMessage, ...]) -> dict[str, object]:
        if mode[0] not in {"eda", "correlation"}:
            return {"findings": []}
        evidence = _audit_visible_evidence(messages)
        if mode[0] == "eda":
            findings = [
                {
                    "control_id": "SENSITIVE-ATTRIBUTE-IN-EDA",
                    "title": "Sensitive age feature is included in credit-data EDA",
                    "finding": (
                        "The registered credit-data EDA includes the sensitive age feature."
                    ),
                    "severity": "HIGH",
                    "confidence": 0.96,
                    "evidence": evidence,
                }
            ]
        elif mode[0] == "correlation":
            findings = [
                {
                    "control_id": "CLAIMED-AUTOMATED-DECISION",
                    "title": "The correlation request made an automated credit decision",
                    "finding": (
                        "The correlation profiling action automatically approved or denied "
                        "a credit application."
                    ),
                    "severity": "CRITICAL",
                    "confidence": 0.99,
                    "evidence": evidence,
                },
                {
                    "control_id": "UNRESOLVED-AGE-FEATURE-ASSESSMENT",
                    "title": "Age-feature handling in the correlation report is undocumented",
                    "finding": (
                        "The Run records an age-inclusive correlation profile, but does not "
                        "establish whether the report documents how this sensitive feature is "
                        "handled."
                    ),
                    "severity": "CRITICAL",
                    "confidence": 0.97,
                    "evidence": evidence,
                },
            ]
        return {"findings": findings}

    return respond


def _finding_verdict():
    """Refute only the operative-action claim; leave downstream applicability unresolved."""

    def respond(prompt: str) -> dict[str, object]:
        evidence = _verification_visible_evidence(prompt)
        if "Sensitive age feature" in prompt:
            return {
                "supported": True,
                "rationale": "The registered profile and executed report show the age column.",
                "applicability": {
                    "status": "applicable",
                    "rationale": "Age is declared sensitive in the credit-data activity profile.",
                    "concrete_action": "A human reviews the descriptive report before use.",
                    "evidence": evidence,
                },
            }
        if "automatically approved or denied" in prompt:
            return {
                "supported": False,
                "rationale": (
                    "The cited completed action is profile_dataset, which emits a descriptive "
                    "report and contains no decision operation."
                ),
                "applicability": {
                    "status": "not_applicable",
                    "rationale": (
                        "The recorded activity computes an aggregate correlation report and "
                        "does not decide an application."
                    ),
                    "concrete_action": "No operative credit decision is recorded in this Run.",
                    "evidence": evidence,
                },
            }
        if "Age-feature handling in the correlation report" in prompt:
            return {
                "supported": None,
                "rationale": (
                    "The source records the correlation profile, but the available report "
                    "evidence does not establish whether the report explains its treatment of age."
                ),
                "applicability": {
                    "status": "applicable",
                    "rationale": (
                        "The cited completed action computes the age-inclusive matrix and the "
                        "recorded activity profile marks age as sensitive."
                    ),
                    "concrete_action": (
                        "Review and document how the sensitive age feature is handled in this "
                        "descriptive correlation report."
                    ),
                    "evidence": evidence,
                },
            }
        raise AssertionError("the fixture encountered an unrecognized audit candidate")

    return respond


def _experiment_result_from_run(provider: _EvidenceScriptedProvider, _messages):
    """Report only the values emitted by the actual run_experiment tool event."""
    assert callable(provider.active_log)
    messages = _messages
    experiment_tool_call = next(
        call for message in messages for call in message.tool_calls if call.name == "run_experiment"
    )
    tool_result = next(
        message
        for message in messages
        if message.role == "tool" and message.tool_call_id == experiment_tool_call.id
    )
    completed = next(
        event
        for event in provider.active_log().events()
        if event.type is EventType.EXPERIMENT_COMPLETED
    )
    experiment = completed.payload["experiment"]
    assert experiment["model_artifact_id"] in tool_result.content
    assert experiment["tracker_run_id"] in tool_result.content
    expected_seed = experiment_tool_call.arguments["seed"]
    assert expected_seed == 7
    assert experiment["seed"] == expected_seed
    trained = next(
        event
        for event in provider.active_log().events()
        if event.type is EventType.MODEL_TRAINED
        and event.payload.get("model_artifact_id") == experiment["model_artifact_id"]
        and event.payload.get("tracker_run_id") == experiment["tracker_run_id"]
    )
    assert trained.payload["seed"] == expected_seed
    assert trained.payload["split"]["random_state"] == expected_seed
    assert trained.payload["estimator_args"]["random_state"] == expected_seed
    return ExperimentResult(
        parameters=experiment["parameters"],
        metrics=experiment["metrics"],
        seed=experiment["seed"],
        model_artifact_id=experiment["model_artifact_id"],
        tracker_run_id=experiment["tracker_run_id"],
    )


def _queue_model_training(provider: _EvidenceScriptedProvider, mode: list[str]) -> None:
    """Queue one real experiment specialist pass after the two profiling Runs."""
    judgment, classification = _risk_for_mode(mode[0])

    provider._responses.extend(
        [
            judgment,
            classification,
            classification,
            PlanOutput(
                tasks=(
                    AgentTask(
                        id="train-credit-baseline",
                        agent=ThyAgentKind.EXPERIMENT,
                        phase=ThyPhase.EXECUTE,
                        instruction=(
                            "Train a small local baseline on german_credit using is_high_risk."
                        ),
                    ),
                ),
                work_kind="train_new",
            ),
            LLMToolCall(
                id="integrated-credit-train",
                name="run_experiment",
                arguments={
                    "dataset": "german_credit",
                    "target_column": "is_high_risk",
                    "seed": 7,
                    "experiment_name": "integrated-credit-baseline",
                    "description": "Train the requested small synthetic credit baseline.",
                },
            ),
            partial(_experiment_result_from_run, provider),
            SynthesisNarrative(
                tradeoffs="One deterministic baseline gives a compact local reference.",
                limitations="The synthetic fixture is small and not suitable for deployment.",
            ),
            {"findings": []},
        ]
    )


def _evaluation_result_from_run(provider: _EvidenceScriptedProvider, messages):
    """Return the metrics artifact identity emitted by the real evaluator tool."""
    assert callable(provider.active_log)
    call = next(
        call for message in messages for call in message.tool_calls if call.name == "evaluate_model"
    )
    result = next(
        message
        for message in messages
        if message.role == "tool" and message.tool_call_id == call.id
    )
    completed = next(
        event
        for event in provider.active_log().events()
        if event.type is EventType.TOOL_COMPLETED
        and event.payload.get("tool") == "evaluate_model"
        and event.payload.get("status") == "COMPLETED"
    )
    started = next(
        event
        for event in provider.active_log().events()
        if event.type is EventType.TOOL_STARTED
        and event.payload.get("tool_call_id") == completed.payload["tool_call_id"]
    )
    assert started.payload["tool"] == call.name
    assert all(started.payload["arguments"][key] == value for key, value in call.arguments.items())
    assert completed.payload["value"]["kind"] == "success"
    artifact_id = completed.payload["value"]["value"]["artifact_id"]
    assert artifact_id in completed.payload["artifact_ids"]
    # The bridge exposes typed value.text; the output artifact ID stays in structured evidence.
    assert completed.payload["value"]["value"]["text"] in result.content
    return EvaluationResult(
        summary="Recorded holdout metrics were calculated for the prior model.",
        metrics_artifact_id=artifact_id,
    )


def _queue_model_evaluation(
    provider: _EvidenceScriptedProvider, mode: list[str], current_reference
) -> None:
    """Resolve the tool call from the frozen reference after Session publishes its Run."""
    judgment, classification = _risk_for_mode(mode[0])

    def evaluation_call(_messages):
        reference = current_reference()
        return LLMToolCall(
            id="integrated-credit-evaluation",
            name="evaluate_model",
            arguments={
                "model_artifact": reference.model_alias,
                "dataset": reference.dataset_alias,
                "target_column": reference.target_column,
                "evaluation_context_artifact": reference.context_alias,
                "description": "Calculate metrics on the original recorded holdout.",
                "split": "recorded_holdout",
            },
        )

    provider._responses.extend(
        [
            judgment,
            classification,
            classification,
            PlanOutput(
                tasks=(
                    AgentTask(
                        id="evaluate-prior-credit-model",
                        agent=ThyAgentKind.EVALUATION,
                        phase=ThyPhase.EXECUTE,
                        instruction=(
                            "Evaluate the existing model without fitting. Calculate precision, "
                            "recall, F1 and ROC-AUC on its recorded holdout."
                        ),
                    ),
                ),
                work_kind="evaluate_existing",
            ),
            evaluation_call,
            partial(_evaluation_result_from_run, provider),
            {"findings": []},
        ]
    )


def _make_provider(mode: list[str]):
    def candidate(_messages):
        return _audit_candidate(mode)(_messages)

    def verdict(prompt):
        return _finding_verdict()(prompt)

    first_run = [
        _mira_risk_judgment(),
        _risk_classification(),
        _risk_classification(),
        PlanOutput(
            tasks=(
                AgentTask(
                    id="credit-eda",
                    agent=ThyAgentKind.DATA,
                    phase=ThyPhase.EXECUTE,
                    instruction=(
                        "Profile german_credit with profile_dataset and summarize the shape "
                        "and missingness."
                    ),
                ),
            )
        ),
        LLMToolCall(
            id="eda-profile-call",
            name="profile_dataset",
            arguments={
                "dataset": "german_credit",
                "description": "Summarize registered credit data shape and missingness.",
            },
        ),
        LLMToolCall(
            id="eda-final",
            name="final_result",
            arguments={
                "columns": ["age", "duration_months", "credit_amount", "is_high_risk"],
                "dtypes": {
                    "age": "Int64",
                    "duration_months": "Int64",
                    "credit_amount": "Int64",
                    "is_high_risk": "Int64",
                },
                "missing": {"age": 0, "duration_months": 0, "credit_amount": 0, "is_high_risk": 0},
                "target_candidates": ["is_high_risk"],
            },
        ),
        candidate,
        verdict,
    ]
    second_run = [
        _mira_risk_judgment(),
        _risk_classification(),
        _risk_classification(),
        PlanOutput(
            tasks=(
                AgentTask(
                    id="credit-correlations",
                    agent=ThyAgentKind.DATA,
                    phase=ThyPhase.EXECUTE,
                    instruction=(
                        "Compute a separate numeric correlation matrix for german_credit "
                        "using profile_dataset."
                    ),
                ),
            )
        ),
        LLMToolCall(
            id="correlation-profile-call",
            name="profile_dataset",
            arguments={
                "dataset": "german_credit",
                "description": (
                    "Compute and preserve the numeric correlation matrix for this follow-up."
                ),
            },
        ),
        LLMToolCall(
            id="correlation-final",
            name="final_result",
            arguments={
                "columns": ["age", "duration_months", "credit_amount", "is_high_risk"],
                "dtypes": {
                    "age": "Int64",
                    "duration_months": "Int64",
                    "credit_amount": "Int64",
                    "is_high_risk": "Int64",
                },
                "missing": {"age": 0, "duration_months": 0, "credit_amount": 0, "is_high_risk": 0},
                "target_candidates": ["is_high_risk"],
            },
        ),
        candidate,
        verdict,
        verdict,
    ]

    return _EvidenceScriptedProvider(first_run + second_run)


def _production_deps(
    tmp_path: Path, workspace: Path, provider: _EvidenceScriptedProvider, mode: list[str]
) -> RuntimeDeps:
    assembled: list[RuntimeDeps] = []
    factories: list[GraphFactory] = []
    registry = builtins_registry(
        sandbox=ContainerSandbox(image=os.environ.get("THYMIRA_TEST_SANDBOX_IMAGE", "thymira:dev")),
        subprocess_mode=SandboxMode.WORKSPACE_WRITE,
    )
    readiness_status = readiness.python_runtime_status(registry)
    if readiness_status.status != "ready":
        pytest.skip(
            "the composed model-tool stage requires a ready Docker sandbox: "
            f"{readiness_status.code}: {readiness_status.summary} {readiness_status.next_step}"
        )

    def prepare_model_inputs(item, store, log, selected):
        materialize_model_inputs(
            tmp_path / "runtime",
            item,
            store,
            log,
            get_run=assembled[0].run_store.get,
            events=assembled[0].event_store,
            selected_model_ids=selected,
        )

    def graph_factory(run: Run) -> CompiledStateGraph:
        deps = assembled[0]
        provider.active_run[:] = [run.id]
        if not factories:
            spec = AuditAgentSpec(
                name="integrated-credit-risk-auditor",
                framework=Framework.CREDIT_RISK,
                task_kinds=("audit_judgement",),
                max_turns=1,
                system_prompt="Report only evidence-grounded credit-data profile findings.",
            )
            assert deps.project_resolution is not None
            factories.append(
                build_runtime_graph_factory(
                    deps.run_store,
                    gate_factory=deps.gate_factory,
                    artifact_store_factory=deps.artifact_store_factory,
                    tool_manager=ToolManager(registry),
                    tool_registry=registry,
                    mira_tool_registry=deps.mira_tool_registry,
                    record_repository=deps.record_repository,
                    checkpoint_repository=deps.checkpoint_repository,
                    provider=provider,
                    project_dir=workspace,
                    project_config=deps.project_resolution.config,
                    catalog=full_agent_catalog(),
                    specs=(spec,),
                    risk_interview=deps.risk_interview,
                    activity_profile_factory=lambda item: _complete_sensitive_profile(item, mode),
                    discovery=DiscoveryLimits(max_rounds=1),
                    plan_repository=deps.board_repository,
                    dag_repository=deps.dag_repository,
                    prepare_model_inputs=prepare_model_inputs,
                )
            )
        return factories[0](run)

    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        graph_factory=graph_factory,
        principal_resolver=TEST_CREDENTIAL,
        provider=provider,
    )
    assembled.append(deps)
    provider.active_log = lambda: deps.event_store.open(provider.active_run[0])
    return deps


def _approve_waiting_reviews(client, deps: RuntimeDeps, session_id: str, run_id: str) -> list[str]:
    approved_rules: list[str] = []
    for _ in range(8):
        detail = client.get(f"/sessions/{session_id}").json()
        turn = next(item for item in detail["turns"] if item["run"]["id"] == run_id)
        if turn["state"]["condition"] != "waiting":
            return approved_rules
        transition = next(
            event
            for event in reversed(deps.event_store.read(run_id))
            if event.type is EventType.RUN_TRANSITIONED
            and event.payload.get("command") == "wait_for_approval"
        )
        decision = transition.payload["policy_decision"]
        approved_rules.append(decision["rule_id"])
        approved = client.post(
            f"/runs/{run_id}/approve",
            json={
                "expected_decision_id": decision["id"],
                "note": "Reviewed the exact local evidence and approved this scoped analysis.",
            },
        )
        assert approved.status_code == 202, approved.text
    raise AssertionError(f"Run {run_id} kept requesting approval after eight public resolutions")


def _send_profile_run(
    client, deps: RuntimeDeps, provider: ScriptedProvider, session_id: str, message: str, key: str
):
    sent = client.post(
        f"/sessions/{session_id}/messages",
        json={"message": message, "client_message_id": key},
    )
    assert sent.status_code == 201, sent.text
    run_id = sent.json()["id"]
    approvals = _approve_waiting_reviews(client, deps, session_id, run_id)
    detail = client.get(f"/sessions/{session_id}").json()
    turn = next(item for item in detail["turns"] if item["run"]["id"] == run_id)
    events = deps.event_store.read(run_id)
    if not any(":mira-evidence:START>>>" in call["prompt"] for call in provider.calls):
        pytest.fail(
            f"Run {run_id} did not send MIRA an audit evidence frame; "
            f"outcome={turn['state']['outcome']}, provider_calls={len(provider.calls)}"
        )
    _assert_profile_action_visible(provider, events)
    if turn["state"]["outcome"] != "completed":
        decisions = [
            (event.payload.get("decision"), event.payload.get("rule_id"))
            for event in events
            if event.type is EventType.POLICY_DECISION
        ]
        audit_statuses = [
            event.payload.get("audit_report", {}).get("status")
            for event in events
            if event.type is EventType.AUDIT_COMPLETED
        ]
        recent_events = [
            (
                event.seq,
                event.type.value,
                event.payload.get("tool"),
                event.payload.get("status"),
            )
            for event in events[-10:]
        ]
        pytest.fail(
            json.dumps(
                {
                    "outcome": turn["state"]["outcome"],
                    "run_error": deps.run_service.get_run(run_id).error,
                    "audit_statuses": audit_statuses,
                    "decisions": decisions,
                    "recent_events": recent_events,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    assert verify_events(events).valid
    assert any(event.type is EventType.AUDIT_COMPLETED for event in events)
    assert any(event.type is EventType.HUMAN_APPROVAL for event in events)
    return run_id, events, approvals


def _acceptance_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[RuntimeDeps, Any, _EvidenceScriptedProvider, list[str], str]:
    """Create the real Session API, project policy and deterministic provider fixture."""
    for name in (
        "THYMIRA_ALLOWED_MODEL_ROUTES",
        "THYMIRA_MODEL_FAST",
        "THYMIRA_MODEL_STANDARD",
        "THYMIRA_MODEL_FRONTIER",
    ):
        monkeypatch.setenv(name, "test-model")
    workspace = tmp_path / "workspace"
    config_dir = workspace / ".thymira"
    config_dir.mkdir(parents=True)
    shutil.copyfile(_REPO / "examples/credit-risk/.thymira/config.yaml", config_dir / "config.yaml")
    shutil.copyfile(
        _REPO / "examples/credit-risk/.thymira/policies.yaml", config_dir / "policies.yaml"
    )
    mode = ["eda"]
    provider = _make_provider(mode)
    deps = _production_deps(tmp_path, workspace, provider, mode)
    client = authenticated_client(create_app(deps))
    session_id = client.post("/sessions", json={}).json()["id"]
    upload = client.put(
        "/project/datasets/german_credit?filename=applications.csv", content=_CREDIT_CSV
    )
    assert upload.status_code == 200, upload.text
    return deps, client, provider, mode, session_id


def _assert_completed_control_inventory(events: list[Any]) -> None:
    """Require the persisted audit to evaluate every applicable control."""
    audit = next(event for event in events if event.type is EventType.AUDIT_COMPLETED)
    report = audit.payload["audit_report"]
    assert report["status"] != "incomplete"
    controls = report["controls"]
    control_ids = [item["control_id"] for item in controls]
    expected_ids = {control.control_id for control in CONTROLS} - {"A2", "A7", "A8"}
    assert len(control_ids) == len(set(control_ids))
    assert expected_ids <= set(control_ids)
    assert all(item["status"] != ControlStatus.NOT_EVALUATED.value for item in controls)


def _assert_initial_eda(
    deps: RuntimeDeps, client: Any, provider: ScriptedProvider, session_id: str
) -> str:
    """Run real registered-dataset EDA and check its completed audit evidence."""
    eda_id, eda_events, eda_approvals = _send_profile_run(
        client,
        deps,
        provider,
        session_id,
        "Inspect german_credit and summarize the data shape and missingness.",
        "integrated-credit-eda",
    )
    assert "CRX-102" in eda_approvals
    eda_profile = json.loads(
        deps.artifact_store_factory(eda_id).load_bytes("profile/german_credit.json")
    )
    assert eda_profile["shape"] == {"rows": 80, "columns": 4}
    assert eda_profile["source"]["sha256"]
    eda_artifact = deps.artifact_store_factory(eda_id).get("profile/german_credit.json")
    assert eda_artifact is not None
    _assert_completed_control_inventory(eda_events)
    return eda_artifact.id


def _assert_correlation_review(
    deps: RuntimeDeps,
    client: Any,
    provider: ScriptedProvider,
    session_id: str,
    eda_artifact_id: str,
) -> str:
    """Run the follow-up correlation profile and require the public scoped review."""
    correlation_id, correlation_events, correlation_approvals = _send_profile_run(
        client,
        deps,
        provider,
        session_id,
        "Compute a separate numeric correlation matrix for german_credit and report the result.",
        "integrated-credit-correlations",
    )
    assert "GOV-101-INDETERMINATE" in correlation_approvals
    correlation_store = deps.artifact_store_factory(correlation_id)
    correlation_profile = json.loads(correlation_store.load_bytes("profile/german_credit.json"))
    assert correlation_profile["correlation"]
    assert "age" in correlation_profile["correlation"]
    correlation_artifact = correlation_store.get("profile/german_credit.json")
    assert correlation_artifact is not None
    assert correlation_artifact.id != eda_artifact_id
    correlation_tool_start = next(
        event
        for event in correlation_events
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "profile_dataset"
    )
    assert "correlation matrix" in correlation_tool_start.payload["arguments"]["description"]

    correlation_audit = next(
        event for event in correlation_events if event.type is EventType.AUDIT_COMPLETED
    )
    _assert_completed_control_inventory(correlation_events)
    report = correlation_audit.payload["audit_report"]
    assessment_by_id = {item["finding_id"]: item for item in report["applicability_assessments"]}
    assert len(assessment_by_id) == 2
    unresolved = next(item for item in assessment_by_id.values() if item["supported"] is None)
    retained = next(
        finding for finding in report["findings"] if finding["id"] == unresolved["finding_id"]
    )
    assert retained["control_id"] == "model:UNRESOLVED-AGE-FEATURE-ASSESSMENT"
    assert retained["severity"] == "CRITICAL"
    assert unresolved["applicability"]["status"] == "applicable"
    assert "descriptive correlation report" in unresolved["applicability"]["concrete_action"]
    assert "age-inclusive matrix" in unresolved["applicability"]["rationale"]
    refuted = next(item for item in assessment_by_id.values() if item["supported"] is False)
    assert refuted["applicability"]["status"] == "not_applicable"
    assert "profile_dataset" in refuted["verification_rationale"]
    pending_transition = next(
        event
        for event in reversed(correlation_events)
        if event.type is EventType.RUN_TRANSITIONED
        and event.payload.get("wait_reason") == "approval"
    )
    decision = pending_transition.payload["policy_decision"]
    assert decision["rule_id"] == "GOV-101-INDETERMINATE"
    assert decision["decision"] == "REQUIRE_HUMAN_REVIEW"
    request = next(
        event
        for event in correlation_events
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
        and event.payload.get("decision_id") == decision["id"]
    )
    request_context = request.payload["finding_review_context"]
    assert request_context["eligible_finding_ids"] == [unresolved["finding_id"]]
    assert [item["finding_id"] for item in request_context["candidates"]] == [
        unresolved["finding_id"]
    ]
    assert request_context["candidates"][0]["supported"] is None
    assert request_context["candidates"][0]["applicability_status"] == "applicable"
    approval = next(
        event
        for event in correlation_events
        if event.type is EventType.HUMAN_APPROVAL
        and event.payload.get("decision_id") == decision["id"]
    )
    assert approval.payload["automatic"] is False
    assert approval.actor.kind.value == "human"
    assert approval.payload["decision_id"] == decision["id"]
    policy_hash = report["policy_sha256"]
    assert policy_hash == deps.gate_factory(correlation_id).engine.policy_sha256
    assert deps.project_resolution is not None
    assert deps.project_resolution.config.project.domain == "credit_risk"
    return correlation_id


def _assert_explanation_clarification_and_repair(
    deps: RuntimeDeps,
    client: Any,
    session_id: str,
    correlation_run_id: str,
) -> tuple[Any, Any, _ClarifiedRepairProvider]:
    """Resolve an evidence question, repair its invalid citation, and charge both calls."""
    provider = _ClarifiedRepairProvider(correlation_run_id)
    deps.session_chat.provider = provider
    original_runs = deps.session_service.get_session(session_id).run_ids
    first = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain the correlation results briefly.",
            "client_message_id": "integrated-explain-correlation",
            "mode": "ask",
        },
    )
    assert first.status_code == 201, first.text
    clarification = first.json()
    assert clarification["kind"] == "clarification"
    assert clarification["run_id"] is None

    answer_response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Exacto",
            "client_message_id": "integrated-exacto-correlation",
            "mode": "auto",
        },
    )
    assert answer_response.status_code == 201, answer_response.text
    answer = answer_response.json()
    assert answer["kind"] == "answer", {
        "response": answer,
        "provider_index": provider._index,
        "provider_calls": [
            {"prompt_tail": call["prompt"][-180:], "system_tail": call["system"][-140:]}
            for call in provider.calls
        ],
    }
    assert answer["run_id"] is None
    assert answer["citations"]
    assert {item["run_id"] for item in answer["citations"]} == {correlation_run_id}
    assert deps.session_service.get_session(session_id).run_ids == original_runs
    assert len(provider.calls) == 3
    assert "Explain the correlation results briefly." in provider.calls[0]["prompt"]
    assert "Exacto" in provider.calls[1]["prompt"]
    assert "Explain the correlation results briefly." in provider.calls[1]["prompt"]
    assert "¿Te refieres a la matriz de correlaciones?" in provider.calls[1]["prompt"]
    assert "SERVER CITATION REPAIR" in provider.calls[2]["prompt"]
    assert "Exacto" in provider.calls[2]["prompt"]
    assert all(call["max_output_tokens"] > 0 for call in provider.calls)

    repository = LocalDiscussionRepository(deps.session_chat.root, session_id)
    clarification_record = next(
        item for item in repository.records() if item.discussion.id == clarification["id"]
    )
    answer_record = next(
        item for item in repository.records() if item.discussion.id == answer["id"]
    )
    clarification_log = SessionProviderLog(deps.session_chat.root, session_id, clarification["id"])
    answer_log = SessionProviderLog(deps.session_chat.root, session_id, answer["id"])
    clarification_events = clarification_log.events()
    answer_events = answer_log.events()
    assert clarification_record.clarification_scope == "evidence"
    assert answer_record.discussion.user_message == "Exacto"
    assert answer_record.discussion.mode == "auto"
    assert (
        sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in clarification_events) == 1
    )
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in answer_events) == 2
    usage = [
        event.payload
        for event in (*clarification_events, *answer_events)
        if event.type is EventType.SESSION_MODEL_USAGE
    ]
    assert len(usage) == 3
    assert all(item["usage_status"] == "scripted_estimate" for item in usage)
    assert all(item["input_tokens"] > 0 and item["output_tokens"] > 0 for item in usage)
    assert all(item["usage_source"] == "scripted_local_estimate" for item in usage)
    assert session_usage_snapshot(answer_log).tokens <= 16_000
    assert usage[1]["repair_projection_tokens"] > 0
    assert usage[1]["reserved_for_repair_tokens"] == usage[1]["repair_projection_tokens"]
    expected_inputs = [
        estimate_structured_input_tokens(
            "test-model",
            call["prompt"],
            call["system"],
            schema,
        )
        for call, schema in zip(provider.calls, provider.schemas, strict=True)
    ]
    assert [item["input_tokens"] for item in usage] == expected_inputs
    assert session_usage_snapshot(clarification_log).tokens == sum(
        (usage[0]["input_tokens"], usage[0]["output_tokens"])
    )
    assert session_usage_snapshot(answer_log).tokens == sum(
        item["input_tokens"] + item["output_tokens"] for item in usage[1:]
    )
    return clarification, answer, provider


def _assert_training_execution(events: list[Any], approvals: list[str]) -> dict[str, Any]:
    """Check authenticated approval, specialist execution and observed sandbox evidence."""
    assert verify_events(events).valid
    _assert_completed_control_inventory(events)
    assert "GOV-007" in approvals
    human_approval = next(
        event
        for event in events
        if event.type is EventType.HUMAN_APPROVAL and event.actor.kind.value == "human"
    )
    assert human_approval.payload["approved"] is True
    assert human_approval.payload["automatic"] is False
    gov_007 = next(
        event
        for event in events
        if event.type is EventType.POLICY_DECISION and event.payload.get("rule_id") == "GOV-007"
    )
    assert human_approval.payload["decision_id"] == gov_007.payload["id"]
    started_agents = [
        event.payload["agent"] for event in events if event.type is EventType.AGENT_STARTED
    ]
    assert started_agents.count("experiment") == 1
    assert set(started_agents) <= {"experiment", "integrated-credit-risk-auditor"}
    starts = [
        event
        for event in events
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "run_experiment"
    ]
    assert len(starts) == 1
    assert starts[0].payload["arguments"]["dataset"] == "german_credit"
    assert starts[0].payload["arguments"]["target_column"] == "is_high_risk"
    completed = [
        event
        for event in events
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "run_experiment"
    ]
    assert len(completed) == 1
    tool = completed[0].payload
    assert tool["status"] == "COMPLETED"
    assert tool["sandbox_mode"] == "workspace_write"
    assert tool["sandbox_enforcement"] == "partial"
    assert tool["sandbox_cleanup_confirmed"] is True
    assert tool["sandbox_spec"]["backend"] == "container"
    assert tool["sandbox_spec"]["network"] == "none"
    assert {"rlimits", "workspace_quota"} <= set(tool["sandbox_spec"]["unenforced"])
    termination = tool["sandbox_termination"]
    assert termination["control_channel_validated"] is True
    assert termination["tree_scope"] == "container"
    assert termination["outcome"] == "completed"
    assert termination["probe"]["network"] == "none"
    assert termination["probe"]["read_only_rootfs"] is True
    assert termination["reap"] == "quiesced"
    trained = [event for event in events if event.type is EventType.MODEL_TRAINED]
    assert len(trained) == 1
    experiment_event = next(
        event for event in events if event.type is EventType.EXPERIMENT_COMPLETED
    )
    experiment = experiment_event.payload["experiment"]
    assert experiment["parameters"]["dataset"] == "german_credit"
    assert experiment["parameters"]["target_column"] == "is_high_risk"
    assert trained[0].payload["model_artifact_id"] == experiment["model_artifact_id"]
    return experiment


def _assert_training_artifact_lineage(
    deps: RuntimeDeps, run_id: str, experiment: dict[str, Any]
) -> None:
    """Verify hashes and registered dataset/model identities emitted by training."""
    store = deps.artifact_store_factory(run_id)
    model = next(
        item for item in store.manifest().values() if item.id == experiment["model_artifact_id"]
    )
    assert model.kind.value == "model"
    model_bytes = store.load_bytes(model.name)
    assert model.sha256 == hashlib.sha256(model_bytes).hexdigest()
    dataset = store.get("datasets/german_credit.csv")
    assert dataset is not None
    assert dataset.sha256 == hashlib.sha256(_CREDIT_CSV).hexdigest()
    assert experiment["dataset_artifact_id"] == dataset.id


def _assert_original_training_request_runs_real_experiment(
    deps: RuntimeDeps,
    client: Any,
    provider: _EvidenceScriptedProvider,
    mode: list[str],
    session_id: str,
) -> tuple[str, dict[str, Any]]:
    """Publish the original Spanish request and execute the governed experiment specialist."""
    mode[0] = "training"
    assert provider._index == len(provider._responses)
    _queue_model_training(provider, mode)
    chat_provider = ScriptedProvider(
        [{"kind": "action", "message": "", "sources": [], "work_kind": "train_new"}],
        model="test-model",
    )
    deps.session_chat.provider = chat_provider
    message = "Realiza un modelo basico sobre el dataset"
    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": message,
            "client_message_id": "integrated-original-training-request",
            "mode": "auto",
        },
    )
    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["kind"] == "action"
    run_id = discussion["run_id"]
    assert run_id
    approvals = _approve_waiting_reviews(client, deps, session_id, run_id)
    turn = next(
        item
        for item in client.get(f"/sessions/{session_id}").json()["turns"]
        if item["run"]["id"] == run_id
    )
    if turn["state"]["outcome"] != "completed":
        events = deps.event_store.read(run_id)
        recent = [
            (event.seq, event.type.value, event.payload.get("tool"), event.payload.get("error"))
            for event in events[-8:]
        ]
        pytest.fail(f"training Run {run_id} did not complete; approvals={approvals}; {recent}")

    repository = LocalDiscussionRepository(deps.session_chat.root, session_id)
    saved = next(item for item in repository.records() if item.discussion.id == discussion["id"])
    run = deps.run_service.get_run(run_id)
    assert run.conversation is not None
    assert run.conversation.message == message
    assert run.conversation.requested_work == message
    assert run.conversation.work_kind == "train_new"
    assert run.conversation.model_inputs == ()
    assert saved.work_kind == "train_new"
    events = deps.event_store.read(run_id)
    experiment = _assert_training_execution(events, approvals)
    _assert_training_artifact_lineage(deps, run_id, experiment)
    return run_id, experiment


def _assert_evaluation_execution(
    deps: RuntimeDeps, run_id: str, reference: Any, approvals: list[str]
) -> tuple[list[Any], Any]:
    """Check the evaluator agent, its public authorization and exact tool arguments."""
    events = deps.event_store.read(run_id)
    assert verify_events(events).valid
    _assert_completed_control_inventory(events)
    assert "GOV-008" in approvals
    tool_request = next(
        event
        for event in events
        if event.type is EventType.HUMAN_APPROVAL_REQUESTED
        and event.payload.get("tool") == "evaluate_model"
    )
    approval = next(
        event
        for event in events
        if event.type is EventType.HUMAN_APPROVAL
        and event.payload.get("decision_id") == tool_request.payload["decision_id"]
    )
    assert approval.actor.kind.value == "human"
    assert approval.payload["approved"] is True
    assert approval.payload["automatic"] is False
    assert not any(
        event.type in {EventType.MODEL_TRAINED, EventType.EXPERIMENT_COMPLETED} for event in events
    )
    agents = [event.payload["agent"] for event in events if event.type is EventType.AGENT_STARTED]
    assert agents.count("evaluation") == 1
    assert set(agents) <= {"evaluation", "integrated-credit-risk-auditor"}
    starts = [
        event
        for event in events
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "evaluate_model"
    ]
    assert len(starts) == 1
    arguments = starts[0].payload["arguments"]
    assert arguments["model_artifact"] == reference.model_alias
    assert arguments["dataset"] == reference.dataset_alias
    assert arguments["target_column"] == reference.target_column
    assert arguments["split"] == "recorded_holdout"
    completions = [
        event
        for event in events
        if event.type is EventType.TOOL_COMPLETED and event.payload.get("tool") == "evaluate_model"
    ]
    assert len(completions) == 1
    completion = completions[0].payload
    assert completion["status"] == "COMPLETED"
    assert completion["value"]["kind"] == "success"
    assert completion["sandbox_mode"] == "workspace_write"
    assert completion["sandbox_enforcement"] == "partial"
    assert completion["sandbox_cleanup_confirmed"] is True
    assert completion["sandbox_spec"]["backend"] == "container"
    assert completion["sandbox_spec"]["network"] == "none"
    assert completion["sandbox_termination"]["probe"]["network"] == "none"
    return events, completion


def _assert_materialized_prior_inputs(
    deps: RuntimeDeps, run_id: str, source_run_id: str, reference: Any, store: Any
) -> tuple[Any, Any, Any]:
    """Verify copied prior inputs preserve source identities and content lineage."""
    materialized_model = store.get(reference.model_alias)
    materialized_dataset = store.get(f"datasets/{reference.dataset_alias}.csv")
    materialized_schema = store.get(f"datasets/{reference.dataset_alias}.schema.json")
    assert materialized_model is not None
    assert materialized_dataset is not None
    assert materialized_schema is not None
    source_store = deps.artifact_store_factory(source_run_id)
    source_model = source_store.get(reference.model_name)
    source_dataset = next(
        item
        for item in source_store.manifest().values()
        if item.id == reference.dataset_artifact_id
    )
    source_schema = next(
        item for item in source_store.manifest().values() if item.id == reference.schema_artifact_id
    )
    assert source_model is not None
    assert source_model.id == reference.model_artifact_id
    assert source_model.sha256 == reference.model_sha256
    assert source_dataset.sha256 == reference.dataset_sha256
    assert source_schema.sha256 == reference.schema_sha256
    assert materialized_model.name == reference.model_alias
    assert materialized_dataset.sha256 == source_dataset.sha256
    assert materialized_schema.kind.value == "other"
    assert materialized_model.sha256 == source_model.sha256
    assert set(materialized_model.input_artifact_ids) == {
        materialized_dataset.id,
        materialized_schema.id,
    }
    assert hashlib.sha256(store.load_bytes(materialized_model.name)).hexdigest() == (
        reference.model_sha256
    )
    assert hashlib.sha256(store.load_bytes(materialized_dataset.name)).hexdigest() == (
        reference.dataset_sha256
    )
    source_schema_value = json.loads(source_store.load_bytes(source_schema.name))
    copied_schema_value = json.loads(store.load_bytes(materialized_schema.name))
    source_schema_value.pop("name", None)
    source_schema_value.pop("artifact_name", None)
    copied_schema_value.pop("name", None)
    copied_schema_value.pop("artifact_name", None)
    assert copied_schema_value == source_schema_value
    events = deps.event_store.read(run_id)
    for copied, original in (
        (materialized_model, source_model),
        (materialized_dataset, source_dataset),
        (materialized_schema, source_schema),
    ):
        created = next(
            event
            for event in events
            if event.type is EventType.ARTIFACT_CREATED
            and event.payload.get("artifact_id") == copied.id
        )
        assert created.payload["input_copy"] is True
        assert created.payload["source_run_id"] == source_run_id
        assert created.payload["source_artifact_ids"] == [original.id]
        assert created.payload["sha256"] == copied.sha256
    return materialized_model, materialized_dataset, materialized_schema


def _assert_evaluation_metrics(
    deps: RuntimeDeps, run_id: str, source_run_id: str, reference: Any, metrics_id: str
) -> Any:
    """Verify held-out metrics point to the frozen model, dataset and schema revisions."""
    store = deps.artifact_store_factory(run_id)
    artifact = next(item for item in store.manifest().values() if item.id == metrics_id)
    assert artifact.kind.value == "metrics"
    payload = json.loads(store.load_bytes(artifact.name))
    materialized_model, materialized_dataset, materialized_schema = (
        _assert_materialized_prior_inputs(deps, run_id, source_run_id, reference, store)
    )
    assert set(payload["metrics"]) == {"accuracy", "precision", "recall", "f1", "roc_auc"}
    method = payload["methodology"]
    assert method["split"] == "recorded_holdout"
    assert method["is_holdout"] is True
    assert method["evaluated_rows"] == 20
    assert method["source_run_id"] == source_run_id
    assert method["source_model_artifact_id"] == reference.model_artifact_id
    assert method["target_column"] == reference.target_column
    assert payload["model"]["artifact"] == materialized_model.name
    assert payload["model"]["artifact_id"] == materialized_model.id
    assert payload["model"]["sha256"] == materialized_model.sha256
    assert payload["dataset"]["artifact_id"] == materialized_dataset.id
    assert payload["dataset"]["sha256"] == materialized_dataset.sha256
    assert payload["dataset"]["schema_artifact"] == materialized_schema.name
    assert payload["dataset"]["schema_sha256"] == materialized_schema.sha256
    return artifact


def _assert_prior_model_evaluation(
    deps: RuntimeDeps,
    client: Any,
    provider: _EvidenceScriptedProvider,
    mode: list[str],
    session_id: str,
    source_run_id: str,
    source_experiment: dict[str, Any],
) -> tuple[str, Any, Any]:
    """Evaluate the frozen training artifact on its recorded holdout through SessionChat."""
    mode[0] = "evaluation"
    assert provider._index == len(provider._responses)
    chat_provider = ScriptedProvider(
        [{"kind": "action", "message": "", "sources": [], "work_kind": "train_new"}],
        model="test-model",
    )
    deps.session_chat.provider = chat_provider

    def current_reference():
        active_run = deps.run_service.get_run(provider.active_run[0])
        assert active_run.conversation is not None
        assert len(active_run.conversation.model_inputs) == 1
        return active_run.conversation.model_inputs[0]

    _queue_model_evaluation(provider, mode, current_reference)
    request = "Calculate precision, recall, F1, and ROC-AUC for the previous model"
    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": request,
            "client_message_id": "integrated-prior-model-evaluation",
            "mode": "auto",
        },
    )
    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["kind"] == "action"
    run_id = discussion["run_id"]
    assert run_id
    run = deps.run_service.get_run(run_id)
    assert run.conversation is not None
    assert run.conversation.message == request
    assert run.conversation.requested_work == request
    assert run.conversation.work_kind == "evaluate_existing"
    assert len(run.conversation.model_inputs) == 1
    reference = run.conversation.model_inputs[0]
    assert reference.source_run_id == source_run_id
    assert reference.model_artifact_id == source_experiment["model_artifact_id"]
    assert reference.model_sha256
    assert reference.dataset_name == "german_credit"
    assert reference.target_column == "is_high_risk"
    source_events = tuple(deps.event_store.read(source_run_id))
    trained = next(event for event in source_events if event.type is EventType.MODEL_TRAINED)
    assert reference.training_seq == trained.seq
    assert reference.training_hash == trained.hash
    approvals = _approve_waiting_reviews(client, deps, session_id, run_id)
    turn = next(
        item
        for item in client.get(f"/sessions/{session_id}").json()["turns"]
        if item["run"]["id"] == run_id
    )
    if turn["state"]["outcome"] != "completed":
        events = deps.event_store.read(run_id)
        recent = [
            (event.seq, event.type.value, event.payload.get("tool"), event.payload.get("error"))
            for event in events[-8:]
        ]
        pytest.fail(
            f"evaluation Run {run_id} did not complete; error={run.error}; "
            f"approvals={approvals}; recent={recent}"
        )
    saved = next(
        item
        for item in LocalDiscussionRepository(deps.session_chat.root, session_id).records()
        if item.discussion.id == discussion["id"]
    )
    assert saved.work_kind == "evaluate_existing"
    assert saved.discussion.user_message == request
    events, completion = _assert_evaluation_execution(deps, run_id, reference, approvals)
    artifact = _assert_evaluation_metrics(
        deps,
        run_id,
        source_run_id,
        reference,
        completion["value"]["value"]["artifact_id"],
    )
    assert tuple(deps.event_store.read(source_run_id)) == source_events
    assert chat_provider._index == 1
    assert len(chat_provider.calls) == 1
    return run_id, reference, artifact


def _session_state_fingerprint(deps: RuntimeDeps, session_id: str) -> tuple[Any, ...]:
    """Capture persisted runs, event identities, discussions and usage for replay comparison."""
    run_ids = tuple(deps.session_service.get_session(session_id).run_ids)
    runs = tuple(
        (
            run_id,
            deps.run_service.get_run(run_id).model_dump(mode="json"),
            deps.run_service.get_run_state(run_id).model_dump(mode="json"),
        )
        for run_id in run_ids
    )
    event_identities = tuple(
        (
            run_id,
            tuple(
                (event.seq, event.type.value, event.hash) for event in deps.event_store.read(run_id)
            ),
        )
        for run_id in run_ids
    )
    records = LocalDiscussionRepository(deps.session_chat.root, session_id).records()
    discussions = tuple(item.model_dump(mode="json") for item in records)
    usage = tuple(
        (
            item.discussion.id,
            session_usage_snapshot(
                SessionProviderLog(deps.session_chat.root, session_id, item.discussion.id)
            ),
        )
        for item in records
    )
    return run_ids, runs, event_identities, discussions, usage


def _assert_reconstructed_replays_are_idempotent(
    original_deps: RuntimeDeps,
    tmp_path: Path,
    workspace: Path,
    provider: _EvidenceScriptedProvider,
    mode: list[str],
    session_id: str,
    replay_ids: tuple[str, str],
) -> None:
    """Reopen the persisted services and replay two client keys without new work or charges."""
    before_provider_index = provider._index
    before_provider_calls = len(provider.calls)
    original = _session_state_fingerprint(original_deps, session_id)
    reopened_provider = _make_provider(mode)
    reopened = _production_deps(tmp_path, workspace, reopened_provider, mode)
    empty_chat_provider = ScriptedProvider([], model="test-model")
    reopened.session_chat.provider = empty_chat_provider
    client = authenticated_client(create_app(reopened))
    before = _session_state_fingerprint(reopened, session_id)
    assert before == original
    training_run_id, exacto_discussion_id = replay_ids
    records = LocalDiscussionRepository(reopened.session_chat.root, session_id).records()
    training_record = next(
        item
        for item in records
        if item.discussion.user_message == "Realiza un modelo basico sobre el dataset"
    )

    training_replay = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Realiza un modelo basico sobre el dataset",
            "client_message_id": "integrated-original-training-request",
            "mode": "auto",
        },
    )
    assert training_replay.status_code in {200, 201}, training_replay.text
    assert training_replay.json()["id"] == training_record.discussion.id
    assert training_replay.json()["run_id"] == training_run_id
    exacto_replay = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Exacto",
            "client_message_id": "integrated-exacto-correlation",
            "mode": "auto",
        },
    )
    assert exacto_replay.status_code in {200, 201}, exacto_replay.text
    assert exacto_replay.json()["id"] == exacto_discussion_id
    assert exacto_replay.json()["citations"]

    after = _session_state_fingerprint(reopened, session_id)
    assert after == before
    assert empty_chat_provider.calls == []
    assert reopened_provider.calls == []
    assert provider._index == before_provider_index
    assert len(provider.calls) == before_provider_calls


def test_six_stage_session_acceptance_preserves_real_work_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose six session stages with real MIRA, Gate and a local Docker tool sandbox.

    Requires the Docker daemon and the image selected by ``THYMIRA_TEST_SANDBOX_IMAGE``
    (default ``thymira:dev``). The sandbox records observed enforcement; the test does not infer
    confinement from the requested mode.
    """
    deps, client, provider, mode, session_id = _acceptance_harness(tmp_path, monkeypatch)
    eda_artifact_id = _assert_initial_eda(deps, client, provider, session_id)
    mode[0] = "correlation"
    correlation_id = _assert_correlation_review(deps, client, provider, session_id, eda_artifact_id)
    _, exacto, _ = _assert_explanation_clarification_and_repair(
        deps, client, session_id, correlation_id
    )
    training_run_id, experiment = _assert_original_training_request_runs_real_experiment(
        deps, client, provider, mode, session_id
    )
    _assert_prior_model_evaluation(
        deps,
        client,
        provider,
        mode,
        session_id,
        training_run_id,
        experiment,
    )
    _assert_reconstructed_replays_are_idempotent(
        deps,
        tmp_path,
        tmp_path / "workspace",
        provider,
        mode,
        session_id,
        (training_run_id, exacto["id"]),
    )
