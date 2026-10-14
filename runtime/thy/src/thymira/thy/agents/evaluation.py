"""Evaluate an existing model with no training or generic code tools."""

from pydantic import BaseModel

from thymira.agents import AgentCatalog, AgentSpec
from thymira.agents.llm.routing import Role


class EvaluationResult(BaseModel):
    """Describe the observed evaluation without creating a training Experiment."""

    summary: str
    metrics_artifact_id: str | None = None


EVALUATION_AGENT_SPEC = AgentSpec(
    name="evaluation",
    role=Role.AGENT,
    task_kinds=("code",),
    tool_allowlist=("evaluate_model",),
    max_turns=8,
    max_depth=1,
    system_prompt_ref="evaluation",
    output_schema_ref="thymira.thy.agents.evaluation:EvaluationResult",
)


def evaluation_agent_catalog() -> AgentCatalog:
    """Build the bounded evaluator; tool policy and approvals still apply."""
    return AgentCatalog(
        (EVALUATION_AGENT_SPEC,),
        system_prompts={
            "evaluation": (
                "Evaluate the exact model and dataset named by your instruction. "
                "Use evaluate_model, the registered context and recorded_holdout. "
                "Never train or fit another model. Report only returned metrics. "
                "State undefined or unavailable metrics honestly. A failed or denied "
                "call is not a completed evaluation. Preserve the registered artifact "
                "ID in metrics_artifact_id. Accuracy does not establish precision/recall balance."
            )
        },
        output_schemas={"evaluation": EvaluationResult},
    )
