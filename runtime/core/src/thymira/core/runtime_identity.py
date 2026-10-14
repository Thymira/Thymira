"""Capture an honest, deterministic identity for one in-process Thymira runtime."""

from __future__ import annotations

import os
import sys
import threading
import types
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from thymira.core._runtime_identity_files import (
    TRANSFORMER_DISTRIBUTIONS,
    FileInventory,
    installed_source_roots,
    source_file_inventory,
    transformer_file_inventory,
)
from thymira.events import canonical_json, sha256_text
from thymira.schemas import ThymiraModel, utc_now

if TYPE_CHECKING:
    from types import CodeType, ModuleType
    from typing import Any

    from thymira.agents import AgentCatalog
    from thymira.mira.agents.spec import AuditAgentSpec
    from thymira.mira.preflight.models import ReviewedPack


_SOURCE_SCOPE = (
    "Startup snapshot of installed thymira namespace .py files and .md/.yaml/.yml/.json "
    "resources under prompts/defaults/catalogs/specs; not proof that every file was loaded."
)
_LOADED_CODE_SCOPE = (
    "Code objects bound by loaded thymira modules and their classes at observation, including "
    "rebound callables; module globals and dynamic settings are outside this digest."
)
_SCHEMA_SCOPE = (
    "Local JSON Schema projection of the effective Pydantic classes. The response_format adapter "
    "argument remains a Pydantic class; this is not HTTP serialization or remote acceptance."
)
_CATALOG_SCOPE = "Canonical snapshot of the resolved runtime catalog, specs and prompts."
_TRANSFORMER_SCOPE = (
    "Startup installed-files snapshot for package-owned LiteLLM/OpenAI/Pydantic schema-transformer "
    "files and distribution metadata; loaded serializer bytes and lazy imports are not attested, "
    "and this does not prove an HTTP payload or remote acceptance."
)


class RuntimeIdentityLayer(ThymiraModel):
    """Describe one bounded, independently verifiable part of a runtime fingerprint."""

    name: str
    scope: str
    sha256: str | None
    item_count: int
    status: Literal["verified", "unavailable"]


class RuntimeIdentity(ThymiraModel):
    """Immutable startup generation and the scoped evidence used to identify it."""

    schema_version: str = "1.0"
    generation_id: str
    started_at: datetime
    pid: int
    fingerprint_sha256: str | None
    loaded_binding_instances_sha256: str | None = None
    layers: tuple[RuntimeIdentityLayer, ...]
    unavailable_reasons: tuple[str, ...] = ()


class RuntimeObservation(ThymiraModel):
    """Describe the loaded code and schemas observed when one Run is actually executed."""

    startup_generation_id: str
    execution_generation_id: str
    observed_at: datetime
    loaded_code_sha256: str | None
    loaded_schema_sha256: str | None
    loaded_binding_instances_sha256: str | None = None
    state: Literal["matches_startup", "changed_or_late_imports", "unavailable"]
    reasons: tuple[str, ...] = ()


_PROCESS_IDENTITIES: dict[int, RuntimeIdentity] = {}
_PROCESS_IDENTITY_LOCK = threading.Lock()
_LOAD_REFERENCES: dict[str, tuple[tuple[object, ...], tuple[object, ...]]] = {}
_LOAD_OBSERVATION_STATE: dict[str, tuple[str | None, int]] = {}
_LOAD_REFERENCES_LOCK = threading.Lock()


