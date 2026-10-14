"""Read-only SessionChat lookups use the existing structured proposal path."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from tests.thymira.test_api_sessions import _client, _complete, _send
from thymira.agents.llm import ScriptedProvider
from thymira.agents.llm.base import LLMStructuredOutputError
from thymira.core import session_read_tools
from thymira.core.session_read_tools import execute_session_read
from thymira.events import verify_events
from thymira.policies import CapabilityRule, Policy
from thymira.schemas import Actor, ArtifactKind, Decision, EventType, ExecutionConstraints, new_id
from thymira.schemas.session_chat import SessionChatProposal, SessionReadRequest
from thymira.state.session_discussions import SessionProviderLog

if TYPE_CHECKING:
    from thymira.agents.llm.base import BaseModelT, LLMResponse

pytestmark = pytest.mark.integration


class _LowUsageProvider(ScriptedProvider):
    """Keep request-limit tests independent of accumulated synthetic token estimates."""

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: type[BaseModelT],
        system: str = "",
    ) -> tuple[BaseModelT, LLMResponse]:
        proposal, response = super().complete_structured(prompt, schema=schema, system=system)
        return proposal, response.model_copy(
            update={
                "input_tokens": 1,
                "output_tokens": 1,
                "metadata": {**response.metadata, "usage_known": True},
            }
        )


class _UnknownUsageProvider(_LowUsageProvider):
    """Model a malformed response whose dispatched usage cannot be trusted."""

    def complete_structured_bounded(self, prompt, *, schema, system="", max_output_tokens):
        try:
            return super().complete_structured_bounded(
                prompt,
                schema=schema,
                system=system,
                max_output_tokens=max_output_tokens,
            )
        except LLMStructuredOutputError as exc:
            if exc.response is None:
                raise
            response = exc.response.model_copy(
                update={
                    "metadata": {
                        **exc.response.metadata,
                        "usage_known": False,
                        "session_chat_usage_source": "unknown",
                    }
                }
            )
            raise LLMStructuredOutputError(
                "structured response was rejected", response=response
            ) from None


class _SchemaRecordingProvider(_LowUsageProvider):
    """Record each exact schema submitted by the public SessionChat request surface."""

    def __init__(self, responses, *, model="scripted"):
        super().__init__(responses, model=model)
        self.schemas = []

    def complete_structured(self, prompt, *, schema, system=""):
        self.schemas.append(schema)
        return super().complete_structured(prompt, schema=schema, system=system)


class _RouteSwitchingProvider(_LowUsageProvider):
    """Change the configured default after the first dispatched proposal."""

    def __init__(self, responses, *, on_first_call, model="scripted"):
        super().__init__(responses, model=model)
        self.on_first_call = on_first_call

    def complete_structured(self, prompt, *, schema, system=""):
        try:
            return super().complete_structured(prompt, schema=schema, system=system)
        finally:
            if len(self.calls) == 1:
                self.on_first_call()


@pytest.fixture(autouse=True)
def _model_route(monkeypatch):
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")


def _read(
    deps,
    tmp_path,
    session_id,
    operation,
    path,
    *,
    pattern=None,
    offset=0,
    limit=20,
    policy=None,
    log=None,
    detail=None,
):
    """Call the Session read boundary with this fixture's real Session scope and policy."""
    assert deps.project_resolution is not None
    detail = detail or deps.session_chat.detail(
        session_id, project_id=deps.project_resolution.project_id
    )
    log = log or SessionProviderLog(deps.session_chat.root, session_id, new_id("turn"))
    output = execute_session_read(
        SessionReadRequest(
            operation=operation,
            path=path,
            pattern=pattern,
            offset=offset,
            limit=limit,
        ),
        detail=detail,
        project_root=deps.project_resolution.project_dir,
        runtime_root=tmp_path / "runtime",
        events=deps.event_store,
        policy=policy or deps.session_chat.policy,
        log=log,
    )
    return output, log


def _append_artifact_created(deps, run_id, artifact, *, name=None):
    """Append the hash-chained registration event emitted by a producing Run."""
    return deps.event_store.open(run_id).append(
        EventType.ARTIFACT_CREATED,
        Actor.system(),
        {
            "artifact_id": artifact.id,
            "name": name or artifact.name,
            "kind": artifact.kind.value,
            "sha256": artifact.sha256,
        },
    )


def test_project_lookup_chain_returns_bound_path_citation_without_creating_a_run(tmp_path):
    client, deps = _client(tmp_path)
    project = tmp_path / "workspace"
    source = project / "src" / "main.py"
    source.parent.mkdir(parents=True)
    original = "def answer():\n    return 'needle'\n"
    source.write_text(original, encoding="utf-8")
    assert deps.project_resolution is not None
    assert deps.project_resolution.workspace == project / ".thymira"
    assert deps.project_resolution.project_dir == project
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {"operation": "list_files", "path": "project"},
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": "project",
                        "pattern": "**/*.py",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "grep",
                        "path": "project/src",
                        "pattern": "needle",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "read_file",
                        "path": "project/src/main.py",
                        "offset": 0,
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The project defines answer() and returns the literal needle.",
                    "sources": ["q4"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Find the Python file containing needle and explain its function.",
            "client_message_id": "read-tools-chain",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["kind"] == "answer", discussion
    assert discussion["run_id"] is None
    assert discussion["citations"] == [
        {
            "run_id": None,
            "seq": None,
            "path": "project/src/main.py",
            "label": "project/src/main.py",
        }
    ]
    assert deps.session_service.get_session(session_id).run_ids == ()
    assert source.read_text(encoding="utf-8") == original
    assert len(provider.calls) == 5
    assert (
        client.post(
            f"/sessions/{session_id}/chat",
            json={
                "message": "Find the Python file containing needle and explain its function.",
                "client_message_id": "read-tools-chain",
                "mode": "ask",
            },
        ).json()
        == discussion
    )
    assert len(provider.calls) == 5

    # A failed lookup is returned as a bounded error and a later request in the same turn works.
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {"operation": "read_file", "path": "project/../outside"},
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {"operation": "read_file", "path": "project/src/main.py"},
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The safe file contains the needle implementation.",
                    "sources": ["q2"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider
    recovered = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Read the matching source safely.",
            "client_message_id": "read-tools-recover",
            "mode": "ask",
        },
    ).json()
    assert recovered["status"] == "completed", recovered
    assert recovered["citations"][0]["path"] == "project/src/main.py"
    assert '"tool_error_id":"read_error_1"' in provider.calls[1]["prompt"]
    assert len(provider.calls) == 3
    rebuilt, rebuilt_deps = _client(tmp_path)
    assert (
        rebuilt.post(
            f"/sessions/{session_id}/chat",
            json={
                "message": "Read the matching source safely.",
                "client_message_id": "read-tools-recover",
                "mode": "ask",
            },
        ).json()
        == recovered
    )
    assert rebuilt_deps.session_service.get_session(session_id).run_ids == ()
    assert len(provider.calls) == 3


