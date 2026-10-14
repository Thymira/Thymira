"""Manual, isolated real-provider follow-up check; never collected by pytest.

Run with ``uv run python diagnostics/historical-artifact-followup/real_followup.py``.
The real API, Gate, graphs, local stores and LiteLLM gateway run in-process. Each
invocation creates a fresh workspace unless --resume-root selects a previous scenario.
Original runs and datasets remain untouched.
Approvals require an operator to inspect pending-review.json, then put the exact
decision id in approved-decisions.json (a JSON list). No approval is automatic.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from fastapi.testclient import TestClient

from scripts.real_e2e_smoke import _pending_approval, _pending_question, _terminal_outcome
from thymira.api.credential import ProcessCredential, mint_api_token
from thymira.api.env import load_env_file
from thymira.api.permissions import principal_for_role
from thymira.api.server import build_local_app
from thymira.events import canonical_json, read_events, redact_value, sha256_file, sha256_text, verify_log
from thymira.schemas import ActorKind, Event, EventType, approval_names_decision
from thymira.state import LocalArtifactStore
from thymira.tools.artifact_validation import validate_artifact_bytes

FIRST_REQUEST = (
    "Realiza un EDA descriptivo del dataset german_credit: calidad, valores ausentes, "
    "distribuciones y correlaciones numéricas. Usa profile_dataset(include_plots=true). "
    "Es investigación retrospectiva: no entrenes modelos ni tomes decisiones sobre "
    "solicitantes reales."
)
FOLLOWUP_REQUEST = "¿puedes hacer algo más con el dataset?"
FACTS = {
    "purpose": "Descriptive exploratory analysis of the historical German Credit dataset for offline research only.",
    "affected_population": "Historical German credit applicants represented by the registered dataset; no live applicant is affected.",
    "decision_effect": "Outputs inform human research only; no credit approval, denial, pricing or ranking decision is made.",
    "autonomy": "Tools calculate descriptive statistics and produce research artifacts; no model training, deployment or credit decisions.",
    "human_oversight": "The researcher reviews artifacts and MIRA findings before any downstream use and can stop the run.",
    "jurisdiction": "Spain and the European Union for offline research; the historical records describe applicants in Germany.",
    "data_categories": "Historical account status, credit history, loan purpose and amount, employment, housing, demographic attributes and target is_high_risk.",
    "sensitive_attributes": "Age, personal_status_sex and foreign_worker are sensitive or protected attributes in this dataset.",
    "potential_consequences": "Misleading descriptive analysis, privacy harm, discriminatory feature choices or downstream misuse. No operational use is authorized.",
}


def save(path: Path, value: object) -> None:
    """Persist only redacted diagnostics with predictable line endings."""
    path.write_text(
        json.dumps(redact_value(value), ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8", newline="\n",
    )


def request(client: TestClient, method: str, url: str, body: object = None) -> object:
    """Call the authenticated API and retain no credential in diagnostics."""
    response = client.request(method, url, json=body)
    if response.status_code >= 400:
        raise RuntimeError(f"API {method} {url}: HTTP {response.status_code}")
    return response.json()


def plan_review_evidence(events: list[Event]) -> list[dict[str, object]]:
    """Capture displayed plan candidates and exact manual answers for diagnostic inspection."""
    reviews = []
    for request_event in events:
        if (
            request_event.type is not EventType.HUMAN_APPROVAL_REQUESTED
            or request_event.payload.get("action_type") != "plan.proposed"
        ):
            continue
        decision_id = request_event.payload.get("decision_id")
        answers = [
            event.payload["approved"]
            for event in events
            if event.type is EventType.HUMAN_APPROVAL
            and event.seq > request_event.seq
            and event.run_id == request_event.run_id
            and event.subject_id == request_event.subject_id
            and event.actor.kind is ActorKind.HUMAN
            and event.actor.authenticated
            and event.payload.get("automatic", False) is False
            and isinstance(decision_id, str)
            and approval_names_decision(event.payload, decision_id)
            and isinstance(event.payload.get("approved"), bool)
        ]
        reviews.append({
            "seq": request_event.seq,
            "decision_id": decision_id,
            "plan": request_event.payload.get("plan"),
            "plan_review": request_event.payload.get("plan_review"),
            "approved": True in answers and False not in answers,
        })
    return reviews


def drive(client: TestClient, root: Path, run_id: str, timeout: float) -> dict[str, object]:
    """Drive facts while waiting for individually reviewed operator approvals."""
    deadline = time.monotonic() + timeout
    events_path = root / "workspace" / ".thymira" / "runtime" / "runs" / run_id / "events.jsonl"
    question_count = 0
    announced = set()
    while time.monotonic() < deadline:
        verification = verify_log(events_path)
        if not verification.valid:
            raise RuntimeError("Canonical event chain failed verification")
        events = read_events(events_path)
        verdict = _terminal_outcome(events)
        if verdict is not None:
            run = request(client, "GET", f"/runs/{run_id}")
            store = LocalArtifactStore(
                root / "workspace" / ".thymira" / "runtime" / "artifacts" / run_id,
                run_id,
            )
            artifact_errors = store.verify()
            validated_profiles = []
            for artifact in store.list_active():
                if artifact.name.startswith("profile/") and artifact.name.endswith(".json"):
                    validate_artifact_bytes(
                        artifact.name, store.load_bytes(artifact.name), artifact.media_type
                    )
                    validated_profiles.append(artifact.name)
            plans = [event.payload for event in events if event.type is EventType.PLAN_PUBLISHED]
            completions = [
                event.payload for event in events
                if event.type is EventType.AGENT_COMPLETED
                and event.payload.get("completion_evidence") is not None
            ]
            result = {
                "run_id": run_id, "terminal_evidence_valid": verdict[0], "verdict": verdict[1],
                "run": run, "plans": plans, "plan_reviews": plan_review_evidence(events),
                "task_completions": completions,
                "event_count": len(events), "event_chain_valid": True,
                "artifact_errors": artifact_errors,
                "strict_json_profiles": validated_profiles,
                "artifacts": [artifact.model_dump(mode="json") for artifact in store.list_active()],
                "tool_outputs": [
                    {"seq": event.seq, "payload": event.payload}
                    for event in events if event.type is EventType.TOOL_COMPLETED
                ],
            }
            save(root / f"{run_id}-summary.json", result)
            if artifact_errors:
                raise RuntimeError("Artifact manifest failed verification")
            print(json.dumps({"run_id": run_id, "verdict": verdict}, ensure_ascii=False), flush=True)
            if not verdict[0] or run["status"] != "COMPLETED":
                raise RuntimeError("Run did not complete with a verified governance decision; inspect saved summary")
            return result
        question = _pending_question(events)
        if question is not None:
            question_count += 1
            if question_count > 12:
                raise RuntimeError("Risk interview exceeded bounded factual answers")
            field = str(question.get("field", ""))
            answer = FACTS.get(field)
            if answer is None:
                save(root / "pending-question.json", question)
                raise RuntimeError("Interview requires an operator fact not defined by this scenario")
            request(client, "POST", f"/runs/{run_id}/risk-interview", {"answer": answer})
            continue
        decision_id = _pending_approval(events)
        if decision_id:
            if decision_id not in announced:
                relevant = [
                    {"seq": event.seq, "type": event.type.value, "payload": event.payload}
                    for event in events
                    if event.type in {EventType.HUMAN_APPROVAL_REQUESTED, EventType.POLICY_DECISION, EventType.PLAN_PUBLISHED}
                ][-5:]
                save(root / "pending-review.json", {"run_id": run_id, "decision_id": decision_id, "scope": relevant})
                print(f"REVIEW REQUIRED: {decision_id}; inspect {root / 'pending-review.json'}", flush=True)
                announced.add(decision_id)
            approved = json.loads((root / "approved-decisions.json").read_text(encoding="utf-8"))
            if decision_id in approved:
                request(client, "POST", f"/runs/{run_id}/approve", {
                    "expected_decision_id": decision_id,
                    "note": "Operator inspected the exact isolated research review scope.",
                })
                continue
        time.sleep(2)
    raise RuntimeError("Isolated real-provider scenario reached its deadline")


def validate_resume_root(root: Path) -> Path:
    """Accept only this scenario's existing temporary project, without changing approvals."""
    resolved = root.resolve(strict=True)
    temporary = Path(tempfile.gettempdir()).resolve()
    if not resolved.is_relative_to(temporary) or not resolved.name.startswith("thymira-historical-followup-"):
        raise RuntimeError("Resume root must be an existing isolated scenario under the temporary directory")
    workspace = resolved / "workspace"
    for name in ("config.yaml", "context.md"):
        supplied = workspace / ".thymira" / name
        reference = REPO / "examples" / "credit-risk" / ".thymira" / name
        if supplied.read_bytes() != reference.read_bytes():
            raise RuntimeError("Resume workspace configuration differs from the isolated scenario")
    if sha256_file(workspace / "data" / "applications.csv") != sha256_file(REPO / "data" / "german_credit.csv"):
        raise RuntimeError("Resume workspace does not contain the copied German Credit dataset")
    approvals = json.loads((resolved / "approved-decisions.json").read_text(encoding="utf-8"))
    if not isinstance(approvals, list) or not all(isinstance(value, str) for value in approvals):
        raise RuntimeError("Existing manual approval list is malformed")
    return resolved


