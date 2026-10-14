"""Render scientific result facts from recorded experiments and registered artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thymira.events import canonical_json, sha256_bytes
from thymira.schemas import ArtifactKind, EventType, TaskStatus
from thymira.thy.agents.coding import CodeResult
from thymira.thy.agents.data import DataProfile
from thymira.thy.models import ThyAgentKind
from thymira.tools.builtins.experiment_repro import is_default_training_evidence

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.schemas import Artifact, Event, Experiment, Task
    from thymira.state import ArtifactStore
    from thymira.thy.models import AgentTask, Recommendation, ThyState


_MAX_PROFILE_BYTES = 16 * 1024 * 1024
_MAX_SCHEMA_BYTES = 1024 * 1024
_MAX_DATASET_BYTES = 256 * 1024 * 1024
_MAX_TASK_SUMMARY = 16_000


@dataclass(frozen=True, slots=True)
class _VerifiedProfile:
    """The profile facts whose bytes match the active dataset and schema records."""

    dataset: str
    report: Artifact
    source: Artifact
    schema: Artifact
    rows: int
    columns: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True, slots=True)
class ExploratoryReport:
    """Human-readable EDA and the exact evidence needed to publish it with lineage."""

    markdown: str
    source_artifact_ids: tuple[str, ...]


def experiment_facts(best: Experiment, store: ArtifactStore, events: Sequence[Event]) -> str:
    """Describe measured metrics, exact model identity and recorded methodology only."""
    model = next((item for item in store.list_active() if item.id == best.model_artifact_id), None)
    trained = next(
        (
            event.payload
            for event in reversed(events)
            if event.type is EventType.MODEL_TRAINED
            and event.payload.get("model_artifact_id") == best.model_artifact_id
        ),
        {},
    )
    lines = [
        f"Experiment: {best.name}",
        f"Model artifact: {model.name if model else 'not registered'}",
        f"Model artifact ID: {best.model_artifact_id or 'not recorded'}",
        "Data and target: " + canonical_json(best.parameters),
        f"Seed: {best.seed if best.seed is not None else 'not recorded'}",
        "Evaluation split: " + canonical_json(trained.get("split", "not recorded")),
    ]
    if is_default_training_evidence(trained):
        lines.append(
            "Preparation: the recorded default pipeline imputes and scales numeric columns "
            "and imputes/one-hot encodes categorical columns, fitted on the training split."
        )
    else:
        lines.append("Preparation: not established by the available structured evidence.")
    return "\n".join("- " + line for line in lines)


def training_report(
    best: Experiment, recommendation: Recommendation, store: ArtifactStore, events: Sequence[Event]
) -> str:
    """Lead with access, method and actual metric values before the model-generated discussion."""
    rows = "\n".join(f"| {key} | {value:g} |" for key, value in best.metrics.items())
    missing = ", ".join(
        key for key in ("precision", "recall", "f1", "roc_auc") if key not in best.metrics
    )
    limitation = (
        "Accuracy does not establish a balance between precision and recall. "
        f"Additional metrics not recorded: {missing}."
        if missing
        else "These metrics describe this evaluation; they do not establish external validity."
    )
    return (
        f"# Model result\n\n{experiment_facts(best, store, events)}\n\n"
        f"[Open model and result artifacts](#/runs/{best.run_id}/artifacts)\n\n"
        f"## Recorded metrics\n\n| Metric | Value |\n| --- | --- |\n{rows}\n\n"
        f"## Evidence limits\n\n{limitation}\n\n"
        f"## Tradeoffs\n\n{recommendation.tradeoffs}\n\n"
        f"## Limitations\n\n{recommendation.limitations}\n\n"
        "You can ask about the recorded training or request new metrics or plots "
        "in a new governed Run.\n"
    )


def evaluation_report(state: ThyState, store: ArtifactStore) -> str | None:
    """Render existing-model evaluation artifacts without inventing an Experiment or ranking."""
    names = {artifact.id for artifact in state.artifacts}
    reports = []
    for artifact in store.list_active():
        if artifact.id not in names or artifact.kind is not ArtifactKind.METRICS:
            continue
        try:
            data = store.load_bytes_bounded(artifact.name, 1024 * 1024)
            if sha256_bytes(data) != artifact.sha256:
                continue
            payload = json.loads(data.decode("utf-8"))
        except (ValueError, OSError, KeyError):
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("metrics"), dict):
            continue
        model = payload.get("model")
        method = payload.get("methodology")
        if not isinstance(model, dict) or not isinstance(method, dict):
            continue
        rows = "\n".join(
            f"| {key} | {value if value is not None else 'Unavailable'} |"
            for key, value in payload["metrics"].items()
        )
        facts = [
            f"Model: {model.get('artifact', 'not recorded')}",
            f"Source Run: {method.get('source_run_id', 'not recorded')}",
            f"Source model ID: {method.get('source_model_artifact_id', 'not recorded')}",
            f"Target: {method.get('target_column', 'not recorded')}",
            f"Evaluation: {method.get('split', 'not recorded')}",
            f"Evaluated rows: {method.get('evaluated_rows', 'not recorded')}",
            f"Averaging: {method.get('averaging', 'not recorded')}",
            f"Positive class: {method.get('positive_class', 'not recorded')}",
        ]
        unavailable = method.get("roc_auc_unavailable_reason")
        limitation = (
            "No model was fitted during this evaluation. These measurements describe the "
            "recorded evaluation rows; they do not establish external validity."
        )
        if unavailable:
            limitation += " ROC-AUC unavailable: " + str(unavailable)
        reports.append(
            f"## Existing model evaluation\n\nArtifact: {artifact.name}\n\n"
            + "\n".join("- " + fact for fact in facts)
            + f"\n\n| Metric | Value |\n| --- | --- |\n{rows}\n\n"
            + limitation
            + f"\n\n[Open evaluation artifact](#/runs/{state.run.id}/artifacts)"
        )
    return "\n\n".join(reports) if reports else None


def exploratory_report(
    state: ThyState, store: ArtifactStore, events: Sequence[Event]
) -> ExploratoryReport | None:
    """Render EDA from source-linked profile artifacts and typed task outcomes only."""
    profiles = _verified_profiles(state, store)
    if not profiles:
        return None

    pairs = _task_pairs(state)
    data_outputs = [
        output
        for plan, task in pairs
        if plan.agent is ThyAgentKind.DATA
        and task.status is TaskStatus.COMPLETED
        and (output := _task_output(task, DataProfile)) is not None
        and isinstance(output, DataProfile)
    ]
    active_by_name = {artifact.name: artifact for artifact in store.list_active()}
    input_ids: list[str] = []
    profile_sections: list[str] = []
    for profile in profiles:
        input_ids.extend((profile.report.id, profile.source.id, profile.schema.id))
        registered = next(
            (dataset for dataset in state.datasets if dataset.name == profile.dataset), None
        )
        declared_target = registered.target if registered is not None else None
        candidates = _target_candidates(profile, data_outputs)
        missing_columns = tuple(name for name, _, count in profile.columns if count > 0)
        if missing_columns:
            missing_names = ", ".join(f"`{_inline_code(name)}`" for name in missing_columns)
            review = (
                "The profile reviewed the column structure and registered types. It found "
                f"missing entries in {missing_names}. "
                "These columns need a treatment decision before modeling; this report does not "
                "claim that cleaning or imputation was applied."
            )
        else:
            review = (
                f"Within the {profile.rows} profiled rows, no missing entries were recorded "
                "for the columns shown. This describes the profile only."
            )
        rows = "\n".join(
            f"| {_table_cell(name)} | {_table_cell(dtype)} | {null_count} |"
            for name, dtype, null_count in profile.columns
        )
        target = _inline_code(declared_target) if declared_target else "none recorded"
        possible_targets = ", ".join(_inline_code(name) for name in candidates)
        profile_sections.append(
            f"**Dataset: `{_inline_code(profile.dataset)}`**\n\n"
            f"Source data: `{_inline_code(profile.source.name)}`. Registered schema: "
            f"`{_inline_code(profile.schema.name)}`.\n\n"
            f"{review}\n\n"
            f"Profiled shape: {profile.rows} rows and {len(profile.columns)} columns.\n\n"
            "| Column | Registered type | Missing values |\n"
            "| --- | --- | ---: |\n"
            f"{rows}\n\n"
            f"Project-declared target: {target}.\n\n"
            "Possible target columns (unconfirmed): "
            f"{possible_targets or 'none recorded'}."
        )

    task_lines, output_names = _work_summary(
        pairs, events, active_by_name, state.run.id, data_outputs, profiles
    )
    input_ids.extend(active_by_name[name].id for name in output_names if name in active_by_name)
    unique_input_ids = tuple(dict.fromkeys(input_ids))
    artifact_link = f"#/runs/{state.run.id}/artifacts"
    run_link = f"#/runs/{state.run.id}"
    profile_evidence = "\n".join(
        f"- Profile report `{_inline_code(profile.report.name)}`, dataset "
        f"`{_inline_code(profile.source.name)}`, schema `{_inline_code(profile.schema.name)}`."
        for profile in profiles
    )
    markdown = (
        "# Exploratory data analysis\n\n"
        "## Dataset facts\n\n"
        f"Run: [open run]({run_link}) | [open artifacts]({artifact_link}).\n\n"
        + "\n\n".join(profile_sections)
        + "\n\n## Work completed\n\n"
        + ("\n".join(task_lines) if task_lines else "No completed agent tasks were recorded.")
        + "\n\n## Evidence and limitations\n\n"
        + profile_evidence
        + "\n\nThe profile values refer to the profile report, registered schema, and dataset "
        + "artifacts listed above. MIRA recomputes the profile from the registered dataset "
        + "bytes and accepts its values only after that check passes. Generated images are "
        + "linked as artifacts; this report does not infer findings from image pixels. Possible "
        + "target columns are unconfirmed and should be checked against the project objective.\n"
    )
    return ExploratoryReport(
        markdown=markdown,
        source_artifact_ids=unique_input_ids,
    )


def conversation_task_summary(state: ThyState) -> str | None:
    """Summarize completed conversation tasks without exposing structured JSON payloads."""
    pairs = _task_pairs(state)
    if pairs:
        lines = [line for plan, task in pairs for line in _conversation_task_lines(plan, task)]
    else:
        lines = [
            line
            for task in state.agent_messages
            if (line := _unplanned_task_line(task)) is not None
        ]
    return "\n\n".join(lines)[:_MAX_TASK_SUMMARY] if lines else None


def _conversation_task_lines(plan: AgentTask, task: Task) -> list[str]:
    """Describe one planned outcome without exposing its structured payload."""
    label = plan.agent.value.replace("-", " ")
    if task.status is not TaskStatus.COMPLETED:
        return _incomplete_task_lines(label, task.status)
    if plan.agent is ThyAgentKind.DATA:
        return [
            (
                "The data task completed, but no verified dataset profile was "
                "available to support dataset facts."
            )
        ]
    if plan.agent is ThyAgentKind.CODING:
        return [
            (
                "The coding task completed; its structured result and console output "
                "remain in the run evidence."
            )
        ]
    if task.summary and not _is_json_payload(task.summary):
        return [task.summary[:_MAX_TASK_SUMMARY]]
    return [f"The {label} task completed; its result is in the run evidence."]


def _unplanned_task_line(task: Task) -> str | None:
    """Keep unplanned task output readable while structured payloads remain in evidence."""
    if task.status is not TaskStatus.COMPLETED or not task.summary:
        return None
    if _is_json_payload(task.summary):
        return "A task completed with a structured result stored in the run evidence."
    return task.summary[:_MAX_TASK_SUMMARY]


def _verified_profiles(state: ThyState, store: ArtifactStore) -> list[_VerifiedProfile]:
    """Load only active, hash-matched profile reports tied to registered source artifacts."""
    active = {artifact.name: artifact for artifact in store.list_active()}
    state_artifact_ids = {artifact.id for artifact in state.artifacts}
    profiles: list[_VerifiedProfile] = []
    for artifact in active.values():
        if (
            artifact.id not in state_artifact_ids
            or artifact.kind is not ArtifactKind.REPORT
            or not artifact.name.startswith("profile/")
            or not artifact.name.endswith(".json")
        ):
            continue
        try:
            raw = store.load_bytes_bounded(artifact.name, _MAX_PROFILE_BYTES)
            if sha256_bytes(raw) != artifact.sha256:
                continue
            payload = json.loads(raw.decode("utf-8"))
            profile = _profile_payload(payload, artifact, active, store)
        except (KeyError, OSError, UnicodeDecodeError, ValueError, RecursionError):
            continue
        if profile is not None:
            profiles.append(profile)
    return profiles


def _profile_payload(
    value: object,
    report: Artifact,
    active: dict[str, Artifact],
    store: ArtifactStore,
) -> _VerifiedProfile | None:
    """Check profile source hashes and schema fields before using its numeric facts."""
    declaration = _profile_declaration(value, report)
    if declaration is None:
        return None
    dataset, source_info, shape, raw_columns = declaration
    registered = _profile_sources(dataset, source_info, raw_columns, active, store)
    if registered is None:
        return None
    source, schema, names, dtypes = registered
    rows = _profile_rows(shape, len(names))
    if rows is None:
        return None
    columns = _profile_column_facts(names, dtypes, raw_columns, rows)
    if columns is None:
        return None
    return _VerifiedProfile(
        dataset=dataset,
        report=report,
        source=source,
        schema=schema,
        rows=rows,
        columns=columns,
    )


def _profile_declaration(
    value: object, report: Artifact
) -> tuple[str, dict[str, object], dict[str, object], dict[str, object]] | None:
    """Validate the bounded profile fields needed to resolve source lineage."""
    if not isinstance(value, dict) or value.get("profile_version") != 1:
        return None
    dataset = value.get("dataset")
    source_info = value.get("source")
    shape = value.get("shape")
    raw_columns = value.get("columns")
    if (
        not isinstance(dataset, str)
        or not dataset
        or report.name != f"profile/{dataset}.json"
        or not isinstance(source_info, dict)
        or not isinstance(shape, dict)
        or not isinstance(raw_columns, dict)
    ):
        return None
    return dataset, source_info, shape, raw_columns


def _profile_sources(
    dataset: str,
    source_info: dict[str, object],
    raw_columns: dict[str, object],
    active: dict[str, Artifact],
    store: ArtifactStore,
) -> tuple[Artifact, Artifact, tuple[str, ...], tuple[str, ...]] | None:
    """Verify schema and dataset artifacts and return their registered column order."""
    source_name = source_info.get("artifact")
    source_digest = source_info.get("sha256")
    schema_digest = source_info.get("schema_sha256")
    if not isinstance(source_name, str):
        return None
    schema_name = f"datasets/{dataset}.schema.json"
    source = active.get(source_name)
    schema = active.get(schema_name)
    if (
        source is None
        or source.kind is not ArtifactKind.DATASET
        or source.sha256 != source_digest
        or schema is None
        or schema.kind is not ArtifactKind.OTHER
        or schema.sha256 != schema_digest
    ):
        return None
    source_raw = store.load_bytes_bounded(source.name, _MAX_DATASET_BYTES)
    schema_raw = store.load_bytes_bounded(schema.name, _MAX_SCHEMA_BYTES)
    if sha256_bytes(source_raw) != source.sha256 or sha256_bytes(schema_raw) != schema.sha256:
        return None
    schema_value = json.loads(schema_raw.decode("utf-8"))
    if not isinstance(schema_value, dict):
        return None
    names = schema_value.get("columns")
    dtypes = schema_value.get("dtypes")
    if (
        schema_value.get("name") != dataset
        or schema_value.get("artifact_name") != source.name
        or schema_value.get("sha256") != source.sha256
        or not isinstance(names, list)
        or not all(isinstance(name, str) and name for name in names)
        or not isinstance(dtypes, list)
        or not all(isinstance(dtype, str) and dtype for dtype in dtypes)
        or len(names) != len(dtypes)
        or len(set(names)) != len(names)
        or set(raw_columns) != set(names)
    ):
        return None
    return source, schema, tuple(names), tuple(dtypes)


def _profile_rows(shape: dict[str, object], expected_columns: int) -> int | None:
    """Validate reported shape dimensions against the registered schema."""
    rows = shape.get("rows")
    column_count = shape.get("columns")
    if (
        isinstance(rows, bool)
        or not isinstance(rows, int)
        or rows < 0
        or isinstance(column_count, bool)
        or not isinstance(column_count, int)
        or column_count != expected_columns
    ):
        return None
    return rows


def _profile_column_facts(
    names: tuple[str, ...],
    dtypes: tuple[str, ...],
    raw_columns: dict[str, object],
    rows: int,
) -> tuple[tuple[str, str, int], ...] | None:
    """Validate missing counts and retain the schema's original column order."""
    columns: list[tuple[str, str, int]] = []
    for name, dtype in zip(names, dtypes, strict=True):
        details = raw_columns[name]
        if not isinstance(details, dict):
            return None
        missing = details.get("null_count")
        if isinstance(missing, bool) or not isinstance(missing, int) or not 0 <= missing <= rows:
            return None
        columns.append((name, dtype, missing))
    return tuple(columns)


