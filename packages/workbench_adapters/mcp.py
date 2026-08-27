from __future__ import annotations

from typing import Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from packages.contracts_py.decision_hub_contracts.models import (
    CapabilityManifest,
    FeedbackCreate,
    FeedbackView,
    ResearchMemoCreate,
    ResearchMemoView,
    WorkbenchOverviewView,
)
from packages.kernel.decision_hub_kernel.application.workbench import WorkbenchAssetService


def build_core_mcp_server(
    workbench: WorkbenchAssetService,
    *,
    owner_id: str,
) -> MCPServer:
    """Build the official MCP SDK server over the Core Workbench port.

    The adapter exposes typed query/command tools only. MCP transport, session and
    protocol lifecycle remain owned by the official SDK; the Core service owns all
    persistence, idempotency and reference validation.
    """
    if not owner_id:
        raise ValueError("owner_identity_required")

    server = MCPServer(
        name="decision-hub-workbench",
        version="workbench-assets.v1",
        instructions=(
            "Use typed workbench queries to inspect Decision Hub assets. "
            "Research memo and feedback writes are owner-bound and never publish decisions."
        ),
    )

    @server.tool(
        name="workbench_overview",
        description="Return the bounded typed Workbench overview.",
        structured_output=True,
    )
    def workbench_overview(limit: int = 100) -> WorkbenchOverviewView:
        return workbench.overview(limit=limit)

    @server.tool(
        name="list_research_memos",
        description="List submitted research memos without raw provider/session state.",
        structured_output=True,
    )
    def list_research_memos(limit: int = 100) -> list[ResearchMemoView]:
        return workbench.list_memos(limit=max(1, min(limit, 500)))

    @server.tool(
        name="list_feedback",
        description="List owner feedback attached to existing Core assets.",
        structured_output=True,
    )
    def list_feedback(limit: int = 100) -> list[FeedbackView]:
        return workbench.list_feedback(limit=max(1, min(limit, 500)))

    @server.tool(
        name="list_capabilities",
        description="List discovered and owner-audited capability manifests.",
        structured_output=True,
    )
    def list_capabilities() -> list[CapabilityManifest]:
        return workbench.list_capabilities()

    @server.tool(
        name="submit_research_memo",
        description="Submit an owner research memo linked to an existing Run or Snapshot.",
        structured_output=True,
    )
    def submit_research_memo(
        request_id: str,
        domain_pack_ref: str,
        created_by: str,
        claims: list[str],
        evidence_refs: list[str],
        run_id: str | None = None,
        snapshot_id: str | None = None,
        counterpoints: list[str] | None = None,
        uncertainties: list[str] | None = None,
        follow_up_questions: list[str] | None = None,
    ) -> ResearchMemoView:
        _require_owner(created_by, owner_id)
        request = ResearchMemoCreate(
            request_id=request_id,
            run_id=run_id,
            snapshot_id=snapshot_id,
            domain_pack_ref=domain_pack_ref,
            created_by=created_by,
            claims=claims,
            evidence_refs=evidence_refs,
            counterpoints=counterpoints or [],
            uncertainties=uncertainties or [],
            follow_up_questions=follow_up_questions or [],
        )
        try:
            return workbench.create_memo(request)
        except (ValueError, PermissionError) as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        name="submit_feedback",
        description="Submit owner feedback for an existing Core Workbench asset.",
        structured_output=True,
    )
    def submit_feedback(
        request_id: str,
        target_type: Literal[
            "run", "memo", "experiment", "candidate", "experience", "failure_pattern"
        ],
        target_id: str,
        created_by: str,
        verdict: Literal["useful", "not_useful", "incorrect", "needs_review"],
        notes: str,
    ) -> FeedbackView:
        _require_owner(created_by, owner_id)
        request = FeedbackCreate(
            request_id=request_id,
            target_type=target_type,
            target_id=target_id,
            created_by=created_by,
            verdict=verdict,
            notes=notes,
        )
        try:
            return workbench.create_feedback(request)
        except (ValueError, PermissionError) as exc:
            raise ToolError(str(exc)) from exc

    return server


def _require_owner(declared_owner: str, configured_owner: str) -> None:
    if declared_owner != configured_owner:
        raise ToolError("owner_identity_mismatch")
