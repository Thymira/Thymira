"""Session continuity at publication, provider, artifact and reconstruction boundaries.

Real EDA tools, stable risk interviews and scripted providers exercise the relevant consumers.
Governance variants add one scoped synthetic INTERNAL finding to test the real MIRA-to-Gate
boundary; they do not represent the full MIRA model fan-out.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest

from tests.thymira.api_support import TEST_CREDENTIAL, authenticated_client
from tests.thymira.fixtures_agent_output import DataProfileOutput
from tests.thymira.test_api_sessions import _client, _complete, _send
from thymira.agents import LLMToolCall, RiskClassification, load_agent_specs
from thymira.agents.llm import ScriptedProvider
from thymira.api import RuntimeDeps, build_default_deps, create_app
from thymira.core import build_runtime_graph_factory, default_activity_profile
from thymira.core.governance_binding import final_governance_binding
from thymira.events import verify_events
from thymira.mira import AuditAgentSpec
from thymira.mira.preflight import RiskJudgment
from thymira.mira.verification import DiscoveryLimits, FindingVerdict
from thymira.policies import ActionRule, FindingRule, Policy, decision_from_events
from thymira.schemas import (
    ActivityProfile,
    ArtifactKind,
    Decision,
    EventType,
    Framework,
    ProjectConfig,
    RiskLevel,
    Run,
)
from thymira.state.session_discussions import LocalDiscussionRepository
from thymira.thy.agents import full_agent_catalog
from thymira.thy.models import (
    AgentTask,
    ArtifactRequirement,
    PlanOutput,
    ThyAgentKind,
    ThyPhase,
)

if TYPE_CHECKING:
    from pathlib import Path

    from langgraph.graph.state import CompiledStateGraph

pytestmark = pytest.mark.slow


def _composition(
    tmp_path: Path,
    provider: ScriptedProvider,
    *,
    finding_decision: Decision | None = None,
    profile_tool: bool = False,
    full_catalog: bool = False,
    complete_risk_profile: bool = False,
    synthetic_audit_finding: bool = False,
) -> RuntimeDeps:
    workspace = tmp_path / "workspace"
    config = workspace / ".thymira" / "config.yaml"
    config.parent.mkdir(parents=True, exist_ok=True)
    if not config.exists():
        config.write_text("project:\n  name: conversations\n  domain: research\n", encoding="utf-8")
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir(exist_ok=True)
    (catalog_dir / "data.yaml").write_text(
        "name: data\nrole: agent\ntask_kinds: [analyze]\nmax_turns: 2\nmax_depth: 1\n"
        "system_prompt_ref: data.md\n"
        "output_schema_ref: tests.thymira.fixtures_agent_output:DataProfileOutput\n"
        + ("tool_allowlist: [profile_dataset]\n" if profile_tool else ""),
        encoding="utf-8",
    )
    (catalog_dir / "data.md").write_text("Report the sample profile.", encoding="utf-8")
    audit_specs = (
        (
            AuditAgentSpec(
                name="conversation-profile-audit",
                framework=Framework.INTERNAL,
                task_kinds=("audit_judgement",),
                max_turns=1,
                system_prompt="Return one bounded finding about the registered profile facts.",
            ),
        )
        if synthetic_audit_finding
        else ()
    )
    project_config = (
        ProjectConfig.model_validate(
            {
                "project": {"name": "conversations", "domain": "research"},
                "governance": {"frameworks": [Framework.INTERNAL.value]},
            }
        )
        if synthetic_audit_finding
        else None
    )
    catalog = (
        full_agent_catalog()
        if full_catalog
        else load_agent_specs(
            catalog_dir, known_capabilities=frozenset({"profile_dataset"} if profile_tool else ())
        )
    )
    policy = Policy(
        name="conversation-test",
        version="1.0",
        finding_rules=(
            FindingRule(
                id="TEST-FINDINGS",
                decision=finding_decision or Decision.WARNING,
                reason="Test review",
            ),
        ),
        action_rules=(
            ActionRule(
                id="TEST-PLAN",
                action_types=("plan.proposed",),
                decision=Decision.PASS,
                reason="The no-tool test plan needs no review.",
            ),
        ),
    )
    (config.parent / "policies.yaml").write_text(policy.model_dump_json(), encoding="utf-8")
    assembled: list[RuntimeDeps] = []

    def factory(run: Run) -> CompiledStateGraph:
        deps = assembled[0]
        return build_runtime_graph_factory(
            deps.run_store,
            gate_factory=deps.gate_factory,
            artifact_store_factory=deps.artifact_store_factory,
            tool_manager=deps.tool_manager,
            tool_registry=deps.tool_registry if profile_tool else None,
            project_dir=workspace,
            record_repository=deps.record_repository,
            checkpoint_repository=deps.checkpoint_repository,
            risk_interview=deps.risk_interview if complete_risk_profile else None,
            activity_profile_factory=(
                _complete_activity_profile if complete_risk_profile else None
            ),
            provider=provider,
            catalog=catalog,
            specs=audit_specs,
            project_config=project_config,
            discovery=DiscoveryLimits(max_rounds=1) if synthetic_audit_finding else None,
            plan_repository=deps.board_repository,
            dag_repository=deps.dag_repository,
        )(run)

    deps = build_default_deps(
        tmp_path / "runtime",
        workspace=workspace,
        graph_factory=factory,
        principal_resolver=TEST_CREDENTIAL,
    )
    assembled.append(deps)
    return deps


def _complete_activity_profile(run: Run) -> ActivityProfile:
    """Use deterministic, fully declared risk facts in real-tool conversation fixtures."""
    return default_activity_profile(run).model_copy(
        update={
            "purpose": "Bounded exploratory analysis of the registered sample.",
            "affected_population": "No individual decisions are made about applicants.",
            "decision_effect": "The analysis does not change eligibility or review order.",
            "autonomy": "The profile report is descriptive and makes no decisions.",
            "human_oversight": "A researcher reviews the descriptive report.",
            "jurisdiction": "EU",
            "data_categories": ("application records",),
            "sensitive_attributes": (),
            "potential_consequences": (
                "No person-level decision or operational action follows from this analysis.",
            ),
            "created_at": run.created_at,
        }
    )


def _risk_classification() -> RiskClassification:
    """Return a stable, low-impact proposal before any real profile tool is started."""
    return RiskClassification(
        risk_level=RiskLevel.LOW,
        activity_category="data_analysis",
        confidence=0.8,
        needs_human_review=False,
        summary="Bounded descriptive analysis with no person-level decision effect.",
    )


def _mira_risk_judgment() -> RiskJudgment:
    """Return the reviewed base-risk pair for the complete synthetic profile."""
    return RiskJudgment(
        risk_level=RiskLevel.LOW,
        activity_category="data_analysis",
        confidence=0.9,
        justification="The registered analysis is descriptive and has no person-level effect.",
    )


def _profile_audit_candidate() -> dict[str, object]:
    """Return one synthetic audit-agent candidate over the recorded profile facts."""
    return {
        "findings": [
            {
                "control_id": "REGISTERED-SAMPLE-PROFILE",
                "title": "Registered sample report artifact",
                "finding": (
                    "A REPORT artifact named profile/sample.json was registered for this Run."
                ),
                "severity": "MEDIUM",
                "confidence": 0.9,
            }
        ]
    }


def _profile_finding_verdict() -> FindingVerdict:
    """Support the one bounded synthetic candidate through MIRA's real verifier seam."""
    return FindingVerdict(
        supported=True,
        rationale=(
            "The artifact.created event records profile/sample.json as a REPORT for this Run."
        ),
    )