def capture_runtime_identity(
    *,
    source_roots: Sequence[Path] | None = None,
    modules: Sequence[ModuleType] | None = None,
    distributions: Sequence[str] = TRANSFORMER_DISTRIBUTIONS,
    schemas: Sequence[type[BaseModel]] | None = None,
    catalog_snapshot: Mapping[str, Any] | None = None,
    catalog_unavailable_reason: str | None = None,
    generation_id: str | None = None,
    started_at: datetime | None = None,
    pid: int | None = None,
) -> RuntimeIdentity:
    """Capture bounded startup evidence without using Git, workspace data or credentials.

    ``source_roots``, ``modules``, ``distributions`` and ``schemas`` are explicit seams for
    deterministic synthetic tests. The disk snapshot, loaded code and effective schemas remain
    separate layers so none is presented as a substitute for another.
    """
    resolved_modules = _loaded_thymira_modules() if modules is None else tuple(modules)
    roots = installed_source_roots() if source_roots is None else tuple(source_roots)
    resolved_schemas = _merge_schemas(
        _loaded_pydantic_models(resolved_modules) if schemas is None else tuple(schemas),
        _loaded_pydantic_models(resolved_modules),
    )

    layers: list[RuntimeIdentityLayer] = []
    reasons: list[str] = []
    source_layer, source_reasons = _source_snapshot_layer(roots)
    code_layer, code_reasons = _loaded_code_layer(resolved_modules)
    loaded_instances_sha256, load_references = _loaded_binding_instances(resolved_modules)
    schema_layer, schema_reasons = _schema_layer(resolved_schemas)
    transformer_layer, transformer_reasons = _installed_transformer_layer(distributions)
    interpreter_layer = _interpreter_layer()

    layers.extend(
        (
            source_layer,
            code_layer,
            _catalog_layer(catalog_snapshot, catalog_unavailable_reason),
            schema_layer,
            transformer_layer,
            interpreter_layer,
        )
    )
    reasons.extend(source_reasons)
    reasons.extend(code_reasons)
    reasons.extend(schema_reasons)
    reasons.extend(transformer_reasons)
    if catalog_snapshot is None:
        reasons.append("resolved runtime catalog snapshot was not supplied")
    elif layers[2].status == "unavailable":
        reasons.append(
            catalog_unavailable_reason or "resolved runtime catalog snapshot was not representable"
        )

    complete = all(layer.status == "verified" for layer in layers)
    fingerprint = (
        sha256_text(
            canonical_json(
                {
                    "schema_version": "1.0",
                    "layers": [{"name": layer.name, "sha256": layer.sha256} for layer in layers],
                }
            )
        )
        if complete
        else None
    )
    resolved_generation_id = generation_id or f"boot_{os.urandom(16).hex()}"
    with _LOAD_REFERENCES_LOCK:
        _LOAD_REFERENCES[resolved_generation_id] = (load_references, load_references)
        _LOAD_OBSERVATION_STATE[resolved_generation_id] = (loaded_instances_sha256, 0)
    return RuntimeIdentity(
        generation_id=resolved_generation_id,
        started_at=started_at or utc_now(),
        pid=os.getpid() if pid is None else pid,
        fingerprint_sha256=fingerprint,
        loaded_binding_instances_sha256=loaded_instances_sha256,
        layers=tuple(layers),
        unavailable_reasons=tuple(dict.fromkeys(reasons)),
    )


def process_runtime_identity() -> RuntimeIdentity:
    """Return the one conservative identity captured for this process PID."""
    pid = os.getpid()
    with _PROCESS_IDENTITY_LOCK:
        identity = _PROCESS_IDENTITIES.get(pid)
        if identity is None:
            identity = capture_runtime_identity()
            _PROCESS_IDENTITIES[pid] = identity
        return identity


