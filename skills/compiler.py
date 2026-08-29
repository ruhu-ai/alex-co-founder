"""Strict, atomic compiler for repository-owned skill packages."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import threading
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

import yaml
from pydantic import ValidationError
from yaml.events import AliasEvent

from services.capability_registry import (
    CONTROLLED_ACTION_CAPABILITIES,
    EXTERNAL_ACTION_CAPABILITIES,
    STATIC_CAPABILITIES,
    CapabilityDescriptor,
)
from skills.contracts import require_contract
from skills.models import CompiledCatalog, CompiledSkill, QualificationEvidence, SkillManifest
from skills.offline_capabilities import OFFLINE_DRAFT_CAPABILITIES


class SkillCompileError(ValueError):
    """A safe build-time rejection; authored content is never echoed wholesale."""


CAPABILITY_CATALOG: Mapping[str, CapabilityDescriptor] = MappingProxyType({
    **STATIC_CAPABILITIES,
    **{item.capability_id: item for item in EXTERNAL_ACTION_CAPABILITIES.values()},
    **{item.capability_id: item for item in CONTROLLED_ACTION_CAPABILITIES.values()},
    **OFFLINE_DRAFT_CAPABILITIES,
})


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueKeyLoader, node, deep: bool = False):
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise SkillCompileError(f"duplicate manifest key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _load_yaml(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        if any(isinstance(event, AliasEvent) for event in yaml.parse(raw)):
            raise SkillCompileError("YAML aliases are forbidden")
        value = yaml.load(raw, Loader=_UniqueKeyLoader)
    except SkillCompileError:
        raise
    except yaml.YAMLError as exc:
        raise SkillCompileError("manifest YAML is invalid") from exc
    if not isinstance(value, dict):
        raise SkillCompileError("manifest must be an object")
    return value


def _safe_file(root: Path, relative: str, *, max_bytes: int) -> tuple[Path, bytes]:
    posix = PurePosixPath(relative)
    if (not relative or posix.is_absolute() or ".." in posix.parts
            or str(posix) != relative or "\\" in relative):
        raise SkillCompileError(f"unsafe package path: {relative}")
    path = root.joinpath(*posix.parts)
    if path.is_symlink() or not path.is_file():
        raise SkillCompileError(f"package file missing or symlinked: {relative}")
    resolved_root = root.resolve()
    try:
        path.resolve().relative_to(resolved_root)
    except ValueError as exc:
        raise SkillCompileError(f"package path escapes root: {relative}") from exc
    if path.stat().st_mode & 0o111:
        raise SkillCompileError(f"executable package content is forbidden: {relative}")
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise SkillCompileError(f"package file exceeds declared limit: {relative}")
    return path, data


def _validate_schema(
    data: bytes,
    path: str,
    *,
    strict_nested: bool = False,
) -> None:
    try:
        schema = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillCompileError(f"invalid JSON schema: {path}") from exc
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SkillCompileError(f"schema must describe an object: {path}")

    if schema.get("additionalProperties") is not False:
        raise SkillCompileError(f"schema must be closed: {path}")

    def require_closed_objects(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and node.get("additionalProperties") is not False:
                raise SkillCompileError(f"schema contains an open object: {path}")
            if "$ref" in node and not str(node["$ref"]).startswith("#/"):
                raise SkillCompileError(f"schema contains an external reference: {path}")
            for value in node.values():
                require_closed_objects(value)
        elif isinstance(node, list):
            for value in node:
                require_closed_objects(value)

    if strict_nested:
        require_closed_objects(schema)


def _contract_ids(manifest: SkillManifest) -> tuple[str, ...]:
    c = manifest.contracts
    return (
        c.input_schema_id, c.output_schema_id, c.error_schema_id,
        c.completion_contract_id, c.context_contract_id, c.evidence_contract_id,
        c.citation_policy_id, manifest.policy.approval_policy_id,
        *manifest.policy.precondition_ids, manifest.execution.retry_policy_id,
        manifest.execution.idempotency_contract_id,
        manifest.evaluation.release_gate_id, manifest.lifecycle.rollout_policy_id,
    )


def _descriptor_contract_ids(descriptor: CapabilityDescriptor) -> tuple[str, ...]:
    return (
        descriptor.input_schema_id,
        descriptor.output_schema_id,
        descriptor.error_schema_id,
        descriptor.approval_policy_id,
        descriptor.idempotency_contract,
        descriptor.retry_contract,
        descriptor.timeout_contract,
        descriptor.reconciliation_contract,
        descriptor.completion_contract_id,
        descriptor.budget_contract_id,
        descriptor.observability_contract_id,
        descriptor.eval_suite_id,
        descriptor.provenance_contract_id,
    )


def compile_package(
    package: Path,
    *,
    capabilities: Mapping[str, CapabilityDescriptor] = CAPABILITY_CATALOG,
) -> CompiledSkill:
    try:
        manifest = SkillManifest.model_validate(_load_yaml(package / "skill.yaml"))
    except ValidationError as exc:
        raise SkillCompileError(f"closed manifest validation failed: {package.name}") from exc

    for contract_id in _contract_ids(manifest):
        require_contract(contract_id)
    for pin in manifest.capabilities.allow:
        descriptor = capabilities.get(pin.capability_id)
        if descriptor is None or descriptor.semantic_version != pin.version:
            raise SkillCompileError(f"unresolved capability pin: {pin.capability_id}@{pin.version}")
        if pin.capability_id in OFFLINE_DRAFT_CAPABILITIES:
            if manifest.status == "DRAFT" and descriptor.lifecycle != "DRAFT":
                raise SkillCompileError(
                    f"draft skill must pin draft-only capabilities: {pin.capability_id}")
            for contract_id in _descriptor_contract_ids(descriptor):
                require_contract(contract_id)

    declared = {"skill.yaml"}
    _, playbook = _safe_file(package, manifest.provenance.playbook_path, max_bytes=65_536)
    declared.add(manifest.provenance.playbook_path)
    schema_hashes: dict[str, str] = {}
    for contract_id, path in (
        (manifest.contracts.input_schema_id, manifest.contracts.input_schema_path),
        (manifest.contracts.output_schema_id, manifest.contracts.output_schema_path),
    ):
        _, data = _safe_file(package, path, max_bytes=65_536)
        _validate_schema(
            data,
            path,
            strict_nested=(
                manifest.skill_id == "documents.produce-grounded-artifact"
            ),
        )
        declared.add(path)
        schema_hashes[contract_id] = _sha(data)

    resource_hashes: dict[str, str] = {}
    for resource in manifest.resources:
        path, data = _safe_file(package, resource.path, max_bytes=resource.max_bytes)
        guessed = mimetypes.guess_type(path.name)[0] or "text/plain"
        if guessed != resource.media_type:
            raise SkillCompileError(f"resource media type mismatch: {resource.path}")
        actual = _sha(data)
        if actual != resource.sha256:
            raise SkillCompileError(f"resource hash mismatch: {resource.path}")
        declared.add(resource.path)
        resource_hashes[resource.resource_id] = actual

    qpath = manifest.evaluation.qualification_path
    _, qdata = _safe_file(package, qpath, max_bytes=65_536)
    declared.add(qpath)
    try:
        qualification = QualificationEvidence.model_validate_json(qdata)
    except ValidationError as exc:
        raise SkillCompileError("qualification evidence validation failed") from exc
    if (qualification.skill_id != manifest.skill_id
            or qualification.skill_version != manifest.version):
        raise SkillCompileError("qualification evidence targets another skill version")
    pinned_set = sorted((pin.capability_id, pin.version) for pin in manifest.capabilities.allow)
    if qualification.capability_set_hash != _sha(_canonical(pinned_set)):
        raise SkillCompileError("qualification capability set hash mismatch")

    present = {
        str(path.relative_to(package)) for path in package.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    extras = present - declared
    if extras:
        raise SkillCompileError(f"undeclared package files: {', '.join(sorted(extras))}")

    definition_material = {
        "manifest": manifest.model_dump(mode="json"),
        "playbook_hash": _sha(playbook),
        "schema_hashes": schema_hashes,
        "resource_hashes": resource_hashes,
    }
    return CompiledSkill(
        manifest=manifest,
        definition_hash=_sha(_canonical(definition_material)),
        playbook_hash=_sha(playbook),
        schema_hashes=schema_hashes,
        resource_hashes=resource_hashes,
        qualification=qualification,
        qualification_hash=_sha(_canonical(qualification.model_dump(mode="json"))),
        package_path=str(package),
    )


def compile_catalog(
    root: str | Path,
    *,
    capabilities: Mapping[str, CapabilityDescriptor] = CAPABILITY_CATALOG,
) -> CompiledCatalog:
    root_path = Path(root)
    manifests = sorted(root_path.glob("**/skill.yaml"))
    compiled = tuple(
        compile_package(path.parent, capabilities=capabilities).model_copy(update={
            "package_path": path.parent.relative_to(root_path).as_posix(),
        })
        for path in manifests
    )
    identities = [skill.identity for skill in compiled]
    if len(identities) != len(set(identities)):
        raise SkillCompileError("duplicate skill identity")
    payload = [{
        "identity": skill.identity,
        "definition_hash": skill.definition_hash,
        "qualification_hash": skill.qualification_hash,
    } for skill in compiled]
    return CompiledCatalog(catalog_hash=_sha(_canonical(payload)), skills=compiled)


class AtomicSkillRegistry:
    """Publishes only complete immutable catalogs; failures retain the prior one."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._catalog = CompiledCatalog(catalog_hash=_sha(b"[]"), skills=())
        self._root = Path(".").resolve()

    def snapshot(self) -> CompiledCatalog:
        with self._lock:
            return self._catalog

    def publish(self, root: str | Path) -> CompiledCatalog:
        candidate = compile_catalog(root)
        with self._lock:
            self._catalog = candidate
            self._root = Path(root).resolve()
            return candidate

    def metadata_index(self) -> Mapping[str, object]:
        catalog = self.snapshot()
        return MappingProxyType({f"{card.skill_id}@{card.version}": card
                                 for card in catalog.cards()})

    def load_playbook(self, identity: str) -> str:
        with self._lock:
            skill = self._catalog.by_identity().get(identity)
            root = self._root
        if skill is None:
            raise KeyError(identity)
        path = root / skill.package_path / skill.manifest.provenance.playbook_path
        data = path.read_bytes()
        if _sha(data) != skill.playbook_hash:
            raise SkillCompileError("playbook hash mismatch at read")
        return data.decode("utf-8")

    def load_resource(self, identity: str, resource_id: str) -> bytes:
        with self._lock:
            skill = self._catalog.by_identity().get(identity)
            root = self._root
        if skill is None:
            raise KeyError(identity)
        decl = next((r for r in skill.manifest.resources if r.resource_id == resource_id), None)
        if decl is None:
            raise KeyError(resource_id)
        _, data = _safe_file(
            root / skill.package_path, decl.path, max_bytes=decl.max_bytes,
        )
        if _sha(data) != skill.resource_hashes[resource_id]:
            raise SkillCompileError("resource hash mismatch at read")
        return data
