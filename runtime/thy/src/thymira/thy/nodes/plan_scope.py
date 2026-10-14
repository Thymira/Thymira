"""Normalize specialist model work against the user's current request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.schemas import ModelWorkIntent, resolve_model_work_intent
from thymira.thy.models import AgentTask, PlanOutput, ThyAgentKind

if TYPE_CHECKING:
    from thymira.agents import AgentCatalog

_TRAINING_AGENTS = frozenset((ThyAgentKind.EXPERIMENT, ThyAgentKind.ML))
_MODEL_AGENTS = _TRAINING_AGENTS | frozenset((ThyAgentKind.EVALUATION,))


def _remove_excluded_model_work(planned: PlanOutput) -> tuple[PlanOutput | None, str | None]:
    """Drop every unrequested model specialist and reject work that depends on one."""
    model_task_ids = frozenset(task.id for task in planned.tasks if task.agent in _MODEL_AGENTS)
    if not model_task_ids:
        return planned, None
    if any(
        task.agent not in _MODEL_AGENTS
        and any(dependency in model_task_ids for dependency in task.depends_on)
        for task in planned.tasks
    ):
        return (
            None,
            (
                "model plan rejected: a remaining task depends on model work not requested by "
                "the current human work"
            ),
        )
    retained = tuple(task for task in planned.tasks if task.agent not in _MODEL_AGENTS)
    if retained:
        return planned.model_copy(
            update={"tasks": retained, "work_kind": "general", "source_model_id": None}
        ), None
    return (
        None,
        "model plan rejected: model work was not requested by the current human work",
    )


def _route_requested_model_work(
    prompt: str, planned: PlanOutput, catalog: AgentCatalog
) -> tuple[PlanOutput | None, str | None]:
    """Ensure explicit model work is assigned to a tracked, auditable agent.

    `ML` (`ml-agent`) satisfies tracking the same way `EXPERIMENT` does: Execute folds a completed
    `ml-agent` settlement into a `schemas.Experiment`, so a plan that selected it is left untouched.
    A coding task is upgraded only when neither specialist was selected; the deterministic fallback
    targets `EXPERIMENT`, leaving the model to choose whether explicit tuning needs `ML`.
    """
    converted_tasks = tuple(
        _training_task_from_evaluation(task, prompt)
        if task.agent is ThyAgentKind.EVALUATION
        else task
        for task in planned.tasks
    )
    planned = planned.model_copy(
        update={
            "tasks": converted_tasks,
            "work_kind": "train_new",
            "source_model_id": None,
        }
    )
    if any(task.agent in _TRAINING_AGENTS for task in planned.tasks):
        return planned, None
    if ThyAgentKind.EXPERIMENT.value not in catalog.names():
        return None, "model work requested but the experiment agent is unavailable"
    tasks = list(planned.tasks)
    coding_index = next(
        (index for index, task in enumerate(tasks) if task.agent is ThyAgentKind.CODING), None
    )
    if coding_index is not None:
        task = tasks[coding_index]
        tasks[coding_index] = task.model_copy(
            update={
                "agent": ThyAgentKind.EXPERIMENT,
                "instruction": (
                    "Use run_experiment for explicitly requested new training; do not "
                    f"train the model through generic code. Original task: {task.instruction}"
                ),
            }
        )
    else:
        tasks.append(
            planned.tasks[0].model_copy(
                update={
                    "id": "model-experiment",
                    "agent": ThyAgentKind.EXPERIMENT,
                    "instruction": (
                        "Use run_experiment for the explicitly requested new model training: "
                        f"{prompt}"
                    ),
                    "depends_on": tuple(task.id for task in planned.tasks),
                    "required_artifacts": (),
                }
            )
        )
    return planned.model_copy(update={"tasks": tuple(tasks)}), None


def _training_task_from_evaluation(task: AgentTask, human_request: str) -> AgentTask:
    """Reuse an evaluator's slot for requested training without binding a prior model."""
    requirements = tuple(
        requirement
        for requirement in task.required_artifacts
        if not (
            requirement.source_run_id
            or requirement.source_model_artifact_id
            or requirement.model_sha256
            or requirement.evaluation_split
        )
    )
    return task.model_copy(
        update={
            "agent": ThyAgentKind.EXPERIMENT,
            "instruction": (
                f"Use run_experiment to train a new model for the human request: {human_request}"
            ),
            "required_artifacts": requirements,
            "model_input_ids": (),
        }
    )


def normalize_model_scope(
    prompt: str,
    planned: PlanOutput,
    catalog: AgentCatalog,
    *,
    clarification_context: tuple[str, ...] = (),
    dataset_names: tuple[str, ...] = (),
) -> tuple[PlanOutput | None, str | None]:
    """Keep model work within the request resolved from current and linked human messages."""
    intent = resolve_model_work_intent(
        prompt,
        clarification_context,
        dataset_names=dataset_names,
    )
    if intent is ModelWorkIntent.TRAIN_NEW:
        human_request = "\n\n".join((*clarification_context, prompt))
        return _route_requested_model_work(human_request, planned, catalog)
    if intent is ModelWorkIntent.CLARIFY:
        return None, "model work is ambiguous; clarify whether to train a new model or evaluate one"
    return _remove_excluded_model_work(planned)


__all__ = ["normalize_model_scope"]
