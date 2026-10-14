"""Canonical plan-candidate digests shared across the THY/Core approval boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from thymira.events import canonical_json, sha256_text

if TYPE_CHECKING:
    from thymira.schemas import ExecutionConstraints, PolicyDecision, Run
    from thymira.thy.models import PlanOutput


def plan_candidate_snapshot(candidate: PlanOutput) -> dict[str, Any]:
    """Return the normalized JSON snapshot the approval request displays and binds."""
    return candidate.model_dump(mode="json")


def plan_candidate_sha256(candidate: PlanOutput) -> str:
    """Hash the exact normalized plan without model-only or checkpoint metadata."""
    return sha256_text(canonical_json(plan_candidate_snapshot(candidate)))


def plan_review_scope(
    run: Run,
    execution_decision: PolicyDecision | None,
    execution_constraints: ExecutionConstraints | None,
    *,
    policy_sha256: str,
) -> dict[str, Any]:
    """Capture the immutable request, prior execution authorization and policy scope."""
    conversation = run.conversation
    return {
        "run_id": run.id,
        "project_id": run.project_id,
        "session_id": run.session_id,
        "prompt": run.prompt,
        "conversation": conversation.model_dump(mode="json") if conversation is not None else None,
        "execution_decision": (
            execution_decision.model_dump(mode="json") if execution_decision is not None else None
        ),
        "execution_constraints": (
            execution_constraints.model_dump(mode="json")
            if execution_constraints is not None
            else None
        ),
        "policy_sha256": policy_sha256,
    }


def plan_review_scope_sha256(scope: dict[str, Any]) -> str:
    """Hash the captured execution scope in canonical JSON form."""
    return sha256_text(canonical_json(scope))


__all__ = [
    "plan_candidate_sha256",
    "plan_candidate_snapshot",
    "plan_review_scope",
    "plan_review_scope_sha256",
]