def observe_runtime_identity(
    identity: RuntimeIdentity,
    *,
    modules: Sequence[ModuleType] | None = None,
    schemas: Sequence[type[BaseModel]] | None = None,
    observed_at: datetime | None = None,
) -> RuntimeObservation:
    """Compare currently loaded code/schema evidence with one immutable startup record.

    This reads in-memory modules only. It never re-hashes the source tree on a per-Run path.
    """
    resolved_modules = _loaded_thymira_modules() if modules is None else tuple(modules)
    resolved_schemas = _merge_schemas(
        _loaded_pydantic_models(resolved_modules) if schemas is None else tuple(schemas),
        _loaded_pydantic_models(resolved_modules),
    )
    code_layer, code_reasons = _loaded_code_layer(resolved_modules)
    schema_layer, schema_reasons = _schema_layer(resolved_schemas)
    loaded_instances_sha256, load_references = _loaded_binding_instances(resolved_modules)
    baseline = {layer.name: layer for layer in identity.layers}
    reasons = list(code_reasons) + list(schema_reasons)
    expected_code = baseline.get("loaded_code_objects")
    expected_schema = baseline.get("effective_pydantic_schemas")
    expected_instances = identity.loaded_binding_instances_sha256
    if expected_code is None or expected_code.sha256 is None:
        reasons.append("startup loaded-code digest was unavailable")
    if expected_schema is None or expected_schema.sha256 is None:
        reasons.append("startup Pydantic schema digest was unavailable")
    if expected_instances is None:
        reasons.append("startup loaded binding-instance digest was unavailable")

    code_matches = expected_code is not None and expected_code.sha256 == code_layer.sha256
    schema_matches = expected_schema is not None and expected_schema.sha256 == schema_layer.sha256
    available = (
        code_layer.status == "verified"
        and schema_layer.status == "verified"
        and expected_code is not None
        and expected_code.sha256 is not None
        and expected_schema is not None
        and expected_schema.sha256 is not None
        and expected_instances is not None
    )
    if not available:
        state: Literal["matches_startup", "changed_or_late_imports", "unavailable"] = "unavailable"
        reasons.append("loaded-code observation is incomplete")
    elif code_matches and schema_matches and expected_instances == loaded_instances_sha256:
        state = "matches_startup"
    else:
        state = "changed_or_late_imports"
        if not code_matches:
            reasons.append("loaded code objects differ from the startup observation")
        if not schema_matches:
            reasons.append("loaded Pydantic schemas differ from the startup observation")
        if expected_instances != loaded_instances_sha256:
            reasons.append("loaded function or class instances were reloaded or rebound")

    with _LOAD_REFERENCES_LOCK:
        previous = _LOAD_REFERENCES.get(identity.generation_id, ((), ()))
        _LOAD_REFERENCES[identity.generation_id] = (previous[0], load_references)
        previous_instances, load_revision = _LOAD_OBSERVATION_STATE.get(
            identity.generation_id,
            (identity.loaded_binding_instances_sha256, 0),
        )
        if loaded_instances_sha256 != previous_instances:
            load_revision += 1
        _LOAD_OBSERVATION_STATE[identity.generation_id] = (
            loaded_instances_sha256,
            load_revision,
        )

    execution_fingerprint = sha256_text(
        canonical_json(
            {
                "startup_generation_id": identity.generation_id,
                "loaded_code_sha256": code_layer.sha256,
                "loaded_schema_sha256": schema_layer.sha256,
                "loaded_binding_instances_sha256": loaded_instances_sha256,
                "load_revision": load_revision,
                "state": state,
            }
        )
    )
    return RuntimeObservation(
        startup_generation_id=identity.generation_id,
        execution_generation_id=f"exec_{execution_fingerprint}",
        observed_at=observed_at or utc_now(),
        loaded_code_sha256=code_layer.sha256,
        loaded_schema_sha256=schema_layer.sha256,
        loaded_binding_instances_sha256=loaded_instances_sha256,
        state=state,
        reasons=tuple(dict.fromkeys(reasons)),
    )


def runtime_catalog_snapshot(
    catalog: AgentCatalog,
    *,
    audit_specs: Sequence[AuditAgentSpec] = (),
    packs: Sequence[ReviewedPack] = (),
    response_schemas: Sequence[type[BaseModel]] = (),
    runtime_skill_manifests: Mapping[str, BaseModel] | None = None,
) -> tuple[Mapping[str, Any], tuple[type[BaseModel], ...]]:
    """Freeze the resolved agent catalog, prompts, structured schemas and audit specs."""
    entries: list[dict[str, Any]] = []
    schemas: list[type[BaseModel]] = []
    for name in sorted(catalog.names()):
        spec = catalog.get(name)
        schema = catalog.output_schema(name)
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise TypeError(f"catalog output schema {name!r} is not a Pydantic model class")
        schemas.append(schema)
        entries.append(
            {
                "name": name,
                "spec": _json_compatible(spec),
                "system_prompt": catalog.system_prompt(name),
                "output_schema": schema.model_json_schema(),
            }
        )
    for schema in response_schemas:
        if not isinstance(schema, type) or not issubclass(schema, BaseModel):
            raise TypeError("effective response schemas must be Pydantic model classes")
        schemas.append(schema)
    snapshot = {
        "thy_catalog": entries,
        "audit_specs": [_json_compatible(spec) for spec in audit_specs],
        "packs": [_json_compatible(pack) for pack in packs],
        "effective_response_schemas": [
            {
                "class": f"{schema.__module__}.{schema.__qualname__}",
                "json_schema": schema.model_json_schema(),
            }
            for schema in response_schemas
        ],
        "runtime_skill_manifests": {
            name: _json_compatible(manifest)
            for name, manifest in sorted((runtime_skill_manifests or {}).items())
        },
    }
    return _freeze_json(snapshot), tuple(schemas)


