from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from packages.kernel.decision_hub_kernel.persistence.db import ResearchEvidenceRecord
from packages.provider_adapters.research.document_store import ContentAddressedDocumentStore


def evidence_original_router(database, archive_root: Path) -> APIRouter:
    router = APIRouter()
    documents = ContentAddressedDocumentStore(archive_root)

    @router.get("/v1/research/runs/{run_id}/evidence/{evidence_id}/original")
    def original(run_id: str, evidence_id: str) -> PlainTextResponse:
        with database.session() as session:
            row = session.get(ResearchEvidenceRecord, evidence_id)
            if row is None or row.run_id != run_id:
                raise HTTPException(404, "这份证据不属于当前研究，无法读取原文。")
            if not row.content_ref or not row.source_content_hash or not row.parser_ref:
                raise HTTPException(
                    404, "这份证据未保存可直接阅读的原文，请返回报告查看摘录与来源。"
                )
            try:
                asset = documents.load(
                    row.content_ref, expected_source_content_hash=row.source_content_hash
                )
                if asset.parser_ref != row.parser_ref:
                    raise ValueError("research_document_parser_mismatch")
            except (OSError, ValueError):
                raise HTTPException(
                    409, "已保存的原文缺失或未通过完整性校验，请保留证据记录并联系维护人员核查。"
                ) from None
        return PlainTextResponse(
            asset.text,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "X-Content-SHA256": asset.source_content_hash,
            },
        )

    return router
