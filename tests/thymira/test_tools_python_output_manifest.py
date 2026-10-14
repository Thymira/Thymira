"""Python publishes the outputs its script enumerates; real stores, fake sandbox boundary."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from tests.thymira.test_tools_python_source_reference import _CapturingSandbox, _context, _manager
from thymira.schemas import ArtifactKind, EventType, new_id
from thymira.state import LocalArtifactStore
from thymira.tools.builtins import python_output_freshness
from thymira.tools.builtins.python_arguments import RunPythonOutputArtifact
from thymira.tools.builtins.run_python import PythonExecutionValue, _publish_output_artifacts
from thymira.tools.models import ToolExecutionError, ToolInvocation
from thymira.tools.sandbox.base import WorkspaceQuotaEvidence

if TYPE_CHECKING:
    from pathlib import Path

    from thymira.tools.sandbox import SandboxRun


def test_python_publishes_script_manifest_without_guessing_categorical_outputs(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    manifest = [{"path": "numeric.txt", "kind": "other"}]
    sandbox = _CapturingSandbox(
        outputs={
            "numeric.txt": b"Only the selected numeric column was plotted",
            "outputs.json": json.dumps(manifest).encode(),
        }
    )
    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('write outputs and a manifest')",
            "description": "Publish the generated outputs",
            "output_manifest_path": "outputs.json",
        },
    )
    assert execution.result.success, execution.result.error
    assert context.artifact_store.get("numeric.txt") is not None
    assert context.artifact_store.get("categorical.txt") is None
    assert context.artifact_store.get("outputs.json") is not None
    assert isinstance(execution.result.value, PythonExecutionValue)
    assert "numeric.txt" in execution.result.value.text
    assert context.artifact_store.verify() == []


def test_publication_error_names_successful_execution_and_missing_declarations(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    sandbox = _CapturingSandbox(outputs={"numeric.txt": b"generated"})
    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('done')",
            "description": "Generate numeric plots",
            "output_artifacts": [
                {"path": "numeric.txt"},
                {"path": "category.txt"},
                {"path": "other-category.txt"},
            ],
        },
    )
    assert not execution.result.success
    assert execution.result.exit_code == 0
    assert execution.result.error is not None
    assert "Python execution succeeded" in execution.result.error
    assert "category.txt" in execution.result.error
    assert "other-category.txt" in execution.result.error
    assert "numeric.txt" in execution.result.error
    assert "output declarations" in execution.result.error
    assert context.artifact_store.get("numeric.txt") is None


@pytest.mark.parametrize(
    "manifest",
    [
        b"not json",
        b"{}",
        b"[]",
        b'[{"path":"../outside.txt"}]',
        b'[{"path":".thymira/private.txt"}]',
        b'[{"path":".THYMIRA/private.txt"}]',
    ],
)
def test_output_manifest_rejects_invalid_or_uncontained_entries(
    tmp_path: Path, manifest: bytes
) -> None:
    context = _context(tmp_path)
    sandbox = _CapturingSandbox(outputs={"outputs.json": manifest})
    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('done')",
            "description": "Validate generated manifest",
            "output_manifest_path": "outputs.json",
        },
    )
    assert not execution.result.success
    assert len(sandbox.staged_inputs) == 1
    if b".THYMIRA" in manifest:
        assert "may not be stored in .thymira/" in (execution.result.error or "")
    assert list(context.artifact_store.list_active()) == []


def test_existing_output_manifest_is_rejected_before_approval_or_execution(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.workspace.mkdir(parents=True)
    (context.workspace / "outputs.json").write_text("[]", encoding="utf-8")
    sandbox = _CapturingSandbox()
    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('done')",
            "description": "Reject stale output manifest",
            "output_manifest_path": "outputs.json",
        },
    )
    assert not execution.result.success
    assert "already exists" in (execution.result.error or "")
    assert sandbox.staged_inputs == []
    assert not any(
        e.type in {EventType.POLICY_DECISION, EventType.TOOL_STARTED}
        for e in context.event_log.events()
    )


def test_fresh_manifest_does_not_claim_preexisting_unregistered_output(tmp_path: Path) -> None:
    context = _context(tmp_path)
    preexisting = context.workspace / "reports/summary.txt"
    preexisting.parent.mkdir(parents=True)
    preexisting.write_bytes(b"old workspace bytes\n")
    sandbox = _CapturingSandbox(
        outputs={
            "outputs.json": json.dumps([{"path": "reports/summary.txt", "kind": "other"}]).encode()
        }
    )

    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('write a fresh manifest')",
            "description": "Reject an output that predates this execution",
            "output_manifest_path": "outputs.json",
        },
    )

    assert not execution.result.success
    assert context.artifact_store.get("reports/summary.txt") is None
    assert context.artifact_store.get("outputs.json") is None
    assert preexisting.read_bytes() == b"old workspace bytes\n"


def test_manifest_explicit_reuse_records_original_and_source_lineage(tmp_path: Path) -> None:
    context = _context(tmp_path)
    source = context.artifact_store.save_text(
        "analysis.py",
        "print('registered analysis')\n",
        produced_by=context.agent_id,
        kind=ArtifactKind.CODE,
        media_type="text/x-python",
    )
    output_bytes = b"previously registered output\n"
    previous = context.artifact_store.save_bytes(
        "reports/summary.txt",
        output_bytes,
        produced_by=context.agent_id,
        kind=ArtifactKind.OTHER,
        media_type="text/plain",
    )
    context.workspace.mkdir(parents=True)
    output_path = context.workspace / "reports/summary.txt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(output_bytes)
    sandbox = _CapturingSandbox(
        outputs={
            "outputs.json": json.dumps(
                [
                    {
                        "path": "reports/summary.txt",
                        "kind": "other",
                        "reuse_artifact_id": previous.id,
                    }
                ]
            ).encode()
        }
    )

    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "source_artifact_id": source.id,
            "description": "Reuse the verified prior output",
            "output_manifest_path": "outputs.json",
        },
    )

    assert execution.result.success
    published = context.artifact_store.get("reports/summary.txt")
    assert published is not None
    assert published.id != previous.id
    assert published.input_artifact_ids == (source.id, previous.id)
    completed = [
        event for event in context.event_log.events() if event.type is EventType.TOOL_COMPLETED
    ][-1]
    assert completed.payload["output_declarations"] == [
        {
            "path": "reports/summary.txt",
            "kind": "other",
            "reuse_artifact_id": previous.id,
        }
    ]
    assert "outputs.json" not in {item["path"] for item in completed.payload["output_declarations"]}


def test_manifest_rejects_reuse_id_for_different_active_output(tmp_path: Path) -> None:
    context = _context(tmp_path)
    output_bytes = b"registered output bytes\n"
    output = context.artifact_store.save_bytes(
        "reports/summary.txt",
        output_bytes,
        produced_by=context.agent_id,
        kind=ArtifactKind.OTHER,
        media_type="text/plain",
    )
    unrelated = context.artifact_store.save_bytes(
        "reports/other.txt",
        b"different bytes\n",
        produced_by=context.agent_id,
        kind=ArtifactKind.OTHER,
        media_type="text/plain",
    )
    context.workspace.mkdir(parents=True)
    output_path = context.workspace / "reports/summary.txt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(output_bytes)
    sandbox = _CapturingSandbox(
        outputs={
            "outputs.json": json.dumps(
                [
                    {
                        "path": "reports/summary.txt",
                        "kind": "other",
                        "reuse_artifact_id": unrelated.id,
                    }
                ]
            ).encode()
        }
    )

    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('preserve old output')",
            "description": "Reject unrelated output reuse",
            "output_manifest_path": "outputs.json",
        },
    )

    assert not execution.result.success
    assert "reuse_artifact_id" in (execution.result.error or "")
    assert context.artifact_store.get("reports/summary.txt") == output
    assert context.artifact_store.get("outputs.json") is None
    assert output_path.read_bytes() == output_bytes


@pytest.mark.parametrize("mismatch", ["kind", "digest", "stored_bytes"])
def test_manifest_reuse_rejects_kind_digest_and_byte_mismatches_after_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mismatch: str
) -> None:
    context = _context(tmp_path)
    original_bytes = b"registered output bytes\n"
    previous = context.artifact_store.save_bytes(
        "reports/summary.txt",
        original_bytes,
        produced_by=context.agent_id,
        kind=ArtifactKind.CODE if mismatch == "kind" else ArtifactKind.OTHER,
        media_type="text/plain",
    )
    output_path = context.workspace / "reports/summary.txt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(original_bytes)
    current_bytes = b"new workspace output bytes\n" if mismatch == "digest" else original_bytes
    if mismatch == "stored_bytes":
        monkeypatch.setattr(
            context.artifact_store,
            "load_bytes_bounded",
            lambda _name, _limit: b"different stored bytes\n",
        )
    sandbox = _CapturingSandbox(
        outputs={
            "reports/summary.txt": current_bytes,
            "outputs.json": json.dumps(
                [
                    {
                        "path": "reports/summary.txt",
                        "kind": "other",
                        "reuse_artifact_id": previous.id,
                    }
                ]
            ).encode(),
        }
    )

    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('validate the reused output at publication')",
            "description": "Reject mismatched explicit output reuse",
            "output_manifest_path": "outputs.json",
        },
    )

    events = context.event_log.events()
    assert not execution.result.success
    assert "reuse_artifact_id" in (execution.result.error or "")
    assert any(event.type is EventType.TOOL_STARTED for event in events)
    assert sandbox.staged_inputs
    assert context.artifact_store.get("reports/summary.txt") == previous
    assert context.artifact_store.get("outputs.json") is None
    assert context.event_log.verify().valid


def test_manifest_reuse_rejects_active_artifact_from_another_run(tmp_path: Path) -> None:
    foreign_run_id = new_id("run")
    store = LocalArtifactStore(tmp_path / "foreign-artifacts", foreign_run_id)
    reused_bytes = b"verified foreign-run bytes\n"
    previous = store.save_bytes(
        "reports/summary.txt",
        reused_bytes,
        produced_by=new_id("agent"),
        kind=ArtifactKind.OTHER,
        media_type="text/plain",
    )
    workspace = tmp_path / "workspace"
    output = workspace / "reports/summary.txt"
    output.parent.mkdir(parents=True)
    output.write_bytes(reused_bytes)
    invocation = ToolInvocation(
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        workspace=workspace,
        artifact_store=store,
    )

    with pytest.raises(ToolExecutionError, match="same run"):
        _publish_output_artifacts(
            invocation,
            (
                RunPythonOutputArtifact(
                    path="reports/summary.txt",
                    kind=ArtifactKind.OTHER,
                    reuse_artifact_id=previous.id,
                ).model_dump(mode="python"),
            ),
            freshness_snapshot=python_output_freshness.capture_python_output_freshness(
                workspace, None
            ),
            copied_workspace=False,
            manifest_path=None,
        )

    assert store.get("reports/summary.txt") == previous


def test_quota_workspace_copy_metadata_does_not_prove_same_byte_output_was_written(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    output_bytes = b"old unchanged bytes\n"
    context.workspace.mkdir(parents=True)
    output_path = context.workspace / "reports/summary.txt"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(output_bytes)
    quota = WorkspaceQuotaEvidence(
        mechanism="docker_local_tmpfs_volume",
        requested_bytes=1_048_576,
        observed_capacity_bytes=None,
        observed_filesystem=None,
        inode_limit=64,
        observed_inode_capacity=None,
        source_logical_bytes=None,
        staged_input_logical_bytes=None,
        final_logical_bytes=None,
        exported_logical_bytes=None,
        source_entries=None,
        staged_input_entries=None,
        final_entries=None,
        exported_entries=None,
        source_tree_sha256=None,
        staged_input_tree_sha256=None,
        final_tree_sha256=None,
        exported_tree_sha256=None,
        volume_inspected=False,
        worker_mount_inspected=False,
        publication="committed",
        cleanup={},
    )

    class _QuotaCopyingSandbox(_CapturingSandbox):
        def run(self, argv: list[str], **kwargs: object) -> SandboxRun:
            run = super().run(argv, **kwargs)
            return replace(run, quota_evidence=quota)

    sandbox = _QuotaCopyingSandbox(
        outputs={
            "reports/summary.txt": output_bytes,
            "outputs.json": json.dumps([{"path": "reports/summary.txt", "kind": "other"}]).encode(),
        }
    )
    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('copied workspace output')",
            "description": "Reject unchanged bytes from a quota workspace copy",
            "output_manifest_path": "outputs.json",
        },
    )

    assert not execution.result.success
    assert "predates this execution" in (execution.result.error or "")
    assert context.artifact_store.get("reports/summary.txt") is None
    assert context.artifact_store.get("outputs.json") is None
    assert output_path.read_bytes() == output_bytes


def test_fixed_output_witness_failure_cannot_promote_unwitnessed_prior_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = _context(tmp_path)
    context.workspace.mkdir(parents=True)
    prior = context.workspace / "reports/summary.txt"
    prior.parent.mkdir(parents=True)
    prior.write_bytes(b"preexisting bytes\n")
    original_witness = python_output_freshness._file_witness
    failures = 0

    def fail_first_witness(workspace: Path, path: Path):
        nonlocal failures
        if path == prior and failures == 0:
            failures += 1
            raise PermissionError("simulated transient witness failure")
        return original_witness(workspace, path)

    monkeypatch.setattr(python_output_freshness, "_file_witness", fail_first_witness)
    execution = _manager(
        _CapturingSandbox(outputs={"reports/summary.txt": b"new bytes\n"})
    ).execute(
        context,
        "run_python",
        {
            "code": "print('rewrite the declared file')",
            "description": "Exercise fail-closed pre-run freshness evidence",
            "output_artifacts": [{"path": "reports/summary.txt", "kind": "other"}],
        },
    )

    assert failures == 1
    assert not execution.result.success
    assert "could not verify whether declared output" in (execution.result.error or "")
    assert context.artifact_store.get("reports/summary.txt") is None
    assert prior.read_bytes() == b"new bytes\n"


@pytest.mark.parametrize(
    "arguments",
    [
        {"output_manifest_path": "outputs.json", "output_artifacts": [{"path": "one.txt"}]},
        {"output_manifest_path": "../outputs.json"},
        {"output_manifest_path": ".thymira/outputs.json"},
        {"output_manifest_path": ".THYMIRA/outputs.json"},
        {"output_manifest_path": "outputs.txt"},
        {"output_manifest_path": "C:/outputs.json"},
    ],
)
def test_manifest_contract_is_rejected_before_gate(
    tmp_path: Path, arguments: dict[str, object]
) -> None:
    context = _context(tmp_path)
    sandbox = _CapturingSandbox()
    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('done')",
            "description": "Reject invalid manifest declaration",
            **arguments,
        },
    )
    assert not execution.result.success
    assert sandbox.staged_inputs == []
    assert not any(
        e.type in {EventType.POLICY_DECISION, EventType.TOOL_STARTED}
        for e in context.event_log.events()
    )


@pytest.mark.parametrize(
    "manifest",
    [
        b" " * 65537,
        json.dumps([{"path": f"{i}.txt"} for i in range(65)]).encode(),
        b'[{"path":"outputs.json"}]',
        b'[{"path":"one.txt"},{"path":"one.txt"}]',
    ],
    ids=["oversized-json", "too-many-outputs", "self-reference", "duplicate-output"],
)
def test_output_manifest_is_bounded_and_atomic(tmp_path: Path, manifest: bytes) -> None:
    context = _context(tmp_path)
    sandbox = _CapturingSandbox(outputs={"outputs.json": manifest, "one.txt": b"one"})
    execution = _manager(sandbox).execute(
        context,
        "run_python",
        {
            "code": "print('done')",
            "description": "Reject invalid output set",
            "output_manifest_path": "outputs.json",
        },
    )
    assert not execution.result.success
    assert list(context.artifact_store.list_active()) == []
