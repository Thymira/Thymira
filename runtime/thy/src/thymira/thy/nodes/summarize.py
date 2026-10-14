"""Summarize node: model comparison + scientific recommendation + report artifact (THY-13).

`compare_experiments` is the deterministic half ("the LLM proposes, code authorizes"): it alone
picks `best_model`/`metrics` by ranking `state.experiments` on a configured metric. THY's own
(FRONTIER, task='synthesize') call never sees that choice as a decision to make -- it only ever
supplies the tradeoffs/limitations narrative (`SynthesisNarrative`), merged with the deterministic
facts into the final `Recommendation`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent as PydanticAgent

from thymira.agents.llm.routing import Role
from thymira.agents.model_binding import routed_model
from thymira.agents.prompt_framing import frame_untrusted
from thymira.observability import agent as agent_observation
from thymira.schemas import Actor, ArtifactKind, EventType
from thymira.state import ArtifactWrite
from thymira.thy.models import Recommendation, SynthesisNarrative
from thymira.thy.result_report import (
    conversation_task_summary,
    evaluation_report,
    experiment_facts,
    exploratory_report,
    training_report,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from thymira.agents.llm.base import LLMProvider, LLMResponse
    from thymira.agents.llm.routing import ModelChoice
    from thymira.events import EventLog
    from thymira.schemas import Experiment, ModelRoutePolicy
    from thymira.state import ArtifactStore
    from thymira.thy.models import ThyState

_SUMMARIZE_INSTRUCTIONS = (
    "You are THY, writing the recommendation for a completed data-science run. You are given "
    "the best model and its metrics, already chosen by deterministic comparison -- never "
    "propose a different one. Write only the tradeoffs of that choice and its limitations. "
    "Use only recorded metrics and methodology. Accuracy is the fraction classified correctly; "
    "it does not imply balanced precision/recall, fairness, calibration or external validity. "
    "Do not infer unmeasured metrics, overfitting, class balance or data preparation."
)


def compare_experiments(experiments: Sequence[Experiment], *, metric: str) -> Experiment:
    """Rank `experiments` by `metric` (higher is better) and return the best one.

    Raises:
        ValueError: `experiments` is empty, or none of them report `metric`.
    """
    scored = [experiment for experiment in experiments if metric in experiment.metrics]
    if not scored:
        msg = f"no experiment reports the metric {metric!r}"
        raise ValueError(msg)
    return max(scored, key=lambda experiment: experiment.metrics[metric])


def summarize_node(
    artifact_store: ArtifactStore,
    event_log: EventLog,
    *,
    provider: LLMProvider | None = None,
    metric: str = "accuracy",
    before_model_request: Callable[[], Sequence[str]] | None = None,
    before_model_selection: Callable[[], None] | None = None,
    before_model_call: Callable[[ModelChoice], None] | None = None,
    record_model_usage: Callable[[LLMResponse], None] | None = None,
    route_policy: ModelRoutePolicy | None = None,
) -> Callable[..., dict[str, Any]]:
    """Build the Summarize node bound to `artifact_store`.

    Terminal phase: does not call `state.advance()` (SUMMARIZE is already the last `ThyPhase`).
    A run with verified evaluation metrics publishes their report without inventing an
    Experiment. Otherwise, no scored results yield a plain summary. Unscored attempts
    remain available for MIRA to audit, including a refused sandbox execution.
    """

    def _summarize(state: ThyState) -> dict[str, Any]:
        if not state.experiments:
            evaluated = evaluation_report(state, artifact_store)
            exploratory = exploratory_report(state, artifact_store, event_log.events())
            if exploratory is not None:
                rendered = exploratory.markdown
                if evaluated is not None:
                    rendered += "\n\n" + evaluated
                return _publish_report(
                    state,
                    rendered,
                    artifact_store,
                    event_log,
                    report_input_artifact_ids=exploratory.source_artifact_ids,
                )
            if evaluated is not None:
                return _publish_report(state, evaluated, artifact_store, event_log)
            summary = "no experiments to compare; nothing to recommend"
            if state.run.conversation is not None:
                # Structured task JSON stays in the task evidence unless a source-linked report
                # can turn its facts into a checked human narrative.
                summary = conversation_task_summary(state) or summary
            return state.model_copy(update={"summary": summary}).model_dump()
        if not any(metric in experiment.metrics for experiment in state.experiments):
            return state.model_copy(
                update={
                    "summary": f"no experiments report {metric!r}; nothing to recommend",
                    "recommendation": None,
                }
            ).model_dump()

        best = compare_experiments(state.experiments, metric=metric)
        model = routed_model(
            Role.THY,
            "synthesize",
            event_log,
            provider=provider,
            output_schema=SynthesisNarrative,
            before_model_request=before_model_request,
            before_model_selection=before_model_selection,
            before_model_call=before_model_call,
            record_model_usage=record_model_usage,
            route_policy=route_policy,
        )
        agent: PydanticAgent[None, SynthesisNarrative] = PydanticAgent(
            model=model, output_type=SynthesisNarrative, instructions=_SUMMARIZE_INSTRUCTIONS
        )
        with agent_observation(name="thy-summarize", objective=state.run.prompt) as observation:
            result = agent.run_sync(
                frame_untrusted(
                    f"Best model: {best.name!r}. Metrics: {best.metrics}.\n"
                    + experiment_facts(best, artifact_store, event_log.events()),
                    label="thy-experiment-results",
                )
            )
            observation.update(output={"best_model": best.name})
        recommendation = Recommendation(
            best_model=best.name,
            metrics=best.metrics,
            tradeoffs=result.output.tradeoffs,
            limitations=result.output.limitations,
        )

        rendered = training_report(best, recommendation, artifact_store, event_log.events())
        return _publish_report(state, rendered, artifact_store, event_log, recommendation)

    return _summarize


def _publish_report(
    state: ThyState,
    rendered: str,
    artifact_store: ArtifactStore,
    event_log: EventLog,
    recommendation: Recommendation | None = None,
    report_input_artifact_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Publish the actual narrative so independent report controls can inspect its claims."""
    if report_input_artifact_ids:
        (artifact,) = artifact_store.save_artifact_batch(
            (
                ArtifactWrite(
                    name="analysis.md",
                    data=rendered.encode("utf-8"),
                    kind=ArtifactKind.REPORT,
                    media_type="text/markdown",
                    input_artifact_ids=report_input_artifact_ids,
                ),
            ),
            produced_by=state.run.id,
        )
    else:
        artifact = artifact_store.save_text(
            "analysis.md",
            rendered,
            produced_by=state.run.id,
            kind=ArtifactKind.REPORT,
            media_type="text/markdown",
        )
    event_log.append(
        EventType.ARTIFACT_CREATED,
        Actor.system(),
        {
            "name": artifact.name,
            "sha256": artifact.sha256,
            "artifact_id": artifact.id,
            "produced_by": artifact.produced_by,
        },
        subject_id=artifact.id,
        producer="thymira.thy",
    )
    return state.model_copy(
        update={
            "summary": rendered,
            "recommendation": recommendation,
            "artifacts": (*state.artifacts, artifact),
        }
    ).model_dump()


__all__ = ["compare_experiments", "summarize_node"]