def _approve_execution_reviews(client, deps, session_id: str, run_id: str) -> None:
    """Resume only the exact pending execution reviews before reading the Run result."""
    for _ in range(4):
        detail = client.get(f"/sessions/{session_id}").json()
        turn = next(item for item in detail["turns"] if item["run"]["id"] == run_id)
        if turn["state"]["condition"] != "waiting":
            return
        transition = next(
            event
            for event in reversed(deps.event_store.read(run_id))
            if event.type is EventType.RUN_TRANSITIONED
            and event.payload.get("command") == "wait_for_approval"
        )
        decision = transition.payload["policy_decision"]
        if decision.get("subject_kind") == "findings":
            return
        approved = client.post(
            f"/runs/{run_id}/approve",
            json={
                "expected_decision_id": decision["id"],
                "note": "Review the recorded local analysis request.",
            },
        )
        assert approved.status_code == 202, approved.text


def _assert_sample_profile(profile: dict[str, object], csv: bytes) -> None:
    assert profile["shape"] == {"rows": 3, "columns": 3}
    columns = profile.get("columns")
    assert isinstance(columns, dict)
    income = columns.get("income")
    assert isinstance(income, dict)
    assert income.get("null_count") == 1
    source = profile.get("source")
    assert isinstance(source, dict)
    assert source.get("sha256") == hashlib.sha256(csv).hexdigest()