def test_run_file_question_uses_lookup_when_bounded_evidence_omits_file_location(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Inspect sample", "output-file-question").json()["id"]
    artifact = deps.artifact_store_factory(run_id).save_text(
        "plots/eda_correlation.png",
        "registered plot metadata",
        produced_by=run_id,
        kind=ArtifactKind.PLOT,
    )
    _append_artifact_created(deps, run_id, artifact)
    _complete(deps, run_id)
    artifact_path = f"runs/{run_id}/artifacts/{artifact.name}"
    provider = _LowUsageProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": f"runs/{run_id}/artifacts",
                        "pattern": "*.png",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The generated file is registered under the Run artifacts.",
                    "sources": ["q1"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is the generated output file stored?",
            "client_message_id": "run-file-location",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["status"] == "completed", discussion
    assert discussion["kind"] == "answer"
    assert discussion["citations"][0]["path"] == artifact_path
    assert len(provider.calls) == 2
    assert artifact.name not in provider.calls[0]["prompt"]
    system = " ".join(provider.calls[0]["system"].split())
    assert (
        "use a read-only lookup in that Run's virtual tree before saying the file is unavailable"
        in system
    )
    assert "bounded evidence omission does not prove absence" in system
    assert "put filename wildcard syntax only in pattern, never in path" in system
    assert 'path="runs/<run_id>/artifacts", pattern="*.png"' in system


def test_run_png_question_prefers_glob_after_listing_artifact_directories(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Inspect sample", "run-png-inventory").json()["id"]
    artifact_paths = (
        "plots/eda_correlation.png",
        "plots/eda_distribution.png",
    )
    for path in artifact_paths:
        artifact = deps.artifact_store_factory(run_id).save_text(
            path,
            "registered plot metadata",
            produced_by=run_id,
            kind=ArtifactKind.PLOT,
        )
        _append_artifact_created(deps, run_id, artifact)
    _complete(deps, run_id)
    expected_paths = {f"runs/{run_id}/artifacts/{path}" for path in artifact_paths}
    provider = _LowUsageProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "list_files",
                        "path": f"runs/{run_id}/artifacts",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": f"runs/{run_id}/artifacts",
                        "pattern": "*.png",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The registered PNG files are under the Run artifacts directory.",
                    "sources": ["q2_1", "q2_2"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where are the PNG files from this Run?",
            "client_message_id": "run-png-inventory",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["status"] == "completed", discussion
    assert discussion["kind"] == "answer"
    assert {citation["path"] for citation in discussion["citations"]} == expected_paths
    assert len(provider.calls) == 3
    assert all(
        "never authorizes new work or grants approval" in call["system"] for call in provider.calls
    )
    continuation = " ".join(provider.calls[1]["system"].split())
    assert "list_files lists only immediate children" in continuation
    assert "directory listing never proves recursive absence" in continuation
    assert "Example glob: operation=glob" in continuation
    assert 'path="runs/<run_id>/artifacts", pattern="*.png"' in continuation
    assert "When the requested file extension is known, prefer glob" in continuation
    assert "grep searches literal file contents only" in continuation
    assert (
        "Never use grep to search filenames or extensions; use glob for filename patterns"
        in continuation
    )
    assert "Do not repeat an identical operation, path, pattern, and offset" in continuation
    assert "A later tool error cannot invalidate an earlier successful path" in continuation
    assert (
        "Run failure or missing required deliverables does not prove all files are absent"
        in continuation
    )
    assert "grants approval" in continuation
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    usage = [event.payload for event in events if event.type is EventType.SESSION_MODEL_USAGE]
    assert len(usage) == len(provider.calls) <= 6
    assert sum(item["input_tokens"] + item["output_tokens"] for item in usage) <= 16_000


def test_empty_glob_pattern_is_rejected_then_corrected_before_reading_run_artifact(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Inspect sample", "malformed-glob-run").json()["id"]
    artifact = deps.artifact_store_factory(run_id).save_text(
        "plots/eda_correlation.png",
        "registered plot metadata",
        produced_by=run_id,
        kind=ArtifactKind.PLOT,
    )
    _append_artifact_created(deps, run_id, artifact)
    _complete(deps, run_id)
    malformed = {
        "kind": "lookup",
        "message": "",
        "lookup": {
            "operation": "glob",
            "path": f"runs/{run_id}/artifacts/*.png",
            "pattern": "",
        },
    }
    provider = _LowUsageProvider(
        [
            json.dumps(malformed),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": f"runs/{run_id}/artifacts",
                        "pattern": "*.png",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The generated file is registered under the Run artifacts.",
                    "sources": ["q1"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is the generated output file stored?",
            "client_message_id": "malformed-glob-correction",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["status"] == "completed", discussion
    assert discussion["citations"][0]["path"] == f"runs/{run_id}/artifacts/{artifact.name}"
    assert len(provider.calls) == 3
    assert "read_validation_error_1" in provider.calls[1]["prompt"]
    assert "filename pattern" in provider.calls[1]["prompt"]
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    requests = [event for event in events if event.type is EventType.MODEL_REQUEST_RECORDED]
    responses = [event for event in events if event.type is EventType.MODEL_RESPONSE_CHUNK]
    usage = [event for event in events if event.type is EventType.SESSION_MODEL_USAGE]
    assert len(requests) == len(responses) == len(usage) == 3
    assert json.loads(responses[0].payload["message"]["content"]) == malformed
    assert sum(event.type is EventType.POLICY_DECISION for event in events) == 1
    assert sum(event.type is EventType.TOOL_STARTED for event in events) == 1
    assert not any(event.type is EventType.TOOL_DENIED for event in events)
    assert [event.seq for event in events] == list(range(len(events)))


@pytest.mark.parametrize(
    "malformed",
    [
        "{",
        {
            "kind": "lookup",
            "message": "",
            "lookup": {
                "operation": "glob",
                "path": "runs/run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/*.png",
                "pattern": "",
                "offset": "not-an-integer",
            },
        },
        {
            "kind": "lookup",
            "message": "",
            "lookup": {"operation": "grep", "path": "project", "pattern": ""},
        },
        {
            "kind": "lookup",
            "message": "",
            "lookup": {
                "operation": "glob",
                "path": "project",
                "pattern": "",
                "unrecognized": "not a supported read argument",
            },
        },
        {
            "kind": "lookup",
            "message": "",
            "unrecognized": "not a supported proposal field",
            "lookup": {"operation": "glob", "path": "project", "pattern": ""},
        },
        {
            "kind": "lookup",
            "message": "x" * 6001,
            "lookup": {"operation": "glob", "path": "project", "pattern": ""},
        },
        {
            "kind": "lookup",
            "message": "",
            "work_kind": "unsupported",
            "lookup": {"operation": "glob", "path": "project", "pattern": ""},
        },
    ],
    ids=[
        "invalid-json",
        "other-invalid-lookup-field",
        "non-glob-operation",
        "extra-lookup-field",
        "extra-proposal-field",
        "oversized-proposal-message",
        "invalid-work-kind",
    ],
)
def test_unrecognized_malformed_lookup_shapes_remain_terminal(tmp_path, malformed):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _SchemaRecordingProvider(
        [json.dumps(malformed) if isinstance(malformed, dict) else malformed],
        model="test-model",
    )
    deps.session_chat.provider = provider

    discussion = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is the generated output file stored?",
            "client_message_id": "terminal-malformed-lookup",
            "mode": "ask",
        },
    ).json()

    assert discussion["status"] == "failed"
    assert len(provider.calls) == 1
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in events) == 1
    assert sum(event.type is EventType.MODEL_RESPONSE_CHUNK for event in events) == 1
    assert sum(event.type is EventType.SESSION_MODEL_USAGE for event in events) == 1
    assert not any(event.type is EventType.POLICY_DECISION for event in events)
    assert not any(event.type is EventType.TOOL_STARTED for event in events)
    assert [event.seq for event in events] == list(range(len(events)))


def test_unknown_usage_on_empty_glob_is_terminal_without_retry(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _UnknownUsageProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": "runs/run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/*.png",
                        "pattern": "",
                    },
                }
            )
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    discussion = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is the generated output file stored?",
            "client_message_id": "unknown-usage-empty-glob",
            "mode": "ask",
        },
    ).json()

    assert discussion["status"] == "failed"
    assert len(provider.calls) == 1
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in events) == 1
    assert sum(event.type is EventType.MODEL_RESPONSE_CHUNK for event in events) == 1
    assert sum(event.type is EventType.SESSION_MODEL_USAGE for event in events) == 1
    assert events[-1].payload.get("usage_status") == "unknown"
    assert not any(event.type is EventType.POLICY_DECISION for event in events)
    assert not any(event.type is EventType.TOOL_STARTED for event in events)
    assert [event.seq for event in events] == list(range(len(events)))


def test_recovery_correction_schema_rejects_actions(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _SchemaRecordingProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": "runs/run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/*.png",
                        "pattern": "",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "action",
                    "message": "",
                    "sources": [],
                    "lookup": None,
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    discussion = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is the generated output file stored?",
            "client_message_id": "read-only-glob-correction",
            "mode": "ask",
        },
    ).json()

    assert discussion["status"] == "failed"
    assert len(provider.calls) == 2
    assert len(provider.schemas) == 2
    assert provider.schemas[1].model_json_schema()["properties"]["kind"]["enum"] == [
        "answer",
        "clarification",
        "lookup",
    ]
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in events) == 2
    assert sum(event.type is EventType.MODEL_RESPONSE_CHUNK for event in events) == 2
    assert sum(event.type is EventType.SESSION_MODEL_USAGE for event in events) == 2
    assert not any(event.type is EventType.POLICY_DECISION for event in events)
    assert not any(event.type is EventType.TOOL_STARTED for event in events)
    assert not any(event.type is EventType.TOOL_DENIED for event in events)
    assert [event.seq for event in events] == list(range(len(events)))


def test_glob_recovery_keeps_initial_model_route_when_default_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "alternate-model,test-model")
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _RouteSwitchingProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": "runs/run_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/artifacts/*.png",
                        "pattern": "",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "clarification",
                    "message": "Which Run should I inspect?",
                    "sources": [],
                }
            ),
        ],
        model="test-model",
        on_first_call=lambda: monkeypatch.setenv("THYMIRA_MODEL_FAST", "alternate-model"),
    )
    deps.session_chat.provider = provider

    discussion = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is the generated output file stored?",
            "client_message_id": "glob-recovery-stable-route",
            "mode": "ask",
        },
    ).json()

    assert discussion["status"] == "completed", discussion
    assert discussion["kind"] == "clarification"
    assert len(provider.calls) == 2
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    selected = [event for event in events if event.type is EventType.MODEL_SELECTED]
    assert [event.payload["model"] for event in selected] == ["test-model", "test-model"]
    assert not any(event.type is EventType.MODEL_ROUTE_DENIED for event in events)
    assert sum(event.type is EventType.SESSION_MODEL_USAGE for event in events) == 2


def test_empty_glob_recovery_is_limited_once_across_read_query_rounds(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Inspect sample", "repeated-malformed-glob").json()["id"]
    artifact = deps.artifact_store_factory(run_id).save_text(
        "plots/eda_correlation.png",
        "registered plot metadata",
        produced_by=run_id,
        kind=ArtifactKind.PLOT,
    )
    _append_artifact_created(deps, run_id, artifact)
    _complete(deps, run_id)
    malformed = {
        "kind": "lookup",
        "message": "",
        "lookup": {
            "operation": "glob",
            "path": f"runs/{run_id}/artifacts/*.png",
            "pattern": "",
        },
    }
    provider = _LowUsageProvider(
        [
            json.dumps(malformed),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": "runs/run_ffffffffffffffffffffffffffffffff/artifacts",
                        "pattern": "*.png",
                    },
                }
            ),
            json.dumps(malformed),
            json.dumps(
                {
                    "kind": "clarification",
                    "message": "Which Run should I inspect?",
                    "sources": [],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    discussion = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is the generated output file stored?",
            "client_message_id": "repeated-malformed-glob",
            "mode": "ask",
        },
    ).json()

    assert discussion["status"] == "failed", discussion
    assert len(provider.calls) == 3
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in events) == 3
    assert sum(event.type is EventType.MODEL_RESPONSE_CHUNK for event in events) == 3
    assert sum(event.type is EventType.SESSION_MODEL_USAGE for event in events) == 3
    assert verify_events(deps.event_store.read(run_id)).valid


def test_glob_validation_and_runtime_errors_have_distinct_ids(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Inspect sample", "distinct-glob-errors").json()["id"]
    artifact = deps.artifact_store_factory(run_id).save_text(
        "plots/eda_correlation.png",
        "registered plot metadata",
        produced_by=run_id,
        kind=ArtifactKind.PLOT,
    )
    _append_artifact_created(deps, run_id, artifact)
    _complete(deps, run_id)
    provider = _LowUsageProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": f"runs/{run_id}/artifacts/*.png",
                        "pattern": "",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": "runs/run_ffffffffffffffffffffffffffffffff/artifacts",
                        "pattern": "*.png",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": f"runs/{run_id}/artifacts",
                        "pattern": "*.png",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The generated file is registered under the Run artifacts.",
                    "sources": ["q2"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    discussion = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is the generated output file stored?",
            "client_message_id": "distinct-glob-error-ids",
            "mode": "ask",
        },
    ).json()

    assert discussion["status"] == "completed", discussion
    assert discussion["citations"][0]["path"] == f"runs/{run_id}/artifacts/{artifact.name}"
    assert len(provider.calls) == 4
    assert "read_validation_error_1" in provider.calls[1]["prompt"]
    assert "read_validation_error_1" in provider.calls[2]["prompt"]
    assert "read_error_1" in provider.calls[2]["prompt"]
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in events) == 4
    assert sum(event.type is EventType.MODEL_RESPONSE_CHUNK for event in events) == 4
    assert sum(event.type is EventType.SESSION_MODEL_USAGE for event in events) == 4
    assert sum(event.type is EventType.POLICY_DECISION for event in events) == 2
    assert sum(event.type is EventType.TOOL_STARTED for event in events) == 2


def test_confirmation_after_project_citation_keeps_project_reference(tmp_path):
    client, deps = _client(tmp_path)
    source = tmp_path / "workspace" / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("answer = 42\n", encoding="utf-8")
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {"operation": "read_file", "path": "project/src/main.py"},
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The source assigns 42 to `answer`.",
                    "sources": ["q1"],
                }
            ),
            json.dumps(
                {
                    "kind": "clarification",
                    "message": "What would you like to know about it?",
                    "sources": [],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    answer = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "What is the answer?",
            "client_message_id": "path-answer",
            "mode": "ask",
        },
    )
    assert answer.status_code == 201, answer.text
    assert answer.json()["citations"][0]["run_id"] is None
    assert answer.json()["citations"][0]["path"] == "project/src/main.py"

    confirmation = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "yes",
            "client_message_id": "path-confirmation",
            "mode": "ask",
        },
    )
    assert confirmation.status_code == 201, confirmation.text
    assert confirmation.json()["status"] == "completed", confirmation.json()
    assert confirmation.json()["kind"] == "clarification"
    assert len(provider.calls) == 3
    assert "Previously cited project paths: project/src/main.py" in provider.calls[2]["prompt"]


def test_project_read_bounds(  # noqa: PLR0915  # share one API fixture across path-bound assertions
    tmp_path,
):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    project = tmp_path / "workspace"
    source_dir = project / "src"
    source_dir.mkdir()
    (source_dir / "a.txt").write_text("needle one\nneedle two\n", encoding="utf-8")
    (source_dir / "b.txt").write_text("another needle\n", encoding="utf-8")
    (project / ".env").write_text("sk-session-test-secret-12345678901234567890", encoding="utf-8")
    (project / "credentials.txt").write_text("private", encoding="utf-8")
    (project / "server.pem").write_text("private", encoding="utf-8")
    (project / "id_rsa").write_text("private", encoding="utf-8")

    first_page, _ = _read(deps, tmp_path, session_id, "list_files", "project/src", limit=1)
    assert first_page.error is None
    assert first_page.records[0].value["path"] == "project/src/a.txt"
    assert first_page.records[0].value["truncated"] is True
    assert first_page.records[0].value["next_offset"] == 1
    second_page, _ = _read(
        deps, tmp_path, session_id, "list_files", "project/src", offset=1, limit=1
    )
    assert second_page.records[0].value["path"] == "project/src/b.txt"

    grep_page, _ = _read(
        deps, tmp_path, session_id, "grep", "project/src", pattern="needle", limit=1
    )
    assert grep_page.error is None
    assert grep_page.records[0].value["line_range"] == {"start": 1, "end": 1}
    assert grep_page.records[0].value["truncated"] is True
    assert grep_page.records[0].value["next_offset"] == 1
    next_match, _ = _read(
        deps,
        tmp_path,
        session_id,
        "grep",
        "project/src",
        pattern="needle",
        offset=1,
        limit=1,
    )
    assert next_match.error is None
    assert next_match.records[0].value["offset"] == 1

    preview, _ = _read(deps, tmp_path, session_id, "read_file", "project/src/a.txt", limit=1)
    assert preview.error is None
    assert preview.records[0].value["line_range"] == {"start": 1, "end": 1}
    assert preview.records[0].value["next_offset"] == 1
    assert preview.records[0].value["origin_sha256"] is not None

    root_listing, _ = _read(deps, tmp_path, session_id, "list_files", "project")
    listed_paths = {record.value["path"] for record in root_listing.records}
    assert "project/src" in listed_paths
    assert not any(
        any(
            name in str(path).casefold() for name in (".env", "credentials", "server.pem", "id_rsa")
        )
        for path in listed_paths
    )
    for hidden_path in (
        "project/.env",
        "project/credentials.txt",
        "project/server.pem",
        "project/id_rsa",
    ):
        hidden, _ = _read(deps, tmp_path, session_id, "read_file", hidden_path)
        assert hidden.error is not None
        assert "sk-session-test-secret" not in hidden.error

    for invalid_path in (
        "project/../outside",
        "project/src\\a.txt",
        "project/src/a.txt:stream",
        "project/src/CON.txt",
        "project/src/A.txt",
    ):
        invalid, _ = _read(deps, tmp_path, session_id, "read_file", invalid_path)
        assert invalid.error is not None, invalid_path
        assert not invalid.records

    large = project / "large.txt"
    large.write_text("x\n" * 40_000, encoding="utf-8")
    preview, _ = _read(deps, tmp_path, session_id, "read_file", "project/large.txt", limit=1)
    assert preview.error is None
    assert preview.records[0].value["source_truncated"] is True
    assert preview.records[0].value["truncated"] is True
    assert preview.records[0].value["total_lines"] is None
    assert preview.records[0].value["origin_sha256"] is None
    beyond_preview, _ = _read(
        deps, tmp_path, session_id, "read_file", "project/large.txt", offset=40_000
    )
    assert beyond_preview.error is not None

    (source_dir / "binary.bin").write_bytes(b"text\x00private")
    binary, _ = _read(deps, tmp_path, session_id, "read_file", "project/src/binary.bin")
    assert binary.error is not None
    partial_search, _ = _read(deps, tmp_path, session_id, "grep", "project/src", pattern="needle")
    assert partial_search.error is not None
    assert not partial_search.records

    many = project / "many"
    many.mkdir()
    for index in range(201):
        (many / f"f{index:03}.txt").touch()
    incomplete, _ = _read(deps, tmp_path, session_id, "list_files", "project/many")
    assert incomplete.error is not None
    incomplete_search, _ = _read(
        deps, tmp_path, session_id, "grep", "project/many", pattern="absent"
    )
    assert incomplete_search.error is not None
    assert not incomplete_search.records


def test_symlink_project_source_is_not_listed_or_read(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    project = tmp_path / "workspace"
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    link = project / "linked.txt"
    symlink_created = False
    try:
        link.symlink_to(external)
        symlink_created = True
    except (OSError, NotImplementedError):
        pass
    hardlink = project / "hardlinked.txt"
    hardlink_created = False
    try:
        hardlink.hardlink_to(external)
        hardlink_created = True
    except OSError:
        pass
    if not symlink_created and not hardlink_created:
        pytest.skip("the test host does not allow creating a symbolic or hard link")
    listed, _ = _read(deps, tmp_path, session_id, "list_files", "project")
    listed_paths = {record.value["path"] for record in listed.records}
    if symlink_created:
        assert "project/linked.txt" not in listed_paths
        read, _ = _read(deps, tmp_path, session_id, "read_file", "project/linked.txt")
        assert read.error is not None
    if hardlink_created:
        assert "project/hardlinked.txt" not in listed_paths
        read, _ = _read(deps, tmp_path, session_id, "read_file", "project/hardlinked.txt")
        assert read.error is not None


def test_read_query_policy_denials_constraints_and_unknown_operations_fail_closed(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    assert deps.session_chat.policy is not None
    denied_policy = deps.session_chat.policy.model_copy(
        update={
            "capability_rules": (),
            "capability_default_decision": Decision.REQUIRE_HUMAN_REVIEW,
        }
    )
    denied, denied_log = _read(
        deps,
        tmp_path,
        session_id,
        "read_file",
        "project/.thymira/config.yaml",
        policy=denied_policy,
    )
    assert denied.error == "The Session read query was denied by project policy."
    events = denied_log.events()
    assert any(event.type is EventType.POLICY_DECISION for event in events)
    assert any(event.type is EventType.TOOL_DENIED for event in events)
    assert not any(event.type is EventType.TOOL_STARTED for event in events)

    constrained_policy = Policy(
        name="Session read tests",
        version="1",
        capability_rules=(
            CapabilityRule(
                id="allow-list-only",
                decision=Decision.PASS,
                reason="Allow only the exact list operation.",
                risk_levels=("low",),
                activity_categories=("session_read",),
                data_access=("workspace",),
                side_effects=(),
                external_effects=(),
                reversibility=("reversible",),
                execution_constraints=ExecutionConstraints(
                    allowed_tools=("list_files",), max_tool_calls=1
                ),
            ),
        ),
        capability_default_decision=Decision.BLOCK,
    )
    log = SessionProviderLog(deps.session_chat.root, session_id, new_id("turn"))
    allowed, log = _read(
        deps,
        tmp_path,
        session_id,
        "list_files",
        "project",
        policy=constrained_policy,
        log=log,
    )
    assert allowed.error is None
    allowlist_denied, log = _read(
        deps,
        tmp_path,
        session_id,
        "read_file",
        "project/.thymira/config.yaml",
        policy=constrained_policy,
        log=log,
    )
    assert allowlist_denied.error == "The Session read query was denied by project policy."
    call_limit_denied, log = _read(
        deps,
        tmp_path,
        session_id,
        "list_files",
        "project",
        policy=constrained_policy,
        log=log,
    )
    assert call_limit_denied.error == "The Session read query was denied by project policy."
    assert sum(event.type is EventType.TOOL_STARTED for event in log.events()) == 1

    with pytest.raises(ValidationError):
        SessionReadRequest.model_validate({"operation": "write_file", "path": "project/a"})
    with pytest.raises(ValidationError):
        SessionReadRequest.model_validate({"operation": "grep", "path": "project"})


@pytest.mark.parametrize("operation", ["list_files", "read_file"])
@pytest.mark.parametrize("pattern", ["", "*.csv"])
def test_read_request_ignores_a_pattern_for_path_operations(operation, pattern):
    request = SessionReadRequest.model_validate(
        {"operation": operation, "path": "project", "pattern": pattern}
    )

    assert request.pattern is None
    with pytest.raises(ValidationError):
        SessionReadRequest.model_validate({"operation": "glob", "pattern": ""})


def test_proposal_ignores_fields_its_kind_does_not_use():
    read = {"operation": "list_files", "path": "data/", "pattern": "x.csv"}

    action = SessionChatProposal.model_validate({"kind": "action", "message": "", "lookup": read})
    lookup = SessionChatProposal.model_validate(
        {
            "kind": "lookup",
            "message": "",
            "sources": ["src_1"],
            "lookup": read,
            "source_excerpt": {"source": "src_1"},
        }
    )

    assert action.lookup is None
    assert lookup.lookup is not None
    assert lookup.sources == ()
    assert lookup.source_excerpt is None
    with pytest.raises(ValidationError):
        SessionChatProposal.model_validate({"kind": "lookup", "message": ""})


@pytest.mark.parametrize("with_prior_discussion", [False, True])
def test_explicit_png_location_uses_successful_file_results_without_false_model_answer(
    tmp_path, with_prior_discussion
):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    plot_run = _send(client, session_id, "Generate profile plots", "profile-png-run").json()["id"]
    artifact_store = deps.artifact_store_factory(plot_run)
    artifact_paths = (
        f"runs/{plot_run}/artifacts/profile/roc_curve.png",
        f"runs/{plot_run}/artifacts/profile/residuals.png",
    )
    for name in ("profile/roc_curve.png", "profile/residuals.png"):
        artifact = artifact_store.save_text(
            name, "registered PNG fixture", produced_by=plot_run, kind=ArtifactKind.PLOT
        )
        _append_artifact_created(deps, plot_run, artifact)
    _complete(deps, plot_run, response="The profile plots were published.")

    failed_run = _send(
        client, session_id, "Fit final model with required deliverables", "latest-failed-run"
    ).json()["id"]
    from thymira.core import RunController, RunTransitionKind

    RunController(deps.run_store).advance(failed_run, RunTransitionKind.START)
    deps.run_service.fail(
        failed_run, actor=Actor.system(), error="Missing required deliverables: final model"
    )

    if with_prior_discussion:
        deps.session_chat.provider = _LowUsageProvider(
            [
                json.dumps(
                    {
                        "kind": "answer",
                        "message": (
                            "The latest Run failed because required deliverables were missing."
                        ),
                        "sources": ["r0"],
                    }
                )
            ],
            model="test-model",
        )
        prior = client.post(
            f"/sessions/{session_id}/chat",
            json={
                "message": "Why did the latest Run fail?",
                "client_message_id": "prior-failed-run-explanation",
                "mode": "ask",
            },
        )
        assert prior.status_code == 201, prior.text
        assert prior.json()["status"] == "completed", prior.text
        assert prior.json()["kind"] == "answer"

    provider = _LowUsageProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "list_files",
                        "path": f"runs/{plot_run}/artifacts",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "list_files",
                        "path": f"runs/{plot_run}/artifacts/profile",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": (
                        "No PNG files are available because the latest Run missed required "
                        "deliverables."
                    ),
                    "sources": ["q2_1", "q2_2"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "¿Dónde están los PNG de la run?",
            "client_message_id": "locate-profile-png-files",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["status"] == "completed", discussion
    assert discussion["ordinal"] == (2 if with_prior_discussion else 1)
    assert discussion["assistant_message"].startswith("Archivos ubicados en esta consulta:")
    assert all(path in discussion["assistant_message"] for path in artifact_paths)
    assert len(provider.calls) == 2
    assert {citation["path"] for citation in discussion["citations"]} == set(artifact_paths)
    assert all(citation["run_id"] == plot_run for citation in discussion["citations"])
    read_events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    assert (
        sum(
            event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "list_files"
            for event in read_events
        )
        == 2
    )
    assert deps.session_service.get_session(session_id).run_ids == (plot_run, failed_run)


def test_read_query_limit_counts_errors_and_stays_within_four_queries_and_six_requests(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    project = tmp_path / "workspace"
    (project / "public.txt").write_text("public", encoding="utf-8")
    provider = _SchemaRecordingProvider(
        [
            *[
                json.dumps(
                    {
                        "kind": "lookup",
                        "message": "",
                        "lookup": {"operation": "read_file", "path": "project/../outside"},
                    }
                )
                for _ in range(4)
            ],
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {"operation": "read_file", "path": "project/public.txt"},
                }
            ),
            json.dumps(
                {"kind": "answer", "message": "The safe read limit was reached.", "sources": []}
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider
    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Inspect a bounded file",
            "client_message_id": "read-cap",
            "mode": "ask",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.json()
    assert len(provider.calls) == 6
    assert all('"tool_error_id":"read_error_' in call["prompt"] for call in provider.calls[1:5])
    assert '"tool_error_id":"read_query_limit"' in provider.calls[5]["prompt"]
    assert provider.schemas[4].model_json_schema()["properties"]["kind"]["enum"] == [
        "answer",
        "clarification",
        "lookup",
    ]
    assert provider.schemas[5].model_json_schema()["properties"]["kind"]["const"] == "answer"
    assert "public" not in provider.calls[5]["prompt"]
    assert (
        sum(
            event.type is EventType.TOOL_STARTED
            and event.payload.get("tool") in {"list_files", "read_file", "glob", "grep"}
            for event in SessionProviderLog(
                deps.session_chat.root, session_id, response.json()["id"]
            ).events()
        )
        == 4
    )
    assert deps.session_service.get_session(session_id).run_ids == ()


def test_interrupted_dispatched_lookup_stays_failed_but_new_message_works(tmp_path):
    client, deps = _client(tmp_path)
    project = tmp_path / "workspace"
    source = project / "src" / "main.py"
    source.parent.mkdir()
    source.write_text("answer = 42\n", encoding="utf-8")
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {"operation": "read_file", "path": "project/src/main.py"},
                }
            )
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider
    request = {
        "message": "Read the existing implementation.",
        "client_message_id": "interrupted-read",
        "mode": "ask",
    }
    response = client.post(f"/sessions/{session_id}/chat", json=request)
    assert response.status_code == 201
    failed = response.json()
    assert failed["status"] == "failed"
    assert failed["run_id"] is None
    assert len(provider.calls) == 2
    events = SessionProviderLog(deps.session_chat.root, session_id, failed["id"]).events()
    assert sum(event.type is EventType.TOOL_STARTED for event in events) == 1
    assert sum(event.type is EventType.TOOL_COMPLETED for event in events) == 1

    retry = client.post(f"/sessions/{session_id}/chat", json=request)
    assert retry.json() == failed
    assert len(provider.calls) == 2
    assert (
        sum(
            event.type is EventType.TOOL_STARTED
            for event in SessionProviderLog(
                deps.session_chat.root, session_id, failed["id"]
            ).events()
        )
        == 1
    )

    provider = ScriptedProvider(
        [json.dumps({"kind": "answer", "message": "The session is ready.", "sources": []})],
        model="test-model",
    )
    deps.session_chat.provider = provider
    resumed = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Can you answer now?",
            "client_message_id": "new-read-question",
            "mode": "ask",
        },
    )
    assert resumed.json()["status"] == "completed", resumed.json()
    assert len(provider.calls) == 1


def test_glob_scopes_are_relative_and_exact_run_artifacts_are_files(tmp_path):
    client, deps = _client(tmp_path)
    project_source = tmp_path / "workspace" / "src" / "main.py"
    project_source.parent.mkdir(parents=True)
    project_source.write_text("project needle\n", encoding="utf-8")
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Create approved source", "glob-run").json()["id"]
    artifact = deps.artifact_store_factory(run_id).save_text(
        "data/approved.txt", "artifact needle\n", produced_by=run_id, kind=ArtifactKind.OTHER
    )
    _append_artifact_created(deps, run_id, artifact)
    _complete(deps, run_id, response="Approved result for the scoped Run.")
    artifact_path = f"runs/{run_id}/artifacts/{artifact.name}"

    project_matches, _ = _read(deps, tmp_path, session_id, "glob", "project", pattern="src/*.py")
    assert project_matches.error is None
    assert [item.value["path"] for item in project_matches.records] == ["project/src/main.py"]
    nested_matches, _ = _read(deps, tmp_path, session_id, "glob", "project/src", pattern="*.py")
    assert [item.value["path"] for item in nested_matches.records] == ["project/src/main.py"]
    root_matches, _ = _read(deps, tmp_path, session_id, "glob", "", pattern="project/src/*.py")
    assert [item.value["path"] for item in root_matches.records] == ["project/src/main.py"]

    artifact_matches, _ = _read(
        deps,
        tmp_path,
        session_id,
        "glob",
        f"runs/{run_id}/artifacts",
        pattern="data/*.txt",
    )
    assert artifact_matches.error is None
    assert [item.value["path"] for item in artifact_matches.records] == [artifact_path]
    exact_file_match, _ = _read(deps, tmp_path, session_id, "glob", artifact_path, pattern="*.txt")
    assert [item.value["path"] for item in exact_file_match.records] == [artifact_path]
    exact_file_search, _ = _read(
        deps, tmp_path, session_id, "grep", artifact_path, pattern="needle"
    )
    assert exact_file_search.error is None
    assert exact_file_search.records[0].value["total_count"] == 1

    missing, _ = _read(
        deps,
        tmp_path,
        session_id,
        "glob",
        f"runs/{run_id}/artifacts/missing",
        pattern="*.txt",
    )
    assert missing.error is not None
    assert not missing.records


def test_grep_excerpt_truncation_reports_clipped_line_context(tmp_path):
    client, deps = _client(tmp_path)
    source = tmp_path / "workspace" / "src" / "excerpt.txt"
    source.parent.mkdir(parents=True)
    source.write_text(
        "\n".join(
            (
                "needle",
                f"{'x' * 200}needle{'x' * 200}",
                f"needle{'x' * 200}",
                f"{'x' * 200}needle",
            )
        ),
        encoding="utf-8",
    )
    session_id = client.post("/sessions", json={}).json()["id"]
    output, _ = _read(
        deps, tmp_path, session_id, "grep", "project/src/excerpt.txt", pattern="needle"
    )
    assert output.error is None
    assert [item.value["excerpt_truncated"] for item in output.records] == [
        False,
        True,
        True,
        True,
    ]


def test_artifact_content_requires_the_approved_creation_snapshot(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Create approved source", "approved-snapshot").json()["id"]
    store = deps.artifact_store_factory(run_id)
    approved = store.save_text(
        "data/reused.txt", "approved original bytes", produced_by=run_id, kind=ArtifactKind.OTHER
    )
    _append_artifact_created(deps, run_id, approved)
    _complete(deps, run_id, response="The Run has an approved result.")
    approved_path = f"runs/{run_id}/artifacts/{approved.name}"

    original, _ = _read(deps, tmp_path, session_id, "read_file", approved_path)
    assert original.error is None
    assert "approved original bytes" in original.records[0].value["content"]

    late = store.save_text(
        "data/late.txt", "unapproved late bytes", produced_by=run_id, kind=ArtifactKind.OTHER
    )
    _append_artifact_created(deps, run_id, late)
    late_path = f"runs/{run_id}/artifacts/{late.name}"
    late_read, _ = _read(deps, tmp_path, session_id, "read_file", late_path)
    assert late_read.error is not None
    assert "unapproved late bytes" not in str(late_read.records)
    late_search, _ = _read(deps, tmp_path, session_id, "grep", late_path, pattern="unapproved")
    assert late_search.error is not None
    assert not late_search.records

    replacement = store.save_text(
        "data/reused.txt",
        "unapproved replacement bytes",
        produced_by=run_id,
        kind=ArtifactKind.OTHER,
    )
    _append_artifact_created(deps, run_id, replacement)
    replacement_read, _ = _read(deps, tmp_path, session_id, "read_file", approved_path)
    assert replacement_read.error is not None
    assert "unapproved replacement bytes" not in str(replacement_read.records)


def test_post_audit_artifact_invalidation_revokes_content_with_stale_manifest(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Create approved source", "post-audit-invalidation").json()[
        "id"
    ]
    artifact = deps.artifact_store_factory(run_id).save_text(
        "data/revoked.txt", "revoked approved bytes", produced_by=run_id, kind=ArtifactKind.OTHER
    )
    _append_artifact_created(deps, run_id, artifact)
    _complete(deps, run_id, response="The result was approved before invalidation.")
    deps.event_store.open(run_id).append(
        EventType.ARTIFACT_INVALIDATED,
        Actor.system(),
        {
            "name": artifact.name,
            "artifact_id": artifact.id,
            "reason": "fixture post-audit invalidation",
        },
        subject_id=artifact.id,
        producer="thymira.core",
        producer_version="0.1",
    )
    assert session_read_tools.read_manifest(tmp_path / "runtime", run_id)[artifact.name].valid

    artifact_path = f"runs/{run_id}/artifacts/{artifact.name}"
    read, _ = _read(deps, tmp_path, session_id, "read_file", artifact_path)
    assert read.error is not None
    assert "revoked approved bytes" not in str(read.records)
    search, _ = _read(deps, tmp_path, session_id, "grep", artifact_path, pattern="revoked")
    assert search.error is not None
    assert not search.records


def test_new_approved_artifact_revision_survives_old_revision_invalidation(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Recover artifact", "artifact-recovery").json()["id"]
    store = deps.artifact_store_factory(run_id)
    old = store.save_text(
        "data/recovered.txt", "old artifact bytes", produced_by=run_id, kind=ArtifactKind.OTHER
    )
    _append_artifact_created(deps, run_id, old)
    assert store.invalidate((old.name,), "fixture revision replacement") == [old.name]
    deps.event_store.open(run_id).append(
        EventType.ARTIFACT_INVALIDATED,
        Actor.system(),
        {
            "name": old.name,
            "artifact_id": old.id,
            "reason": "fixture revision replacement",
        },
        subject_id=old.id,
        producer="thymira.core",
        producer_version="0.1",
    )
    current = store.save_text(
        old.name, "new approved artifact bytes", produced_by=run_id, kind=ArtifactKind.OTHER
    )
    assert current.id != old.id
    _append_artifact_created(deps, run_id, current)
    _complete(deps, run_id, response="The new artifact revision was approved.")

    artifact_path = f"runs/{run_id}/artifacts/{current.name}"
    read, _ = _read(deps, tmp_path, session_id, "read_file", artifact_path)
    assert read.error is None
    assert "new approved artifact bytes" in read.records[0].value["content"]
    assert "old artifact bytes" not in str(read.records)


def test_run_artifact_citation_label_is_bounded_for_long_logical_path(tmp_path, monkeypatch):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id, "Create approved source", "long-artifact-path").json()["id"]
    artifact = deps.artifact_store_factory(run_id).save_text(
        "short.txt", "physical URI stays short", produced_by=run_id, kind=ArtifactKind.OTHER
    )
    long_name = f"data/{'logical' * 34}.txt"
    _append_artifact_created(deps, run_id, artifact, name=long_name)
    _complete(deps, run_id, response="The logical artifact is approved.")
    logical_artifact = artifact.model_copy(update={"name": long_name})
    monkeypatch.setattr(
        session_read_tools,
        "read_manifest",
        lambda _root, _run_id: {long_name: logical_artifact},
    )

    path = f"runs/{run_id}/artifacts/{long_name}"
    assert len(path) > 200
    output, _ = _read(deps, tmp_path, session_id, "read_file", path)
    assert output.error is None
    assert output.records[0].citation.label == path[:200]
    assert len(output.records[0].citation.label) == 200


def test_old_run_artifact_bindings(  # noqa: PLR0915  # exercise artifact gates using one Session fixture
    tmp_path,
):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    old_run = _send(client, session_id, "Create approved source", "old-source").json()["id"]
    store = deps.artifact_store_factory(old_run)
    valid = store.save_text(
        "data/valid.txt",
        "approved artifact text",
        produced_by=old_run,
        kind=ArtifactKind.OTHER,
    )
    tampered = store.save_text(
        "data/tampered.txt",
        "registered original bytes",
        produced_by=old_run,
        kind=ArtifactKind.OTHER,
    )
    invalidated = store.save_text(
        "data/invalidated.txt",
        "invalidated original bytes",
        produced_by=old_run,
        kind=ArtifactKind.OTHER,
    )
    artifact_events = {}
    for artifact in (valid, tampered, invalidated):
        artifact_events[artifact.name] = deps.event_store.open(old_run).append(
            EventType.ARTIFACT_CREATED,
            Actor.system(),
            {
                "artifact_id": artifact.id,
                "name": artifact.name,
                "kind": artifact.kind.value,
                "sha256": artifact.sha256,
            },
        )
    store.invalidate((invalidated.name,), "fixture invalidation")
    _complete(deps, old_run, response="Approved result for the older Run.")
    tampered_path = tmp_path / "runtime" / "artifacts" / old_run / tampered.uri
    tampered_path.write_bytes(b"tampered replacement bytes")

    later_runs = []
    for index in range(4):
        run_id = _send(client, session_id, f"Later request {index}", f"later-{index}").json()["id"]
        _complete(deps, run_id)
        later_runs.append(run_id)
    detail = deps.session_chat.detail(session_id, project_id=deps.project_resolution.project_id)
    assert detail.turns[0].run.id == old_run
    assert old_run not in {turn.run.id for turn in detail.turns[-4:]}

    foreign_session = client.post("/sessions", json={}).json()["id"]
    foreign_run = _send(client, foreign_session, "Foreign session request", "foreign").json()["id"]
    head_before = deps.event_store.read(old_run)[-1].hash
    manifest_path = tmp_path / "runtime" / "artifacts" / old_run / "manifest.json"
    manifest_before = manifest_path.read_bytes()
    tampered_before = tampered_path.read_bytes()
    assert deps.session_chat.provider is None
    provider = ScriptedProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "read_file",
                        "path": f"runs/{old_run}/artifacts/{valid.name}",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "read_file",
                        "path": f"runs/{old_run}/artifacts/{tampered.name}",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "read_file",
                        "path": f"runs/{old_run}/artifacts/{invalidated.name}",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {"operation": "read_file", "path": f"runs/{foreign_run}/status.json"},
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The older registered artifact is still verified.",
                    "sources": ["q1"],
                }
            ),
        ],
        model="test-model",
    )
    deps.session_chat.provider = provider
    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Read the older Run artifact and verify its registration.",
            "client_message_id": "older-run-artifact",
            "mode": "ask",
        },
    )
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["status"] == "completed", value
    assert value["run_id"] is None
    assert value["citations"] == [
        {
            "run_id": old_run,
            "seq": artifact_events[valid.name].seq,
            "path": f"runs/{old_run}/artifacts/{valid.name}",
            "label": f"runs/{old_run}/artifacts/{valid.name}",
        }
    ]
    assert "approved artifact text" in provider.calls[1]["prompt"]
    for call in provider.calls[2:5]:
        assert '"tool_error_id":"read_error_' in call["prompt"]
    assert "tampered replacement bytes" not in "".join(call["prompt"] for call in provider.calls)
    assert "invalidated original bytes" not in "".join(call["prompt"] for call in provider.calls)
    assert manifest_path.read_bytes() == manifest_before
    assert tampered_path.read_bytes() == tampered_before
    assert deps.event_store.read(old_run)[-1].hash == head_before
    assert deps.session_service.get_session(session_id).run_ids == (old_run, *later_runs)

    # A foreign project scope in the reconstructed detail is rejected before trace access.
    old_turn = detail.turns[0]
    foreign_project_turn = old_turn.model_copy(
        update={"run": old_turn.run.model_copy(update={"project_id": new_id("project")})}
    )
    foreign_project_detail = detail.model_copy(
        update={"turns": (foreign_project_turn, *detail.turns[1:])}
    )
    cross_project, _ = _read(
        deps,
        tmp_path,
        session_id,
        "read_file",
        f"runs/{old_run}/status.json",
        detail=foreign_project_detail,
    )
    assert cross_project.error is not None

    # Pending and failed Runs expose status and safe trace facts, never a provisional result.
    pending_run = _send(client, session_id, "Pending request", "pending").json()["id"]
    deps.event_store.open(pending_run).append(
        EventType.AGENT_MESSAGE,
        Actor.system(),
        {"kind": "conversation.response", "content": "PRIVATE provisional output"},
    )
    pending_status, _ = _read(
        deps, tmp_path, session_id, "read_file", f"runs/{pending_run}/status.json"
    )
    assert pending_status.error is None
    assert '"approved_result_available":false' in str(pending_status.records[0].value)
    pending_result, _ = _read(
        deps, tmp_path, session_id, "read_file", f"runs/{pending_run}/result.txt"
    )
    assert pending_result.error is not None
    pending_trace, _ = _read(
        deps, tmp_path, session_id, "read_file", f"runs/{pending_run}/trace.jsonl"
    )
    assert pending_trace.error is None
    assert "PRIVATE provisional output" not in str(pending_trace.records[0].value)

    deps.run_service.fail(
        pending_run,
        actor=Actor.system(),
        error="PRIVATE provider prompt and failed provisional result",
    )
    failed_status, _ = _read(
        deps, tmp_path, session_id, "read_file", f"runs/{pending_run}/status.json"
    )
    assert failed_status.error is None
    assert '"status":"FAILED"' in str(failed_status.records[0].value)
    assert '"approved_result_available":false' in str(failed_status.records[0].value)
    failed_result, _ = _read(
        deps, tmp_path, session_id, "read_file", f"runs/{pending_run}/result.txt"
    )
    assert failed_result.error is not None
    failed_trace, _ = _read(
        deps, tmp_path, session_id, "read_file", f"runs/{pending_run}/trace.jsonl"
    )
    assert failed_trace.error is None
    assert "PRIVATE provider prompt" not in str(failed_trace.records[0].value)
