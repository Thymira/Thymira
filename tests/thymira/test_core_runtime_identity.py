"""Synthetic regressions for immutable process/runtime identity evidence."""

from __future__ import annotations

import importlib.metadata
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import BaseModel

from thymira.core import RunService, SessionService
from thymira.events import sha256_text
from thymira.schemas import Actor, EventType, new_id
from thymira.state import LocalRunStore, LocalSessionRepository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from thymira.core.dispatch import GraphFactory
    from thymira.schemas import Run


class OutputV1(BaseModel):
    """First effective structured-output schema."""

    result: str


class OutputV2(BaseModel):
    """Changed effective structured-output schema."""

    result: str
    count: int


def _loaded_marker() -> str:
    """Provide one deterministic synthetic code object for runtime observations."""
    return "loaded at startup"


_SYNTHETIC_MODULE = ModuleType("thymira.synthetic_identity")
_loaded_marker.__module__ = _SYNTHETIC_MODULE.__name__
_SYNTHETIC_MODULE.__dict__["loaded_marker"] = _loaded_marker


def _exec_synthetic(module: ModuleType, source: str, *, filename: str | None = None) -> None:
    """Execute test-owned module source for reload and rebinding regressions."""
    compiled = compile(source, filename or f"<{module.__name__}>", "exec")
    exec(compiled, vars(module))  # noqa: S102 -- synthetic test code only


@dataclass(frozen=True)
class SyntheticDistribution:
    """Small installed-distribution view backed by test-owned files."""

    root: Path
    files: Sequence[Path]

    def locate_file(self, path: Path) -> Path:
        """Resolve an installed file beneath this synthetic distribution root."""
        return self.root / path

    @property
    def version(self) -> str:
        """Return a deterministic package version."""
        return "1.0"


def _source_tree(root: Path) -> Path:
    """Create a synthetic installed Thymira package with code and prompt resources."""
    package = root / "thymira"
    (package / "core").mkdir(parents=True)
    (package / "prompts").mkdir()
    (package / "core" / "runs.py").write_text("RUN_LIMIT = 3\n", encoding="utf-8")
    (package / "prompts" / "planner.md").write_text("Plan safely.\n", encoding="utf-8")
    return package


def _capture(
    package: Path,
    *,
    generation_id: str = "boot-a",
    modules: tuple[ModuleType, ...] = (_SYNTHETIC_MODULE,),
    distributions: tuple[str, ...] = (),
    schemas: tuple[type[BaseModel], ...] = (),
    catalog_snapshot: dict[str, object] | None = None,
):
    """Capture one identity from entirely synthetic runtime inputs."""
    from thymira.core.runtime_identity import capture_runtime_identity

    return capture_runtime_identity(
        source_roots=(package,),
        modules=modules,
        distributions=distributions,
        schemas=schemas,
        catalog_snapshot=catalog_snapshot or {},
        generation_id=generation_id,
    )


def test_run_started_links_the_process_identity_without_rehashing_the_workspace(
    tmp_path: Path,
) -> None:
    store = LocalRunStore(tmp_path / "runs")
    sessions = SessionService(LocalSessionRepository(tmp_path))
    service = RunService(
        store,
        sessions,
        policy_sha256=sha256_text("synthetic policy"),
        graph_definition_hash=sha256_text("synthetic graph"),
    )
    session = sessions.create(project_id=new_id("project"), client="cli")

    run = service.create_run(
        session.id,
        "Inspect the synthetic project.",
        actor=Actor.system(),
        workspace=tmp_path,
        dispatch=False,
    )
    started = next(event for event in store.events(run.id) if event.type is EventType.RUN_STARTED)

    runtime_identity = started.payload["run_environment"]["runtime_identity"]
    assert runtime_identity["generation_id"]
    assert runtime_identity["fingerprint_sha256"] is None
    assert any(
        "catalog snapshot was not supplied" in reason
        for reason in runtime_identity["unavailable_reasons"]
    )


