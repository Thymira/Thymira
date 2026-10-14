"""The Visualization agent: produce a plot artifact with a caption (THY-21).

The hand-written `AgentSpec` uses `run_python.output_artifacts` for PNG plots and `write_file` for
requested SVG exports. Both paths register a real `Artifact` with `kind="plot"`, so each delivered
plot has a sha256 in the manifest and an id the agent can report.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from thymira.agents import AgentCatalog, AgentSpec
from thymira.agents.llm.routing import Role

_PROMPTS_DIR = Path(__file__).parent / "prompts"

VISUALIZATION_AGENT_SPEC = AgentSpec(
    name="visualization-agent",
    role=Role.AGENT,
    task_kinds=("analyze",),
    tool_allowlist=("run_python", "write_file"),
    max_turns=6,
    max_depth=1,
    system_prompt_ref="prompts/visualization.md",
    output_schema_ref="thymira.thy.agents.visualization:PlotResult",
)


class Plot(BaseModel):
    """One rendered plot: the artifact it became and how to read it."""

    artifact_id: str = Field(min_length=1)
    caption: str = Field(min_length=1)


class PlotResult(BaseModel):
    """The Visualization agent's structured output: at least one plot, each captioned."""

    plots: tuple[Plot, ...] = Field(min_length=1)


def visualization_agent_catalog() -> AgentCatalog:
    """Build the real, single-spec `AgentCatalog` entry for `VISUALIZATION_AGENT_SPEC`."""
    system_prompt = (_PROMPTS_DIR / "visualization.md").read_text(encoding="utf-8")
    return AgentCatalog(
        (VISUALIZATION_AGENT_SPEC,),
        system_prompts={VISUALIZATION_AGENT_SPEC.name: system_prompt},
        output_schemas={VISUALIZATION_AGENT_SPEC.name: PlotResult},
    )


__all__ = [
    "VISUALIZATION_AGENT_SPEC",
    "Plot",
    "PlotResult",
    "visualization_agent_catalog",
]
