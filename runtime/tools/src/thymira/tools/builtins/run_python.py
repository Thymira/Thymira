"""The policy-gated Python execution tool."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path, PureWindowsPath
from typing import Any, cast

from pydantic import BaseModel, Field

from thymira.policies import ToolCapability
from thymira.schemas import Artifact, ArtifactKind, SandboxMode, new_id
from thymira.state import ArtifactWrite, canonical_artifact_name
from thymira.tools.artifact_validation import (
    MAX_GENERIC_ARTIFACT_BYTES,
    artifact_read_limit,
    resolve_artifact_media_type,
    validate_artifact_bytes,
    validate_report_kind,
)
from thymira.tools.builtins.audit_model import AuditModel
from thymira.tools.builtins.compare_models import CompareModels
from thymira.tools.builtins.data_analysis import AnalyzeDataset, ProfileDataset
from thymira.tools.builtins.evaluate_model import EvaluateModel
from thymira.tools.builtins.export_pdf import ExportPdf
from thymira.tools.builtins.files import (
    EditFile,
    ListFiles,
    ReadFile,
    WriteFile,
    contained_path,
)
from thymira.tools.builtins.freshness import FreshnessPolicy, ReadLedger
from thymira.tools.builtins.git import GitCommit, GitDiff, GitLog, GitStatus
from thymira.tools.builtins.inspect_model import InspectModel
from thymira.tools.builtins.mlflow_tools import mlflow_tools
from thymira.tools.builtins.python_arguments import RunPythonArguments, RunPythonOutputArtifact
from thymira.tools.builtins.python_output_freshness import (
    PythonOutputFreshnessSnapshot,
    capture_python_output_freshness,
    require_fresh_python_output,
)
from thymira.tools.builtins.python_output_manifest import (
    load_output_manifest,
    manifest_publication_descriptor,
    output_manifest_path,
    publication_failure,
    published_manifest_text,
)
from thymira.tools.builtins.python_reference import (
    PythonSourceReference,
    verify_prepared_python_source,
)
from thymira.tools.builtins.python_source import (
    MAX_PYTHON_SOURCE_BYTES,
)
from thymira.tools.builtins.query_mlflow import QueryMlflow
from thymira.tools.builtins.query_sql import QuerySql
from thymira.tools.builtins.run_experiment import RunExperiment
from thymira.tools.builtins.run_notebook import RunNotebook
from thymira.tools.builtins.run_statistics import RunStatistics
from thymira.tools.builtins.search import Glob, Grep
from thymira.tools.builtins.subprocess_command import (
    assert_no_link_components,
    ensure_workspace_root,
    link_shaped_refusal,
    persist_for_uninstrumented_backend,
    python_workspace_command,
    read_only_staging_refusal,
    with_sandbox_evidence,
)
from thymira.tools.builtins.subprocess_mode import (
    capability_for_sandbox_mode,
    requested_sandbox_mode,
)
from thymira.tools.builtins.worktrees import GitWorktreeCreate, GitWorktreeList, GitWorktreeRemove
from thymira.tools.models import Tool, ToolExecutionError, ToolInvocation, ToolResult
from thymira.tools.publication_progress import validated_python_output_declarations
from thymira.tools.registry import ToolRegistry
from thymira.tools.results import ProcessToolValue, PythonPublicationProgress
from thymira.tools.sandbox import LocalSubprocessSandbox, Sandbox, StagedInput

_MAX_OUTPUT_ARTIFACTS = 64
_MAX_OUTPUT_ARTIFACT_TOTAL_BYTES = MAX_GENERIC_ARTIFACT_BYTES


class PythonExecutionValue(ProcessToolValue):
    """Process output with optional provenance when execution came from a code artifact."""

    source_artifact_id: str | None = Field(default=None, min_length=1)
    source_artifact_name: str | None = Field(default=None, min_length=1)
    source_artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class MissingOutputArtifactsError(ToolExecutionError):
    """Atomic Python publication failed after validating every present declared output."""

    def __init__(self, progress: PythonPublicationProgress, present_paths: tuple[str, ...]) -> None:
        self.progress = progress
        missing_paths = [path for path, _kind in progress.missing]
        super().__init__(
            f"declared output artifact does not exist: {missing_paths[0]}; "
            f"missing declarations: {missing_paths}; "
            f"existing declared files: {list(present_paths)}"
        )


@dataclass(frozen=True, slots=True)
class RunPython:
    """Execute a Python snippet inside the configured sandbox."""

    sandbox: Sandbox = field(default_factory=LocalSubprocessSandbox)
    mode: SandboxMode = SandboxMode.WORKSPACE_WRITE
    name: str = "run_python"
    description: str = (
        "Execute Python code in a fresh interpreter through the runtime-configured sandbox with "
        "a bounded timeout. Syntax is checked before approval, and Python source is limited to "
        f"{MAX_PYTHON_SOURCE_BYTES} UTF-8 bytes. Nothing persists between calls: write files "
        "for anything the next "
        'call needs. Non-zero exits are reported as "[exit code: N]"; check that marker on '
        "every result and investigate a failure before moving on. Declare files created by the "
        "code in output_artifacts, or write a fresh output_manifest_path JSON list inside the "
        "script for data-dependent outputs. This publishes raw bytes, including .joblib and .png "
        "files; do not use read_file for binary outputs. Long output is truncated to its tail. "
        "A sandbox or policy refusal means execution is not permitted; do not retry another way."
    )
    arguments_model: type[BaseModel] = RunPythonArguments
    result_model: type[BaseModel] = PythonExecutionValue
    _capability: ToolCapability = field(
        default_factory=lambda: ToolCapability(
            id="run_python",
            risk_tags=("code_execution",),
            side_effects=("workspace_write",),
            external_effects=(),
            reversibility="reversible",
        )
    )

    @property
    def capability(self) -> ToolCapability:
        """Return the capability that records this instance's requested sandbox mode."""
        return capability_for_sandbox_mode(self._capability, self.mode)

    def execute(self, invocation: ToolInvocation, arguments: dict[str, Any]) -> ToolResult:
        """Write a temporary script under the workspace and execute it."""
        mode = requested_sandbox_mode(self.capability)
        if refusal := read_only_staging_refusal(mode):
            return refusal
        source_reference: PythonSourceReference | None = None
        if arguments.get("source_artifact_id") is not None:
            source_reference = verify_prepared_python_source(invocation, arguments)
        elif any(
            key in arguments
            for key in (
                "source_artifact_name",
                "source_artifact_sha256",
            )
        ):
            raise ToolExecutionError("run_python source metadata requires a prepared artifact ID")
        manifest_name = arguments.get("output_manifest_path")
        if manifest_name is not None:
            output_manifest_path(invocation, manifest_name, fresh=True)
        workspace = Path(invocation.workspace).resolve()
        requested_outputs = arguments.get("output_artifacts") or ()
        freshness_snapshot = capture_python_output_freshness(
            workspace,
            None if manifest_name else tuple(str(item["path"]) for item in requested_outputs),
        )
        script_dir = workspace / ".thymira"
        # Checked before the directory is created and before anything is written into it: a
        # link planted here would otherwise carry the staged script out of the workspace.
        if refusal := link_shaped_refusal(mode, workspace, script_dir):
            return refusal
        ensure_workspace_root(workspace)
        script_path = script_dir / f"{new_id('tool')}.py"
        command = python_workspace_command(workspace, script_path)
        staged = StagedInput(
            script_path.relative_to(workspace).as_posix(),
            source_reference.content
            if source_reference is not None
            else arguments["code"].encode("utf-8"),
        )
        run = self.sandbox.run(
            command,
            workspace=workspace,
            mode=mode,
            timeout_s=arguments.get("timeout_s", 30.0),
            staged_inputs=(staged,),
        )
        if run.spec is None:
            persist_for_uninstrumented_backend(workspace, (staged,))
        result = with_sandbox_evidence(
            ToolResult(
                success=run.exit_code == 0,
                value=PythonExecutionValue(
                    text=run.stdout,
                    stdout=run.stdout,
                    stderr=run.stderr,
                    exit_code=run.exit_code,
                    source_artifact_id=(
                        source_reference.artifact_id if source_reference is not None else None
                    ),
                    source_artifact_name=(
                        source_reference.name if source_reference is not None else None
                    ),
                    source_artifact_sha256=(
                        source_reference.sha256 if source_reference is not None else None
                    ),
                ),
                stdout=run.stdout,
                stderr=run.stderr,
                exit_code=run.exit_code,
                error=None if run.exit_code == 0 else (run.stderr or "python execution failed"),
            ),
            run,
            timeout_s=arguments.get("timeout_s", 30.0),
        )
        if not result.success or not (arguments.get("output_artifacts") or manifest_name):
            return result
        output_declarations: tuple[RunPythonOutputArtifact, ...] | None = None
        try:
            output_declarations = (
                load_output_manifest(invocation, manifest_name)
                if manifest_name
                else validated_python_output_declarations(
                    tuple(
                        RunPythonOutputArtifact.model_validate(item) for item in requested_outputs
                    )
                )
                if requested_outputs
                else ()
            )
            descriptors = tuple(item.model_dump(mode="python") for item in output_declarations)
            if manifest_name:
                descriptors = (*descriptors, manifest_publication_descriptor(manifest_name))
            artifact_ids = _publish_output_artifacts(
                invocation,
                descriptors,
                freshness_snapshot=freshness_snapshot,
                copied_workspace=run.quota_evidence is not None,
                manifest_path=manifest_name,
                input_artifact_ids=(
                    (source_reference.artifact_id,) if source_reference is not None else ()
                ),
            )
        except MissingOutputArtifactsError as exc:
            return replace(
                result,
                success=False,
                error=publication_failure(exc),
                publication_progress=exc.progress,
                output_declarations=output_declarations,
            )
        except (OSError, ToolExecutionError, ValueError) as exc:
            return replace(
                result,
                success=False,
                error=publication_failure(exc),
                output_declarations=output_declarations or None,
            )
        if manifest_name and isinstance(result.value, PythonExecutionValue):
            result = replace(
                result,
                value=result.value.model_copy(
                    update={
                        "text": published_manifest_text(run.stdout, descriptors),
                    }
                ),
            )
        return replace(
            result,
            artifact_ids=artifact_ids,
            output_declarations=output_declarations or None,
        )


