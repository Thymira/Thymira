"""Bounded recovery for exact, plan-declared plot artifacts.

The plan and its stable recovery task live in ``ThyProgress`` so a gated correction can pause and
resume through the normal Delegator path. This module keeps the artifact check, correction
identity, and recovery orchestration together, outside the already-large Execute node.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from thymira.agents.task_completion import tool_attempts_for_task
from thymira.events import canonical_json, sha256_bytes, sha256_text
from thymira.schemas import (
    Actor,
    ActorKind,
    ArtifactKind,
    EventSurface,
    EventType,
    TaskStatus,
)
from thymira.thy.task_completion import task_completion_validator
from thymira.thy.task_settlement import stopped_settlement
from thymira.tools import artifact_read_limit, resolve_artifact_media_type, validate_artifact_bytes

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thymira.agents import AgentCatalog, Delegator
    from thymira.events import EventLog
    from thymira.schemas import Artifact, Task
    from thymira.state import ArtifactStore
    from thymira.thy.models import AgentTask, ArtifactRequirement, ThyState

_RECOVERY_MARKER = "THY_ARTIFACT_RECOVERY_ID="
_RECOVERY_PREFIX = "thy-artifact-recovery-"


def required_artifact_line(requirement: ArtifactRequirement) -> str:
    """Render the exact output path, kind, and optional media type for an agent instruction."""
    media_type = (
        "" if requirement.media_type is None else f', media_type="{requirement.media_type}"'
    )
    return f'- {requirement.name} (kind="{requirement.kind.value}"{media_type})'


def delegation_instruction(agent_task: AgentTask) -> str:
    """Tell a delegated agent how the Run validates its declared deliverables."""
    if not agent_task.required_artifacts:
        return agent_task.instruction
    if any(item.match == "content" or item.metric_keys for item in agent_task.required_artifacts):
        contract = canonical_json(
            [item.model_dump(mode="json") for item in agent_task.required_artifacts]
        )
        return agent_task.instruction + (
            "\nDeliverable contract: "
            + contract
            + "\nFor match=content the logical filename may differ; kind, requested metrics and "
            "model digest must match. Register the actual output. Exact paths remain mandatory "
            "when match=exact. A successful tool call alone does not complete missing metrics."
        )
    lines = "\n".join(
        required_artifact_line(requirement) for requirement in agent_task.required_artifacts
    )
    return (
        f"{agent_task.instruction}\n\n"
        "Only an exact workspace-relative path, kind, and declared media type satisfies the "
        "required-artifact check. The Run validates each declared output by its exact path, "
        "kind, and any declared media type. A same-basename file in another directory does not "
        "satisfy the requirement. Register each output as a real Artifact, not merely a workspace "
        "file -- pass the matching kind shown below to write_file's `kind` argument, or to the "
        "corresponding entry's `kind` in run_python's `output_artifacts`. If a tool result shows "
        "that one of these exact outputs is missing or registered under another path, make at most "
        "one targeted corrective attempt to publish it at the listed path; do not claim a similar "
        "name satisfies it. If the correction still fails, report the actual path and stop:\n"
        f"{lines}"
    )


def recovery_skip_reason(
    agent_task: AgentTask,
    *,
    plan: Sequence[AgentTask],
    position: int,
    event_log: EventLog,
    store: ArtifactStore | None,
    may_resume: bool,
) -> str | None:
    """Return why a correction must be skipped, unless it is the pending call being resumed."""
    if position not in _recovery_task_indices(plan) or may_resume:
        return None
    if recovery_identity_recorded(event_log, agent_task.id, agent_task.recovery_of_position):
        return "this plot-artifact correction identity already has a recorded attempt"
    if not missing_required_plot_artifacts(agent_task.required_artifacts, store):
        return "the required plot artifact is already registered and verified"
    return None


def execute_plot_recoveries(
    plan: Sequence[AgentTask],
    completed: Sequence[AgentTask],
    outcomes: Sequence[Task],
    *,
    store: ArtifactStore | None,
    event_log: EventLog,
    delegator: Delegator,
    catalog: AgentCatalog,
    state: ThyState,
    actor: Actor,
    unresolved: Callable[[AgentTask], Task | None],
) -> tuple[list[AgentTask], list[AgentTask], list[Task], list[Artifact]]:
    """Append and run one stable correction per completed step with an invalid exact plot.

    Delegation uses the ordinary Delegate path so review-gated tools can park. The returned plan
    includes all generated corrections before the first call;
    if one parks, ThyProgress therefore persists both its identity and every remaining correction.
    """
    recoveries = _new_plot_recoveries(plan, completed, outcomes, store, event_log)
    if not recoveries:
        return [], [], [], []

    plan_updates = list(recoveries)
    completed_updates: list[AgentTask] = []
    outcome_updates: list[Task] = []
    artifact_updates: list[Artifact] = []
    for agent_task in recoveries:
        if recovery_identity_recorded(event_log, agent_task.id, agent_task.recovery_of_position):
            outcome = stopped_settlement(
                state,
                agent_task,
                event_log,
                actor,
                reason="this plot-artifact correction identity already has a recorded attempt",
            ).task
            produced: Sequence[Artifact] = ()
        elif (failed := unresolved(agent_task)) is not None:
            outcome = failed
            produced = ()
        else:
            record_recovery_attempt_if_needed(event_log, agent_task)
            before_ids = artifact_ids(store)
            outcome = delegator.delegate(
                "thy",
                catalog.get(agent_task.agent.value),
                delegation_instruction(agent_task),
                depth=0,
                skill_names=agent_task.skill_names,
                task_completion_validator=task_completion_validator(agent_task, store),
            ).task
            produced = new_artifacts(store, before_ids)
        completed_updates.append(agent_task)
        outcome_updates.append(outcome)
        artifact_updates.extend(produced)
        if outcome.status is TaskStatus.PENDING:
            break
    return plan_updates, completed_updates, outcome_updates, artifact_updates


def recover_plots_if_settled(
    plan: list[AgentTask],
    completed: list[AgentTask],
    outcomes: list[Task],
    artifacts: list[Artifact],
    *,
    store: ArtifactStore | None,
    event_log: EventLog,
    delegator: Delegator,
    catalog: AgentCatalog,
    state: ThyState,
    actor: Actor,
    unresolved: Callable[[AgentTask], Task | None],
) -> None:
    """Run and fold plot corrections only after the planned pass has no parked task."""
    if any(outcome.status is TaskStatus.PENDING for outcome in outcomes):
        return
    plan_updates, completed_updates, outcome_updates, artifact_updates = execute_plot_recoveries(
        plan,
        completed,
        outcomes,
        store=store,
        event_log=event_log,
        delegator=delegator,
        catalog=catalog,
        state=state,
        actor=actor,
        unresolved=unresolved,
    )
    plan.extend(plan_updates)
    completed.extend(completed_updates)
    outcomes.extend(outcome_updates)
    artifacts.extend(artifact_updates)


def _new_plot_recoveries(
    plan: Sequence[AgentTask],
    completed: Sequence[AgentTask],
    outcomes: Sequence[Task],
    store: ArtifactStore | None,
    event_log: EventLog,
) -> list[AgentTask]:
    """Make a correction for completed tasks or plot-only delivery failures with prior work."""
    if store is None:
        return []
    recoveries: list[AgentTask] = []
    recovery_positions = _recovery_task_indices(plan)
    recovered_sources = {plan[position].recovery_of_position for position in recovery_positions}
    original_ids = {task.id for task in plan if task.recovery_of_position is None}
    base_outcomes = (
        (position, task, outcome)
        for position, (task, outcome) in enumerate(zip(completed, outcomes, strict=True))
        if position not in recovery_positions
    )
    for position, agent_task, outcome in base_outcomes:
        missing = missing_required_plot_artifacts(agent_task.required_artifacts, store)
        if not missing:
            continue
        if outcome.status is not TaskStatus.COMPLETED and not _plot_only_delivery_failure(
            agent_task, outcome, missing, event_log, store
        ):
            continue
        if position in recovered_sources:
            continue
        identity = _recovery_identity(position, agent_task, original_ids)
        recoveries.append(
            agent_task.model_copy(
                update={
                    "id": identity,
                    "instruction": _recovery_instruction(agent_task, identity, missing),
                    "depends_on": (),
                    "required_artifacts": missing,
                    "recovery_of_position": position,
                }
            )
        )
    return recoveries


def _plot_only_delivery_failure(
    agent_task: AgentTask,
    outcome: Task,
    missing: Sequence[ArtifactRequirement],
    event_log: EventLog,
    store: ArtifactStore,
) -> bool:
    """Prove the runner failed only required plots after a successful plot publication."""
    if store.verify():
        return False
    completed = next(
        (
            event
            for event in reversed(event_log.events())
            if event.type is EventType.AGENT_COMPLETED
            and event.payload.get("task_id") == outcome.id
            and (event.payload.get("completion_evidence") or {}).get("plan_task_id")
            == agent_task.id
        ),
        None,
    )
    if completed is None or completed.payload.get("end_reason") != "tool_failure":
        return False
    evidence = completed.payload.get("completion_evidence")
    names = [requirement.name for requirement in missing]
    if not isinstance(evidence, dict) or (
        evidence.get("missing_required_artifacts") != names
        or evidence.get("unresolved_tool_call_ids")
        or evidence.get("repaired_tool_call_ids")
        or evidence.get("ignored_tool_call_ids")
        or evidence.get("validation_failures")
        != [f"required artifact {name!r} is missing or invalid" for name in names]
    ):
        return False
    attempts = tool_attempts_for_task(event_log.events(), outcome.id)
    if any(not attempt.succeeded for attempt in attempts):
        return False
    active = {artifact.id: artifact for artifact in store.list_active() if artifact.valid}
    published_plots = {
        artifact_id
        for attempt in attempts
        for artifact_id in attempt.artifact_ids
        if artifact_id in active and active[artifact_id].kind is ArtifactKind.PLOT
    }
    return bool(published_plots)


def _recovery_task_indices(plan: Sequence[AgentTask]) -> frozenset[int]:
    """Validate Execute-owned markers against their source task and exact identity."""
    indices: set[int] = set()
    original_ids = {task.id for task in plan if task.recovery_of_position is None}
    for candidate_position, candidate in enumerate(plan):
        source_position = candidate.recovery_of_position
        if source_position is None:
            continue
        if source_position >= candidate_position:
            msg = "Execute-owned plot correction points outside its earlier source task"
            raise ValueError(msg)
        source = plan[source_position]
        if source.recovery_of_position is not None:
            msg = "Execute-owned plot correction cannot target another correction"
            raise ValueError(msg)
        identity = _recovery_identity(source_position, source, original_ids)
        if candidate.id != identity or not _matches_generated_recovery(candidate, source, identity):
            msg = "Execute-owned plot correction does not match its source task"
            raise ValueError(msg)
        indices.add(candidate_position)
    if indices and indices != set(range(min(indices), len(plan))):
        msg = "Execute-owned plot corrections must remain at the end of the persisted plan"
        raise ValueError(msg)
    return frozenset(indices)


def _matches_generated_recovery(candidate: AgentTask, source: AgentTask, identity: str) -> bool:
    """Validate the generated instruction and cloned task fields, not the reserved prefix alone."""
    required = candidate.required_artifacts
    return (
        candidate.agent is source.agent
        and candidate.phase is source.phase
        and candidate.skill_names == source.skill_names
        and not candidate.depends_on
        and bool(required)
        and all(
            requirement in source.required_artifacts
            and requirement.kind is ArtifactKind.PLOT
            and _is_concrete_artifact_path(requirement.name)
            for requirement in required
        )
        and candidate.instruction == _recovery_instruction(source, identity, required)
    )


def _recovery_instruction(
    source: AgentTask, identity: str, requirements: Sequence[ArtifactRequirement]
) -> str:
    names = ", ".join(requirement.name for requirement in requirements)
    return (
        f"{_RECOVERY_MARKER}{identity}\nPerform the single bounded plot-artifact correction "
        f"for planned task {source.id!r}. Its completed work registered a required plot under "
        f"the wrong path or with invalid content. Preserve the existing plots and create/register "
        f"only these missing exact output(s): {names}. Do not treat a matching basename elsewhere "
        "as equivalent. Use the original task's context to regenerate only what is needed: "
        f"{source.instruction}"
    )


def _recovery_identity(position: int, agent_task: AgentTask, occupied_ids: set[str]) -> str:
    """Create a stable identity, skipping original plan ids if a digest collides."""
    identity = {
        "position": position,
        "task_id": agent_task.id,
        "agent": agent_task.agent.value,
        "required_artifacts": [
            {
                "name": requirement.name,
                "kind": requirement.kind.value,
                "media_type": requirement.media_type,
            }
            for requirement in agent_task.required_artifacts
        ],
    }
    collision = 0
    while True:
        payload = identity if collision == 0 else {**identity, "collision": collision}
        recovery_id = f"{_RECOVERY_PREFIX}{sha256_text(canonical_json(payload))}"
        if recovery_id not in occupied_ids:
            return recovery_id
        collision += 1


def recovery_identity_recorded(
    event_log: EventLog, identity: str, source_position: int | None
) -> bool:
    """Whether code recorded this correction attempt on the event chain."""
    if source_position is None:
        return False
    return any(
        event.type is EventType.AGENT_MESSAGE
        and event.actor.kind is ActorKind.SYSTEM
        and event.subject_id == identity
        and event.payload.get("artifact_recovery_attempt")
        == {"identity": identity, "source_position": source_position}
        for event in event_log.events()
    )


def record_recovery_attempt_if_needed(
    event_log: EventLog, agent_task: AgentTask, *, fresh: bool = True
) -> None:
    """Write one code-owned marker before the first dispatch of this correction task."""
    if (
        not fresh
        or agent_task.recovery_of_position is None
        or recovery_identity_recorded(event_log, agent_task.id, agent_task.recovery_of_position)
    ):
        return
    source_position = agent_task.recovery_of_position
    event_log.append(
        EventType.AGENT_MESSAGE,
        Actor.system(),
        {
            "artifact_recovery_attempt": {
                "identity": agent_task.id,
                "source_position": source_position,
            }
        },
        subject_id=agent_task.id,
        surface=EventSurface.LOG_ONLY,
    )


def missing_required_plot_artifacts(
    requirements: Sequence[ArtifactRequirement], store: ArtifactStore | None
) -> tuple[ArtifactRequirement, ...]:
    """Return concrete PLOT paths that fail the exact, content-checked delivery rule."""
    if store is None:
        return ()
    missing: list[ArtifactRequirement] = []
    for requirement in requirements:
        if requirement.kind is not ArtifactKind.PLOT or not _is_concrete_artifact_path(
            requirement.name
        ):
            continue
        artifact = store.get(requirement.name)
        if artifact is None or not artifact.valid or artifact.kind is not requirement.kind:
            missing.append(requirement)
            continue
        try:
            required_media_type = resolve_artifact_media_type(
                requirement.name, requirement.media_type
            )
            actual_media_type = resolve_artifact_media_type(artifact.name, artifact.media_type)
            if required_media_type is not None and actual_media_type != required_media_type:
                missing.append(requirement)
                continue
            current = store.get(artifact.name)
            if (
                current is None
                or not current.valid
                or current.id != artifact.id
                or current.sha256 != artifact.sha256
            ):
                missing.append(requirement)
                continue
            validation_media_type = required_media_type or actual_media_type
            data = store.load_bytes_bounded(
                artifact.name, artifact_read_limit(validation_media_type)
            )
            if sha256_bytes(data) != artifact.sha256:
                missing.append(requirement)
                continue
            validate_artifact_bytes(artifact.name, data, validation_media_type)
        except (KeyError, OSError, ValueError):
            missing.append(requirement)
    return tuple(missing)


def _is_concrete_artifact_path(name: str) -> bool:
    """Mirror ThyGraph's exact-identity rule for path-like requirements."""
    return "/" in name or "\\" in name or bool(PurePosixPath(name).suffix)


def artifact_ids(store: ArtifactStore | None) -> frozenset[str]:
    """Ids of the store's active artifacts right now, or an empty set when there is no store."""
    if store is None:
        return frozenset()
    return frozenset(artifact.id for artifact in store.list_active())


def new_artifacts(store: ArtifactStore | None, before_ids: frozenset[str]) -> list[Artifact]:
    """Active artifacts that appeared since ``before_ids`` (empty without a store)."""
    if store is None:
        return []
    return [artifact for artifact in store.list_active() if artifact.id not in before_ids]