def _start_german_credit_eda(tmp_path, monkeypatch):
    for name in (
        "THYMIRA_ALLOWED_MODEL_ROUTES",
        "THYMIRA_MODEL_FAST",
        "THYMIRA_MODEL_STANDARD",
        "THYMIRA_MODEL_FRONTIER",
    ):
        monkeypatch.setenv(name, "test-model")
    plan = PlanOutput(
        tasks=(
            AgentTask(
                id="eda",
                agent=ThyAgentKind.DATA,
                phase=ThyPhase.EXECUTE,
                instruction="Profile german_credit using profile_dataset.",
            ),
        )
    )
    provider = ScriptedProvider(
        [
            _risk_classification(),
            _risk_classification(),
            plan,
            LLMToolCall(
                id="profile-csv",
                name="profile_dataset",
                arguments={
                    "dataset": "german_credit",
                    "description": "Compute missingness and distributions for the uploaded CSV.",
                },
            ),
            LLMToolCall(
                id="profile-answer",
                name="final_result",
                arguments={"row_count": 3, "columns": ["age", "income", "risk"]},
            ),
        ]
    )
    deps = _composition(tmp_path, provider, profile_tool=True, complete_risk_profile=True)
    config = tmp_path / "workspace" / ".thymira" / "config.yaml"
    config.write_text(
        config.read_text()
        + "datasets:\n  - name: german_credit\n    path: data/applications.csv\n",
        encoding="utf-8",
    )
    client = authenticated_client(create_app(deps))
    session_id = client.post("/sessions", json={}).json()["id"]
    message = {"message": "Do an EDA of german_credit", "client_message_id": "eda-first"}
    missing = client.post(f"/sessions/{session_id}/messages", json=message)
    assert missing.status_code == 409, missing.text
    assert client.get(f"/sessions/{session_id}").json()["turns"] == []
    csv = b"age,income,risk\n30,1000,0\n45,,1\n28,2000,0\n"
    upload = client.put("/project/datasets/german_credit?filename=applications.csv", content=csv)
    assert upload.status_code == 200, upload.text
    sent = client.post(f"/sessions/{session_id}/messages", json=message)
    assert sent.status_code == 201, sent.text
    run_id = sent.json()["id"]
    _approve_execution_reviews(client, deps, session_id, run_id)
    detail = client.get(f"/sessions/{session_id}").json()
    assert detail["turns"][0]["state"]["outcome"] == "completed", detail
    return deps, provider, session_id, run_id, csv


def _assert_profile_artifact_lineage(deps, run_id, csv):
    initial_events = deps.event_store.read(run_id)
    risk_classification = next(
        event for event in initial_events if event.type is EventType.RISK_CLASSIFIED
    )
    profile_tool_start = next(
        event
        for event in initial_events
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "profile_dataset"
    )
    assert risk_classification.seq < profile_tool_start.seq
    store = deps.artifact_store_factory(run_id)
    report = json.loads(store.load_bytes("profile/german_credit.json"))
    assert report["shape"] == {"rows": 3, "columns": 3}
    assert report["columns"]["income"]["null_count"] == 1
    assert report["source"]["sha256"] == hashlib.sha256(csv).hexdigest()
    profile_artifact = next(
        artifact
        for artifact in store.manifest().values()
        if artifact.name == "profile/german_credit.json"
    )
    assert (
        profile_artifact.sha256
        == hashlib.sha256(store.load_bytes("profile/german_credit.json")).hexdigest()
    )
    source_artifact = store.get(report["source"]["artifact"])
    schema_artifact = store.get("datasets/german_credit.schema.json")
    analysis_artifact = store.get("analysis.md")
    assert source_artifact is not None
    assert schema_artifact is not None
    assert analysis_artifact is not None
    assert source_artifact.sha256 == report["source"]["sha256"]
    assert (
        source_artifact.sha256 == hashlib.sha256(store.load_bytes(source_artifact.name)).hexdigest()
    )
    assert schema_artifact.sha256 == report["source"]["schema_sha256"]
    assert {
        profile_artifact.id,
        source_artifact.id,
        schema_artifact.id,
    } <= set(analysis_artifact.input_artifact_ids)
    report_event = next(
        event
        for event in deps.event_store.read(run_id)
        if event.type is EventType.ARTIFACT_CREATED and event.payload.get("name") == "analysis.md"
    )
    assert report_event.payload["artifact_id"] == analysis_artifact.id
    assert report_event.payload["sha256"] == analysis_artifact.sha256
    assert verify_events(deps.event_store.read(run_id)).valid
    artifacts = {
        artifact.name: (artifact.id, artifact.sha256)
        for artifact in (profile_artifact, source_artifact, schema_artifact, analysis_artifact)
    }
    return report, artifacts


