"""In-process route callbacks only, private DB/carrier; no HTTP or issuer pass."""
import importlib.util
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.responses import Response

from test_publication_jobs48 import durable, boundary, actual39, OWNER, prior

source=Path(__file__).resolve().parents[2]/'artifacts/market-risk-implementation-20261007/B2/report-serving-r3-48/source/apps/hub_api/research_quality_routes.py'
spec=importlib.util.spec_from_file_location('_private_report_routes48',source)
routes=importlib.util.module_from_spec(spec);spec.loader.exec_module(routes)


def callbacks(db,life):
    router=routes.research_quality_router(db,publication_lifecycle=life,clock=lambda:prior.NOW)
    return {route.path.rsplit('/',1)[1]:route.endpoint for route in router.routes}


def test_pending_route_409_is_not_an_original_integrity_failure(durable,monkeypatch):
    db,life,_,carrier,_=durable
    def forbidden(*args,**kwargs):raise AssertionError('pending GET must never run full reader')
    monkeypatch.setattr(routes.ResearchQualityQuery,'read',forbidden)
    endpoints=callbacks(db,life)
    assert endpoints['status'](carrier.value.run_id)['state']=='queued'
    with pytest.raises(HTTPException) as error:endpoints['quality'](carrier.value.run_id,Response())
    assert error.value.status_code==409 and error.value.detail=='research_quality_bundle_not_confirmed'
    assert error.value.headers['X-Decision-Hub-Report-State-Version']=='1'


def test_confirmed_download_keeps_original_bytes_etag_and_serving_head(durable,monkeypatch):
    db,life,_,carrier,_=durable
    claim=life.claim_one(owner=OWNER);life.save_verified(claim,carrier)
    def forbidden(*args,**kwargs):raise AssertionError('confirmed GET invoked full reader or writer')
    monkeypatch.setattr(routes.ResearchQualityQuery,'read',forbidden)
    monkeypatch.setattr(life,'_attempt',forbidden)
    endpoints=callbacks(db,life)
    response=endpoints['download'](claim.run_id)
    assert response.body==carrier.value.report()
    assert response.headers['ETag']=='"f3c1629fd171f4b21e03afcfb6829515325d91cb4c051dfdb1644bff6211c523"'
    assert response.headers['X-Decision-Hub-Report-Read-Mode']=='confirmed_bundle'
    assert response.headers['X-Decision-Hub-Report-State-Version']=='4'


def test_revoked_route_has_no_full_reader_fallback(durable,monkeypatch):
    db,life,_,carrier,_=durable
    claim=life.claim_one(owner=OWNER);life.save_verified(claim,carrier)
    life.revoke(claim.run_id,reason='private_revocation',expected_version=4)
    def forbidden(*args,**kwargs):raise AssertionError('revoked GET must not use legacy reader')
    monkeypatch.setattr(routes.ResearchQualityQuery,'read',forbidden)
    endpoints=callbacks(db,life)
    with pytest.raises(HTTPException) as error:endpoints['download'](claim.run_id)
    assert error.value.detail=='research_quality_bundle_not_confirmed'
    assert error.value.headers['X-Decision-Hub-Report-Read-Mode']=='revoked'
