"""Fetch actual public pages into an isolated evidence ledger; no LLM or formal Run writes."""

import argparse
import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from packages.contracts_py.decision_hub_contracts import (
    EvidenceRequirement,
    ResearchCapabilityQuery,
)
from packages.kernel.decision_hub_kernel.application.fact_lineage import FactLineageStore
from packages.kernel.decision_hub_kernel.application.research_evidence import (
    ResearchEvidenceService,
)
from packages.kernel.decision_hub_kernel.application.run import RunService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.provider_adapters.research.document_store import ContentAddressedDocumentStore
from packages.provider_adapters.research.documents import HttpDocumentResearchAdapter
from packages.provider_adapters.research.raw_capture import (
    CaptureBatch,
    RawBodyArchive,
    capture_scope,
)
from packages.provider_adapters.research.source_registry import ResearchSourceRegistry


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    database_path = (args.output_dir / "evidence.sqlite3").resolve().as_posix()
    db = Database(f'sqlite+pysqlite:///{database_path}')
    db.create_all()
    try:
        run_id, _ = RunService(db).create('publication-time-probe')
        requirement = EvidenceRequirement(
            requirement_id='market_narrative', description='Time audit of public materials',
            importance='soft', source_priority=['official'], authority_floor='verified_web',
            preferred_capabilities=['web.fetch'], freshness_seconds=86400,
            minimum_independent_sources=1, allowed_fallbacks=[], confidence_cap=0.5,
        )
        adapter = HttpDocumentResearchAdapter(
            capability_id='web.fetch', kind='web', authority='verified_web', source_id='web-fetch',
            source_registry=ResearchSourceRegistry.from_pack(Path('packs/crypto_macro')),
            document_store=ContentAddressedDocumentStore(args.output_dir / 'documents'),
        )
        results = []
        for index, url in enumerate([
            'https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm',
            'https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm',
        ]):
            now = datetime.now(UTC)
            query = ResearchCapabilityQuery(
                schema_version='research-capability-query.v1', request_id=f'publication-{index}',
                capability_id='web.fetch', requirement_id='market_narrative',
                query='publication time and policy statement', target_url=url,
                symbols=[], fields=[], allowed_domains=['federalreserve.gov'], max_results=1,
                max_cost_usd=0, research_session_id='isolated-publication-probe', round=1,
                mode='live', observed_at=now, cutoff_at=now + timedelta(seconds=60),
            )
            batch = CaptureBatch(f'publication-probe:{index}', 'publication-time.v1',
                                 RawBodyArchive(args.output_dir / 'raw-captures'),
                                 FactLineageStore(db).record_capture)
            with capture_scope(batch):
                result = await adapter.execute(query)
            accepted = ResearchEvidenceService(db).accept_candidates(
                run_id=run_id, capability_id='web.fetch', candidates=result.evidence_candidates,
                requirements={'market_narrative': requirement}, cutoff_at=result.completed_at,
            )
            candidate = accepted[0]
            results.append({'url':url, 'published_at':candidate.published_at.isoformat()
                            if candidate.published_at else None,
                            'observed_at':candidate.observed_at.isoformat(),
                            'freshness_status':candidate.freshness_status,
                            'quality':candidate.quality, 'parser_ref':candidate.parser_ref,
                            'content_ref':candidate.content_ref,
                            'captures':[c['capture_id'] for c in batch.captures]})
        proof = {'mode':'isolated_actual_http_no_model', 'formal_database_written':False,
                 'results':results}
        (args.output_dir / 'proof.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        print(json.dumps(proof))
    finally:
        db.engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