def _ask_about_reopened_profile(tmp_path, session_id, run_id, report, artifacts):
    second_provider = ScriptedProvider(
        [
            _risk_classification(),
            _risk_classification(),
            PlanOutput(
                tasks=(
                    AgentTask(
                        id="explain-profile",
                        agent=ThyAgentKind.DATA,
                        phase=ThyPhase.EXECUTE,
                        instruction="Explain the previous profile.",
                    ),
                )
            ),
            LLMToolCall(
                id="followup-answer",
                name="final_result",
                arguments={"row_count": 3, "columns": ["age", "income", "risk"]},
            ),
        ]
    )
    reopened_deps = _composition(
        tmp_path, second_provider, profile_tool=True, complete_risk_profile=True
    )
    reopened = authenticated_client(create_app(reopened_deps))
    reopened_store = reopened_deps.artifact_store_factory(run_id)
    persisted_report = json.loads(reopened_store.load_bytes("profile/german_credit.json"))
    assert persisted_report["source"]["sha256"] == report["source"]["sha256"]
    for name, (artifact_id, digest) in artifacts.items():
        persisted = reopened_store.get(name)
        assert persisted is not None
        assert (persisted.id, persisted.sha256) == (artifact_id, digest)
    assert reopened.get("/project/datasets").json()["items"][0]["present"] is True
    ask_provider = ScriptedProvider(
        [
            {
                "kind": "answer",
                "message": "El perfil describe tres filas y tres columnas.",
                "sources": ["r0"],
            }
        ],
        model="test-model",
    )
    reopened_deps.session_chat.provider = ask_provider
    run_ids_before_ask = reopened_deps.session_service.get_session(session_id).run_ids
    ask = reopened.post(
        f"/sessions/{session_id}/chat",
        json={"message": "¿Qué es eso?", "client_message_id": "eda-ask", "mode": "ask"},
    )
    assert ask.status_code == 201, ask.text
    ask_value = ask.json()
    assert ask_value["status"] == "completed", ask_value
    assert {citation["run_id"] for citation in ask_value["citations"]} == {run_id}
    assert "Profiled shape: 3 rows and 3 columns" in ask_provider.calls[0]["prompt"]
    assert reopened_deps.session_service.get_session(session_id).run_ids == run_ids_before_ask
    ask_id = ask_value["id"]
    persisted_ask = next(
        record
        for record in LocalDiscussionRepository(
            reopened_deps.session_chat.root, session_id
        ).records()
        if record.discussion.id == ask_id
    )
    assert persisted_ask.discussion.mode == "ask"
    assert persisted_ask.discussion.citations[0].run_id == run_id
    return ask_id, second_provider


def _assert_reopened_profile_followup(
    tmp_path, session_id, run_id, csv, report, ask_id, second_provider
):
    continued_deps = _composition(
        tmp_path, second_provider, profile_tool=True, complete_risk_profile=True
    )
    client = authenticated_client(create_app(continued_deps))
    detail = client.get(f"/sessions/{session_id}").json()
    assert any(discussion["id"] == ask_id for discussion in detail["discussions"])
    assert client.get("/project/datasets").json()["items"][0]["present"] is True
    followup = client.post(
        f"/sessions/{session_id}/messages",
        json={"message": "Explain the previous row count", "client_message_id": "eda-second"},
    )
    assert followup.status_code == 201, followup.text
    followup_run_id = followup.json()["id"]
    _approve_execution_reviews(client, continued_deps, session_id, followup_run_id)
    prompt = second_provider.calls[2]["prompt"]
    for required in (
        "Profiled shape: 3 rows and 3 columns",
        "| income |",
        "german_credit",
        "datasets/german_credit.csv",
        f"#/runs/{run_id}",
        "profile/german_credit.json",
    ):
        assert required in prompt
    assert report["source"]["sha256"] == hashlib.sha256(csv).hexdigest()
    persisted = json.loads(
        continued_deps.artifact_store_factory(run_id).load_bytes("profile/german_credit.json")
    )
    assert persisted["source"]["sha256"] == hashlib.sha256(csv).hexdigest()