def _task_pairs(state: ThyState) -> tuple[tuple[AgentTask, Task], ...]:
    """Return only positional plan/outcome pairs when their parallel invariant holds."""
    if len(state.completed) != len(state.agent_messages):
        return ()
    return tuple(zip(state.completed, state.agent_messages, strict=True))


def _task_output(task: Task, output_type: type[DataProfile] | type[CodeResult]) -> object | None:
    """Validate a completed task's declared JSON output rather than narrating its raw text."""
    if task.status is not TaskStatus.COMPLETED or not task.summary:
        return None
    try:
        return output_type.model_validate_json(task.summary[:_MAX_TASK_SUMMARY])
    except ValueError:
        return None


def _target_candidates(
    profile: _VerifiedProfile, data_outputs: list[DataProfile]
) -> tuple[str, ...]:
    """Use target suggestions only when one structured profile matches the registered schema."""
    profile_columns = {name for name, _, _ in profile.columns}
    matches = [
        output
        for output in data_outputs
        if len(output.columns) == len(set(output.columns))
        and set(output.columns) == profile_columns
    ]
    if len(matches) != 1:
        return ()
    available = {name for name, _, _ in profile.columns}
    return tuple(name for name in matches[0].target_candidates if name in available)


def _work_summary(
    pairs: tuple[tuple[AgentTask, Task], ...],
    events: Sequence[Event],
    active_by_name: dict[str, Artifact],
    run_id: str,
    data_outputs: list[DataProfile],
    profiles: list[_VerifiedProfile],
) -> tuple[list[str], tuple[str, ...]]:
    """Describe task outcomes, keeping recovered tool errors apart from unfinished tasks."""
    lines: list[str] = []
    output_names: list[str] = []
    matched_profiles = {
        profile.dataset for profile in profiles if _target_candidates(profile, data_outputs)
    }
    artifact_link = f"#/runs/{run_id}/artifacts"
    for plan, task in pairs:
        label = plan.agent.value.replace("-", " ")
        if task.status is TaskStatus.COMPLETED:
            had_failed_attempt = _has_failed_tool_call(events, task.id)
            if plan.agent is ThyAgentKind.DATA and task.summary:
                detail = (
                    "A dataset profile was prepared and summarized above."
                    if matched_profiles
                    else "The data review completed; its verified profile is summarized above."
                )
                lines.append(f"- {detail}")
            elif plan.agent is ThyAgentKind.CODING:
                result = _task_output(task, CodeResult)
                if isinstance(result, CodeResult):
                    detail = (
                        "The coding agent reported success."
                        if result.exit_code == 0
                        else "The coding agent reported an error; inspect its run evidence."
                    )
                    if result.stdout.strip():
                        detail += " Console output remains in the task evidence."
                    names = tuple(name for name in result.artifacts if name in active_by_name)
                    if names:
                        output_names.extend(names)
                        detail += (
                            " Files available: "
                            + ", ".join(
                                f"[`{_inline_code(name)}`]({artifact_link})" for name in names
                            )
                            + "."
                        )
                    lines.append(f"- {detail}")
                else:
                    lines.append(
                        "- The coding task completed; its output remains in task evidence."
                    )
            elif task.summary and not _is_json_payload(task.summary):
                lines.append(f"- {task.summary[:_MAX_TASK_SUMMARY]}")
            else:
                lines.append(f"- The {label} task completed; its result is in the run evidence.")
            if had_failed_attempt:
                lines.append(
                    "  Earlier tool attempts failed; the required task work was subsequently "
                    "verified as complete. Individual attempts remain in the run evidence."
                )
        else:
            lines.extend(_incomplete_task_lines(label, task.status))
    return lines, tuple(dict.fromkeys(output_names))


