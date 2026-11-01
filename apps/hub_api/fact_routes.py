import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import AwareDatetime, BaseModel

from packages.contracts_py.decision_hub_contracts.generated.research_fact import (
    ArchivedInputView,
    FactVersionListView,
    FactVersionView,
    RawCaptureView,
)
from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
from packages.kernel.decision_hub_kernel.application.spot_evidence_captures import (
    spot_evidence_captures_in_session,
)
from packages.kernel.decision_hub_kernel.persistence.db import (
    EvidenceParsedLinkRecord,
    ParsedVersionRecord,
    RawCaptureRecord,
    ResearchEvidenceRecord,
    as_utc,
    utcnow,
)
from packages.provider_adapters.research.raw_capture import RawBodyArchive, safe_url
from packages.workbench_adapters.fact_assets import FactAssetResolver


class FactInstrumentOption(BaseModel):
    instrument: str
    metric_family: str


class FactFilterOptions(BaseModel):
    as_of: AwareDatetime
    fields: list[str]
    sources: list[str]
    instruments: list[FactInstrumentOption]


def fact_router(database, data_root: Path):
    router = APIRouter(tags=["fact-lineage"])
    store = FactLineageStore(database)
    archive = RawBodyArchive(data_root / "raw-captures")
    market_store = MarketPathStore(database, archive)
    assets = FactAssetResolver(data_root)

    @router.get("/v1/facts/versions", response_model=FactVersionListView)
    def versions(
        as_of: AwareDatetime | None = None,
        market: str | None = None,
        instrument: str | None = None,
        field: str | None = None,
        source_id: str | None = None,
        fact_instrument: str | None = None,
        metric_family: str | None = None,
        semantic_key: str | None = None,
        limit: int = Query(30, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return store.list_versions(
            as_of=as_of or utcnow(),
            market=market,
            instrument=instrument,
            field=field,
            source_id=source_id,
            fact_instrument=fact_instrument,
            metric_family=metric_family,
            semantic_key=semantic_key,
            limit=limit,
            offset=offset,
        )

    @router.get("/v1/facts/options", response_model=FactFilterOptions)
    def filter_options(as_of: AwareDatetime | None = None):
        return store.filter_options(as_of=as_of or utcnow())

    @router.get("/v1/facts/versions/{version_id}", response_model=FactVersionView)
    def version(version_id: str):
        try:
            return store.get_version(version_id)
        except KeyError as exc:
            raise HTTPException(404, "fact_version_not_found") from exc

    @router.get("/v1/facts/versions/{version_id}/input", response_model=ArchivedInputView)
    def archived_input(version_id: str, partition: int | None = Query(None, ge=0)):
        try:
            return assets.read(store.get_version(version_id), partition)
        except KeyError as exc:
            raise HTTPException(404, str(exc.args[0])) from exc
        except (ValueError, OSError) as exc:
            raise HTTPException(409, "fact_input_archive_unavailable") from exc

    @router.get("/v1/research/runs/{run_id}/evidence/{evidence_id}/captures",
                response_model=list[RawCaptureView])
    def evidence_captures(run_id: str, evidence_id: str):
        with database.session() as session:
            evidence = session.get(ResearchEvidenceRecord, evidence_id)
            if evidence is None or evidence.run_id != run_id:
                raise HTTPException(404, "research_evidence_not_found")
            try:
                captures = spot_evidence_captures_in_session(
                    session, run_id=run_id, evidence=evidence, store=market_store,
                )
            except (ValueError, OSError) as exc:
                code = str(exc)
                if not code.startswith("research_spot_capture_"):
                    code = "research_spot_capture_original_invalid"
                raise HTTPException(409, code) from exc
            if captures is None:
                link = session.get(EvidenceParsedLinkRecord, evidence_id)
                parsed = session.get(ParsedVersionRecord, link.parsed_version_id) if link else None
                captures = [session.get(RawCaptureRecord, identity)
                            for identity in json.loads(parsed.capture_ids_json)] if parsed else []
            if any(c is None for c in captures):
                raise HTTPException(409, "research_capture_receipt_missing")
            # This is the complete retained call input set, which may span sources.
            # Preserve each recorded endpoint without exposing context IDs or headers.
            return [{"capture_id": c.capture_id, "url": safe_url(c.url),
                     "body_ref": c.body_ref, "body_hash": c.body_hash,
                     "status_code": c.status_code, "byte_count": c.byte_count,
                     "media_type": c.media_type, "started_at": as_utc(c.started_at),
                     "completed_at": as_utc(c.completed_at)} for c in captures]

    @router.get("/v1/raw-captures/{capture_id}/body")
    def raw_body(capture_id: str):
        with database.session() as session:
            row = session.get(RawCaptureRecord, capture_id)
            if row is None or not row.body_ref:
                raise HTTPException(404, "raw_capture_not_found")
            ref = row.body_ref
        try:
            body = archive.read(ref)
        except (OSError, ValueError) as exc:
            raise HTTPException(409, "raw_capture_archive_unavailable") from exc
        # Never execute downloaded HTML/JS under the product origin.
        return Response(
            body,
            media_type="text/plain",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    return router