def _assert_graph_followup_keeps_required_plot(tmp_path, session_id, run_id):
    graph_provider = ScriptedProvider(
        [
            _risk_classification(),
            _risk_classification(),
            PlanOutput(
                tasks=(
                    AgentTask(
                        id="prior-analysis-plot",
                        agent=ThyAgentKind.CODING,
                        phase=ThyPhase.EXECUTE,
                        instruction="Generate a plot from the previous verified analysis.",
                        required_artifacts=(
                            ArtifactRequirement(
                                name="plots/prior-analysis.png", kind=ArtifactKind.PLOT
                            ),
                        ),
                    ),
                    AgentTask(
                        id="malicious-extra-training",
                        agent=ThyAgentKind.EXPERIMENT,
                        phase=ThyPhase.EXECUTE,
                        instruction="Train a new model while making the plot.",
                    ),
                )
            ),
            LLMToolCall(
                id="plot-plan-result",
                name="final_result",
                arguments={"stdout": "The plot task used the previous analysis.", "exit_code": 0},
            ),
        ]
    )
    deps = _composition(tmp_path, graph_provider, full_catalog=True, complete_risk_profile=True)
    client = authenticated_client(create_app(deps))
    sent = client.post(
        f"/sessions/{session_id}/messages",
        json={
            "message": "Generate plots for the previous analysis",
            "client_message_id": "eda-graph-followup",
        },
    )
    assert sent.status_code == 201, sent.text
    graph_run_id = sent.json()["id"]
    _approve_execution_reviews(client, deps, session_id, graph_run_id)
    prompt = graph_provider.calls[2]["prompt"]
    for required in (
        "Generate plots for the previous analysis",
        f"#/runs/{run_id}",
        "german_credit",
        "datasets/german_credit.csv",
        "Profiled shape: 3 rows and 3 columns",
    ):
        assert required in prompt
    events = deps.event_store.read(graph_run_id)
    started = {
        event.payload.get("agent") for event in events if event.type is EventType.AGENT_STARTED
    }
    assert "coding" in started
    assert "experiment" not in started
    assert not any(event.type is EventType.MODEL_TRAINED for event in events)
    completion = next(
        event
        for event in events
        if event.type is EventType.AGENT_COMPLETED
        and event.payload.get("completion_evidence", {}).get("plan_task_id")
        == "prior-analysis-plot"
    )
    assert completion.payload["status"] == "FAILED"
    assert completion.payload["completion_evidence"]["missing_required_artifacts"] == [
        "plots/prior-analysis.png"
    ]
    run = deps.run_service.get_run(graph_run_id)
    assert run.status.value == "FAILED"
    assert "plots/prior-analysis.png" in (run.error or "")


def _assert_missing_model_reference_fails_closed(tmp_path, session_id):
    provider = ScriptedProvider(
        [
            PlanOutput(
                tasks=(
                    AgentTask(
                        id="unreferenced-metrics",
                        agent=ThyAgentKind.EXPERIMENT,
                        phase=ThyPhase.EXECUTE,
                        instruction="Calculate more metrics for the previous model.",
                    ),
                )
            )
        ]
    )
    deps = _composition(tmp_path, provider, full_catalog=True)
    client = authenticated_client(create_app(deps))
    sent = client.post(
        f"/sessions/{session_id}/messages",
        json={
            "message": "Calculate more metrics for the previous model",
            "client_message_id": "eda-metrics-without-model",
        },
    )
    assert sent.status_code == 201, sent.text
    run_id = sent.json()["id"]
    prompt = provider.calls[0]["prompt"]
    assert "Calculate more metrics for the previous model" in prompt
    assert "Human-resolved model work intent: evaluate_existing" in prompt
    assert "verified-prior-model-catalog" in prompt
    run = deps.run_service.get_run(run_id)
    assert run.error is not None
    assert "approved source Run" in run.error
    events = deps.event_store.read(run_id)
    assert not any(event.type is EventType.AGENT_STARTED for event in events)
    assert not any(event.type is EventType.MODEL_TRAINED for event in events)


def test_session_upload_reaches_real_eda_tool_and_survives_reconstruction(tmp_path, monkeypatch):
    """Profile a real upload, then observe Ask and follow-up consumers after reopening."""
    deps, _provider, session_id, run_id, csv = _start_german_credit_eda(tmp_path, monkeypatch)
    report, artifacts = _assert_profile_artifact_lineage(deps, run_id, csv)
    ask_id, second_provider = _ask_about_reopened_profile(
        tmp_path, session_id, run_id, report, artifacts
    )
    _assert_reopened_profile_followup(
        tmp_path, session_id, run_id, csv, report, ask_id, second_provider
    )
    _assert_graph_followup_keeps_required_plot(tmp_path, session_id, run_id)
    _assert_missing_model_reference_fails_closed(tmp_path, session_id)


def _profile_gate_provider():
    plan = PlanOutput(
        tasks=(
            AgentTask(
                id="profile",
                agent=ThyAgentKind.DATA,
                phase=ThyPhase.EXECUTE,
                instruction="Profile the sample.",
            ),
        )
    )
    return ScriptedProvider(
        [
            _mira_risk_judgment(),
            _risk_classification(),
            _risk_classification(),
            plan,
            LLMToolCall(
                id="profile-first-sample",
                name="profile_dataset",
                arguments={"dataset": "sample", "description": "Profile the uploaded sample."},
            ),
            LLMToolCall(
                id="profile-first-result",
                name="final_result",
                arguments={"row_count": 3, "columns": ["age", "income", "risk"]},
            ),
            _profile_audit_candidate(),
            _profile_finding_verdict(),
            _mira_risk_judgment(),
            _risk_classification(),
            plan,
            LLMToolCall(
                id="profile-second-sample",
                name="profile_dataset",
                arguments={"dataset": "sample", "description": "Profile the uploaded sample."},
            ),
            LLMToolCall(
                id="profile-second-result",
                name="final_result",
                arguments={"row_count": 3, "columns": ["age", "income", "risk"]},
            ),
        ]
    )


