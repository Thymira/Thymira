"""Real local Python generates and publishes only plots selected from mixed data."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import TYPE_CHECKING, cast

import pytest

from tests.thymira.test_tools_python_source_reference import _code_artifact, _context
from thymira.agents.task_completion import assess_task_completion
from thymira.schemas import Actor, ActorKind, ArtifactKind, EventType, SandboxMode, Task, new_id
from thymira.thy.agents.coding import CodeResult
from thymira.thy.models import AgentTask, ArtifactRequirement, ThyAgentKind, ThyPhase
from thymira.thy.task_completion import task_completion_validator
from thymira.tools import Tool, ToolManager, ToolRegistry
from thymira.tools.builtins.run_python import RunPython

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.integration
@pytest.mark.parametrize(
    ("include_categorical", "wrong_requirement"),
    [(False, False), (True, False), (True, True)],
)
def test_real_manifest_publication_reaches_task_completion_consumer(
    tmp_path: Path, include_categorical: bool, wrong_requirement: bool
) -> None:
    initial_context = _context(tmp_path)
    source = f"""import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
columns = {{"age": [21, 35, 49]}}
if {include_categorical!r}:
    columns["savings_status"] = ["low", "high", "low"]
frame = pd.DataFrame(columns)
outputs = []
for column in frame.select_dtypes(include="number").columns:
    fig, ax = plt.subplots()
    ax.hist(frame[column])
    path = f"histogram_{{column}}.png"
    fig.savefig(path)
    plt.close(fig)
    outputs.append({{"path": path, "kind": "plot", "media_type": "image/png"}})
Path("outputs.json").write_text(json.dumps(outputs), encoding="utf-8")
print("Generated numeric histograms")
"""
    artifact = _code_artifact(initial_context, "analysis.py", source)
    task_id = new_id("task")
    task = Task(
        id=task_id,
        run_id=initial_context.run_id,
        agent_id=initial_context.agent_id,
        objective="Publish the numeric histogram.",
    )
    context = replace(initial_context, task_id=task_id)
    required_path = "histogram_savings_status.png" if wrong_requirement else "histogram_age.png"
    planned_task = AgentTask(
        id="numeric-histogram",
        agent=ThyAgentKind.CODING,
        phase=ThyPhase.EXECUTE,
        instruction=task.objective,
        required_artifacts=(ArtifactRequirement(name=required_path, kind=ArtifactKind.PLOT),),
    )
    context.event_log.append(
        EventType.AGENT_STARTED,
        Actor(kind=ActorKind.AGENT, id=context.agent_id),
        {"task_id": task_id, "agent_id": context.agent_id, "agent": "coding"},
    )
    manager = ToolManager(
        ToolRegistry((cast("Tool", RunPython(mode=SandboxMode.DANGER_FULL_ACCESS)),))
    )
    execution = manager.execute(
        context,
        "run_python",
        {
            "source_artifact_id": artifact.id,
            "description": "Generate numeric plots from synthetic data",
            "output_manifest_path": "outputs.json",
        },
    )
    assert execution.result.success, execution.result.error
    assert execution.result.exit_code == 0
    plot = context.artifact_store.get("histogram_age.png")
    assert plot is not None
    assert plot.input_artifact_ids == (artifact.id,)
    plot_bytes = (context.workspace / plot.name).read_bytes()
    assert plot_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert plot.sha256 == hashlib.sha256(plot_bytes).hexdigest()
    assert artifact.sha256 == hashlib.sha256(source.encode()).hexdigest()
    assert context.artifact_store.get("histogram_savings_status.png") is None
    manifest = json.loads((context.workspace / "outputs.json").read_text(encoding="utf-8"))
    assert [entry["path"] for entry in manifest] == ["histogram_age.png"]
    assert context.artifact_store.get("outputs.json") is not None
    completed = next(
        event
        for event in reversed(context.event_log.events())
        if event.type is EventType.TOOL_COMPLETED
    )
    assert completed.payload["output_declarations"] == [
        {"path": "histogram_age.png", "kind": "plot", "media_type": "image/png"}
    ]
    assert all(
        descriptor["path"] != "outputs.json"
        for descriptor in completed.payload["output_declarations"]
    )
    started = next(
        event
        for event in context.event_log.events()
        if event.type is EventType.TOOL_STARTED and event.payload.get("tool") == "run_python"
    )
    assert started.payload["arguments"]["source_artifact_sha256"] == artifact.sha256
    evidence = assess_task_completion(context.event_log.events(), task_id, context.artifact_store)
    settled = task_completion_validator(planned_task, context.artifact_store)(
        task,
        CodeResult(stdout="numeric histogram generated", exit_code=0, artifacts=[]),
        context.event_log.events(),
        evidence,
    )
    if wrong_requirement:
        assert settled.error is not None
        assert required_path in settled.missing_required_artifacts
    else:
        assert settled.error is None
        assert plot.id in settled.verified_artifact_ids
    assert context.artifact_store.verify() == []
    assert context.event_log.verify().valid