def followup_client_id(attempt: int) -> str:
    """Keep each requested attempt stable across process restarts."""
    return "isolated-turn-2" if attempt == 0 else f"isolated-turn-2-retry-{attempt}"


def validate_run_identity(run: dict[str, object], events: list[Event], expected_id: str) -> None:
    """Bind recovery to the canonical immutable request rather than mutable projections."""
    recorded = [event.payload.get("run") for event in events if event.type is EventType.RUN_STARTED]
    if len(recorded) != 1 or not isinstance(recorded[0], dict) or run.get("id") != expected_id:
        raise RuntimeError("Scenario Run has no unique canonical identity")
    fields = ("id", "project_id", "session_id", "prompt", "conversation")
    original = {field: recorded[0].get(field) for field in fields}
    current = {field: run.get(field) for field in fields}
    if sha256_text(canonical_json(original)) != sha256_text(canonical_json(current)):
        raise RuntimeError("Scenario Run request differs from its canonical identity")


def recover_scenario(client: TestClient, root: Path, attempt: int = 0) -> tuple[str, str, str | None]:
    """Find the existing first turn through verified logs and the authoritative Run API."""
    runs = []
    run_root = root / "workspace" / ".thymira" / "runtime" / "runs"
    for events_path in sorted(run_root.glob("run_*/events.jsonl")):
        if not verify_log(events_path).valid:
            raise RuntimeError("Existing scenario event chain failed verification")
        run = request(client, "GET", f"/runs/{events_path.parent.name}")
        validate_run_identity(run, read_events(events_path), events_path.parent.name)
        runs.append(run)
    first = [
        run for run in runs
        if (run.get("conversation") or {}).get("client_message_id") == "isolated-turn-1"
        and (run.get("conversation") or {}).get("message") == FIRST_REQUEST
    ]
    if len(first) != 1:
        raise RuntimeError("Resume requires exactly one existing first scenario turn")
    if first[0]["conversation"].get("ordinal") != 1 or first[0]["conversation"].get("context_run_ids"):
        raise RuntimeError("Original EDA must be the context-free first scenario turn")
    session_id = first[0]["session_id"]
    by_attempt = {}
    for run in runs:
        if run["id"] == first[0]["id"]:
            continue
        metadata = run.get("conversation") or {}
        identity = metadata.get("client_message_id")
        matched = [number for number in range(attempt + 1) if identity == followup_client_id(number)]
        if (
            len(matched) != 1
            or matched[0] in by_attempt
            or metadata.get("message") != FOLLOWUP_REQUEST
        ):
            raise RuntimeError("Resume root contains an unexpected or ambiguous follow-up")
        by_attempt[matched[0]] = run
    if any(
        run["session_id"] != session_id or run["project_id"] != first[0]["project_id"]
        for run in runs
    ):
        raise RuntimeError("Resume root contains an ambiguous scenario")
    if attempt > 0:
        if first[0]["status"] != "COMPLETED":
            raise RuntimeError("A follow-up retry requires the original EDA to be completed")
        for number in range(attempt):
            prior = by_attempt.get(number)
            if prior is None or prior["status"] not in {"COMPLETED", "FAILED", "BLOCKED"}:
                raise RuntimeError("Every prior follow-up attempt must exist and be terminal")
    for number, run in by_attempt.items():
        metadata = run["conversation"]
        contexts = metadata.get("context_run_ids", [])
        allowed = {first[0]["id"], *(prior["id"] for index, prior in by_attempt.items() if index < number)}
        if (
            not isinstance(contexts, list)
            or len(contexts) != len(set(contexts))
            or first[0]["id"] not in contexts
            or not set(contexts) <= allowed
            or metadata.get("ordinal") != number + 2
        ):
            raise RuntimeError("Follow-up context must retain the first EDA and only verified earlier attempts")
    save(root / f"followup-attempt-{attempt}-recovery.json", {
        "session_id": session_id,
        "first_run_id": first[0]["id"],
        "attempt": attempt,
        "client_message_id": followup_client_id(attempt),
        "prior_attempts": [
            {"attempt": number, "run_id": run["id"], "status": run["status"],
             "ordinal": run["conversation"]["ordinal"],
             "context_run_ids": run["conversation"]["context_run_ids"]}
            for number, run in sorted(by_attempt.items())
        ],
    })
    request(client, "GET", f"/sessions/{session_id}")
    selected = by_attempt.get(attempt)
    return session_id, first[0]["id"], selected["id"] if selected else None