def _start_governance_profile(tmp_path, provider, verdict):
    deps = _composition(
        tmp_path,
        provider,
        finding_decision=verdict,
        profile_tool=True,
        complete_risk_profile=True,
        synthetic_audit_finding=True,
    )
    config = tmp_path / "workspace" / ".thymira" / "config.yaml"
    config.write_text(
        config.read_text() + "datasets:\n  - name: sample\n    path: data/sample.csv\n",
        encoding="utf-8",
    )
    client = authenticated_client(create_app(deps))
    session_id = client.post("/sessions", json={}).json()["id"]
    csv = b"age,income,risk\n30,1000,0\n45,,1\n28,2000,0\n"
    upload = client.put("/project/datasets/sample?filename=sample.csv", content=csv)
    assert upload.status_code == 200, upload.text
    sent = client.post(
        f"/sessions/{session_id}/messages",
        json={"message": "Inspect the first sample", "client_message_id": "first"},
    )
    assert sent.status_code == 201, sent.text
    run_id = sent.json()["id"]
    for _ in range(4):
        detail = client.get(f"/sessions/{session_id}").json()
        if detail["turns"][0]["state"]["condition"] != "waiting":
            break
        events = deps.event_store.read(run_id)
        transition = next(
            event
            for event in reversed(events)
            if event.type is EventType.RUN_TRANSITIONED
            and event.payload.get("command") == "wait_for_approval"
        )
        decision = transition.payload["policy_decision"]
        if decision.get("subject_kind") == "findings":
            break
        approved = client.post(
            f"/runs/{run_id}/approve",
            json={
                "expected_decision_id": decision["id"],
                "note": "Review the local synthetic CSV profiling tool call.",
            },
        )
        assert approved.status_code == 202, approved.text
    profile = json.loads(deps.artifact_store_factory(run_id).load_bytes("profile/sample.json"))
    _assert_sample_profile(profile, csv)
    return deps, session_id, run_id


def _assert_supported_profile_finding(deps, provider, run_id, events):
    candidate = next(
        event for event in events if event.payload.get("kind") == "conversation.response"
    )
    audit_candidate = next(
        event
        for event in events
        if event.type is EventType.AUDIT_FINDING
        and event.payload.get("finding", {}).get("control_id") == "model:REGISTERED-SAMPLE-PROFILE"
    )
    audit_completed = next(event for event in events if event.type is EventType.AUDIT_COMPLETED)
    finding = audit_candidate.payload["finding"]
    assert finding["run_id"] == run_id
    assert finding["framework"] == Framework.INTERNAL.value
    assert finding["severity"] == "MEDIUM"
    assert finding["finding"] == (
        "A REPORT artifact named profile/sample.json was registered for this Run."
    )
    supported = audit_completed.payload["verification_supported_findings"]
    assert len(supported) == 1
    assert supported[0]["finding"]["id"] == finding["id"]
    assert supported[0]["reason"] == (
        "The artifact.created event records profile/sample.json as a REPORT for this Run."
    )
    verifier_prompt = next(
        call["prompt"] for call in provider.calls if "mira-verification" in call["prompt"]
    )
    assert "profile/sample.json" in verifier_prompt
    assert "REPORT" in verifier_prompt
    report_findings = audit_completed.payload["audit_report"]["findings"]
    assert any(record["id"] == finding["id"] for record in report_findings)
    assert audit_completed.run_id == run_id
    assert candidate.payload["provisional"] is True
    return candidate, audit_candidate, audit_completed, finding


def _assert_finding_gate_binding(events, run_id, verdict, candidate, audit_candidate, audit):
    final_decision = next(
        event
        for event in reversed(events)
        if event.type is EventType.POLICY_DECISION
        and event.payload.get("subject_kind") == "findings"
    )
    assert candidate.seq < final_decision.seq
    assert audit_candidate.seq < audit.seq < final_decision.seq
    assert final_decision.payload["finding_ids"] == [audit_candidate.payload["finding"]["id"]]
    decision = decision_from_events(events, final_decision.payload["id"])
    binding = final_governance_binding(
        run_id,
        decision,
        tuple(event for event in events if event.seq <= final_decision.seq),
    )
    assert binding["audit_completed_seq"] == audit.seq
    assert binding["audit_revision"] == audit.payload["audit_revision"]
    assert binding["audit_terminal_hash"] == audit.payload["audit_report"]["terminal_hash"]
    if verdict in (Decision.BLOCK, Decision.WARNING):
        transition = next(
            event
            for event in reversed(events)
            if event.type is EventType.RUN_TRANSITIONED
            and event.payload.get("audit_completed_seq") is not None
        )
        assert transition.payload["audit_completed_seq"] == audit.seq
        assert transition.payload["audit_revision"] == audit.payload["audit_revision"]
        assert (
            transition.payload["audit_terminal_hash"]
            == audit.payload["audit_report"]["terminal_hash"]
        )
        assert transition.payload["policy_decision_id"] == final_decision.payload["id"]
        assert not any(
            event.type is EventType.RUN_TRANSITIONED
            and event.payload.get("command") == "wait_for_approval"
            and event.payload.get("stage") == "auditing"
            for event in events
        )
    else:
        transition = next(
            event
            for event in reversed(events)
            if event.type is EventType.RUN_TRANSITIONED
            and event.payload.get("command") == "wait_for_approval"
            and event.payload.get("stage") == "auditing"
        )
        pending = transition.payload["policy_decision"]
        assert audit.seq < final_decision.seq < transition.seq
        assert pending["id"] == final_decision.payload["id"]
        assert pending["finding_ids"] == final_decision.payload["finding_ids"]
    assert final_decision.payload["decision"] == verdict.value


