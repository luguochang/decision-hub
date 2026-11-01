import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.hub_api.evidence_original_routes import evidence_original_router
from packages.kernel.decision_hub_kernel.persistence.db import ResearchEvidenceRecord
from tests.research.test_document_readback import _query, _runtime


@pytest.mark.asyncio
async def test_saved_original_is_run_scoped_plain_text_and_verified(tmp_path):
    db, run_id, session_id, store, gateway, body = _runtime(tmp_path)
    result = await gateway.execute(
        _query(
            session_id,
            request_id="original-api",
            capability_id="web.fetch",
            query="prior stance restrictive baseline",
            target_url="https://www.federalreserve.gov/newsevents/speech/waller-test.htm",
        )
    )
    evidence = result.evidence_candidates[0]
    app = FastAPI()
    app.include_router(evidence_original_router(db, store.root))
    url = f"/v1/research/runs/{run_id}/evidence/{evidence.evidence_id}/original"
    with TestClient(app) as client:
        response = client.get(url)
        assert response.status_code == 200
        assert response.text == body
        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-content-sha256"] == evidence.source_content_hash
        assert client.get(url.replace(run_id, "other-run")).status_code == 404
        path = store.root / (evidence.content_ref.rsplit("/", 1)[1] + ".json")
        path.write_bytes(b"corrupt original")
        assert client.get(url).status_code == 409
        with db.session() as session:
            session.get(ResearchEvidenceRecord, evidence.evidence_id).content_ref = None
        assert client.get(url).status_code == 404