def test_lifecycle_publication_keeps_identity_out_of_idempotency_binding(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import capture_runtime_identity
    from thymira.state import LocalLifecycleRepository

    root = tmp_path / "lifecycle"
    store = LocalRunStore(root / "runs")
    session_repository = LocalSessionRepository(root / "repositories")
    lifecycle = LocalLifecycleRepository(
        root,
        run_store=store,
        session_repository=session_repository,
        authority_verifier=lambda _command: True,
    )
    first_identity = capture_runtime_identity(catalog_snapshot={}, distributions=())
    service = RunService(
        store,
        SessionService(session_repository),
        policy_sha256=sha256_text("synthetic policy"),
        graph_definition_hash=sha256_text("synthetic graph"),
        lifecycle_repository=lifecycle,
        runtime_identity=first_identity,
    )
    project_id = new_id("project")
    run, _receipt = service.create_run_published(
        None,
        "Published runtime identity.",
        actor=Actor.system(),
        workspace=tmp_path,
        project_id=project_id,
        session_client="test",
        idempotency_key="same-request",
        dispatch=False,
    )

    second_identity = capture_runtime_identity(catalog_snapshot={}, distributions=())
    reopened_store = LocalRunStore(root / "runs")
    reopened_sessions = LocalSessionRepository(root / "repositories")
    reconstructed = RunService(
        reopened_store,
        SessionService(reopened_sessions),
        policy_sha256=sha256_text("synthetic policy"),
        graph_definition_hash=sha256_text("synthetic graph"),
        lifecycle_repository=LocalLifecycleRepository(
            root,
            run_store=reopened_store,
            session_repository=reopened_sessions,
            authority_verifier=lambda _command: True,
        ),
        runtime_identity=second_identity,
    )
    retry, _retry_receipt = reconstructed.create_run_published(
        None,
        "Published runtime identity.",
        actor=Actor.system(),
        workspace=tmp_path,
        project_id=project_id,
        session_client="test",
        idempotency_key="same-request",
        dispatch=False,
    )

    events = store.events(run.id)
    started = next(event for event in events if event.type is EventType.RUN_STARTED)
    assert retry.id == run.id
    assert (
        started.payload["run_environment"]["runtime_identity"]["generation_id"]
        == first_identity.generation_id
    )
    assert not any(event.type is EventType.RUNTIME_ATTACHED for event in events)


def test_worker_attaches_runtime_through_live_owner_after_factory(
    tmp_path: Path, monkeypatch
) -> None:
    from thymira.core import LifecycleGraphExecutor, LiveOwnerContext
    from thymira.core.runtime_identity import capture_runtime_identity
    from thymira.events import verify_events
    from thymira.schemas import EventSurface, Run, Session, WorkItem
    from thymira.state import LocalLifecycleRepository

    root = tmp_path / "worker"
    store = LocalRunStore(root / "runs")
    session_repository = LocalSessionRepository(root / "repositories")
    repository = LocalLifecycleRepository(
        root,
        run_store=store,
        session_repository=session_repository,
        authority_verifier=lambda _command: True,
    )
    startup_identity = capture_runtime_identity(catalog_snapshot={}, distributions=())
    session = Session(id=new_id("session"), project_id=new_id("project"), client="worker-test")
    run = Run(
        id=new_id("run"),
        project_id=session.project_id,
        session_id=session.id,
        prompt="Record worker runtime identity.",
    )
    work = WorkItem(
        run_id=run.id,
        ordinal=0,
        kind="run.execute",
        idempotency_key=f"runtime-identity-{run.id}",
    )
    prepared = repository.prepare_publication(
        session,
        run,
        work,
        creation_payload={"run_environment": {"runtime_identity": startup_identity.to_json_dict()}},
        creation_actor=Actor.system(),
    )
    publication_owner = repository.commit_publication(prepared)
    publication_owner.close()
    owner_token = repository.acquire_owner(run.id, "runtime-identity-worker")
    assert owner_token is not None
    live_owner = repository.run_handle(owner_token)
    continuation_identity = capture_runtime_identity(catalog_snapshot={}, distributions=())
    module = ModuleType("thymira._worker_runtime_identity_lazy_test")

    class Graph:
        def invoke(self, _state: object, _config: object, *, durability: str) -> dict[str, object]:
            assert durability == "sync"
            events = store.events(run.id)
            started = next(event for event in events if event.type is EventType.RUN_STARTED)
            attached = next(event for event in events if event.type is EventType.RUNTIME_ATTACHED)
            assert attached.surface is EventSurface.LOG_ONLY
            assert attached.payload["runtime_observation"]["state"] == "changed_or_late_imports"
            assert attached.payload["run_started"] == {"seq": started.seq, "hash": started.hash}
            return {}

    def graph_factory(_run: Run) -> Graph:
        _exec_synthetic(module, "def loaded_late(): return 'worker build'\n")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return Graph()

    executor = LifecycleGraphExecutor(
        repository,
        store,
        cast("GraphFactory", graph_factory),
        LiveOwnerContext(),
        runtime_identity=continuation_identity,
    )
    try:
        result = executor.execute(work, live_owner)
    finally:
        live_owner.close()

    events = store.events(run.id)
    attached = next(event for event in events if event.type is EventType.RUNTIME_ATTACHED)
    assert result.kind.value == "succeeded", attached.payload["runtime_observation"]
    assert (
        attached.payload["runtime_identity"]["generation_id"] == continuation_identity.generation_id
    )
    assert verify_events(events).valid


def test_reconstructed_resume_preserves_creation_identity_and_attaches_after_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thymira.core import InlineDispatcher, MiraControlPlane, RunController, RunTransitionKind
    from thymira.core.runtime_identity import capture_runtime_identity
    from thymira.policies import PolicyEngine, load_default_policy
    from thymira.schemas import ActionIntent, ActionKind, ActorKind, EventSurface

    store = LocalRunStore(tmp_path / "runs")
    sessions = SessionService(LocalSessionRepository(tmp_path))
    first_identity = capture_runtime_identity(catalog_snapshot={}, distributions=())
    first_service = RunService(
        store,
        sessions,
        policy_sha256=sha256_text("synthetic policy"),
        graph_definition_hash=sha256_text("synthetic graph"),
        runtime_identity=first_identity,
    )
    session = sessions.create(project_id=new_id("project"), client="cli")
    run = first_service.create_run(
        session.id,
        "Inspect the synthetic project.",
        actor=Actor.system(),
        workspace=tmp_path,
        dispatch=False,
    )
    controller = RunController(store)
    controller.advance(run.id, RunTransitionKind.START)
    controller.advance(run.id, RunTransitionKind.BEGIN_EXECUTION)
    MiraControlPlane(store, PolicyEngine(load_default_policy())).handle(
        ActionIntent(
            id=new_id("intent"),
            run_id=run.id,
            requester=Actor(kind=ActorKind.AGENT, id="mira", authenticated=True),
            action_kind=ActionKind.PAUSE_RUN,
            subject_kind="run",
            subject_id=run.id,
            purpose="Pause before reconstructed resume.",
            idempotency_key=new_id("intent"),
        )
    )
    continuation_identity = capture_runtime_identity(catalog_snapshot={}, distributions=())
    module = ModuleType("thymira._runtime_identity_lazy_test")
    reopened_store = LocalRunStore(tmp_path / "runs")
    reopened_sessions = SessionService(LocalSessionRepository(tmp_path))

    class Graph:
        def get_state(self, _config: object) -> SimpleNamespace:
            return SimpleNamespace(values={"run_id": run.id}, next=())

        def update_state(self, _config: object, _values: object, *, as_node: str) -> None:
            assert as_node

        def invoke(self, _state: object, _config: object, *, durability: str) -> dict[str, object]:
            assert durability == "sync"
            events = reopened_store.events(run.id)
            attached = next(event for event in events if event.type is EventType.RUNTIME_ATTACHED)
            assert attached.payload["runtime_observation"]["state"] == "changed_or_late_imports"
            assert attached.surface is EventSurface.LOG_ONLY
            return {}

    def graph_factory(_run: Run) -> Graph:
        _exec_synthetic(module, "def loaded_late(): return 'same source'\n")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        return Graph()

    dispatcher = InlineDispatcher(
        reopened_store,
        cast("GraphFactory", graph_factory),
        runtime_identity=continuation_identity,
    )
    resumed_service = RunService(
        reopened_store,
        reopened_sessions,
        dispatcher=dispatcher,
        policy_sha256=sha256_text("synthetic policy"),
        graph_definition_hash=sha256_text("synthetic graph"),
        runtime_identity=continuation_identity,
    )
    resumed_service.resume(run.id, actor=Actor.system())

    events = reopened_store.events(run.id)
    started = next(event for event in events if event.type is EventType.RUN_STARTED)
    attached = next(event for event in events if event.type is EventType.RUNTIME_ATTACHED)
    assert (
        started.payload["run_environment"]["runtime_identity"]["generation_id"]
        == first_identity.generation_id
    )
    assert (
        attached.payload["runtime_identity"]["generation_id"] == continuation_identity.generation_id
    )
    assert attached.payload["run_started"] == {"seq": started.seq, "hash": started.hash}


def test_relevant_source_and_prompt_changes_change_the_next_boot_fingerprint(
    tmp_path: Path,
) -> None:
    package = _source_tree(tmp_path)
    initial = _capture(package)

    (package / "core" / "runs.py").write_text("RUN_LIMIT = 4\n", encoding="utf-8")
    changed_code = _capture(package)
    (package / "prompts" / "planner.md").write_text("Plan with evidence.\n", encoding="utf-8")
    changed_prompt = _capture(package)

    assert initial.fingerprint_sha256 != changed_code.fingerprint_sha256
    assert changed_code.fingerprint_sha256 != changed_prompt.fingerprint_sha256


def test_post_start_disk_edits_do_not_rewrite_the_boot_identity(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import observe_runtime_identity

    package = _source_tree(tmp_path)
    identity = _capture(package)
    source_layer = next(
        layer for layer in identity.layers if layer.name == "startup_source_snapshot"
    )
    before = observe_runtime_identity(identity, modules=(_SYNTHETIC_MODULE,), schemas=())

    (package / "core" / "runs.py").write_text("RUN_LIMIT = 999\n", encoding="utf-8")
    after = observe_runtime_identity(identity, modules=(_SYNTHETIC_MODULE,), schemas=())

    assert (
        next(layer for layer in identity.layers if layer.name == "startup_source_snapshot")
        == source_layer
    )
    assert before.state == after.state == "matches_startup"
    assert before.loaded_code_sha256 == after.loaded_code_sha256


def test_installed_namespace_ignores_client_roots_but_not_empty_runtime_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from thymira.core.runtime_identity import capture_runtime_identity

    core_root = tmp_path / "runtime" / "core" / "src" / "thymira"
    cli_root = tmp_path / "runtime" / "cli" / "src" / "thymira"
    web_root = tmp_path / "apps" / "web" / "src" / "thymira"
    (core_root / "core").mkdir(parents=True)
    (cli_root / "core").mkdir(parents=True)
    (web_root / "core").mkdir(parents=True)
    (core_root / "core" / "runs.py").write_text("RUN_LIMIT = 3\n", encoding="utf-8")
    (cli_root / "core" / "client.py").write_text("CLIENT = True\n", encoding="utf-8")
    (web_root / "core" / "client.py").write_text("CLIENT = True\n", encoding="utf-8")
    package = sys.modules["thymira"]
    monkeypatch.setattr(package, "__path__", [str(core_root), str(cli_root), str(web_root)])

    identity = capture_runtime_identity(
        modules=(_SYNTHETIC_MODULE,), distributions=(), schemas=(), catalog_snapshot={}
    )
    source_layer = next(
        layer for layer in identity.layers if layer.name == "startup_source_snapshot"
    )
    assert source_layer.status == "verified"
    assert source_layer.item_count == 1

    (core_root / "core" / "runs.py").unlink()
    missing = capture_runtime_identity(
        modules=(_SYNTHETIC_MODULE,), distributions=(), schemas=(), catalog_snapshot={}
    )
    missing_source = next(
        layer for layer in missing.layers if layer.name == "startup_source_snapshot"
    )
    assert missing_source.status == "unavailable"


def test_source_enumeration_order_does_not_change_the_fingerprint(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import capture_runtime_identity

    first = tmp_path / "first" / "thymira"
    second = tmp_path / "second" / "thymira"
    (first / "core").mkdir(parents=True)
    (second / "core").mkdir(parents=True)
    (first / "core" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (second / "core" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")

    left = capture_runtime_identity(
        source_roots=(first, second),
        modules=(_SYNTHETIC_MODULE,),
        distributions=(),
        schemas=(),
        catalog_snapshot={},
        generation_id="boot-a",
    )
    right = capture_runtime_identity(
        source_roots=(second, first),
        modules=(_SYNTHETIC_MODULE,),
        distributions=(),
        schemas=(),
        catalog_snapshot={},
        generation_id="boot-a",
    )

    assert left.fingerprint_sha256 == right.fingerprint_sha256
    assert left.fingerprint_sha256 is not None


def test_incomplete_source_snapshot_is_not_reported_as_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _source_tree(tmp_path)
    (package / "core" / "readable.py").write_text("VALUE = 1\n", encoding="utf-8")
    blocked = package / "core" / "blocked.py"
    blocked.write_text("VALUE = 2\n", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == blocked:
            raise PermissionError("synthetic unreadable source")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    identity = _capture(package)
    source_layer = next(
        layer for layer in identity.layers if layer.name == "startup_source_snapshot"
    )

    assert source_layer.status == "unavailable"
    assert source_layer.sha256 is None
    assert identity.fingerprint_sha256 is None


def test_secret_dataset_artifact_and_history_changes_are_excluded(tmp_path: Path) -> None:
    package = _source_tree(tmp_path)
    before = _capture(package)
    (package / ".env").write_text("API_KEY=secret-before\n", encoding="utf-8")
    (package / "data").mkdir()
    (package / "data" / "dataset.csv").write_text("private,before\n", encoding="utf-8")
    (package / "artifacts").mkdir()
    (package / "artifacts" / "result.json").write_text("private-before\n", encoding="utf-8")
    (package / "history.jsonl").write_text("user history before\n", encoding="utf-8")
    for excluded in (".aws", "secrets", "runs", "history", "artifacts"):
        path = package / excluded / "ignored.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"PRIVATE = {excluded!r}\n", encoding="utf-8")
    after = _capture(package)

    assert before.fingerprint_sha256 == after.fingerprint_sha256
    assert "secret-before" not in str(after.to_json_dict())
    assert "private,before" not in str(after.to_json_dict())


def test_effective_catalog_snapshot_and_schema_are_fingerprinted_separately(
    tmp_path: Path,
) -> None:
    package = _source_tree(tmp_path)
    catalog: dict[str, object] = {"coding": {"prompt": "Use tools carefully."}}
    initial = _capture(package, schemas=(OutputV1,), catalog_snapshot=catalog)
    changed_catalog = _capture(
        package,
        schemas=(OutputV1,),
        catalog_snapshot={"coding": {"prompt": "Use tools carefully and exactly."}},
    )
    changed_schema = _capture(package, schemas=(OutputV2,), catalog_snapshot=catalog)

    assert initial.fingerprint_sha256 != changed_catalog.fingerprint_sha256
    assert initial.fingerprint_sha256 != changed_schema.fingerprint_sha256
    assert changed_catalog.fingerprint_sha256 != changed_schema.fingerprint_sha256


def test_installed_serializer_distribution_content_changes_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _source_tree(tmp_path / "source")
    installed = tmp_path / "site-packages"
    transformer = installed / "litellm" / "schema_adapter.py"
    metadata = installed / "litellm-1.0.dist-info" / "METADATA"
    transformer.parent.mkdir(parents=True)
    metadata.parent.mkdir()
    transformer.write_text("def transform(value): return value\n", encoding="utf-8")
    metadata.write_text("Name: litellm\nVersion: 1.0\n", encoding="utf-8")
    distribution = SyntheticDistribution(
        installed,
        (Path("litellm/schema_adapter.py"), Path("litellm-1.0.dist-info/METADATA")),
    )
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: distribution)

    before = _capture(package, distributions=("litellm",))
    transformer.write_text("def transform(value): return normalize(value)\n", encoding="utf-8")
    after = _capture(package, distributions=("litellm",))

    assert before.fingerprint_sha256 != after.fingerprint_sha256


@pytest.mark.parametrize(
    ("distribution_name", "package_file", "metadata_dir"),
    [
        ("litellm", "litellm/schema_adapter.py", "litellm-1.0.dist-info"),
        ("openai", "openai/lib/_pydantic.py", "openai-1.0.dist-info"),
        ("pydantic-core", "pydantic_core/core.py", "pydantic_core-1.0.dist-info"),
        (
            "typing-inspection",
            "typing_inspection/introspection.py",
            "typing_inspection-1.0.dist-info",
        ),
        ("typing-extensions", "typing_extensions.py", "typing_extensions-1.0.dist-info"),
    ],
)
def test_transformer_distribution_content_changes_fingerprint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    distribution_name: str,
    package_file: str,
    metadata_dir: str,
) -> None:
    package = _source_tree(tmp_path / "source")
    installed = tmp_path / "site-packages"
    code = installed / package_file
    metadata = installed / metadata_dir / "METADATA"
    code.parent.mkdir(parents=True, exist_ok=True)
    metadata.parent.mkdir(parents=True)
    code.write_text("VERSION = 1\n", encoding="utf-8")
    metadata.write_text(f"Name: {distribution_name}\nVersion: 1.0\n", encoding="utf-8")
    distribution = SyntheticDistribution(
        installed,
        (Path(package_file), Path(metadata_dir) / "METADATA"),
    )
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: distribution)

    before = _capture(package, distributions=(distribution_name,))
    assert before.fingerprint_sha256 is not None
    code.write_text("VERSION = 2\n", encoding="utf-8")
    after = _capture(package, distributions=(distribution_name,))

    assert before.fingerprint_sha256 != after.fingerprint_sha256


def test_distribution_metadata_without_package_files_is_not_verified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _source_tree(tmp_path / "source")
    installed = tmp_path / "site-packages"
    metadata = installed / "litellm-1.0.dist-info" / "METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("Name: litellm\nVersion: 1.0\n", encoding="utf-8")
    distribution = SyntheticDistribution(
        installed,
        (Path("litellm-1.0.dist-info/METADATA"),),
    )
    monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: distribution)
    identity = _capture(package, distributions=("litellm",))

    assert identity.fingerprint_sha256 is None
    assert any("package code files" in reason for reason in identity.unavailable_reasons)


def test_unreadable_distribution_file_inventory_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _source_tree(tmp_path / "source")

    class UnreadableDistribution:
        @property
        def files(self) -> None:
            raise PermissionError("synthetic inaccessible inventory")

    monkeypatch.setattr(importlib.metadata, "distribution", lambda _name: UnreadableDistribution())
    identity = _capture(package, distributions=("litellm",))

    assert identity.fingerprint_sha256 is None
    assert any("file inventory is unreadable" in reason for reason in identity.unavailable_reasons)


def test_generation_changes_on_restart_without_changing_same_build_fingerprint(
    tmp_path: Path,
) -> None:
    package = _source_tree(tmp_path)
    first = _capture(package, generation_id="boot-one")
    restarted = _capture(package, generation_id="boot-two")

    assert first.generation_id != restarted.generation_id
    assert first.fingerprint_sha256 == restarted.fingerprint_sha256


def test_reloaded_nested_code_changes_execution_observation(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import observe_runtime_identity

    package = _source_tree(tmp_path)
    module = ModuleType("thymira.reload_probe")
    _exec_synthetic(
        module,
        "def execute():\n"
        "    def inner():\n"
        "        return 'first', frozenset({'alpha', 'beta'})\n"
        "    return inner\n",
    )
    identity = _capture(package, modules=(module,), schemas=())
    initial_observation = observe_runtime_identity(identity, modules=(module,), schemas=())

    _exec_synthetic(
        module,
        "def execute():\n"
        "    def inner():\n"
        "        return 'second', frozenset({'alpha', 'gamma'})\n"
        "    return inner\n",
    )
    changed_observation = observe_runtime_identity(identity, modules=(module,), schemas=())

    assert initial_observation.state == "matches_startup"
    assert changed_observation.state == "changed_or_late_imports"
    assert (
        changed_observation.execution_generation_id != initial_observation.execution_generation_id
    )


def test_swapping_same_code_bindings_changes_observation(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import observe_runtime_identity

    package = _source_tree(tmp_path)
    module = ModuleType("thymira.binding_probe")
    _exec_synthetic(module, "def a(): return 'A'\ndef b(): return 'B'\nactive = a\ninactive = b\n")
    identity = _capture(package, modules=(module,), schemas=())
    before = observe_runtime_identity(identity, modules=(module,), schemas=())

    active = module.__dict__["active"]
    inactive = module.__dict__["inactive"]
    module.__dict__["active"] = inactive
    module.__dict__["inactive"] = active
    after = observe_runtime_identity(identity, modules=(module,), schemas=())

    assert before.state == "matches_startup"
    assert after.state == "changed_or_late_imports"


def test_foreign_module_function_rebinding_is_observed(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import observe_runtime_identity

    package = _source_tree(tmp_path)
    module = ModuleType("thymira.foreign_binding_probe")
    _exec_synthetic(module, "def active(): return 'A'\n")
    identity = _capture(package, modules=(module,), schemas=())
    before = observe_runtime_identity(identity, modules=(module,), schemas=())

    _exec_synthetic(module, "def replacement(): return 'B'\n")
    module.__dict__["replacement"].__module__ = "external_package.module"
    module.__dict__["active"] = module.__dict__["replacement"]
    after = observe_runtime_identity(identity, modules=(module,), schemas=())

    assert before.state == "matches_startup"
    assert after.state == "changed_or_late_imports"


def test_same_content_reload_changes_execution_generation_only(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import observe_runtime_identity

    package = _source_tree(tmp_path)
    module = ModuleType("thymira.same_content_reload")
    source = "def run():\n    return 'same build'\n"
    _exec_synthetic(module, source, filename="synthetic.py")
    identity = _capture(package, modules=(module,), schemas=())
    before = observe_runtime_identity(identity, modules=(module,), schemas=())

    observed_generations = {before.execution_generation_id}
    for _ in range(1000):
        _exec_synthetic(module, source, filename="synthetic.py")
        after = observe_runtime_identity(identity, modules=(module,), schemas=())
        observed_generations.add(after.execution_generation_id)

    assert (
        identity.fingerprint_sha256
        == _capture(
            package, modules=(module,), schemas=(), generation_id="boot-b"
        ).fingerprint_sha256
    )
    assert before.loaded_code_sha256 == after.loaded_code_sha256
    assert before.state == "matches_startup"
    assert after.state == "changed_or_late_imports"
    assert len(observed_generations) == 1001


def test_foreign_function_rebound_on_thymira_class_is_observed(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import observe_runtime_identity

    package = _source_tree(tmp_path)
    module = ModuleType("thymira.class_binding_probe")
    _exec_synthetic(
        module,
        "class External:\n"
        "    def first(self): return 'first'\n"
        "    def changed(self): return 'changed'\n"
        "class Owned:\n"
        "    run = staticmethod(External.first)\n",
    )
    external = module.__dict__["External"]
    external.first.__module__ = "external_package.module"
    external.changed.__module__ = "external_package.module"
    identity = _capture(package, modules=(module,), schemas=())
    before = observe_runtime_identity(identity, modules=(module,), schemas=())

    module.__dict__["Owned"].run = staticmethod(module.__dict__["External"].changed)
    after = observe_runtime_identity(identity, modules=(module,), schemas=())

    assert before.state == "matches_startup"
    assert after.state == "changed_or_late_imports"


@pytest.mark.parametrize(
    "descriptor",
    [
        "staticmethod(len)",
        "classmethod(dict.fromkeys)",
        "property(len)",
    ],
)
def test_builtin_class_descriptors_make_loaded_code_explicitly_unavailable(
    tmp_path: Path,
    descriptor: str,
) -> None:
    from thymira.core.runtime_identity import observe_runtime_identity

    package = _source_tree(tmp_path)
    module = ModuleType("thymira.builtin_descriptor_probe")
    _exec_synthetic(module, f"class C:\n    count = {descriptor}\n")

    identity = _capture(package, modules=(module,), schemas=())
    code_layer = next(layer for layer in identity.layers if layer.name == "loaded_code_objects")
    observation = observe_runtime_identity(identity, modules=(module,), schemas=())

    assert code_layer.status == "unavailable"
    assert code_layer.sha256 is None
    assert any("no inspectable Python code" in reason for reason in identity.unavailable_reasons)
    assert observation.state == "unavailable"
    assert any("no inspectable Python code" in reason for reason in observation.reasons)


def test_methodtype_inside_class_descriptor_has_inspectable_code(tmp_path: Path) -> None:
    from thymira.core.runtime_identity import observe_runtime_identity

    package = _source_tree(tmp_path)
    module = ModuleType("thymira.method_descriptor_probe")
    _exec_synthetic(
        module,
        "class C:\n    def target(self): return 'inspectable'\nC.code = staticmethod(C().target)\n",
    )

    identity = _capture(package, modules=(module,), schemas=())
    code_layer = next(layer for layer in identity.layers if layer.name == "loaded_code_objects")
    observation = observe_runtime_identity(identity, modules=(module,), schemas=())

    assert code_layer.status == "verified"
    assert code_layer.sha256 is not None
    assert observation.state == "matches_startup"


def test_missing_installed_distribution_is_explicit_and_not_a_conclusive_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _source_tree(tmp_path)

    def missing_distribution(name: str) -> None:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", missing_distribution)
    identity = _capture(package, distributions=("litellm",))

    assert identity.fingerprint_sha256 is None
    assert any("litellm" in reason for reason in identity.unavailable_reasons)
