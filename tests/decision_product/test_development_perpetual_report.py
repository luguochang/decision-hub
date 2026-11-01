"""DEV retains the genuine V5 owner/original checks without a V4 issuer."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from packages.kernel.decision_hub_kernel.application import commit
from packages.kernel.decision_hub_kernel.application import (
    research_graph_history_versions as history,
)
from packages.kernel.decision_hub_kernel.application.perpetual_saved_report import (
    PerpetualSavedReportQuery,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRoleOutputRecord
from tests.decision_product.test_okx_perpetual_original import (
    persisted_original as persisted_original,
)
from tests.decision_product.test_perpetual_action_publication import (
    publication_case as publication_case,
)
from tests.decision_product.test_perpetual_research_binding import AT, OWNER
from tests.decision_product.test_perpetual_research_binding import binding_case as binding_case
from tests.test_development_runtime import layout as layout


def test_development_v5_read_still_authenticates_owner_source_and_artifact(
    layout: tuple[Path, Path], publication_case: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert history._REGISTRY_BINDING is None  # pyright: ignore[reportPrivateUsage]
    value = publication_case
    monkeypatch.setattr(commit, "utcnow", lambda: AT + timedelta(seconds=30))
    artifact_id, _gate = commit.CommitDecisionService(
        value["db"], clock=lambda: AT + timedelta(seconds=30),
    ).commit_research_result(
        value["request"].run_id, value["request"].event_id, value["final"].result,
        expected_lease_owner=value["lease_owner"], graph_final_ref=value["graph_final_ref"],
    )
    query = PerpetualSavedReportQuery(value["db"], now=lambda: AT + timedelta(seconds=50))
    run_id = value["request"].run_id
    saved = query.read(OWNER, run_id)
    assert saved["artifact_id"] == artifact_id
    assert saved["financial_qualified"] is saved["execution_authorized"] is False
    assert saved["bundle_export_status"] == "unsupported_perpetual_version"
    assert history._REGISTRY_BINDING is None  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(PermissionError, match="perpetual_report_owner_mismatch"):
        query.read("foreign-owner", run_id)
    with value["db"].session() as session:
        source = session.scalar(select(RunRoleOutputRecord).where(
            RunRoleOutputRecord.run_id == run_id,
            RunRoleOutputRecord.role == "research_publication_source",
        ))
        assert source is not None
        source.payload_json += " "
    with pytest.raises(ValueError):
        query.read(OWNER, run_id)