def main() -> int:
    """Create a fresh real-data project and perform two sequential conversation turns."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--resume-root", type=Path)
    parser.add_argument("--followup-attempt", type=int, default=0)
    args = parser.parse_args()
    if args.followup_attempt < 0 or (args.followup_attempt > 0 and args.resume_root is None):
        parser.error("--followup-attempt must be nonnegative; retries require --resume-root")
    root = validate_resume_root(args.resume_root) if args.resume_root else Path(
        tempfile.mkdtemp(prefix="thymira-historical-followup-")
    )
    workspace = root / "workspace"
    if args.resume_root is None:
        config_dir = workspace / ".thymira"
        config_dir.mkdir(parents=True)
        (workspace / "data").mkdir()
        for filename in ("config.yaml", "context.md"):
            shutil.copyfile(REPO / "examples" / "credit-risk" / ".thymira" / filename, config_dir / filename)
        shutil.copyfile(REPO / "data" / "german_credit.csv", workspace / "data" / "applications.csv")
        save(root / "approved-decisions.json", [])
    print(f"ISOLATED_ROOT={root}", flush=True)
    load_env_file(REPO / ".env")
    if not os.environ.get("THYMIRA_ALLOWED_MODEL_ROUTES", "").strip():
        raise RuntimeError("Explicit THYMIRA_ALLOWED_MODEL_ROUTES is required; no route policy was weakened")
    token = mint_api_token()
    credential = ProcessCredential(token, principal_for_role("isolated-smoke-operator", "admin"))
    app = build_local_app(workspace, principal_resolver=credential)
    with TestClient(app, headers={"Authorization": f"Bearer {token}"}) as client:
        if args.resume_root is not None:
            session_id, first_run_id, second_run_id = recover_scenario(client, root, args.followup_attempt)
            existing = {1: first_run_id, 2: second_run_id}
        else:
            session = request(client, "POST", "/sessions", {"title": "Historical artifact scope isolation"})
            session_id = session["id"]
            existing = {}
        results = []
        for index, message in enumerate((FIRST_REQUEST, FOLLOWUP_REQUEST), start=1):
            run_id = existing.get(index)
            if run_id is None:
                created = request(client, "POST", f"/sessions/{session_id}/messages", {
                    "message": message,
                    "client_message_id": "isolated-turn-1" if index == 1 else followup_client_id(args.followup_attempt),
                })
                run_id = created["id"]
                if index == 2:
                    recovered_session, recovered_first, recovered_second = recover_scenario(
                        client, root, args.followup_attempt
                    )
                    if (recovered_session, recovered_first, recovered_second) != (session_id, results[0]["run_id"], run_id):
                        raise RuntimeError("Created follow-up does not match the verified scenario")
            print(f"TURN_{index}={run_id}", flush=True)
            results.append(drive(client, root, run_id, args.timeout))
        detail = request(client, "GET", f"/sessions/{session_id}")
        save(root / "scenario-summary.json", {"session": detail, "results": results})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"SCENARIO STOPPED: {type(exc).__name__}: {redact_value(str(exc))}", flush=True)
        raise SystemExit(1) from None
