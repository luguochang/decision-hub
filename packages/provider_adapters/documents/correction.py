from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts_py.decision_hub_contracts import (
    CorrectionChange,
    DocumentBlock,
    DocumentCorrection,
)
from packages.kernel.decision_hub_kernel.ports.documents import (
    CorrectionResult,
    DocumentCorrectionPort,
    ParsedDocument,
)
from packages.kernel.decision_hub_kernel.ports.runtime import AgentRequest, AgentRuntime
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore

_RISK_RE = re.compile(
    r"(?:[-+]?\d+(?:\.\d+)?%?|\b(?:买入|卖出|做多|做空|看多|看空|不破|突破|跌破|预期|前值|实际)\b)"
)


class FinancialSemanticCorrectionAdapter(DocumentCorrectionPort):
    """Safe baseline correction plus an explicit seam for a structured model.

    The baseline only normalizes Unicode/whitespace. It never guesses a missing
    digit or changes a directional claim. A future model adapter can supply
    additional changes, which are always marked for review when risky tokens
    change.
    """

    correction_model_ref = "financial-correction.deterministic.v1"
    prompt_ref = "financial-document-correction.v1"

    def __init__(self, store: DocumentBundleStore) -> None:
        self.store = store

    async def correct(
        self,
        document: ParsedDocument,
        *,
        title: str,
        market_scopes: tuple[str, ...],
        instrument_scopes: tuple[str, ...],
        privacy_mode: str = "local_only",
    ) -> CorrectionResult:
        changes: list[CorrectionChange] = []
        corrected_blocks: list[DocumentBlock] = []
        unresolved: list[dict[str, object]] = []
        for block in document.blocks:
            corrected = _normalize(block.content)
            changed = corrected != block.content
            if changed:
                risky = bool(_RISK_RE.search(block.content)) or bool(_RISK_RE.search(corrected))
                changes.append(
                    CorrectionChange(
                        schema_version="document-correction-change.v1",
                        change_id=f"change:{block.block_id}",
                        page_no=block.page_no,
                        block_id=block.block_id,
                        original=block.content,
                        corrected=corrected,
                        reason="unicode_and_whitespace_normalization",
                        confidence="high" if not risky else "medium",
                        requires_user_confirmation=risky,
                        accepted=not risky,
                    )
                )
            corrected_blocks.append(
                block.model_copy(
                    update={
                        "content": corrected,
                        "needs_review": block.needs_review
                        or changed
                        and bool(_RISK_RE.search(corrected)),
                    }
                )
            )
            if "�" in block.content or "[UNREADABLE]" in block.content:
                unresolved.append(
                    {
                        "page_no": block.page_no,
                        "block_id": block.block_id,
                        "text": block.content,
                        "reason": "source_contains_unreadable_text",
                    }
                )
        corrected_text = "\n".join(block.content for block in corrected_blocks).strip()
        corrected_ref = (
            self.store.write_text(corrected_text, name="corrected.md") if corrected_text else None
        )
        pending = any(
            item.requires_user_confirmation and not item.accepted for item in changes
        ) or bool(unresolved)
        status = "needs_review" if pending else "ready_for_correction"
        correction = DocumentCorrection(
            schema_version="document-correction.v1",
            correction_id=f"corr_{document.receipt.version_id.replace('docver_', '')}",
            version_id=document.receipt.version_id,
            correction_model_ref=self.correction_model_ref,
            prompt_ref=self.prompt_ref,
            corrected_content_ref=corrected_ref,
            quality_status=status,
            changes=changes,
            unresolved_fragments=unresolved,
            created_at=datetime.now(UTC),
            accepted_at=None,
        )
        return CorrectionResult(
            correction=correction,
            corrected_blocks=tuple(corrected_blocks),
            corrected_text=corrected_text,
        )


class ModelCorrectionChange(BaseModel):
    """The only mutation a remote correction model is allowed to propose."""

    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1, max_length=128)
    original: str = Field(max_length=20_000)
    corrected: str = Field(max_length=20_000)
    reason: str = Field(min_length=1, max_length=1_000)
    confidence: str = Field(pattern="^(high|medium|low)$")


class FinancialSemanticCorrectionPayload(BaseModel):
    """Strict structured output for optional model-assisted correction.

    The model returns block-level proposals, never a replacement document. The
    adapter verifies every proposal against the immutable source block before it
    can be persisted.
    """

    model_config = ConfigDict(extra="forbid")

    changes: list[ModelCorrectionChange] = Field(default_factory=list, max_length=500)
    unresolved: list[str] = Field(default_factory=list, max_length=200)