def runtime_attachment_payload(
    identity: RuntimeIdentity,
    *,
    run_started_seq: int,
    run_started_hash: str,
    schemas: Sequence[type[BaseModel]] | None = None,
) -> dict[str, Any]:
    """Build a bounded event payload linking the executing generation to Run creation."""
    observation = observe_runtime_identity(identity, schemas=schemas)
    return {
        "runtime_identity": identity.to_json_dict(),
        "runtime_observation": observation.to_json_dict(),
        "run_started": {"seq": run_started_seq, "hash": run_started_hash},
    }


def _json_compatible(  # noqa: PLR0911 -- project each supported catalog value type explicitly
    value: object,
) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return _json_compatible(value.value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("runtime catalog mappings must use string keys")
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        converted = [_json_compatible(item) for item in value]
        return (
            sorted(converted, key=canonical_json)
            if isinstance(value, (set, frozenset))
            else converted
        )
    if is_dataclass(value) and not isinstance(value, type):
        return _json_compatible(asdict(value))
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"unsupported runtime catalog value type: {type(value).__name__}")


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _merge_schemas(
    explicit: Sequence[type[BaseModel]], loaded: Sequence[type[BaseModel]]
) -> tuple[type[BaseModel], ...]:
    unique = {
        f"{schema.__module__}.{schema.__qualname__}": schema for schema in (*explicit, *loaded)
    }
    return tuple(unique[name] for name in sorted(unique))


def _loaded_binding_instances(
    modules: Sequence[ModuleType],
) -> tuple[str | None, tuple[object, ...]]:
    entries: list[tuple[str, int]] = []
    references: list[object] = []
    for module in sorted(modules, key=lambda item: item.__name__):
        entries.append((f"{module.__name__}.__module__", id(module)))
        references.append(module)
        for name, value in vars(module).items():
            if isinstance(value, types.FunctionType):
                entries.append((f"{module.__name__}.{name}", id(value)))
                references.append(value)
            elif isinstance(value, type) and value.__module__ == module.__name__:
                entries.append((f"{module.__name__}.{name}", id(value)))
                references.append(value)
                for member_name, member in vars(value).items():
                    binding = f"{module.__name__}.{name}.{member_name}"
                    if isinstance(member, (classmethod, staticmethod)):
                        entries.append((binding, id(member)))
                        references.extend((member, member.__func__))
                        entries.append((f"{binding}.__func__", id(member.__func__)))
                    elif isinstance(member, types.FunctionType):
                        entries.append((binding, id(member)))
                        references.append(member)
                    elif isinstance(member, property):
                        entries.append((binding, id(member)))
                        references.append(member)
                        for accessor_name, accessor in (
                            ("get", member.fget),
                            ("set", member.fset),
                            ("delete", member.fdel),
                        ):
                            if accessor is not None:
                                references.append(accessor)
                                entries.append((f"{binding}.{accessor_name}", id(accessor)))
    if not modules:
        return None, ()
    return sha256_text(canonical_json(sorted(entries))), tuple(references)


def _source_snapshot_layer(
    roots: Sequence[Path],
) -> tuple[RuntimeIdentityLayer, list[str]]:
    inventory = source_file_inventory(list(roots))
    return _file_inventory_layer("startup_source_snapshot", _SOURCE_SCOPE, inventory), list(
        inventory.reasons
    )


def _loaded_thymira_modules() -> tuple[ModuleType, ...]:
    return tuple(
        module
        for name, module in sorted(sys.modules.items())
        if (name == "thymira" or name.startswith("thymira."))
        and isinstance(module, types.ModuleType)
    )


