"""Actual policy originals must be read without reserving the SQLite writer."""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import FrameType
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from packages.kernel.decision_hub_kernel.persistence import original_preparation as preparation
from packages.kernel.decision_hub_kernel.persistence.db import (
    ResearchEvidenceRecord,
    RunRecord,
    utcnow,
)
from tests.decision_product.test_action_research_reader import (
    accepted_draft,
    draft,
    forbid_external_connections,
    isolated_reader_data_dir,
)
from tests.research.test_graph_citation_membership_v2 import SPOT_A
from tests.research.test_graph_citation_publication_v2 import Draft, _publish
from tests.research.test_publication_short_writer import (
    _heartbeat,
)
from tests.research.test_research_publication_consumer_v2 import _sql

pytestmark = pytest.mark.usefixtures("forbid_external_connections")
assert all((draft, accepted_draft, forbid_external_connections, isolated_reader_data_dir))


def test_complete_commit_policy_authentication_leaves_writer_available(draft: Draft) -> None:
    entered, release = threading.Event(), threading.Event()
    writer_modes: list[bool] = []

    def trace(frame: FrameType, event: str, arg: Any) -> None:
        del arg
        if entered.is_set() or event != "call" or frame.f_code.co_name != "_policy_materials":
            return
        parent = frame.f_back
        while parent is not None:
            if parent.f_code.co_name in {
                "_prepare_commit_policy_in_session",
                "_prepare_commit_policy_inputs_in_session",
            }:
                writer_modes.append(bool(frame.f_locals.get("writer", False)))
                entered.set()
                assert release.wait(12), "test did not release policy authentication"
                return
            parent = parent.f_back

    def publish() -> str:
        sys.setprofile(trace)
        try:
            return _publish(draft)
        finally:
            sys.setprofile(None)

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(publish)
        try:
            assert entered.wait(60), "the real policy preparation path was not reached"
            _heartbeat(draft.source.database)
        finally:
            release.set()
        assert task.result(timeout=120)
    assert writer_modes == [False]


def test_commit_still_rejects_original_drift_after_writes(
    draft: Draft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = preparation.PreparedOriginals.verify_after_writes
    before = _sql(draft.source)

    def changed(self: Any, session: Session) -> None:
        session.execute(
            update(ResearchEvidenceRecord)
            .where(
                ResearchEvidenceRecord.evidence_id == SPOT_A,
            )
            .values(source_id="changed-after-original-authentication")
        )
        verify(self, session)

    monkeypatch.setattr(preparation.PreparedOriginals, "verify_after_writes", changed)
    with pytest.raises(ValueError, match="original_changed"):
        _publish(draft)
    assert _sql(draft.source) == before


def test_commit_still_rejects_expired_lease_after_readset_verification(
    draft: Draft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    verify = preparation.PreparedOriginals.verify
    before = _sql(draft.source)

    def expired(self: Any, session: Session) -> None:
        verify(self, session)
        session.execute(
            update(RunRecord)
            .where(
                RunRecord.run_id == draft.source.request.run_id,
            )
            .values(lease_expires_at=utcnow() - timedelta(seconds=1))
        )

    monkeypatch.setattr(preparation.PreparedOriginals, "verify", expired)
    with pytest.raises(PermissionError, match="lease_lost|permission_lost"):
        _publish(draft)
    assert _sql(draft.source) == before
