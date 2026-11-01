"""Exact release-derived resource authority and bounded frozen data reader.

Uninstalled business candidate. Current runtime has no frozen-resource seam;
this module does not monkeypatch filesystem functions or execute bundle code.
The normal registered runtime must explicitly wire the frozen reader contract.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import research_graph_history_versions as history
from packages.kernel.decision_hub_kernel.application import research_graph_recipe_v3 as recipe_v3
from packages.kernel.decision_hub_kernel.application import research_graph_recipe as recipe_v2
from packages.kernel.decision_hub_kernel.application.product_release_provenance import resolve_product_receipt
from packages.kernel.decision_hub_kernel.application import product_release_provenance as provenance_codec
from packages.kernel.decision_hub_kernel.application.research_graph_binding import _ReadSourceGuard
from packages.kernel.decision_hub_kernel.application.research_graph_binding import _retained_original_files
from packages.kernel.decision_hub_kernel.application import research_context_selection_versions as selector_versions
from packages.kernel.decision_hub_kernel.application.research_recipe_archive import _planned_manifest


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


@dataclass(frozen=True)
class SourceFile:
    relative_ref: str
    expected_sha256: str


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# The authenticated original receipt chooses this closed historical codec.
# These are byte attestations of the existing promotion before-images. No
# historical Python is imported or executed, and these refs never enter the
# frozen reader's code/data authorization set.
_PRELOOKUP_CODEC = "publication-resource-closure-prelookup.v1"
_PRELOOKUP_SOURCES = {
    "packages/kernel/decision_hub_kernel/application/research_publication_resources.py":
        "76d3acd23b338479937eab691c1f5eda6f0ac7d9454434896869efd6192d045c",
    "packages/kernel/decision_hub_kernel/application/research_publication_runtime48.py":
        "92e38d9e381555fb80b9bf5ef8cbc8598eb6748bbac4ede6b62226dcabd629be",
}
_LOOKUP_SOURCES = {
    "packages/kernel/decision_hub_kernel/application/research_publication_resources.py":
        "9cf3ab1024f329a9deafaecf302533a9dab736fe382d09e7be97bf5520260812",
    "packages/kernel/decision_hub_kernel/application/research_publication_runtime48.py":
        "059be3968350d0d717778c5d0ae2e9e51509dc78f21d6c0a855b3de774a93128",
}
_PRELOOKUP_PROMOTION = "artifacts/core-goal-20261011/promotion74_01/"
_PRELOOKUP_PROMOTION_SHA = "6b2754bc9784e2c86cb56c0b2edb4a95e74bacd4c8cedd4de976734b46b91d78"
_PRELOOKUP_FIELDS = (
    "nonce", "repository_root", "run_id", "receipt_ref", "receipt_sha256",
    "registry_ref", "registry_sha256", "selected_directory",
    "selected_index_sha256", "entries", "closure_sha256", "mode", "active_binding_json",
)
_NORMAL_SELF_READ_SOURCE = "packages/kernel/decision_hub_kernel/application/product_release_provenance.py"


@dataclass(frozen=True)
class ResourceBounds:
    max_resources: int
    max_resource_bytes: int
    max_total_bytes: int

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise ValueError("publication_resource_explicit_bounds_required")


@dataclass(frozen=True)
class FrozenResource:
    namespace: str
    logical_ref: str
    physical_ref: str
    sha256: str
    bytes: int
    purpose: str  # .py attestations never enter data or code execution loaders.


@dataclass(frozen=True)
class AuthenticatedResourceClosure:
    nonce: str
    repository_root: str
    run_id: str
    receipt_ref: str
    receipt_sha256: str
    registry_ref: str
    registry_sha256: str
    selected_directory: str
    selected_index_sha256: str
    entries: tuple[FrozenResource, ...]
    closure_sha256: str
    mode: str = "retained-installation"
    active_binding_json: str | None = None
    registration_lookup_indexes: tuple[SourceFile, ...] = ()

    def source_files(self) -> tuple[SourceFile, ...]:
        unique = {}
        for item in self.entries:
            previous = unique.setdefault(item.physical_ref, item.sha256)
            if previous != item.sha256:
                raise ValueError("publication_resource_physical_identity_conflict")
        return tuple(SourceFile(path, digest) for path, digest in sorted(unique.items()))


class FrozenPublicationResourceReader:
    """Only authority-issued exact copies; data misses never open Root files."""
    CONTRACT = "publication-frozen-data-resources.v1"

    def __init__(self, closure: AuthenticatedResourceClosure,
                 entries: Mapping[tuple[str, str], tuple[FrozenResource, bytes]]):
        self.closure = closure
        self._entries = MappingProxyType(dict(entries))
        self.requests: list[tuple[str, str, str]] = []

    def read_data(self, namespace: str, logical_ref: str) -> bytes:
        item = self._entries.get((namespace, logical_ref))
        if item is None:
            raise ValueError("publication_frozen_resource_missing")
        description, raw = item
        if description.purpose != "data_resource":
            raise ValueError("publication_frozen_code_as_data_forbidden")
        if len(raw) != description.bytes or sha(raw) != description.sha256:
            raise ValueError("publication_frozen_resource_bytes_changed")
        self.requests.append((namespace, logical_ref, description.sha256))
        return raw

    def attest_source(self, namespace: str, logical_ref: str) -> tuple[str, int]:
        item = self._entries.get((namespace, logical_ref))
        if item is None or item[0].purpose != "source_attestation":
            raise ValueError("publication_frozen_source_attestation_missing")
        description, raw = item
        if len(raw) != description.bytes or sha(raw) != description.sha256:
            raise ValueError("publication_frozen_resource_bytes_changed")
        # Only digest/length are exported for source attestations. Normal
        # registered code must still be loaded/guarded from installed Root.
        return description.sha256, description.bytes

    def require_registered_contract(self, reader_module) -> None:
        function = getattr(reader_module, "consume_frozen_publication_resources_in_session", None)
        if (getattr(reader_module, "FROZEN_PUBLICATION_RESOURCE_CONTRACT", None) != self.CONTRACT
                or not callable(function)):
            raise ValueError("publication_registered_frozen_resource_seam_unavailable")
        # Merely finding the seam does not grant publication authority. The
        # normal product guard must pin this module and its code identity.


class OriginalPublicationResourceAuthority:
    """Issuer accepts normal process binding, not user-provided source lists."""
    def __init__(self, *, trusted_root: Path, bounds: ResourceBounds):
        self.root = trusted_root.absolute()
        self.bounds = bounds
        if self.root != history._CURRENT_ROOT.absolute():
            raise ValueError("publication_resource_runtime_root_changed")
        self._issued: dict[str, AuthenticatedResourceClosure] = {}
        self._saved_current = {}

    @staticmethod
    def _snapshot(session: Session):
        connection = session.connection()
        native = connection.connection.driver_connection
        if (not isinstance(native, sqlite3.Connection) or not native.in_transaction
                or native.execute("PRAGMA query_only").fetchone() != (1,)
                or session.new or session.dirty or session.deleted):
            raise ValueError("publication_resource_clean_ro_snapshot_required")
        return (connection, native, session.get_transaction(), session.get_nested_transaction(), native.total_changes)

    def resolve(self, session: Session, run_id: str, *, receipt_ref: str,
                receipt_sha256: str) -> AuthenticatedResourceClosure:
        """Resolve all source/resource identities in one borrowed RO snapshot.

        Existing V3 archival size/runtime/installation gates run unchanged.
        Logical Root identities are never relocated to a staging directory.
        """
        entry = self._snapshot(session)
        binding = history._REGISTRY_BINDING
        if (binding is None or binding.receipt_hash != receipt_sha256
                or binding.receipt_path.absolute() != self.root / receipt_ref):
            raise ValueError("publication_resource_normal_registry_binding_required")
        raw_receipt = history._managed_read(receipt_ref)
        if sha(raw_receipt) != receipt_sha256:
            raise ValueError("publication_resource_receipt_changed")
        resolved = resolve_product_receipt(raw_receipt, expected_root=self.root,
            read_resource=history._managed_read, expected_sha256=receipt_sha256)
        installed = recipe_v3._installation_original()
        if installed.receipt.path != receipt_ref or installed.receipt.sha256 != receipt_sha256:
            raise ValueError("publication_resource_normal_v3_installation_binding_required")
        registrations = (*history._REGISTRATIONS, *history._registry_registrations(binding, force_full=True))
        registration = history._original_recipe_registration(session, run_id)
        active = None
        if registration is None:
            # Current is an explicit installation descriptor, never a made-up
            # retained index or a fallback to the latest selection.
            selection = recipe_v3._read_graph_recipe_selection_v3_original(session, run_id)
            if (selection is None or selection.receipt.installation_receipt != installed.receipt
                    or selection.receipt.installed_pointer != installed.pointer
                    or selection.receipt.dependency_hash != installed.dependency_hash
                    or selection.receipt.manifest != installed.manifest):
                raise ValueError("publication_resource_exact_current_installation_required")
            active = canonical({"schema_version":"publication-current-installation-descriptor.v1",
                "selection_ref":selection.reference.model_dump(mode="json"),
                "selection":selection.receipt.model_dump(mode="json"),
                "installation_receipt":installed.receipt.model_dump(mode="json"),
                "installed_pointer":installed.pointer.model_dump(mode="json"),
                "dependency_hash":installed.dependency_hash,
                "manifest":installed.manifest.model_dump(mode="json")}).decode()
        else:
            index, payloads, _code_root = history._verified_release_uncached(registration)
            archive = "packages/kernel/research_recipe_versions/" + registration.directory + "/"
        graph_hash = selection.receipt.dependency_hash if registration is None else registration.graph_hash
        lookup_indexes = tuple(SourceFile(
            "packages/kernel/research_recipe_versions/" + candidate.directory + "/release.json",
            candidate.index_hash) for candidate in registrations if candidate.graph_hash == graph_hash)
        root_refs = {receipt_ref: receipt_sha256, binding.registry_path: binding.registry_hash,
                     installed.pointer.path:installed.pointer.sha256, **dict(resolved.transport)}
        for reference in lookup_indexes:
            if binding.source_hashes.get(reference.relative_ref) != reference.expected_sha256:
                raise ValueError("publication_resource_lookup_index_binding_changed")
            previous = root_refs.setdefault(reference.relative_ref, reference.expected_sha256)
            if previous != reference.expected_sha256:
                raise ValueError("publication_resource_lookup_index_identity_conflict")
        for manifest in (recipe_v2.graph_recipe_manifest_v2(), recipe_v3.graph_recipe_manifest_v3()):
            for value in (*manifest.source_files, *manifest.schema_files):
                previous = root_refs.setdefault(value.path, value.payload_hash)
                if previous != value.payload_hash:
                    raise ValueError("publication_resource_current_manifest_conflict")
            if hasattr(manifest, "registration"):
                root_refs[manifest.registration.path] = manifest.registration.sha256
        guard = _ReadSourceGuard.capture()
        if guard is None or not guard.matches():
            raise ValueError("publication_resource_normal_source_guard_failed")
        for ref, digest in guard.files:
            previous = root_refs.setdefault(ref, digest)
            if previous != digest:
                raise ValueError("publication_resource_current_guard_conflict")
        specs: list[tuple[str, str, str, str]] = [
            ("current-root", ref, ref, digest) for ref, digest in sorted(root_refs.items())]
        if registration is not None:
            specs.append(("selected-installation", "release.json", archive + "release.json", registration.index_hash))
            stored = set(index["stored_files"])
            for ref, expected in sorted(index["all_files"].items()):
                if sha(payloads[ref]) != expected:
                    raise ValueError("publication_resource_selected_payload_changed")
                physical = archive + "root/" + ref if ref in stored else ref
                specs.append(("selected-installation", ref, physical, expected))
        if len(specs) > self.bounds.max_resources:
            raise ValueError("publication_resource_count_bound")
        entries, unique_bytes, total = [], {}, 0
        for namespace, logical, physical, expected in specs:
            # Existing managed reader enforces Root-relative paths/reparse
            # denial; this resolver never reads a supplied fallback directory.
            if namespace == "selected-installation" and logical != "release.json":
                # Reuse only this resolve call's already verified entry bytes.
                # Current-root reads and every original exit read stay fresh.
                selected_physical = archive + "root/" + logical if logical in stored else logical
                if physical != selected_physical or logical not in payloads:
                    raise ValueError("publication_resource_selected_entry_mapping_changed")
                raw = payloads[logical]
            else:
                raw = history._managed_read(physical)
            if sha(raw) != expected or len(raw) > self.bounds.max_resource_bytes:
                raise ValueError("publication_resource_bytes_or_file_bound")
            if physical not in unique_bytes:
                unique_bytes[physical] = (expected, len(raw))
                total += len(raw)
                if total > self.bounds.max_total_bytes:
                    raise ValueError("publication_resource_total_bound")
            elif unique_bytes[physical] != (expected, len(raw)):
                raise ValueError("publication_resource_physical_identity_conflict")
            purpose = "source_attestation" if logical.endswith(".py") else "data_resource"
            entries.append(FrozenResource(namespace, logical, physical, expected, len(raw), purpose))
        for _namespace, _logical, physical, expected in specs:
            if sha(history._managed_read(physical)) != expected:
                raise ValueError("publication_resource_original_exit_changed")
        if (history._original_recipe_registration(session, run_id) != registration
                or self._snapshot(session) != entry or not guard.matches()
                or history._REGISTRY_BINDING is not binding
                or recipe_v3._installation_original() != installed):
            raise ValueError("publication_resource_snapshot_or_source_exit_changed")
        if active is not None:
            exit_selection = recipe_v3._read_graph_recipe_selection_v3_original(session,run_id)
            if exit_selection != selection:
                raise ValueError("publication_resource_current_selection_exit_changed")
        directory = registration.directory if registration is not None else installed.dependency_hash
        index_hash = registration.index_hash if registration is not None else sha(active.encode())
        body = {"root": str(self.root), "run_id": run_id, "receipt_ref": receipt_ref,
            "receipt_sha256": receipt_sha256, "registry_ref": binding.registry_path,
            "registry_sha256": binding.registry_hash, "selected_directory": directory,
            "selected_index_sha256": index_hash, "mode":"current-installation" if active is not None else "retained-installation",
            "active_binding_json":active,
            "registration_lookup_indexes": [vars(item) for item in lookup_indexes],
            "entries": [vars(item) for item in entries]}
        closure = AuthenticatedResourceClosure(uuid4().hex, str(self.root), run_id,
            receipt_ref, receipt_sha256, binding.registry_path, binding.registry_hash,
            directory, index_hash, tuple(entries), sha(canonical(body)),
            "current-installation" if active is not None else "retained-installation",active,lookup_indexes)
        self._issued[closure.nonce] = closure
        return closure

    @staticmethod
    def _saved_description(closure, codec=None):
        actual = asdict(closure)
        if codec == _PRELOOKUP_CODEC:
            # The source-authenticated old dataclass had exactly these fields.
            # The fresh lookup is still independently derived and verified.
            if closure.registration_lookup_indexes:
                raise ValueError("publication_saved_confirmation_original_lookup_changed")
            actual = {field:actual[field] for field in _PRELOOKUP_FIELDS}
        elif codec is not None:
            raise ValueError("publication_saved_confirmation_original_codec_unsupported")
        return actual

    @staticmethod
    def _require_saved_description(closure, expected, *, codec=None):
        actual = OriginalPublicationResourceAuthority._saved_description(closure, codec)
        if (type(expected) is not dict or set(expected) != set(actual)
                or type(expected.get("nonce")) is not str
                or len(expected["nonce"]) != 32
                or any(c not in "0123456789abcdef" for c in expected["nonce"])
                or canonical({k:v for k,v in expected.items() if k != "nonce"})
                    != canonical({k:v for k,v in actual.items() if k != "nonce"})):
            raise ValueError("publication_saved_confirmation_resource_authority_changed")

    def _verified_saved_descriptor_codec(self, original_source_hashes):
        if all(original_source_hashes.get(ref) == digest
                for ref, digest in _PRELOOKUP_SOURCES.items()):
            sources, image, codec = _PRELOOKUP_SOURCES, "before", _PRELOOKUP_CODEC
        elif all(original_source_hashes.get(ref) == digest
                for ref, digest in _LOOKUP_SOURCES.items()):
            sources, image, codec = _LOOKUP_SOURCES, "after", None
        else:
            raise ValueError("publication_saved_confirmation_original_codec_unsupported")
        raw = history._managed_read(_PRELOOKUP_PROMOTION + "promotion.json")
        if len(raw) > self.bounds.max_resource_bytes or sha(raw) != _PRELOOKUP_PROMOTION_SHA:
            raise ValueError("publication_saved_confirmation_original_codec_seal_changed")
        promotion = json.loads(raw)
        if (type(promotion) is not dict
                or promotion.get("schema_version") != "core-selective-promotion74.v1"
                or promotion.get("executed") is not True
                or type(promotion.get("files")) is not list):
            raise ValueError("publication_saved_confirmation_original_codec_seal_changed")
        for ref, digest in sources.items():
            rows = [row for row in promotion["files"]
                if type(row) is dict and row.get("path") == ref]
            if (len(rows) != 1 or rows[0].get("kind") != "production"
                    or rows[0].get("purpose") != "production"
                    or rows[0].get(image + "_sha256") != digest):
                raise ValueError("publication_saved_confirmation_original_codec_seal_changed")
            source = history._managed_read(_PRELOOKUP_PROMOTION + image + "/" + ref)
            if len(source) > self.bounds.max_resource_bytes or sha(source) != digest:
                raise ValueError("publication_saved_confirmation_original_codec_source_changed")
        return codec

    def saved_confirmation_description(self, closure):
        if self._issued.get(closure.nonce) is not closure:
            raise ValueError("publication_resource_issuer_identity_changed")
        saved = self._saved_current.get(closure.nonce)
        codec = None
        if saved is not None:
            if saved[0] is not closure or history._REGISTRY_BINDING is not saved[1]:
                raise ValueError("publication_resource_normal_source_binding_changed")
            codec = self._verified_saved_descriptor_codec(saved[4].source_hashes)
            if codec != saved[8]:
                raise ValueError("publication_saved_confirmation_original_codec_source_changed")
        return self._saved_description(closure, codec)

    def _verified_normal_self_read_source(self, binding, index, payloads):
        """The sole installed codec self-read, authenticated at both sources.

        This changes one existing source attestation's actual location. All
        archived leaves still pass the independent full registered verifier;
        frozen data and mismatching latest code gain no access.
        """
        ref = _NORMAL_SELF_READ_SOURCE
        expected = index["all_files"].get(ref)
        if (expected is None or ref not in index["stored_files"]
                or binding.source_hashes.get(ref) != expected
                or provenance_codec.SOURCE_REF != ref
                or Path(provenance_codec.__file__).absolute() != self.root / ref
                or provenance_codec._LOADED_SOURCE_SHA != expected):
            raise ValueError("publication_saved_confirmation_normal_self_read_source_changed")
        raw = history._managed_read(ref)
        original = payloads[ref]
        if (sha(raw) != expected or sha(original) != expected
                or len(raw) != len(original) or raw != original
                or len(raw) > self.bounds.max_resource_bytes):
            raise ValueError("publication_saved_confirmation_normal_self_read_source_changed")
        provenance_codec._loaded_codec()
        return ref

    def _declared_original_selector_resources(self, guard):
        """Attest the normal declared capsule bytes, without executing it.

        The normal old-selector replay additionally requires today's shared
        executable dependencies to match its old version. This authority only
        authenticates the complete provenance originals already inventoried
        by the normal guard. Selected executable sources retain their separate
        registered 277-file authority and runtime verification.
        """
        files, auxiliary = selector_versions._FILES, selector_versions._AUXILIARY_SCHEMAS
        if (type(files) is not dict or type(auxiliary) is not dict
                or set(files).intersection(auxiliary)
                or selector_versions._ROOT != self.root
                or sha(selector_versions._canonical({"files":files,
                    "codec":"canonical_context_selection.v3"}).encode()) != selector_versions._VERSION):
            raise ValueError("publication_saved_confirmation_original_selector_identity_changed")
        pins = {**files, **auxiliary}
        prefix = selector_versions._RESOURCE_ROOT.relative_to(self.root).as_posix() + "/"
        retained = {p.relative_to(self.root).as_posix() for p in _retained_original_files(self.root)}
        if {prefix + ref for ref in pins} != retained:
            raise ValueError("publication_saved_confirmation_original_selector_set_changed")
        guarded = dict(guard.files)
        originals = {}
        for ref, expected in pins.items():
            physical = prefix + ref
            if guarded.get(physical) != expected:
                raise ValueError("publication_saved_confirmation_original_selector_set_changed")
            raw = history._managed_read(physical)
            if sha(raw) != expected or len(raw) > self.bounds.max_resource_bytes:
                raise ValueError("publication_saved_confirmation_original_selector_bytes_changed")
            originals[ref] = raw
        return originals

    def resolve_saved_confirmation(self, session: Session, *, run_id: str,
                                   expected_authority: dict) -> AuthenticatedResourceClosure:
        """Derive a saved current installation from normal registered originals.

        Old JSON is only the final expected description. All source locations
        come from the current authenticated registry and its exact native Run
        selection, the registered index, and existing retained selector pins.
        Neither the current process binding nor an original namespace changes.
        """
        entry = self._snapshot(session)
        binding = history._REGISTRY_BINDING
        if binding is None:
            raise ValueError("publication_resource_normal_registry_binding_required")
        registrations = history._registry_registrations(binding, force_full=True)
        registration = history._original_recipe_registration(session, run_id)
        # Current and existing retained material retain their original resolver.
        # Only the independently selected registered former current needs a map.
        if (registration is None or type(expected_authority) is not dict
                or expected_authority.get("mode") != "current-installation"):
            closure = self.resolve(session, run_id,
                receipt_ref=binding.receipt_path.relative_to(self.root).as_posix(),
                receipt_sha256=binding.receipt_hash)
            try:
                self._require_saved_description(closure, expected_authority)
                return closure
            except BaseException:
                self.discard(closure)
                raise
        if registration not in registrations:
            raise ValueError("publication_saved_confirmation_registered_installation_required")
        guard = _ReadSourceGuard.capture()
        if guard is None or not guard.matches():
            raise ValueError("publication_resource_normal_source_guard_failed")
        installed = recipe_v3._installation_original()
        index, payloads, _root = history._verified_release_uncached(registration)
        closure = None
        try:
            with history._verified_release_read_scope(
                    session, run_id, registration, (index, payloads, _root)):
                if (index.get("schema_version") != "research-retained-recipe-release.v3"
                        or set(index["all_files"]) != set(index["stored_files"])
                        or index.get("shared_auxiliary_files") != {}):
                    raise ValueError("publication_saved_confirmation_complete_v3_archive_required")
                archive = "packages/kernel/research_recipe_versions/" + registration.directory + "/"
                mapping = {name: archive + "root/" + name for name in index["all_files"]}
                if any(binding.source_hashes.get(mapping[name]) != digest
                        for name, digest in index["all_files"].items()):
                    raise ValueError("publication_saved_confirmation_archive_binding_changed")
                # This normal already-loaded codec attests its own ROOT __file__. Its
                # registered archive and fresh current product both pin the same whole
                # bytes. Replace only that existing source location, never add a key.
                normal_self_read = self._verified_normal_self_read_source(binding, index, payloads)
                mapping[normal_self_read] = normal_self_read
                old_binding = index["installation_binding"]
                receipt_ref, receipt_hash = old_binding["receipt"]["path"], old_binding["receipt"]["sha256"]
                old_resolved = resolve_product_receipt(payloads[receipt_ref], expected_root=self.root,
                    expected_sha256=receipt_hash, read_resource=payloads.__getitem__)
                descriptor_codec = self._verified_saved_descriptor_codec(old_resolved.value["sources"])
                evidence = old_resolved.value["evidence"]["retained_recipe_archives_v1"]
                if (type(evidence) is not dict or set(evidence) != {"registry"}
                        or type(evidence["registry"]) is not dict
                        or set(evidence["registry"]) != {"path", "sha256"}):
                    raise ValueError("publication_saved_confirmation_old_registry_invalid")
                registry_ref, registry_hash = evidence["registry"]["path"], evidence["registry"]["sha256"]
                if (registry_ref != "packages/kernel/research_recipe_versions/registries/" + registry_hash + ".json"
                        or old_resolved.value["sources"].get(registry_ref) != registry_hash):
                    raise ValueError("publication_saved_confirmation_old_registry_invalid")
                # A local immutable verifier input is not installed as process binding.
                old_registry_binding = history._RegistryBinding(self.root/receipt_ref, receipt_hash,
                    registry_ref, registry_hash, MappingProxyType(dict(old_resolved.value["sources"])),
                    MappingProxyType(dict(old_resolved.transport)))
                old_registrations = history._registry_registrations(old_registry_binding, force_full=True)
                selection = recipe_v3._read_graph_recipe_selection_v3_original(session, run_id)
                if (selection is None or selection.receipt.dependency_hash != registration.graph_hash
                        or selection.receipt.manifest.model_dump(mode="json") != index["graph_recipe_manifest"]
                        or selection.receipt.installation_receipt.model_dump(mode="json") != old_binding["receipt"]
                        or selection.receipt.installed_pointer.model_dump(mode="json") != old_binding["pointer"]):
                    raise ValueError("publication_saved_confirmation_original_selection_changed")
                manifest = index["graph_recipe_manifest"]
                v2 = _planned_manifest(self.root, payloads, manifest["runtime_versions"],
                    version=2, closed_overlay=True)
                if _planned_manifest(self.root, payloads, manifest["runtime_versions"],
                        version=3, closed_overlay=True) != manifest:
                    raise ValueError("publication_saved_confirmation_original_manifest_changed")
                root_refs = {receipt_ref: receipt_hash, registry_ref: registry_hash,
                    old_binding["pointer"]["path"]: old_binding["pointer"]["sha256"],
                    **dict(old_resolved.transport)}
                for body in (v2, manifest):
                    for item in (*body["source_files"], *body["schema_files"]):
                        previous = root_refs.setdefault(item["path"], item["payload_hash"])
                        if previous != item["payload_hash"]:
                            raise ValueError("publication_resource_current_manifest_conflict")
                    if "registration" in body:
                        ref = body["registration"]
                        root_refs[ref["path"]] = ref["sha256"]
                # These extra original selector resources already belong to the normal
                # guard. Eight schema refs are absent from the product source map; the
                # installed original selector's existing static pins own their identity.
                originals = self._declared_original_selector_resources(guard)
                selector_root = selector_versions._RESOURCE_ROOT.relative_to(self.root).as_posix() + "/"
                extra = {selector_root + ref: sha(raw) for ref, raw in originals.items()}
                retained = {p.relative_to(self.root).as_posix() for p in _retained_original_files(self.root)}
                if (set(extra) != retained
                        or any(dict(guard.files).get(ref) != digest for ref, digest in extra.items())):
                    raise ValueError("publication_saved_confirmation_original_selector_set_changed")
                for ref, digest in extra.items():
                    previous = root_refs.setdefault(ref, digest)
                    if previous != digest:
                        raise ValueError("publication_resource_current_guard_conflict")
                    mapping[ref] = ref
                lookup = tuple(SourceFile("packages/kernel/research_recipe_versions/" + candidate.directory
                    + "/release.json", candidate.index_hash) for candidate in (*history._REGISTRATIONS, *old_registrations)
                    if candidate.graph_hash == registration.graph_hash)
                for ref in lookup:
                    if old_resolved.value["sources"].get(ref.relative_ref) != ref.expected_sha256:
                        raise ValueError("publication_resource_lookup_index_binding_changed")
                    root_refs[ref.relative_ref] = ref.expected_sha256
                    mapping[ref.relative_ref] = ref.relative_ref
                # The original registry is rooted in the authenticated original receipt,
                # not in an invented current source-map entry or supplied material path.
                mapping[registry_ref] = registry_ref
                if set(root_refs) != set(mapping) or len(root_refs) > self.bounds.max_resources:
                    raise ValueError("publication_saved_confirmation_original_resource_set_changed")
                entries, total = [], 0
                for ref, digest in sorted(root_refs.items()):
                    raw = history._managed_read(mapping[ref])
                    if sha(raw) != digest or len(raw) > self.bounds.max_resource_bytes:
                        raise ValueError("publication_resource_bytes_or_file_bound")
                    total += len(raw)
                    if total > self.bounds.max_total_bytes:
                        raise ValueError("publication_resource_total_bound")
                    entries.append(FrozenResource("current-root", ref, ref, digest, len(raw),
                        "source_attestation" if ref.endswith(".py") else "data_resource"))
                active = canonical({"schema_version":"publication-current-installation-descriptor.v1",
                    "selection_ref":selection.reference.model_dump(mode="json"),
                    "selection":selection.receipt.model_dump(mode="json"),
                    "installation_receipt":old_binding["receipt"], "installed_pointer":old_binding["pointer"],
                    "dependency_hash":registration.graph_hash, "manifest":manifest}).decode()
                body = {"root":str(self.root), "run_id":run_id, "receipt_ref":receipt_ref,
                    "receipt_sha256":receipt_hash, "registry_ref":registry_ref, "registry_sha256":registry_hash,
                    "selected_directory":registration.graph_hash, "selected_index_sha256":sha(active.encode()),
                    "mode":"current-installation", "active_binding_json":active,
                    "entries":[vars(v) for v in entries]}
                if descriptor_codec is None:
                    body["registration_lookup_indexes"] = [vars(v) for v in lookup]
                elif lookup:
                    raise ValueError("publication_saved_confirmation_original_lookup_changed")
                closure = AuthenticatedResourceClosure(uuid4().hex, str(self.root), run_id,
                    receipt_ref, receipt_hash, registry_ref, registry_hash, registration.graph_hash,
                    sha(active.encode()), tuple(entries), sha(canonical(body)), "current-installation", active, lookup)
                self._require_saved_description(closure, expected_authority, codec=descriptor_codec)
                self._issued[closure.nonce] = closure
                self._saved_current[closure.nonce] = (closure, binding, registration,
                    MappingProxyType(dict(mapping)), old_registry_binding, guard, installed,
                    MappingProxyType({receipt_ref:receipt_hash, registry_ref:registry_hash, **dict(old_resolved.transport)}),
                    descriptor_codec)
                self.verify_normal_authority(closure)
                if (self._snapshot(session) != entry
                        or history._original_recipe_registration(session, run_id) != registration
                        or recipe_v3._read_graph_recipe_selection_v3_original(session, run_id) != selection):
                    raise ValueError("publication_resource_snapshot_or_source_exit_changed")
            return closure
        except BaseException:
            if closure is not None:
                self.discard(closure)
            raise

    def discard(self, closure):
        if self._issued.get(closure.nonce) is closure:
            self._issued.pop(closure.nonce, None)
            self._saved_current.pop(closure.nonce, None)

    def source_path(self, closure, namespace, logical_ref):
        if self._issued.get(closure.nonce) is not closure:
            raise ValueError("publication_resource_issuer_identity_changed")
        matches = [v for v in closure.entries if v.namespace == namespace and v.logical_ref == logical_ref]
        if len(matches) != 1:
            raise ValueError("publication_resource_normal_source_reference_invalid")
        saved = self._saved_current.get(closure.nonce)
        binding = history._REGISTRY_BINDING
        if saved is not None:
            if saved[0] is not closure or binding is not saved[1]:
                raise ValueError("publication_resource_normal_source_binding_changed")
            return self.root / saved[3][matches[0].physical_ref]
        if (binding is None or binding.receipt_hash != closure.receipt_sha256
                or binding.registry_hash != closure.registry_sha256 or binding.registry_path != closure.registry_ref
                or binding.receipt_path.absolute() != self.root / closure.receipt_ref):
            raise ValueError("publication_resource_normal_source_binding_changed")
        return self.root / matches[0].physical_ref

    def read_source_bytes(self, closure, namespace, logical_ref):
        path = self.source_path(closure, namespace, logical_ref)
        item = next(v for v in closure.entries if v.namespace == namespace and v.logical_ref == logical_ref)
        raw = history._managed_read(path.relative_to(self.root).as_posix())
        if len(raw) != item.bytes or sha(raw) != item.sha256:
            raise ValueError("publication_resource_normal_source_bytes_changed")
        return raw

    def authorization_sources(self, closure):
        if self._issued.get(closure.nonce) is not closure:
            raise ValueError("publication_resource_issuer_identity_changed")
        binding = history._REGISTRY_BINDING
        if binding is None:
            raise ValueError("publication_resource_normal_source_binding_changed")
        refs = {closure.receipt_ref:closure.receipt_sha256, binding.registry_path:binding.registry_hash,
            binding.receipt_path.relative_to(self.root).as_posix():binding.receipt_hash, **dict(binding.transport_hashes)}
        saved = self._saved_current.get(closure.nonce)
        if saved is not None:
            if saved[0] is not closure or binding is not saved[1]:
                raise ValueError("publication_resource_normal_source_binding_changed")
            for ref, digest in saved[7].items():
                previous = refs.setdefault(ref, digest)
                if previous != digest:
                    raise ValueError("publication_resource_normal_source_binding_changed")
        return tuple(sorted(refs.items()))

    def verify_normal_authority(self, closure, *, checkpoint=None):
        if self._issued.get(closure.nonce) is not closure:
            raise ValueError("publication_resource_issuer_identity_changed")
        for item in closure.entries:
            if checkpoint is not None:
                checkpoint()
            self.read_source_bytes(closure, item.namespace, item.logical_ref)
        saved = self._saved_current.get(closure.nonce)
        if saved is not None:
            if history._REGISTRY_BINDING is not saved[1]:
                raise ValueError("publication_resource_normal_source_binding_changed")
            registrations = history._registry_registrations(saved[1], force_full=True)
            if saved[2] not in registrations:
                raise ValueError("publication_saved_confirmation_registered_installation_required")
            index, payloads, _root = history._verified_release_uncached(saved[2])
            normal_self_read = self._verified_normal_self_read_source(saved[1], index, payloads)
            if saved[3].get(normal_self_read) != normal_self_read:
                raise ValueError("publication_saved_confirmation_normal_self_read_source_changed")
            history._registry_registrations(saved[4], force_full=True)
            if self._verified_saved_descriptor_codec(saved[4].source_hashes) != saved[8]:
                raise ValueError("publication_saved_confirmation_original_codec_source_changed")
            if not saved[5].matches() or recipe_v3._installation_original() != saved[6]:
                raise ValueError("publication_resource_snapshot_or_source_exit_changed")

    def authorize_frozen(self, closure: AuthenticatedResourceClosure,
                         captured_sources: list[dict]) -> FrozenPublicationResourceReader:
        if self._issued.get(closure.nonce) is not closure:
            raise ValueError("publication_resource_issuer_identity_changed")
        required = {item.physical_ref: item for item in closure.entries}
        actual = {}
        total = 0
        for source in captured_sources:
            if set(source) != {"relative_ref", "sha256", "bytes", "base64"}:
                raise ValueError("publication_resource_captured_fields_changed")
            ref = source["relative_ref"]
            expected = required.get(ref)
            if expected is None or ref in actual:
                raise ValueError("publication_resource_captured_set_changed")
            if (type(source["bytes"]) is not int or source["bytes"] != expected.bytes
                    or source["bytes"] > self.bounds.max_resource_bytes
                    or source["sha256"] != expected.sha256):
                raise ValueError("publication_resource_captured_identity_changed")
            raw = base64.b64decode(source["base64"], validate=True)
            if len(raw) != expected.bytes or sha(raw) != expected.sha256:
                raise ValueError("publication_resource_captured_bytes_changed")
            total += len(raw)
            if total > self.bounds.max_total_bytes:
                raise ValueError("publication_resource_total_bound")
            actual[ref] = raw
        if set(actual) != set(required):
            raise ValueError("publication_resource_captured_set_changed")
        return FrozenPublicationResourceReader(closure,
            {(item.namespace, item.logical_ref): (item, actual[item.physical_ref]) for item in closure.entries})

    def read_normal_source(self, closure: AuthenticatedResourceClosure,
                           namespace: str, logical_ref: str) -> bytes:
        """Fresh normal registered source, never source bytes from a material.

        Only the issuer's exact release-derived object can request a code
        attestation file. Future historical loading may compile these normal
        installed bytes; the frozen data API cannot return executable source.
        This method alone does not authorize a frozen publication replay.
        """
        if self._issued.get(closure.nonce) is not closure:
            raise ValueError("publication_resource_issuer_identity_changed")
        matches = [item for item in closure.entries
            if item.namespace == namespace and item.logical_ref == logical_ref]
        if len(matches) != 1 or matches[0].purpose != "source_attestation":
            raise ValueError("publication_resource_normal_source_reference_invalid")
        return self.read_source_bytes(closure, namespace, logical_ref)