def _publish_output_artifacts(  # noqa: PLR0912, PLR0915  # ordered validation is fail-closed
    invocation: ToolInvocation,
    output_artifacts: tuple[dict[str, Any], ...],
    *,
    freshness_snapshot: PythonOutputFreshnessSnapshot,
    copied_workspace: bool,
    manifest_path: str | None,
    input_artifact_ids: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Read bounded declared outputs and independently validate typed contents before publishing."""
    if invocation.artifact_store is None:
        raise ToolExecutionError("output_artifacts require an artifact store")
    workspace = Path(invocation.workspace).resolve()
    attachments: list[ArtifactWrite] = []
    seen: set[str] = set()
    seen_folded: dict[str, str] = {}
    existing_names = tuple(invocation.artifact_store.manifest())
    total_bytes = 0
    missing: list[tuple[str, ArtifactKind]] = []
    validated_present: list[tuple[str, ArtifactKind]] = []
    active_by_id: dict[str, Artifact] | None = None
    if any(item.get("reuse_artifact_id") is not None for item in output_artifacts):
        if invocation.artifact_store.verify():
            raise ToolExecutionError("cannot verify artifacts for explicit Python output reuse")
        active_by_id = {
            artifact.id: artifact
            for artifact in invocation.artifact_store.list_active()
            if artifact.valid
        }
    bookkeeping_paths = {manifest_path.replace("\\", "/")} if manifest_path else set()
    for descriptor in output_artifacts:
        relative = str(descriptor["path"]).replace("\\", "/")
        candidate = Path(relative)
        if candidate.is_absolute() or PureWindowsPath(relative).is_absolute():
            raise ToolExecutionError("output artifact path must be workspace-relative")
        path = contained_path(workspace, relative)
        logical_name = path.relative_to(workspace).as_posix()
        try:
            canonical_name = canonical_artifact_name(logical_name)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        if canonical_name != logical_name:
            raise ToolExecutionError(
                f"output artifact path is not canonical: {logical_name!r} -> {canonical_name!r}"
            )
        if logical_name.casefold() == ".thymira" or logical_name.casefold().startswith(".thymira/"):
            raise ToolExecutionError("output artifacts may not be stored in .thymira/")
        if logical_name in seen:
            raise ToolExecutionError(f"output artifact declared more than once: {logical_name}")
        seen.add(logical_name)
        folded_name = logical_name.casefold()
        collision = seen_folded.get(folded_name)
        if collision is not None and collision != logical_name:
            raise ToolExecutionError(
                f"output artifact {logical_name!r} collides with {collision!r}"
            )
        existing_collision = next(
            (
                existing
                for existing in existing_names
                if existing.casefold() == folded_name and existing != logical_name
            ),
            None,
        )
        if existing_collision is not None:
            raise ToolExecutionError(
                f"output artifact {logical_name!r} collides with {existing_collision!r}"
            )
        seen_folded[folded_name] = logical_name
        assert_no_link_components(workspace, path)
        declared_kind = descriptor.get("kind", ArtifactKind.OTHER)
        if not isinstance(declared_kind, ArtifactKind):
            raise ToolExecutionError("output artifact kind is invalid")
        if not path.is_file():
            missing.append((logical_name, declared_kind))
            continue
        try:
            media_type = resolve_artifact_media_type(logical_name, descriptor.get("media_type"))
            validate_report_kind(logical_name, descriptor.get("kind", ArtifactKind.OTHER))
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        read_limit = artifact_read_limit(media_type)
        file_size = path.stat().st_size
        if file_size > read_limit:
            raise ToolExecutionError(
                f"declared output artifact {logical_name!r} exceeds {read_limit} bytes"
            )
        total_bytes += file_size
        if total_bytes > _MAX_OUTPUT_ARTIFACT_TOTAL_BYTES:
            raise ToolExecutionError(
                "declared output artifacts exceed the aggregate limit of "
                f"{_MAX_OUTPUT_ARTIFACT_TOTAL_BYTES} bytes"
            )
        with path.open("rb") as handle:
            data = handle.read(read_limit + 1)
        if len(data) > read_limit:
            raise ToolExecutionError(
                f"declared output artifact {logical_name!r} exceeds {read_limit} bytes"
            )
        try:
            validate_artifact_bytes(logical_name, data, media_type)
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc
        reuse_id = descriptor.get("reuse_artifact_id")
        reused: Artifact | None = None
        if reuse_id is not None:
            reused = active_by_id.get(reuse_id) if active_by_id is not None else None
            if (
                reused is None
                or reused.run_id != invocation.run_id
                or reused.name != logical_name
                or reused.kind is not declared_kind
                or reused.sha256 != hashlib.sha256(data).hexdigest()
                or invocation.artifact_store.load_bytes_bounded(reused.name, read_limit) != data
            ):
                raise ToolExecutionError(
                    f"reuse_artifact_id does not identify an active matching artifact in the "
                    f"same run "
                    f"for {logical_name!r}"
                )
        try:
            require_fresh_python_output(
                workspace,
                path,
                data,
                freshness_snapshot,
                copied_workspace=copied_workspace,
                explicitly_reused=reused is not None,
            )
        except (OSError, ValueError) as exc:
            raise ToolExecutionError(str(exc)) from exc
        attachments.append(
            ArtifactWrite(
                name=logical_name,
                data=data,
                kind=descriptor.get("kind", ArtifactKind.OTHER),
                media_type=media_type,
                input_artifact_ids=tuple(
                    dict.fromkeys(
                        (*input_artifact_ids, *((reused.id,) if reused is not None else ()))
                    )
                ),
            )
        )
        if logical_name not in bookkeeping_paths:
            validated_present.append((logical_name, declared_kind))
    if missing:
        present_paths = tuple(item.name for item in attachments)
        logical_missing = tuple(item for item in missing if item[0] not in bookkeeping_paths)
        if not logical_missing:
            raise ToolExecutionError("the output manifest could not be published atomically")
        raise MissingOutputArtifactsError(
            PythonPublicationProgress(
                missing=logical_missing,
                present=tuple(validated_present),
            ),
            present_paths,
        )
    artifacts = invocation.artifact_store.save_artifact_batch(
        attachments,
        produced_by=invocation.agent_id,
    )
    return tuple(artifact.id for artifact in artifacts)


def builtins_registry(
    *, sandbox: Sandbox | None = None, subprocess_mode: SandboxMode | None = None
) -> ToolRegistry:
    """Build the default local registry for the P3 built-in tools.

    ``read_file``, ``write_file`` and ``edit_file`` share one :class:`ReadLedger` -- constructed
    once here, per registry -- and ``write_file``/``edit_file`` are configured with the
    freshness policy turned *on* (F3.3): a write or edit to an existing file this run has not
    read since its last change is refused. Direct construction of these tools elsewhere (most of
    this repository's other tests) leaves the policy at its class-level default of *off*; this
    is the one production seam that turns it on.
    """
    backend = sandbox or LocalSubprocessSandbox()
    if subprocess_mode is not None and not isinstance(subprocess_mode, SandboxMode):
        raise TypeError("subprocess_mode must be a SandboxMode")
    read_mode = subprocess_mode or SandboxMode.READ_ONLY
    write_mode = subprocess_mode or SandboxMode.WORKSPACE_WRITE
    read_ledger = ReadLedger()
    freshness = FreshnessPolicy(require_read_before_edit=True)
    tools = tuple(
        cast("Tool", item)
        for item in (
            RunPython(sandbox=backend, mode=write_mode),
            ReadFile(ledger=read_ledger),
            WriteFile(ledger=read_ledger, freshness=freshness),
            EditFile(ledger=read_ledger, freshness=freshness),
            Glob(),
            Grep(),
            GitWorktreeCreate(sandbox=backend, mode=write_mode),
            GitWorktreeList(sandbox=backend, mode=read_mode),
            GitWorktreeRemove(sandbox=backend, mode=write_mode),
            ListFiles(),
            GitStatus(sandbox=backend, mode=read_mode),
            GitDiff(sandbox=backend, mode=read_mode),
            GitLog(sandbox=backend, mode=read_mode),
            GitCommit(sandbox=backend, mode=write_mode),
            AnalyzeDataset(),
            AuditModel(sandbox=backend, mode=write_mode),
            EvaluateModel(sandbox=backend, mode=write_mode),
            CompareModels(),
            ProfileDataset(),
            QuerySql(),
            RunStatistics(),
            QueryMlflow(),
            RunExperiment(sandbox=backend, mode=write_mode),
            InspectModel(sandbox=backend, mode=write_mode),
            ExportPdf(),
            RunNotebook(sandbox=backend, mode=write_mode),
            *mlflow_tools(),
        )
    )
    return ToolRegistry(tools)


__all__ = [
    "PythonExecutionValue",
    "RunPython",
    "RunPythonArguments",
    "RunPythonOutputArtifact",
    "builtins_registry",
]
