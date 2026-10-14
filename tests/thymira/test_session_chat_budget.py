"""SessionChat reserves its durable turn budget before dispatching a repair."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.thymira.test_api_sessions import _client, _complete, _send
from thymira.agents.llm.base import LLMCallError, LLMConfigurationError, LLMResponse
from thymira.agents.llm.litellm_provider import LiteLLMProvider
from thymira.agents.llm.scripted import ScriptedProvider
from thymira.core import session_chat_model
from thymira.core.session_chat_budget import session_usage_snapshot
from thymira.schemas import EventType
from thymira.state.session_discussions import SessionProviderLog

if TYPE_CHECKING:
    from pydantic import BaseModel

    from thymira.agents.llm.base import BaseModelT

pytestmark = pytest.mark.integration


class _MeasuredProvider(ScriptedProvider):
    """Replay the exact provider usage from the over-budget interpretation and repair."""

    def __init__(self) -> None:
        super().__init__(
            [
                json.dumps(
                    {"kind": "answer", "message": "The result is available.", "sources": []}
                ),
                json.dumps(
                    {
                        "kind": "answer",
                        "message": "The approved result is available.",
                        "sources": ["r0"],
                    }
                ),
            ],
            model="test-model",
        )
        self._usage = [(7_388, 332), (8_049, 339)]

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: type[BaseModelT],
        system: str = "",
    ) -> tuple[BaseModelT, LLMResponse]:
        proposal, response = super().complete_structured(prompt, schema=schema, system=system)
        input_tokens, output_tokens = self._usage.pop(0)
        return proposal, response.model_copy(
            update={"input_tokens": input_tokens, "output_tokens": output_tokens}
        )


class _UsageProvider(ScriptedProvider):
    """Supply deterministic provider usage, including partial and subset-only reports."""

    def __init__(self, responses: list[str], usages: list[dict[str, object]]) -> None:
        super().__init__(responses, model="test-model")
        self._usages = list(usages)

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: type[BaseModelT],
        system: str = "",
    ) -> tuple[BaseModelT, LLMResponse]:
        proposal, response = super().complete_structured(prompt, schema=schema, system=system)
        usage = self._usages.pop(0)
        metadata = usage.get("metadata")
        response_metadata = dict(response.metadata)
        if isinstance(metadata, dict):
            response_metadata.update(metadata)
        return proposal, response.model_copy(
            update={
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cached_input_tokens": usage.get("cached_input_tokens", 0),
                "reasoning_output_tokens": usage.get("reasoning_output_tokens", 0),
                "metadata": response_metadata,
            }
        )


class _BudgetPressureProvider(_UsageProvider):
    """Replay the provider usage that leaves only 3,202 tokens before the final read."""

    def __init__(self, responses: list[str], usages: list[dict[str, object]]) -> None:
        super().__init__(responses, usages)
        self.schemas: list[type[BaseModel]] = []

    def complete_structured(
        self,
        prompt: str,
        *,
        schema: type[BaseModelT],
        system: str = "",
    ) -> tuple[BaseModelT, LLMResponse]:
        self.schemas.append(schema)
        return super().complete_structured(prompt, schema=schema, system=system)


@pytest.fixture(autouse=True)
def _test_model_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("THYMIRA_ALLOWED_MODEL_ROUTES", "test-model")
    monkeypatch.setenv("THYMIRA_MODEL_FAST", "test-model")


def test_repair_is_not_dispatched_when_its_projection_exceeds_the_remaining_turn_budget(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id).json()["id"]
    _complete(deps, run_id)
    provider = _MeasuredProvider()
    deps.session_chat.provider = provider
    projections: list[tuple[str, str]] = []

    def historical_input_estimate(
        model: str, prompt: str, system: str, schema: type[BaseModelT]
    ) -> int:
        del model, schema
        projections.append((prompt, system))
        return 8_049 if "repair" in f"{prompt}\n{system}".casefold() else 7_388

    monkeypatch.setattr(
        session_chat_model,
        "estimate_structured_input_tokens",
        historical_input_estimate,
        raising=False,
    )

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain the approved result.",
            "client_message_id": "historical-budget-case",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["status"] == "failed"
    assert discussion["assistant_message"] is None
    assert discussion["run_id"] is None
    assert len(projections) == 3
    assert "SERVER CITATION REPAIR" in projections[0][0]
    assert "Explain the approved result." in projections[2][0]
    assert len(provider.calls) == 1
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in events) == 1
    usage = [event.payload for event in events if event.type is EventType.SESSION_MODEL_USAGE]
    assert [(item["input_tokens"], item["output_tokens"]) for item in usage] == [(7_388, 332)]
    assert deps.session_service.get_session(session_id).run_ids == (run_id,)


def test_compact_read_continuation_keeps_cited_result_with_only_3202_tokens_left(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, deps = _client(tmp_path)
    assert deps.project_resolution is not None
    project_file = deps.project_resolution.project_dir / "manifest.json"
    project_file.write_text('{"source": "verified"}\n', encoding="utf-8")
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _BudgetPressureProvider(
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
                        "pattern": "manifest.json",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The file is stored at project/manifest.json.",
                    "sources": ["q2"],
                    "lookup": {
                        "operation": "glob",
                        "path": "project/*.json",
                        "pattern": "",
                    },
                }
            ),
        ],
        [
            {
                "input_tokens": 5_800,
                "output_tokens": 200,
                "metadata": {"usage_known": True},
            },
            {
                "input_tokens": 6_598,
                "output_tokens": 200,
                "metadata": {"usage_known": True},
            },
            {
                "input_tokens": 1_400,
                "output_tokens": 200,
                "metadata": {"usage_known": True},
            },
        ],
    )
    deps.session_chat.provider = provider
    estimates: list[tuple[str, int]] = []

    def reported_prompt_estimate(
        model: str, prompt: str, system: str, schema: type[BaseModelT]
    ) -> int:
        del model, system, schema
        has_first = '"source":"q1"' in prompt
        has_second = '"source":"q2"' in prompt
        estimate = 3_226 if has_first and has_second else 800
        estimates.append((prompt, estimate))
        return estimate

    monkeypatch.setattr(
        session_chat_model,
        "estimate_structured_input_tokens",
        reported_prompt_estimate,
        raising=False,
    )

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is manifest.json in the project?",
            "client_message_id": "compact-read-budget",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["status"] == "completed", discussion
    assert discussion["citations"][0]["path"] == "project/manifest.json"
    assert len(provider.calls) == 3
    assert '"source":"q2"' in provider.calls[2]["prompt"]
    assert '"source":"q1"' not in provider.calls[2]["prompt"]
    assert provider.calls[2]["max_output_tokens"] == 578
    assert provider.schemas[-1].model_json_schema()["properties"]["kind"]["enum"] == [
        "answer",
        "clarification",
        "lookup",
    ]
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    usage = [event.payload for event in events if event.type is EventType.SESSION_MODEL_USAGE]
    assert sum(item["input_tokens"] + item["output_tokens"] for item in usage) == 14_398
    assert usage[2]["reserved_for_finalization_tokens"] == 1_440
    assert any(
        '"source":"q2"' in prompt and '"source":"q1"' not in prompt and estimate == 800
        for prompt, estimate in estimates
    )
    assert deps.session_service.get_session(session_id).run_ids == ()


def test_lookup_is_not_executed_when_actual_usage_leaves_no_final_answer_budget(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _UsageProvider(
        [
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {"operation": "list_files", "path": "project"},
                }
            )
        ],
        [
            {
                "input_tokens": 15_300,
                "output_tokens": 100,
                "metadata": {"usage_known": True},
            }
        ],
    )
    deps.session_chat.provider = provider
    monkeypatch.setattr(session_chat_model, "estimate_structured_input_tokens", lambda *args: 800)

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "List the existing project files.",
            "client_message_id": "no-final-answer-budget",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    discussion = response.json()
    assert discussion["status"] == "failed"
    assert "cannot fit a final answer" in discussion["error"]
    assert len(provider.calls) == 1
    events = SessionProviderLog(deps.session_chat.root, session_id, discussion["id"]).events()
    assert not any(
        event.type is EventType.TOOL_STARTED
        and event.payload.get("tool") in {"list_files", "read_file", "glob", "grep"}
        for event in events
    )


def test_compact_read_correction_is_estimated_without_an_unbudgeted_system_suffix(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, deps = _client(tmp_path)
    assert deps.project_resolution is not None
    (deps.project_resolution.project_dir / "manifest.json").write_text(
        '{"source": "verified"}\n', encoding="utf-8"
    )
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _UsageProvider(
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
                    "lookup": {"operation": "glob", "path": "project", "pattern": ""},
                }
            ),
            json.dumps(
                {
                    "kind": "lookup",
                    "message": "",
                    "lookup": {
                        "operation": "glob",
                        "path": "project",
                        "pattern": "manifest.json",
                    },
                }
            ),
            json.dumps(
                {
                    "kind": "answer",
                    "message": "The file is stored at project/manifest.json.",
                    "sources": ["q2"],
                }
            ),
        ],
        [
            {"input_tokens": 5_800, "output_tokens": 200, "metadata": {"usage_known": True}},
            {"input_tokens": 6_800, "output_tokens": 200, "metadata": {"usage_known": True}},
            {"input_tokens": 1, "output_tokens": 1, "metadata": {"usage_known": True}},
            {"input_tokens": 1, "output_tokens": 1, "metadata": {"usage_known": True}},
        ],
    )
    deps.session_chat.provider = provider
    estimated_systems: list[str] = []

    def correction_sensitive_estimate(
        model: str, prompt: str, system: str, schema: type[BaseModelT]
    ) -> int:
        del model, prompt, schema
        estimated_systems.append(system)
        return 1_000 if "latest server evidence contains a safe correction" in system else 800

    monkeypatch.setattr(
        session_chat_model,
        "estimate_structured_input_tokens",
        correction_sensitive_estimate,
        raising=False,
    )

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Where is manifest.json in the project?",
            "client_message_id": "compact-read-correction-reserve",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed", response.json()
    assert len(provider.calls) == 4
    events = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"]).events()
    usage = [event.payload for event in events if event.type is EventType.SESSION_MODEL_USAGE]
    assert usage[2]["estimated_input_tokens"] == 800
    assert usage[2]["reserved_for_finalization_tokens"] == 1_440
    assert provider.calls[2]["system"] in estimated_systems


def test_valid_cited_first_response_records_repair_reservation_without_charging_it(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id).json()["id"]
    _complete(deps, run_id)
    provider = _UsageProvider(
        [json.dumps({"kind": "answer", "message": "The result is recorded.", "sources": ["r0"]})],
        [{"input_tokens": 15, "output_tokens": 10, "metadata": {"usage_known": True}}],
    )
    deps.session_chat.provider = provider
    monkeypatch.setattr(session_chat_model, "estimate_structured_input_tokens", lambda *args: 20)

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain the approved result.",
            "client_message_id": "reserved-first",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"
    assert len(provider.calls) == 1
    events = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"]).events()
    usage = next(event.payload for event in events if event.type is EventType.SESSION_MODEL_USAGE)
    assert usage["repair_projection_tokens"] > 0
    assert usage["reserved_for_repair_tokens"] == usage["repair_projection_tokens"]
    assert (
        session_usage_snapshot(
            SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"])
        ).tokens
        == 25
    )


def test_invalid_source_is_repaired_when_compact_repair_fits(tmp_path, monkeypatch):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    run_id = _send(client, session_id).json()["id"]
    _complete(deps, run_id)
    provider = _UsageProvider(
        [
            json.dumps({"kind": "answer", "message": "Draft answer.", "sources": ["r0e9999"]}),
            json.dumps(
                {"kind": "answer", "message": "The approved result is recorded.", "sources": ["r0"]}
            ),
        ],
        [
            {"input_tokens": 500, "output_tokens": 100, "metadata": {"usage_known": True}},
            {"input_tokens": 700, "output_tokens": 100, "metadata": {"usage_known": True}},
        ],
    )
    deps.session_chat.provider = provider
    monkeypatch.setattr(session_chat_model, "estimate_structured_input_tokens", lambda *args: 300)

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain the approved result.",
            "client_message_id": "repair-fits",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"
    assert response.json()["citations"][0]["run_id"] == run_id
    assert len(provider.calls) == 2
    assert "SERVER CITATION REPAIR" in provider.calls[1]["prompt"]
    assert "Draft answer." in provider.calls[1]["prompt"]
    assert "complete-verified-repair-sources:START>>>" in provider.calls[1]["prompt"]
    assert "read-only session conversation assistant" not in provider.calls[1]["system"].lower()


def test_exact_16000_inclusive_usage_completes_and_subsets_are_not_double_counted(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _UsageProvider(
        [json.dumps({"kind": "answer", "message": "A concise answer.", "sources": []})],
        [
            {
                "input_tokens": 14_400,
                "output_tokens": 1_600,
                "cached_input_tokens": 7_000,
                "reasoning_output_tokens": 900,
                "metadata": {"usage_known": True},
            }
        ],
    )
    deps.session_chat.provider = provider
    monkeypatch.setattr(session_chat_model, "estimate_structured_input_tokens", lambda *args: 0)

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain this session.",
            "client_message_id": "exact-budget",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"
    log = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"])
    assert session_usage_snapshot(log).tokens == 16_000
    usage = next(
        event.payload for event in log.events() if event.type is EventType.SESSION_MODEL_USAGE
    )
    assert usage["cached_input_tokens"] == 7_000
    assert usage["reasoning_output_tokens"] == 900


def test_insufficient_local_projection_refuses_before_provider_dispatch(tmp_path, monkeypatch):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = ScriptedProvider(
        [json.dumps({"kind": "answer", "message": "Should not be used.", "sources": []})],
        model="test-model",
    )
    deps.session_chat.provider = provider
    monkeypatch.setattr(
        session_chat_model, "estimate_structured_input_tokens", lambda *args: 15_361
    )

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain this session.",
            "client_message_id": "insufficient",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"
    assert "minimum useful output" in response.json()["error"]
    assert provider.calls == []
    events = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"]).events()
    assert not any(event.type is EventType.MODEL_REQUEST_RECORDED for event in events)


def test_partial_provider_usage_is_unknown_and_preserves_observed_overage(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _UsageProvider(
        [json.dumps({"kind": "answer", "message": "Not safe to publish.", "sources": []})],
        [
            {
                "input_tokens": 17_000,
                "output_tokens": 0,
                "cached_input_tokens": 4_000,
                "metadata": {"usage_known": False},
            }
        ],
    )
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain this session.",
            "client_message_id": "partial-usage",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["assistant_message"] is None
    assert "unknown provider usage" in response.json()["error"]
    assert len(provider.calls) == 1
    log = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"])
    usage = next(
        event.payload for event in log.events() if event.type is EventType.SESSION_MODEL_USAGE
    )
    assert usage["usage_status"] == "unknown"
    assert usage["input_tokens"] == 17_000
    assert usage["output_tokens"] == 0
    assert usage["cached_input_tokens"] == 4_000
    assert usage["model"] == "test-model"
    assert session_usage_snapshot(log).tokens == 17_000


@pytest.mark.parametrize(
    "usage_known",
    [False, True],
    ids=["provider-marks-unknown", "inconsistent-inclusive-totals"],
)
def test_subset_only_usage_is_unknown_and_charged_at_its_lower_bound(tmp_path, usage_known):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _UsageProvider(
        [json.dumps({"kind": "answer", "message": "Not safe to publish.", "sources": []})],
        [
            {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 4_000,
                "reasoning_output_tokens": 20_000,
                "metadata": {"usage_known": usage_known},
            }
        ],
    )
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain this session.",
            "client_message_id": f"subset-{usage_known}",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"
    assert response.json()["assistant_message"] is None
    assert len(provider.calls) == 1
    log = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"])
    usage = next(
        event.payload for event in log.events() if event.type is EventType.SESSION_MODEL_USAGE
    )
    assert usage["usage_status"] == "unknown"
    assert usage["input_tokens"] == 0
    assert usage["output_tokens"] == 0
    assert usage["cached_input_tokens"] == 4_000
    assert usage["reasoning_output_tokens"] == 20_000
    assert session_usage_snapshot(log).tokens == 24_000


def test_response_above_output_cap_is_charged_and_reported_specifically(tmp_path, monkeypatch):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _UsageProvider(
        [json.dumps({"kind": "answer", "message": "Too long.", "sources": []})],
        [
            {
                "input_tokens": 500,
                "output_tokens": 1_601,
                "metadata": {"usage_known": True},
            }
        ],
    )
    deps.session_chat.provider = provider
    monkeypatch.setattr(session_chat_model, "estimate_structured_input_tokens", lambda *args: 0)

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={"message": "Explain this session.", "client_message_id": "over-cap", "mode": "ask"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"
    assert "output above the effective response cap" in response.json()["error"]
    assert len(provider.calls) == 1
    assert provider.calls[0]["max_output_tokens"] == 1_600
    log = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"])
    assert session_usage_snapshot(log).tokens == 2_101


def test_full_human_message_and_trailing_restriction_reach_the_prepared_prompt(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    restriction = "FINAL HUMAN RESTRICTION: Do not train or replace the registered model."
    message = "Earlier context. " * 400 + restriction
    provider = _UsageProvider(
        [json.dumps({"kind": "answer", "message": "Understood.", "sources": []})],
        [{"input_tokens": 15, "output_tokens": 2, "metadata": {"usage_known": True}}],
    )
    deps.session_chat.provider = provider
    monkeypatch.setattr(session_chat_model, "estimate_structured_input_tokens", lambda *args: 0)

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={"message": message, "client_message_id": "full-human-context", "mode": "ask"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "completed"
    assert message in provider.calls[0]["prompt"]
    assert provider.calls[0]["prompt"].endswith(message)


def test_provider_error_after_dispatch_consumes_unknown_and_never_retries_same_turn(tmp_path):
    class _FailingProvider(ScriptedProvider):
        def complete_structured(self, prompt, *, schema, system=""):
            self._next(prompt, system)
            raise LLMCallError("private provider detail")

    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _FailingProvider(["{}"], model="test-model")
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain this session.",
            "client_message_id": "provider-error",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"
    assert "unknown provider usage" in response.json()["error"]
    assert "private provider detail" not in response.json()["error"]
    assert len(provider.calls) == 1
    log = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"])
    assert session_usage_snapshot(log).tokens == 16_000
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in log.events()) == 1
    retry = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain this session.",
            "client_message_id": "provider-error",
            "mode": "ask",
        },
    )
    reopened, restored = _client(tmp_path)
    after_reopen = reopened.post(f"/sessions/{session_id}/chat/{response.json()['id']}/retry")
    assert retry.status_code == 200
    assert after_reopen.status_code in {200, 201}
    assert len(provider.calls) == 1
    assert (
        session_usage_snapshot(
            SessionProviderLog(restored.session_chat.root, session_id, response.json()["id"])
        ).tokens
        == 16_000
    )


def test_invalid_structured_response_is_charged_from_returned_response(tmp_path):
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = ScriptedProvider([json.dumps({"unexpected": "schema mismatch"})], model="test-model")
    deps.session_chat.provider = provider

    response = client.post(
        f"/sessions/{session_id}/chat",
        json={
            "message": "Explain this session.",
            "client_message_id": "parse-error",
            "mode": "ask",
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "failed"
    assert len(provider.calls) == 1
    log = SessionProviderLog(deps.session_chat.root, session_id, response.json()["id"])
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in log.events()) == 1
    usage = next(
        event.payload for event in log.events() if event.type is EventType.SESSION_MODEL_USAGE
    )
    assert usage["usage_status"] == "scripted_estimate"
    assert usage["input_tokens"] > 0


def test_crashed_dispatched_request_is_settled_after_reopen_without_redispatch(tmp_path):
    class _InterruptedProvider(ScriptedProvider):
        def complete_structured(self, prompt, *, schema, system=""):
            self._next(prompt, system)
            raise KeyboardInterrupt

    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _InterruptedProvider(["{}"], model="test-model")
    deps.session_chat.provider = provider
    with pytest.raises(KeyboardInterrupt):
        client.post(
            f"/sessions/{session_id}/chat",
            json={
                "message": "Explain this session.",
                "client_message_id": "crash-retry",
                "mode": "ask",
            },
        )
    pending = client.get(f"/sessions/{session_id}").json()["discussions"][0]

    reopened, restored = _client(tmp_path)
    restored.session_chat.provider = provider
    response = reopened.post(f"/sessions/{session_id}/chat/{pending['id']}/retry")

    assert response.status_code in {200, 201}, response.text
    assert response.json()["status"] == "failed"
    assert len(provider.calls) == 1
    log = SessionProviderLog(restored.session_chat.root, session_id, pending["id"])
    assert sum(event.type is EventType.MODEL_REQUEST_RECORDED for event in log.events()) == 1
    assert session_usage_snapshot(log).tokens == 16_000


def test_recovery_preserves_inclusive_chunk_usage_if_crash_precedes_session_settlement(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, deps = _client(tmp_path)
    session_id = client.post("/sessions", json={}).json()["id"]
    provider = _UsageProvider(
        [json.dumps({"kind": "answer", "message": "Returned but not settled.", "sources": []})],
        [
            {
                "input_tokens": 15_000,
                "output_tokens": 2_000,
                "cached_input_tokens": 4_000,
                "reasoning_output_tokens": 1_200,
                "metadata": {"usage_known": True},
            }
        ],
    )
    deps.session_chat.provider = provider

    def interrupt_before_settlement(*args, **kwargs):
        del args, kwargs
        raise KeyboardInterrupt

    monkeypatch.setattr(session_chat_model, "_record_usage", interrupt_before_settlement)
    with pytest.raises(KeyboardInterrupt):
        client.post(
            f"/sessions/{session_id}/chat",
            json={
                "message": "Explain this session.",
                "client_message_id": "chunk-before-usage",
                "mode": "ask",
            },
        )
    pending = client.get(f"/sessions/{session_id}").json()["discussions"][0]

    reopened, restored = _client(tmp_path)
    restored.session_chat.provider = provider
    response = reopened.post(f"/sessions/{session_id}/chat/{pending['id']}/retry")

    assert response.status_code in {200, 201}, response.text
    assert response.json()["status"] == "failed"
    assert len(provider.calls) == 1
    log = SessionProviderLog(restored.session_chat.root, session_id, pending["id"])
    usage = next(
        event.payload for event in log.events() if event.type is EventType.SESSION_MODEL_USAGE
    )
    assert usage["usage_status"] == "unknown"
    assert usage["model"] == "test-model"
    assert usage["input_tokens"] == 15_000
    assert usage["output_tokens"] == 2_000
    assert usage["cached_input_tokens"] == 4_000
    assert usage["reasoning_output_tokens"] == 1_200
    assert session_usage_snapshot(log).tokens == 17_000


def test_litellm_refuses_to_dispatch_when_gateway_has_no_output_capability():
    class _Utils:
        @staticmethod
        def get_supported_openai_params(*, model: str) -> list[str]:
            del model
            return ["response_format"]

    class _Gateway:
        utils = _Utils()
        completion_calls = 0

        @staticmethod
        def supports_response_schema(*, model: str) -> bool:
            del model
            return True

        def completion(self, **kwargs):
            del kwargs
            self.completion_calls += 1
            raise AssertionError("unsupported capped request reached the gateway")

    provider = LiteLLMProvider(model="gpt-4o-mini")
    gateway = _Gateway()
    provider._litellm = gateway

    with pytest.raises(
        LLMConfigurationError, match="does not expose a supported output-token limit"
    ):
        provider.complete_structured_bounded(
            "prompt",
            schema=session_chat_model.SessionChatProposal,
            max_output_tokens=128,
        )

    assert gateway.completion_calls == 0
