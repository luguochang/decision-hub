"""Focused exact-retained32 shape-scope controls; no product DB/HTTP/model."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sqlite3
from contextvars import copy_context
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import ConfigDict, StrictBool, StrictInt, ValidationError, model_validator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.application import research_graph_history_versions as h

RECIPE = "f9c9a3297a5805d2843fa6d11e22e2e193ede364ba4c641a2559b704df3f7bbd"
BASE = "packages/contracts_py/decision_hub_contracts/binding_contract_base.py"


@pytest.fixture(scope="module")
def original():
    root = Path(__file__).resolve().parents[2]
    # DEV may not contain the retained copy; an explicit immutable archive
    # fixture can be supplied without changing the imported candidate source.
    archives = Path(os.environ.get(
        "RETAINED_SHAPE_ARCHIVES", root / "packages/kernel/research_recipe_versions",
    ))
    directory = archives / RECIPE
    raw = (directory / "release.json").read_bytes()
    index = json.loads(raw)
    payloads = {}
    for name, digest in index["all_files"].items():
        path = directory / "root" / name
        if not path.exists():
            path = root / name
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        payloads[name] = data
    assert hashlib.sha256(payloads[BASE]).hexdigest() == (
        "eb2013b8fe90cd6495b64557a00a211c6410cd20c3bd75d68ded0c7843ef40f3"
    )
    registration = h._Registration(RECIPE, hashlib.sha256(raw).hexdigest(), RECIPE,
                                    index["selector_identity"]["implementation_hash"], True)
    private = h._PrivateRelease(registration, payloads, directory / "root", v3=True)
    binding = private.load(BASE.removesuffix(".py").replace("/", "."))

    class Probe(binding.ResearchBindingContract):
        model_config = ConfigDict(extra="forbid", revalidate_instances="always")
        flag: StrictBool
        value: StrictInt
        checks: ClassVar[int] = 0
        reject: ClassVar[bool] = False

        @model_validator(mode="after")
        def fresh_semantics(self):
            type(self).checks += 1
            if type(self).reject:
                raise ValueError("probe_semantics_rejected")
            return self

    yield private, binding, Probe
    private.discard()


@pytest.fixture
def snapshot():
    native = sqlite3.connect(":memory:")
    native.execute("PRAGMA query_only=ON")
    engine = create_engine("sqlite://", creator=lambda: native)
    with Session(engine) as session:
        session.connection().exec_driver_sql("BEGIN")
        yield session, native
        assert native.total_changes == 0
    engine.dispose()


def test_actual_private_v3_scope_reuses_only_shape_and_keeps_fresh_semantics(original, snapshot):
    private, binding, probe = original
    session, native = snapshot
    payload = {"flag": True, "value": 1}
    before = probe.checks
    with h._private_v3_read_scope(session, private, "fixture_run", reuse_provider_usage=True):
        memo = binding._SHAPE_MEMO.get()
        assert memo is not None
        first = probe.model_validate(payload)
        second = probe.model_validate(copy.deepcopy(payload))
        assert first == second and first is not second
        assert probe.checks == before + 2
        assert memo.stats.hit_count == memo.stats.miss_count == 1
        probe.reject = True
        try:
            with pytest.raises(ValidationError, match="probe_semantics_rejected"):
                probe.model_validate(payload)
        finally:
            probe.reject = False
        assert memo.stats.hit_count == 2 and probe.checks == before + 3
    assert not memo.active and not memo.successes and not memo.schemas
    assert memo.retained_bytes == 0 and binding._SHAPE_MEMO.get() is None
    assert native.total_changes == 0


def test_payload_and_reachable_schema_changes_cannot_reuse_prior_success(original, snapshot):
    private, binding, probe = original
    session, _native = snapshot
    payload = {"flag": True, "value": 1}
    validator = binding._validator(probe)
    value_schema = validator.schema["properties"]["value"]
    with h._private_v3_read_scope(session, private, "fixture_run", reuse_provider_usage=True):
        memo = binding._SHAPE_MEMO.get()
        probe.model_validate(payload)
        payload["flag"] = 1
        for _ in range(2):
            with pytest.raises(ValidationError):
                probe.model_validate(payload)
        assert memo.stats.hit_count == 0 and memo.stats.miss_count == 3
        payload["flag"] = True
        value_schema["maximum"] = 0
        try:
            with pytest.raises(ValidationError):
                probe.model_validate(payload)
        finally:
            value_schema.pop("maximum")
        assert memo.stats.hit_count == 0 and memo.stats.miss_count == 4


def test_fresh_transaction_and_closed_copied_context_do_not_retain_success(original, snapshot):
    private, binding, probe = original
    session, native = snapshot
    payload = {"flag": True, "value": 1}
    with h._private_v3_read_scope(session, private, "fixture_run", reuse_provider_usage=True):
        first = binding._SHAPE_MEMO.get()
        probe.model_validate(payload)
        copied = copy_context()
    copied.run(lambda: probe.model_validate(payload))
    assert first.stats.hit_count == 0 and first.stats.miss_count == 1
    session.rollback()
    session.connection().exec_driver_sql("BEGIN")
    with h._private_v3_read_scope(session, private, "fixture_run", reuse_provider_usage=True):
        fresh = binding._SHAPE_MEMO.get()
        assert fresh is not first
        probe.model_validate(payload)
        assert fresh.stats.hit_count == 0 and fresh.stats.miss_count == 1
    with pytest.raises(
        ValueError, match="research_graph_read_boundary_changed|research_usage_read_scope_changed",
    ):
        with h._private_v3_read_scope(session, private, "fixture_run", reuse_provider_usage=True):
            broken = binding._SHAPE_MEMO.get()
            probe.model_validate(payload)
            native.commit()
            native.execute("BEGIN")
    assert not broken.active and not broken.successes
    assert binding._SHAPE_MEMO.get() is None and h._V3_READ_SCOPE.get() is None


def test_unknown_bytes_and_non_quality_call_keep_legacy_unscoped_validation(original):
    private, binding, probe = original
    payload = {"flag": True, "value": 1}
    data = private.payloads[BASE]
    for enabled, change in ((False, False), (True, True)):
        if change:
            private.payloads[BASE] = data + b"\n"
        try:
            with h._private_shape_read_scope(private, enabled=enabled):
                assert binding._SHAPE_MEMO.get() is None
                probe.model_validate(payload)
                with pytest.raises(ValidationError):
                    probe.model_validate({"flag": 1, "value": 1})
        finally:
            private.payloads[BASE] = data


def test_replay_private_namespace_scope_is_independent_of_current_preparation(original, snapshot):
    from packages.contracts_py.decision_hub_contracts import binding_contract_base as current
    from packages.kernel.decision_hub_kernel.persistence.original_preparation import (
        _current_shape_read_scope,
    )

    private, binding, probe = original
    session, _native = snapshot
    with _current_shape_read_scope():
        current_memo = current._SHAPE_MEMO.get()
        with h._private_v3_read_scope(session, private, "fixture_run", reuse_shape=True):
            memo = binding._SHAPE_MEMO.get()
            assert memo is not None and memo is not current_memo
            probe.model_validate({"flag": True, "value": 1})
            probe.model_validate({"flag": True, "value": 1})
            assert memo.stats.hit_count == memo.stats.miss_count == 1
            assert current_memo.stats.hit_count == current_memo.stats.miss_count == 0
        assert not memo.active and current_memo.active
    assert not current_memo.active


def test_original_scope_loaded_code_change_rejected_at_entry_and_exit(original):
    private, binding, _probe = original
    original_scope = binding.shape_validation_scope
    binding.shape_validation_scope = lambda: None
    try:
        with pytest.raises(ValueError, match="research_history_original_shape_scope_changed"):
            with h._private_shape_read_scope(private, enabled=True):
                pytest.fail("changed original scope entered")
    finally:
        binding.shape_validation_scope = original_scope
    original_owned = binding._owned
    try:
        with pytest.raises(ValueError, match="research_history_original_shape_scope_changed"):
            with h._private_shape_read_scope(private, enabled=True):
                memo = binding._SHAPE_MEMO.get()
                binding._owned = lambda _memo: True
    finally:
        binding._owned = original_owned
    assert not memo.active and binding._SHAPE_MEMO.get() is None


def test_current_shape_bridge_replacement_cannot_enter_quality_storage(monkeypatch):
    h._require_quality_storage_implementation()
    monkeypatch.setattr(h, "_private_shape_read_scope", lambda *_args, **_kwargs: None)
    with pytest.raises(ValueError, match="research_history_quality_storage_implementation_changed"):
        h._require_quality_storage_implementation()