def _loaded_code_layer(
    modules: Sequence[ModuleType],
) -> tuple[RuntimeIdentityLayer, list[str]]:
    entries: list[tuple[str, str]] = []
    reasons: list[str] = []
    for module in sorted(modules, key=lambda item: item.__name__):
        members: list[tuple[str, str]] = []
        for name, value in vars(module).items():
            if isinstance(value, types.FunctionType):
                # The binding name matters: two same-shaped functions can be swapped between
                # aliases and change the code a consumer calls without changing a qualname set.
                # Imported/rebound functions are included too because the binding is consumed
                # by this Thymira module; their globals are still outside the digest.
                members.append((f"{module.__name__}.{name}", _code_sha256(value.__code__)))
            elif isinstance(value, type) and value.__module__ == module.__name__:
                class_entries, class_reasons = _class_code_entries(module.__name__, name, value)
                members.extend(class_entries)
                reasons.extend(class_reasons)
        members.sort()
        entries.append((module.__name__, sha256_text(canonical_json(members))))
    if not modules:
        reasons.append("no loaded thymira modules were available for code observation")
        return _unavailable_layer("loaded_code_objects", _LOADED_CODE_SCOPE), reasons
    if reasons:
        return _unavailable_layer("loaded_code_objects", _LOADED_CODE_SCOPE), reasons
    return _verified_layer("loaded_code_objects", _LOADED_CODE_SCOPE, entries), reasons


def _class_code_entries(
    module_name: str, class_name: str, model: type[object]
) -> tuple[list[tuple[str, str]], list[str]]:
    entries: list[tuple[str, str]] = []
    reasons: list[str] = []
    for member_name, member in vars(model).items():
        binding = f"{module_name}.{class_name}.{member_name}"
        if isinstance(member, (classmethod, staticmethod)):
            _append_callable_code(binding, member.__func__, entries, reasons)
        elif isinstance(member, types.FunctionType):
            _append_callable_code(binding, member, entries, reasons)
        elif isinstance(member, property):
            for accessor_name, accessor in (
                ("get", member.fget),
                ("set", member.fset),
                ("delete", member.fdel),
            ):
                if accessor is not None:
                    _append_callable_code(f"{binding}.{accessor_name}", accessor, entries, reasons)
    return entries, reasons


def _append_callable_code(
    binding: str,
    function: object,
    entries: list[tuple[str, str]],
    reasons: list[str],
) -> None:
    code = getattr(function, "__code__", None)
    if isinstance(code, types.CodeType):
        entries.append((binding, _code_sha256(code)))
    elif callable(function):
        reasons.append(f"loaded callable binding {binding} has no inspectable Python code")


def _code_sha256(code: CodeType) -> str:
    def constant(  # noqa: PLR0911 -- preserve each nested and scalar code-constant variant
        value: object,
    ) -> object:
        if isinstance(value, types.CodeType):
            return {"code": _code_shape(value, constant)}
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        if isinstance(value, tuple):
            return [constant(item) for item in value]
        if isinstance(value, frozenset):
            return {"frozenset": sorted((constant(item) for item in value), key=canonical_json)}
        if isinstance(value, complex):
            return {"complex": [float(value.real).hex(), float(value.imag).hex()]}
        if isinstance(value, float):
            return {"float": value.hex()}
        if value is None or isinstance(value, (str, int, float, bool)):
            return [type(value).__name__, value]
        if value is Ellipsis:
            return "ellipsis"
        return {"constant_type": type(value).__name__}

    return sha256_text(canonical_json(_code_shape(code, constant)))


def _code_shape(
    code: CodeType,
    constant: Callable[[object], object],
) -> dict[str, object]:
    return {
        "name": code.co_name,
        "qualname": code.co_qualname,
        "bytecode": code.co_code.hex(),
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "nlocals": code.co_nlocals,
        "flags": code.co_flags,
        "stacksize": code.co_stacksize,
        "names": code.co_names,
        "varnames": code.co_varnames,
        "freevars": code.co_freevars,
        "cellvars": code.co_cellvars,
        "exceptiontable": getattr(code, "co_exceptiontable", b"").hex(),
        "constants": [constant(value) for value in code.co_consts],
    }