def _assert_governance_release_and_followup(
    tmp_path, provider, session_id, run_id, verdict, human_answer
):
    deps = _composition(
        tmp_path,
        provider,
        finding_decision=verdict,
        profile_tool=True,
        complete_risk_profile=True,
    )
    client = authenticated_client(create_app(deps))
    detail = client.get(f"/sessions/{session_id}").json()
    turn = detail["turns"][0]
    permitted = verdict in (Decision.PASS, Decision.WARNING)
    if permitted:
        assert "Profiled shape: 3 rows and 3 columns" in turn["assistant_message"]
    else:
        assert turn["assistant_message"] is None
    if verdict is Decision.WARNING:
        assert turn["state"]["outcome"] == "completed"
        assert turn["state"]["condition"] == "terminal"
    if verdict is Decision.REQUIRE_HUMAN_REVIEW:
        assert turn["state"]["wait_reason"] == "approval"
        blocked = client.post(
            f"/sessions/{session_id}/messages",
            json={"message": "Continue", "client_message_id": "blocked-followup"},
        )
        assert blocked.status_code == 409
        resolved = client.post(f"/runs/{run_id}/{human_answer}", json={})
        assert resolved.status_code == 202, resolved.text
        permitted = human_answer == "approve"
        response = client.get(f"/sessions/{session_id}").json()["turns"][0]["assistant_message"]
        assert (response is not None) is permitted
    followup = client.post(
        f"/sessions/{session_id}/messages",
        json={"message": "Inspect another sample", "client_message_id": "second"},
    )
    assert followup.status_code == 201, followup.text
    followup_run_id = followup.json()["id"]
    _approve_execution_reviews(client, deps, session_id, followup_run_id)
    followup_run = deps.run_service.get_run(followup_run_id)
    assert followup_run.error is None
    assert provider.calls, followup.json()
    planner_call = next(
        call
        for call in reversed(provider.calls)
        if "Plan the run for:" in call["prompt"] and "Inspect another sample" in call["prompt"]
    )
    for evidence in (
        "Profiled shape: 3 rows and 3 columns",
        "sample.csv",
        f"#/runs/{run_id}",
    ):
        assert (evidence in planner_call["prompt"]) is permitted
    assert verify_events(deps.event_store.read(run_id)).valid


@pytest.mark.parametrize(
    ("verdict", "human_answer"),
    [
        (Decision.BLOCK, None),
        (Decision.WARNING, None),
        (Decision.REQUIRE_HUMAN_REVIEW, "reject"),
        (Decision.REQUIRE_HUMAN_REVIEW, "approve"),
    ],
)
def test_final_governance_controls_response_visibility_and_followup_context(
    tmp_path, monkeypatch, verdict, human_answer
):
    for name in (
        "THYMIRA_ALLOWED_MODEL_ROUTES",
        "THYMIRA_MODEL_FAST",
        "THYMIRA_MODEL_STANDARD",
        "THYMIRA_MODEL_FRONTIER",
    ):
        monkeypatch.setenv(name, "test-model")
    provider = _profile_gate_provider()
    deps, session_id, run_id = _start_governance_profile(tmp_path, provider, verdict)
    events = deps.event_store.read(run_id)
    candidate, audit_candidate, audit_completed, _finding = _assert_supported_profile_finding(
        deps, provider, run_id, events
    )
    _assert_finding_gate_binding(
        events, run_id, verdict, candidate, audit_candidate, audit_completed
    )
    _assert_governance_release_and_followup(
        tmp_path, provider, session_id, run_id, verdict, human_answer
    )


