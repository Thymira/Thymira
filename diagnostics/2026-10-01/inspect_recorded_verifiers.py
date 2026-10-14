"""Read historical logs without executing code or opening a mutable artifact store.

Only counts, identities, hashes and boolean prompt markers are saved. Historical
prompts and user data remain in their original files and are not copied to this report.
"""

import json
from pathlib import Path
from zoneinfo import ZoneInfo

from thymira.events import verify_events
from thymira.schemas import Event

REPOSITORY = Path(__file__).resolve().parents[2]
RUNTIME = REPOSITORY / "examples" / "credit-risk" / ".thymira" / "runtime"
results = []
chains = []
for path in sorted((RUNTIME / "runs").glob("*/events.jsonl")):
    events = tuple(
        Event.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    chain = verify_events(events)
    terminal = next(
        (
            event.payload
            for event in reversed(events)
            if event.type.value == "run.transitioned"
            and event.payload.get("condition") == "terminal"
        ),
        {},
    )
    chains.append(
        {
            "run_id": path.parent.name,
            "events": len(events),
            "chain_valid": chain.valid,
            "first_event_utc": events[0].ts.isoformat() if events else None,
            "first_event_madrid": events[0].ts.astimezone(ZoneInfo("Europe/Madrid")).isoformat()
            if events
            else None,
            "terminal_outcome": terminal.get("outcome"),
        }
    )
    for event in events:
        payload = event.payload
        if not any(
            owner.get("owner_id") == "mira-finding-verifier"
            for owner in payload.get("owners", ())
            if isinstance(owner, dict)
        ):
            continue
        gateway = payload.get("gateway", {})
        messages = gateway.get("messages") if isinstance(gateway, dict) else None
        if not isinstance(messages, list):
            continue
        prompt = "\n".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        )
        results.append(
            {
                "run_id": event.run_id,
                "seq": event.seq,
                "request_id": payload.get("id"),
                "gateway_sha256": payload.get("gateway_sha256"),
                "candidate_object_visible": '"candidate_finding"' in prompt,
                "truncation_marker": "[truncated]" in prompt,
                "prompt_chars": len(prompt),
            }
        )
summary = {
    "verified_logs": len(chains),
    "valid_logs": sum(item["chain_valid"] for item in chains),
    "verifier_requests": len(results),
    "runs_with_verifier_requests": len({item["run_id"] for item in results}),
    "candidate_object_visible": sum(item["candidate_object_visible"] for item in results),
    "truncated_requests": sum(item["truncation_marker"] for item in results),
}
output = {"summary": summary, "chains": chains, "verifier_requests": results}
Path(__file__).with_name("recorded_verifier_evidence.json").write_text(
    json.dumps(output, indent=2) + "\n", encoding="utf-8", newline="\n"
)
print(json.dumps(summary))  # noqa: T201  # diagnostic CLI summary