def _loaded_pydantic_models(modules: Sequence[ModuleType]) -> tuple[type[BaseModel], ...]:
    models: dict[str, type[BaseModel]] = {}
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and value.__module__ == module.__name__
                and issubclass(value, BaseModel)
            ):
                models[f"{value.__module__}.{value.__qualname__}"] = value
    return tuple(models[name] for name in sorted(models))


def _schema_layer(
    schemas: Sequence[type[BaseModel]],
) -> tuple[RuntimeIdentityLayer, list[str]]:
    entries: list[tuple[str, str]] = []
    reasons: list[str] = []
    for schema in schemas:
        name = f"{schema.__module__}.{schema.__qualname__}"
        try:
            schema_json = schema.model_json_schema()
            schema_hash = sha256_text(canonical_json(schema_json))
        except Exception as exc:  # noqa: BLE001  # a broken schema is explicit unverified evidence
            reasons.append(f"Pydantic schema {name} could not be described ({type(exc).__name__})")
            continue
        entries.append((name, schema_hash))
    if reasons:
        return _unavailable_layer("effective_pydantic_schemas", _SCHEMA_SCOPE), reasons
    return _verified_layer("effective_pydantic_schemas", _SCHEMA_SCOPE, entries), reasons


def _catalog_layer(
    snapshot: Mapping[str, Any] | None,
    unavailable_reason: str | None = None,
) -> RuntimeIdentityLayer:
    if unavailable_reason is not None:
        return _unavailable_layer("resolved_catalog", _CATALOG_SCOPE)
    if snapshot is None:
        return _unavailable_layer("resolved_catalog", _CATALOG_SCOPE)
    try:
        digest = sha256_text(canonical_json(_json_compatible(snapshot)))
    except (TypeError, ValueError):
        return _unavailable_layer("resolved_catalog", _CATALOG_SCOPE)
    return RuntimeIdentityLayer(
        name="resolved_catalog",
        scope=_CATALOG_SCOPE,
        sha256=digest,
        item_count=len(snapshot),
        status="verified",
    )


def _installed_transformer_layer(
    distributions: Sequence[str],
) -> tuple[RuntimeIdentityLayer, list[str]]:
    inventory = transformer_file_inventory(list(distributions))
    return _file_inventory_layer("installed_transformers", _TRANSFORMER_SCOPE, inventory), list(
        inventory.reasons
    )


def _file_inventory_layer(
    name: str,
    scope: str,
    inventory: FileInventory,
) -> RuntimeIdentityLayer:
    if inventory.status == "unavailable":
        return _unavailable_layer(name, scope)
    return _verified_layer(name, scope, inventory.entries)


def _interpreter_layer() -> RuntimeIdentityLayer:
    details = {
        "implementation": sys.implementation.name,
        "version": sys.version_info[:3],
    }
    return RuntimeIdentityLayer(
        name="python_runtime",
        scope="Python implementation and major/minor/micro version.",
        sha256=sha256_text(canonical_json(details)),
        item_count=2,
        status="verified",
    )


def _verified_layer(
    name: str,
    scope: str,
    entries: Sequence[tuple[str, str]],
) -> RuntimeIdentityLayer:
    ordered = sorted(entries)
    return RuntimeIdentityLayer(
        name=name,
        scope=scope,
        sha256=sha256_text(canonical_json(ordered)),
        item_count=len(ordered),
        status="verified",
    )


def _unavailable_layer(name: str, scope: str) -> RuntimeIdentityLayer:
    return RuntimeIdentityLayer(
        name=name,
        scope=scope,
        sha256=None,
        item_count=0,
        status="unavailable",
    )


__all__ = [
    "TRANSFORMER_DISTRIBUTIONS",
    "RuntimeIdentity",
    "RuntimeIdentityLayer",
    "RuntimeObservation",
    "capture_runtime_identity",
    "observe_runtime_identity",
    "process_runtime_identity",
    "runtime_attachment_payload",
    "runtime_catalog_snapshot",
]