def _has_failed_tool_call(events: Sequence[Event], task_id: str) -> bool:
    """Whether this task has any failed tool call retained in its event history."""
    return any(
        event.type is EventType.TOOL_COMPLETED
        and event.payload.get("task_id") == task_id
        and (event.payload.get("status") == "FAILED" or event.payload.get("error") is not None)
        for event in events
    )


def _incomplete_task_lines(label: str, status: TaskStatus) -> list[str]:
    """Describe a non-completed task without presenting its diagnostics as completed findings."""
    if status is TaskStatus.FAILED:
        return [f"- The {label} task failed; review its diagnostics in the run event history."]
    if status is TaskStatus.PENDING:
        return [f"- The {label} task is awaiting a human decision."]
    return [f"- The {label} task did not produce a completed result (status: {status.value})."]


def _is_json_payload(value: str) -> bool:
    """Recognize machine-shaped output so it stays in the task record, not prose."""
    if len(value) > _MAX_TASK_SUMMARY:
        return True
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, RecursionError):
        return False
    return isinstance(decoded, (dict, list))


def _table_cell(value: str) -> str:
    """Escape user-controlled profile labels for a Markdown table cell."""
    return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _inline_code(value: str | None) -> str:
    """Keep metadata labels from breaking the report's Markdown code spans."""
    if value is None:
        return ""
    return value.replace("`", "\\`").replace("\n", " ").replace("\r", " ")
