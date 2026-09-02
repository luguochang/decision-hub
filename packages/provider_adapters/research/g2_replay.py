from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FactReplayVariant:
    status: str
    fixture_ref: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class FactReplayManifest:
    pack_id: str
    cutoff_at: str
    requirements: Mapping[str, Mapping[str, FactReplayVariant]]

    @classmethod
    def load(cls, path: Path) -> FactReplayManifest:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("crypto_macro_fact_replay_invalid") from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != "crypto-macro-fact-pack-replay.v1"
        ):
            raise ValueError("crypto_macro_fact_replay_invalid")
        requirements_raw = payload.get("requirements")
        if not isinstance(requirements_raw, Mapping) or set(requirements_raw) != {
            "event.identity",
            "policy.delta",
            "expectation.pricing",
            "macro.transmission",
            "crypto.spot",
            "crypto.derivatives",
        }:
            raise ValueError("crypto_macro_fact_replay_invalid")
        requirements: dict[str, dict[str, FactReplayVariant]] = {}
        for requirement_id, variants_raw in requirements_raw.items():
            if not isinstance(requirement_id, str) or not isinstance(variants_raw, Mapping):
                raise ValueError("crypto_macro_fact_replay_invalid")
            variants: dict[str, FactReplayVariant] = {}
            for variant_id in ("success", "stale", "provider_failure"):
                raw = variants_raw.get(variant_id)
                if not isinstance(raw, Mapping):
                    raise ValueError("crypto_macro_fact_replay_invalid")
                status = raw.get("status")
                if status not in {"sufficient", "stale", "failed"}:
                    raise ValueError("crypto_macro_fact_replay_invalid")
                fixture_ref = raw.get("fixture_ref")
                error_code = raw.get("error_code")
                if variant_id != "provider_failure" and not isinstance(fixture_ref, str):
                    raise ValueError("crypto_macro_fact_replay_invalid")
                if variant_id == "provider_failure" and not isinstance(error_code, str):
                    raise ValueError("crypto_macro_fact_replay_invalid")
                variants[variant_id] = FactReplayVariant(
                    status=status,
                    fixture_ref=fixture_ref if isinstance(fixture_ref, str) else None,
                    error_code=error_code if isinstance(error_code, str) else None,
                )
            requirements[requirement_id] = variants
        pack_id = payload.get("pack_id")
        cutoff_at = payload.get("cutoff_at")
        if not isinstance(pack_id, str) or not isinstance(cutoff_at, str):
            raise ValueError("crypto_macro_fact_replay_invalid")
        return cls(pack_id=pack_id, cutoff_at=cutoff_at, requirements=requirements)
