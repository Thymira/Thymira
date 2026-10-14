"""Manual model-only contextual planning probe; never executes tasks or records approval.

Use the captured second-turn request in a fresh diagnostic identity and isolated event log.
This does not create or resume a runtime Run and cannot demonstrate EDA execution or audit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from thymira.agents.request_ledger import RequestLedger
from thymira.api.env import load_env_file
from thymira.events import InMemoryEventLog, read_events, redact_value, verify_log
from thymira.policies import Gate, GateMode, PolicyEngine, load_default_policy
from thymira.schemas import EventType, Run, new_id
from thymira.core.graph.agent_catalog import default_thy_catalog
from thymira.thy.models import RegisteredDataset, ThyPhase, ThyState
from thymira.thy.nodes.plan import plan_node
from thymira.thy.plan_review import plan_candidate_sha256


def main() -> int:
    """Inspect one bounded real-provider plan without executing or approving it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--source-run', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    events_path = args.source_root / 'workspace' / '.thymira' / 'runtime' / 'runs' / args.source_run / 'events.jsonl'
    if not verify_log(events_path).valid:
        raise RuntimeError('Captured source chain is invalid')
    recorded = [event.payload['run'] for event in read_events(events_path) if event.type is EventType.RUN_STARTED]
    if len(recorded) != 1:
        raise RuntimeError('Captured request is ambiguous')
    source = Run.model_validate(recorded[0])
    if source.conversation is None or source.conversation.ordinal != 2:
        raise RuntimeError('Probe requires the exact captured second conversation turn')
    run = Run(
        id=new_id('run'), project_id=source.project_id, session_id=new_id('session'),
        prompt=source.prompt, conversation=source.conversation,
        model_route_policy=source.model_route_policy,
    )
    load_env_file(REPO / '.env')
    log = InMemoryEventLog(run.id)
    gate = Gate(PolicyEngine(load_default_policy()), log, mode=GateMode.DEFERRED)
    state = ThyState(
        run=run, phase=ThyPhase.PLAN,
        datasets=(RegisteredDataset(name='german_credit', target='is_high_risk'),),
        project_context=(args.source_root / 'workspace' / '.thymira' / 'context.md').read_text(encoding='utf-8'),
    )
    result = ThyState.model_validate(plan_node(
        default_thy_catalog(), gate, log, route_policy=run.model_route_policy,
        request_ledger=RequestLedger(log),
    )(state))
    events = log.events()
    if any(event.type in {EventType.TOOL_STARTED, EventType.HUMAN_APPROVAL, EventType.RUN_TRANSITIONED} for event in events):
        raise RuntimeError('Model-only probe unexpectedly executed or approved work')
    review = result.pending_plan_review
    candidate = review.candidate.tasks if review is not None else result.plan
    candidate_valid = bool(candidate) and (review is None or plan_candidate_sha256(review.candidate) == review.candidate_sha256)
    output = {
        'kind': 'model_only_planning_probe', 'source_run_id': source.id,
        'diagnostic_run_id': run.id, 'source_conversation': source.conversation.model_dump(mode='json'),
        'source_chain_valid': True, 'error': result.error,
        'candidate_valid': candidate_valid, 'candidate': [task.model_dump(mode='json') for task in candidate],
        'pending_plan_review': review.model_dump(mode='json') if review is not None else None,
        'tool_calls': 0, 'approvals': 0, 'runtime_transitions': 0,
        'events': [event.model_dump(mode='json') for event in events],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(redact_value(output), ensure_ascii=False, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'candidate_valid': candidate_valid, 'error': result.error, 'tool_calls': 0, 'approvals': 0}, ensure_ascii=False))
    return 0 if candidate_valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
