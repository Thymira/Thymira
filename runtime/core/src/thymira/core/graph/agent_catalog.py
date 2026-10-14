"""Compose THY specialists while preserving their explicit tool allowlists."""

from __future__ import annotations

from typing import TYPE_CHECKING

from thymira.agents import AgentCatalog
from thymira.thy.agents import coding_agent_catalog, data_agent_catalog, experiment_agent_catalog
from thymira.thy.agents.evaluation import evaluation_agent_catalog

if TYPE_CHECKING:
    from pydantic import BaseModel

    from thymira.agents import AgentSpec


def default_thy_catalog() -> AgentCatalog:
    """Compose data, coding, training and a separately constrained evaluator."""
    return _merge_catalogs(
        data_agent_catalog(),
        coding_agent_catalog(),
        experiment_agent_catalog(),
        evaluation_agent_catalog(),
    )


def _merge_catalogs(*catalogs: AgentCatalog) -> AgentCatalog:
    """Merge name-disjoint catalogs, keeping each spec's resolved prompt and output schema."""
    specs: list[AgentSpec] = []
    system_prompts: dict[str, str] = {}
    output_schemas: dict[str, type[BaseModel]] = {}
    for catalog in catalogs:
        for name in catalog.names():
            specs.append(catalog.get(name))
            system_prompts[name] = catalog.system_prompt(name)
            output_schemas[name] = catalog.output_schema(name)
    return AgentCatalog(tuple(specs), system_prompts=system_prompts, output_schemas=output_schemas)