def test_no_tool_structured_payload_is_not_a_verified_profile_after_restart(tmp_path, monkeypatch):
    """Raw DataProfileOutput JSON is not evidence of observed dataset facts."""
    for name in (
        "THYMIRA_ALLOWED_MODEL_ROUTES",
        "THYMIRA_MODEL_FAST",
        "THYMIRA_MODEL_STANDARD",
        "THYMIRA_MODEL_FRONTIER",
    ):
        monkeypatch.setenv(name, "test-model")
    plan = PlanOutput(
        tasks=(
            AgentTask(
                id="profile",
                agent=ThyAgentKind.DATA,
                phase=ThyPhase.EXECUTE,
                instruction="Report the sample profile from the conversation.",
            ),
        )
    )
    first_provider = ScriptedProvider([plan, DataProfileOutput(row_count=27, columns=("sample",))])
    first_deps = _composition(tmp_path, first_provider)
    client = authenticated_client(create_app(first_deps))
    session = client.post("/sessions", json={"title": "Sample history"}).json()
    sent = client.post(
        f"/sessions/{session['id']}/messages",
        json={
            "message": "Inspect the sample and keep its row count",
            "client_message_id": "first",
        },
    )
    assert sent.status_code == 201, sent.text
    first_id = sent.json()["id"]
    detail = client.get(f"/sessions/{session['id']}").json()
    assert detail["active_run_id"] is None, detail
    assert "no verified dataset profile was available" in detail["turns"][0]["assistant_message"]

    second_provider = ScriptedProvider([plan, DataProfileOutput(row_count=27, columns=("sample",))])
    second_deps = _composition(tmp_path, second_provider)
    reopened = authenticated_client(create_app(second_deps))
    followup = reopened.post(
        f"/sessions/{session['id']}/messages",
        json={
            "message": "Use that previous row count",
            "client_message_id": "second",
        },
    )
    assert followup.status_code == 201, followup.text
    second_id = followup.json()["id"]
    # This observation is the actual input at the model boundary, not just a UI transcript.
    assert "no verified dataset profile was available" in second_provider.calls[0]["prompt"]
    assert '"row_count":27' not in second_provider.calls[0]["prompt"].replace(" ", "")
    assert "27 rows" not in second_provider.calls[0]["prompt"]
    assert "Inspect the sample" in second_provider.calls[0]["prompt"]
    final = reopened.get(f"/sessions/{session['id']}").json()
    assert [turn["id"] for turn in final["turns"]] == [first_id, second_id]
    assert final["active_run_id"] is None
    for run_id in (first_id, second_id):
        events = second_deps.event_store.read(run_id)
        assert verify_events(events).valid
        assert any(event.type is EventType.AUDIT_COMPLETED for event in events)
        assert any(event.type is EventType.RUN_COMPLETED for event in events)


@pytest.mark.parametrize(
    ("summary_chars", "persist_dialogue"),
    [(20_000, False), (12_000, True)],
    ids=("oversized-summary", "summary-with-persisted-dialogue"),
)
def test_reopened_planner_history_keeps_latest_run_identity_within_budget(
    tmp_path, monkeypatch, summary_chars, persist_dialogue
):
    for name in (
        "THYMIRA_ALLOWED_MODEL_ROUTES",
        "THYMIRA_MODEL_FAST",
        "THYMIRA_MODEL_STANDARD",
        "THYMIRA_MODEL_FRONTIER",
    ):
        monkeypatch.setenv(name, "test-model")
    client, deps = _client(tmp_path)
    session = client.post("/sessions", json={}).json()
    original_id = _send(client, session["id"], "Summarize the released analysis").json()["id"]
    summary = "approved continuity result " * (summary_chars // 28)
    _complete(deps, original_id, response=summary)

    if persist_dialogue:
        deps.session_chat.provider = ScriptedProvider(
            [
                json.dumps(
                    {
                        "kind": "answer",
                        "message": "Brief persisted discussion.",
                        "sources": ["r0"],
                    }
                )
            ],
            model="test-model",
        )
        chat = client.post(
            f"/sessions/{session['id']}/chat",
            json={
                "message": "Explain the released result briefly",
                "client_message_id": "persist-dialogue",
                "mode": "ask",
            },
        )
        assert chat.status_code == 201, chat.text

    plan = PlanOutput(
        tasks=(
            AgentTask(
                id="continuity-plan",
                agent=ThyAgentKind.DATA,
                phase=ThyPhase.EXECUTE,
                instruction="Continue from the previous run.",
            ),
        )
    )
    provider = ScriptedProvider(
        [plan, DataProfileOutput(row_count=1, columns=("sample",))], model="test-model"
    )
    reopened_deps = _composition(tmp_path, provider)
    reopened = authenticated_client(create_app(reopened_deps))
    followup = reopened.post(
        f"/sessions/{session['id']}/messages",
        json={"message": "Continue the analysis", "client_message_id": "large-history-followup"},
    )
    assert followup.status_code == 201, followup.text
    prompt = provider.calls[0]["prompt"]
    assert f"Run ID: {original_id}" in prompt
    assert "Run condition: terminal; outcome: completed" in prompt
    if persist_dialogue:
        assert "Brief persisted discussion." in prompt
