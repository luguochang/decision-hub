"""Preregister comparison definitions; registering never executes or promotes."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError

from packages.kernel.decision_hub_kernel.persistence.db import (
    CandidateVersionRecord,
    ProspectiveTrialRecord,
    as_utc,
    utcnow,
)
from packages.runtime_adapters.execution_recipe import (
    assert_recipe,
    canonical_recipe,
    recipe_hash,
    runtime_recipe,
)


class TrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    domain_pack_ref: Literal["crypto_macro.v1"] = "crypto_macro.v1"
    market: Literal["crypto"] = "crypto"
    instrument: Literal["BTC-USDT-SWAP"] = "BTC-USDT-SWAP"
    horizon: Literal["30m", "24h", "72h"]
    baseline_candidate_id: str = Field(min_length=1, max_length=128)
    candidate_id: str = Field(min_length=1, max_length=128)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    selection_rule: Literal["first-occurrence-per-study.v1"] = "first-occurrence-per-study.v1"
    scoring_rule: Literal["paired-direction-close-1m.v1"] = "paired-direction-close-1m.v1"
    maximum_pairs: int = Field(default=100, ge=100, le=1000)
    pair_deadline_seconds: int = Field(default=120, ge=10, le=300)
    input_rule: Literal[
        "first-recorded-outlook-facts.v1", "first-recorded-facts-with-abstention.v2"
    ] = "first-recorded-facts-with-abstention.v2"
    window_rule: Literal["after-common-deadline.v1"] = "after-common-deadline.v1"

    @model_validator(mode="after")
    def check_window(self):
        if self.ends_at - self.starts_at < timedelta(days=30):
            raise ValueError("prospective_window_below_30_days")
        if self.baseline_candidate_id == self.candidate_id:
            raise ValueError("prospective_candidate_equals_baseline")
        return self


class TrialView(BaseModel):
    trial_id: str
    definition_hash: str
    registered_at: AwareDatetime
    definition: dict


class ProspectiveTrialService:
    def __init__(self, database, *, artifact_root: Path, clock=utcnow):
        self.database, self.artifact_root, self.clock = database, artifact_root, clock

    def _binding(self, session, identity, runtime, domain):
        candidate = session.get(CandidateVersionRecord, identity)
        if candidate is None or candidate.status not in {"active", "candidate", "experimental"}:
            raise ValueError("trial_candidate_unavailable")
        if candidate.execution_scope not in (None, "directional_synthesis"):
            raise ValueError("trial_execution_scope_mismatch")
        from packages.kernel.decision_hub_kernel.application.candidate_execution import (
            candidate_execution_error,
        )
        if error := candidate_execution_error(candidate, domain):
            raise ValueError(error)
        recipe = runtime_recipe(runtime)
        if not recipe.get("live_attestable"):
            raise ValueError("trial_requires_attestable_live_runtime")
        if candidate.content_ref:
            expected = (self.artifact_root / "candidates" / f"{identity}.json").resolve()
            if Path(candidate.content_ref).resolve() != expected:
                raise ValueError("trial_candidate_artifact_ref_invalid")
            body = expected.read_bytes()
            if hashlib.sha256(body).hexdigest() != candidate.content_hash:
                raise ValueError("trial_candidate_artifact_changed")
            artifact = json.loads(body)
            if (artifact.get("schema_version") != "candidate-artifact.v1"
                    or artifact.get("domain_pack_ref") != domain
                    or artifact.get("proposal") != recipe.get("proposal")
                    or recipe["proposal"]["version"] != candidate.version):
                raise ValueError("trial_candidate_recipe_mismatch")
            if artifact["proposal"].get("execution_scope") != candidate.execution_scope:
                raise ValueError("trial_candidate_scope_mismatch")
        else:
            baseline = {"schema_version": "release-baseline.v1", "domain_pack_ref": domain,
                        "version": candidate.version}
            if (candidate.source != "release" or recipe.get("proposal")
                    or recipe_hash(baseline) != candidate.content_hash):
                raise ValueError("trial_release_baseline_mismatch")
        return {"candidate_id": identity, "candidate_hash": candidate.content_hash,
                **({"execution_scope": candidate.execution_scope}
                   if candidate.execution_scope else {}),
                "candidate_version": candidate.version, "recipe": recipe,
                "recipe_hash": recipe_hash(recipe)}

    def register(self, request: TrialRequest, baseline_runtime, candidate_runtime) -> TrialView:
        now = as_utc(self.clock())
        with self.database.session() as session:
            baseline = self._binding(session, request.baseline_candidate_id,
                                     baseline_runtime, request.domain_pack_ref)
            candidate = self._binding(session, request.candidate_id,
                                      candidate_runtime, request.domain_pack_ref)
            if candidate["recipe"].get("proposal", {}).get("parent_version") != (
                baseline["candidate_version"]
            ):
                raise ValueError("trial_candidate_parent_mismatch")
            definition = {"schema_version": "prospective-trial.v1",
                          "request": request.model_dump(mode="json"),
                          "baseline": baseline, "candidate": candidate}
            definition_hash = recipe_hash(definition)
            stored = session.query(ProspectiveTrialRecord).filter_by(
                request_id=request.request_id,
            ).one_or_none()
            if stored:
                return self._matching(stored, definition_hash)
            if request.starts_at <= now:
                raise ValueError("trial_must_be_registered_before_start")
            identity = "trial_" + recipe_hash(request.request_id)[:32]
            row = ProspectiveTrialRecord(
                trial_id=identity, request_id=request.request_id, definition_hash=definition_hash,
                definition_json=canonical_recipe(definition), registered_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(row)
                    session.flush()
            except IntegrityError:
                prior = session.query(ProspectiveTrialRecord).filter_by(
                    request_id=request.request_id,
                ).one()
                return self._matching(prior, definition_hash)
            return self._view(row)

    def list(self, *, limit=20):
        with self.database.session() as session:
            return [self._view(row) for row in session.query(ProspectiveTrialRecord).order_by(
                ProspectiveTrialRecord.registered_at.desc(), ProspectiveTrialRecord.trial_id,
            ).limit(max(1, min(limit, 100)))]

    def get(self, trial_id):
        with self.database.session() as session:
            row = session.get(ProspectiveTrialRecord, trial_id)
            if row is None:
                raise ValueError("trial_not_found")
            return self._view(row)

    def assert_execution(self, trial_id, *, baseline_runtime, candidate_runtime, received_at):
        """Worker must pass this gate before admitting future inputs or any model call."""
        now = as_utc(self.clock())
        with self.database.session() as session:
            row = session.get(ProspectiveTrialRecord, trial_id)
            if row is None:
                raise ValueError("trial_not_found")
            view = self._view(row)
            request = TrialRequest.model_validate(view.definition["request"])
            received = as_utc(received_at)
            if not view.registered_at < request.starts_at <= received <= now < request.ends_at:
                raise ValueError("trial_input_outside_registered_window")
            for arm, runtime in (("baseline", baseline_runtime), ("candidate", candidate_runtime)):
                frozen = view.definition[arm]
                current = self._binding(session, frozen["candidate_id"], runtime,
                                        request.domain_pack_ref)
                if current != frozen:
                    raise ValueError("trial_execution_binding_changed")
                assert_recipe(runtime, frozen["recipe"])
            return view

    def _matching(self, row, expected_hash):
        if row.definition_hash != expected_hash:
            raise ValueError("trial_request_reused")
        return self._view(row)

    @staticmethod
    def _view(row):
        definition = json.loads(row.definition_json)
        if recipe_hash(definition) != row.definition_hash:
            raise ValueError("trial_definition_corrupt")
        return TrialView(trial_id=row.trial_id, definition_hash=row.definition_hash,
                         registered_at=as_utc(row.registered_at), definition=definition)
