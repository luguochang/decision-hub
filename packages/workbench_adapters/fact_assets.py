"""Resolve only the immutable material referenced by a fact's original Evidence."""

from dataclasses import asdict
from pathlib import Path
import re

from packages.provider_adapters.historical.archive import HistoricalDataArchive
from packages.provider_adapters.market.event_window import CryptoEventWindowArchive
from packages.provider_adapters.research.document_store import ContentAddressedDocumentStore


class FactAssetResolver:
    def __init__(self, data_root: Path):
        self.root = data_root

    def read(self, version: dict, partition: int | None = None):
        ref = version.get("structured_payload_ref") or version.get("content_ref")
        if not ref:
            raise KeyError("fact_input_archive_not_recorded")
        # A source registry locator identifies policy, not retained input bytes.
        # Resolve only the original document retained by this same immutable version.
        if re.fullmatch(r"source-registry://[A-Za-z0-9][A-Za-z0-9._-]*", ref):
            original_ref = version.get("content_ref")
            if not original_ref:
                raise KeyError("fact_input_archive_not_recorded")
            if not isinstance(original_ref, str) or re.fullmatch(
                r"document://sha256/[a-f0-9]{64}", original_ref
            ) is None:
                raise KeyError("fact_input_archive_type_unsupported")
            ref = original_ref
        if ref.startswith("coverage-manifest://sha256/"):
            archive = HistoricalDataArchive(self.root / "historical")
            coverage = archive.load_coverage(ref)
            partition_refs = list(
                dict.fromkeys(
                    ref for dataset in coverage.datasets for ref in dataset.partition_refs
                )
            )
            if partition is not None:
                if not 0 <= partition < len(partition_refs):
                    raise KeyError("fact_input_partition_not_found")
                payload_ref = partition_refs[partition]
                payload, _rows = archive.load_partition(payload_ref)
                return {
                    "kind": "historical_partition",
                    "ref": payload_ref,
                    "verified": True,
                    "payload": payload,
                }
            # Verify every frozen partition, not the current coverage state.
            for payload_ref in partition_refs:
                archive.load_partition(payload_ref)
            return {
                "kind": "coverage_manifest",
                "ref": ref,
                "verified": True,
                "payload": coverage.model_dump(mode="json"),
            }
        if partition is not None:
            raise KeyError("fact_input_partition_not_supported")
        if ref.startswith("crypto-window://"):
            payload = CryptoEventWindowArchive(self.root / "event-windows").load(ref)
            return {
                "kind": "event_window",
                "ref": ref,
                "verified": True,
                "payload": payload.model_dump(mode="json"),
            }
        if ref.startswith("document://sha256/"):
            payload = ContentAddressedDocumentStore(self.root / "documents").load(ref)
            return {
                "kind": "original_text",
                "ref": ref,
                "verified": True,
                "payload": asdict(payload),
            }
        raise KeyError("fact_input_archive_type_unsupported")