class ModelFinancialSemanticCorrectionAdapter(FinancialSemanticCorrectionAdapter):
    """Run a structured financial correction model behind an explicit opt-in.

    Local-only material never reaches the runtime. Any model mutation remains a
    review diff, and malformed or stale block references are recorded as
    unresolved instead of being silently applied.
    """

    prompt_ref = "financial-document-correction.v2"

    def __init__(self, store: DocumentBundleStore, runtime: AgentRuntime) -> None:
        super().__init__(store)
        self.runtime = runtime

    async def correct(
        self,
        document: ParsedDocument,
        *,
        title: str,
        market_scopes: tuple[str, ...],
        instrument_scopes: tuple[str, ...],
        privacy_mode: str = "local_only",
    ) -> CorrectionResult:
        if privacy_mode != "remote_allowed" or not bool(getattr(self.runtime, "enabled", False)):
            return await super().correct(
                document,
                title=title,
                market_scopes=market_scopes,
                instrument_scopes=instrument_scopes,
                privacy_mode=privacy_mode,
            )

        evidence = tuple(block.block_id for block in document.blocks)
        source = "\n".join(
            f"[{block.block_id}] page={block.page_no} type={block.block_type}\n{block.content}"
            for block in document.blocks
        )
        request = AgentRequest(
            role="financial_document_correction",
            text=(
                f"Title: {title}\nMarket scopes: {', '.join(market_scopes)}\n"
                f"Instrument scopes: {', '.join(instrument_scopes) or '(none)'}\n"
                "Source blocks (the block id and original text are immutable):\n"
                + source
            ),
            evidence=evidence,
            deadline_at=datetime.now(UTC) + timedelta(seconds=90),
            max_tokens=8_000,
            instructions=(
                "Return JSON matching the correction schema.",
                "Only propose a block-level correction when the source clearly contains "
                "an OCR, encoding, or financial terminology error.",
                "Never invent missing numbers, dates, prices, units, authors, or "
                "directional claims.",
                "The original field must exactly equal the supplied block content.",
                "If evidence is insufficient, put the block id in unresolved and do not change it.",
            ),
        )
        result = await self.runtime.execute(request)
        payload = FinancialSemanticCorrectionPayload.model_validate(result.payload)
        by_id = {block.block_id: block for block in document.blocks}
        changes: list[CorrectionChange] = []
        unresolved: list[dict[str, object]] = []
        corrected_by_id = {block.block_id: _normalize(block.content) for block in document.blocks}
        for block_id in payload.unresolved:
            block = by_id.get(block_id)
            if block is not None:
                unresolved.append(
                    {
                        "page_no": block.page_no,
                        "block_id": block.block_id,
                        "text": block.content,
                        "reason": "model_requested_more_evidence",
                    }
                )
        for proposed in payload.changes:
            block = by_id.get(proposed.block_id)
            if block is None or proposed.original != block.content:
                unresolved.append(
                    {
                        "page_no": block.page_no if block else 1,
                        "block_id": proposed.block_id,
                        "text": proposed.original,
                        "reason": "model_block_reference_mismatch",
                    }
                )
                continue
            if proposed.corrected == block.content:
                continue
            corrected_by_id[block.block_id] = proposed.corrected
            changes.append(
                CorrectionChange(
                    schema_version="document-correction-change.v1",
                    change_id=f"change:{block.block_id}",
                    page_no=block.page_no,
                    block_id=block.block_id,
                    original=block.content,
                    corrected=proposed.corrected,
                    reason=proposed.reason,
                    confidence=proposed.confidence,  # type: ignore[arg-type]
                    requires_user_confirmation=True,
                    accepted=False,
                )
            )
        changed_ids = {change.block_id for change in changes}
        corrected_blocks = tuple(
            block.model_copy(
                update={
                    "content": corrected_by_id[block.block_id],
                    "needs_review": block.needs_review or block.block_id in changed_ids,
                }
            )
            for block in document.blocks
        )
        corrected_text = "\n".join(block.content for block in corrected_blocks).strip()
        corrected_ref = (
            self.store.write_text(corrected_text, name="corrected.md") if corrected_text else None
        )
        pending = bool(unresolved or changes)
        model_name = getattr(getattr(self.runtime, "provider_config", None), "model", "configured")
        correction = DocumentCorrection(
            schema_version="document-correction.v1",
            correction_id=f"corr_{document.receipt.version_id.replace('docver_', '')}",
            version_id=document.receipt.version_id,
            correction_model_ref=f"financial-correction.{model_name}.v2",
            prompt_ref=self.prompt_ref,
            corrected_content_ref=corrected_ref,
            quality_status="needs_review" if pending else "ready_for_correction",
            changes=changes,
            unresolved_fragments=unresolved,
            created_at=datetime.now(UTC),
            accepted_at=None,
        )
        return CorrectionResult(
            correction=correction,
            corrected_blocks=corrected_blocks,
            corrected_text=corrected_text,
        )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = re.sub(r"[ \t]+", " ", normalized)
    return normalized.strip()
