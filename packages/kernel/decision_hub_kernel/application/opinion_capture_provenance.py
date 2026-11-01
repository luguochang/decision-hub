"""Re-read partial originals in existing library/command rows, never a second store."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal, cast

from sqlalchemy import select

from packages.contracts_py.decision_hub_contracts import AnalystClaim

from ..persistence.db import ProductCommandRecord, ResearchDocumentVersionRecord, as_utc
from ..ports.market_path import canonical_hash
from ..ports.opinion_capture import (
    CAPTURE_PARSER,
    IMPORT_SCHEMA_V2,
    capture_summary,
    parse_capture,
)


def claims_from_capture(
    capture, *, version_id, blocks, research_family, instrument_scopes, created_at
):
    """Verbatim captured quotations are author claims, including factual assertions.

    We do not require the author's prose to contain a prediction keyword. The
    whole retained sentence remains candidate/unverified regardless of wording.
    """
    if capture.capture_mode == "metadata_only":
        return ()
    if [block.content for block in blocks] != [item.text for item in capture.excerpts]:
        raise ValueError("opinion_capture_parser_changed_quote")
    output = []
    for index, block in enumerate(blocks, 1):
        conditional = bool(
            re.search(r"\bif\b|\bunless\b|\bwithout\b|如果|若", block.content, re.IGNORECASE)
        )
        output.append(
            AnalystClaim(
                schema_version="analyst-claim.v1",
                claim_id=f"claim_{version_id.removeprefix('docver_')}_{index}",
                version_id=version_id,
                claim_type="scenario" if conditional else "opinion",
                stance="unknown",
                claim_text=block.content,
                supporting_quote=block.content,
                locator_refs=[block.locator],
                market_scope=cast(
                    Literal["crypto_macro", "cn_equity", "cross_asset"], research_family
                ),
                instrument_scope=list(instrument_scopes),
                horizon=None,
                conditions=[block.content] if conditional else [],
                invalidation_conditions=[],
                status="candidate",
                attribution_confidence="medium",
                requires_independent_evidence=True,
                created_at=created_at,
                reviewed_at=None,
            )
        )
    return tuple(output)


def read_capture_provenance(session, *, signal, store, receipt_id, cutoff):
    if signal.source.extraction_version != CAPTURE_PARSER:
        return None
    if receipt_id is None:
        raise ValueError("opinion_capture_acquisition_required")
    version = session.get(ResearchDocumentVersionRecord, signal.version_id, populate_existing=True)
    if version is None:
        raise ValueError("opinion_capture_version_missing")
    raw = store.read(version.original_file_ref)
    capture = parse_capture(raw)
    summary = capture_summary(capture)
    if (
        hashlib.sha256(raw).hexdigest() != signal.content_hash
        or signal.source.source_id != capture.source_id
        or str(signal.source.canonical_url) != capture.canonical_url
        or signal.source.first_seen_at != capture.observed_at
        or signal.source.publication != capture.publication
        or signal.source.language != capture.language
        or signal.source.content_kind != "editorial"
        or signal.source.rights_ref != f"opinion-capture-permission://{capture.permission_hash}"
        or signal.source.syndication_cluster != capture.syndication_cluster
        or signal.author != capture.author
        or signal.supporting_quote not in {item.text for item in capture.excerpts}
        or signal.proposition != signal.supporting_quote
    ):
        raise ValueError("opinion_capture_claim_binding_invalid")
    rows = session.scalars(
        select(ProductCommandRecord)
        .where(
            ProductCommandRecord.kind == "public_opinion_import",
            ProductCommandRecord.target_id == signal.version_id,
        )
        .execution_options(populate_existing=True)
    ).all()
    if len(rows) != 1:
        raise ValueError("opinion_capture_import_receipt_ambiguous")
    row = rows[0]
    body = json.loads(row.result_json)
    if (
        row.owner_id != "system:public-opinion:v1"
        or body.get("schema_version") != IMPORT_SCHEMA_V2
        or canonical_hash(body) != row.payload_hash
        or hashlib.sha256(row.result_json.encode()).hexdigest() != row.payload_hash
        or as_utc(row.created_at) > cutoff
        or body.get("version_id") != signal.version_id
        or body.get("document_id") != signal.document_id
        or body.get("binding", {}).get("capture") != summary
        or body["binding"].get("original_hash") != signal.content_hash
        or body["binding"].get("title") != capture.title
        or body["binding"].get("author") != capture.author
        or body["binding"].get("acquisition_receipt_id") != receipt_id
        or body["binding"].get("source") != signal.source.model_dump(mode="json")
    ):
        raise ValueError("opinion_capture_import_receipt_invalid")
    return {**summary, "import_receipt_id": row.request_id, "import_receipt_hash": row.payload_hash}
