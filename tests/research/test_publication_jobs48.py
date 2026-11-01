"""Private durable-state/IO controls; no normal factory or active report pass.

Saved actual39 bytes are a lifecycle carrier only. The baseline explicitly
injects its private lower-level issuer seam, which is never used by the normal
runtime48 factory. No full original consume, live DB, model or HTTP is run.
"""
from contextlib import contextmanager
from datetime import timedelta
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from packages.kernel.decision_hub_kernel.application.research_publication_jobs48 import (
    DurablePublicationLifecycle, enqueue_in_publication_transaction, exact_selection,
    PublicationBundleWorker,
)
from packages.kernel.decision_hub_kernel.application.research_publication_bundle_lifecycle import (
    BundleCommitUnconfirmed, encoded,
)
from packages.kernel.decision_hub_kernel.persistence.research_publication_bundle_records import (
    PublicationBundleStateEventRecord as Event, PublicationBundleJobRecord as Job,
    PublicationBundleConfirmationRecord as Ack,
)
from packages.kernel.decision_hub_kernel.persistence.db import RunRecord

spec=importlib.util.spec_from_file_location("_private_boundary46",Path(__file__).with_name("test_publication_bundle_lifecycle46.py"))
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
actual39=prior.actual39
boundary=prior.boundary
OWNER="publication-bundle:101:"+"a"*32


@pytest.fixture
def durable(boundary):
    db,old,issuer,old_claim,carrier=boundary
    Event.__table__.create(db.engine)
    with db.session() as session:
        session.execute(delete(Job))
        ticket=enqueue_in_publication_transaction(session,carrier.value.run_id,now=prior.NOW)
    lifecycle=DurablePublicationLifecycle(db,old.archive,preparer=issuer,clock=lambda:prior.NOW)
    yield db,lifecycle,issuer,carrier,ticket
    lifecycle.close()


def test_atomic_enqueue_and_rollback_preserves_single_identity(durable):
    db,life,_,carrier,ticket=durable
    with db.session() as session:
        job=session.get(Job,carrier.value.run_id)
        assert job.request_id==ticket and job.owner is None and job.generation==0
        assert job.selection_native_json==exact_selection(session,job.run_id)
        assert len(session.scalars(select(Event)).all())==1
        assert enqueue_in_publication_transaction(session,job.run_id,now=prior.NOW)==ticket
    assert not life.archive.fenced(carrier.value.run_id)
    with db.factory() as session:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        session.execute(delete(Event));session.execute(delete(Job))
        enqueue_in_publication_transaction(session,carrier.value.run_id,now=prior.NOW)
        session.rollback()
    with life._ro() as session:
        assert session.get(Job,carrier.value.run_id) is not None
        assert len(session.scalars(select(Event)).all())==1


def test_claim_prepare_independent_ack_version_and_original_bytes(durable):
    db,life,_,carrier,_=durable
    claim=life.claim_one(owner=OWNER)
    life.save_verified(claim,carrier)
    bundle,head=life.read_with_head(claim.run_id)
    assert bundle.report()==carrier.value.report()
    assert prior.sha(bundle.report())=="f3c1629fd171f4b21e03afcfb6829515325d91cb4c051dfdb1644bff6211c523"
    assert head["state"]=="confirmed" and head["state_version"]==4
    with life._ro() as session:
        states=session.scalars(select(Event.state).order_by(Event.version)).all()
        assert states==["queued","queued","prepared","confirmed"]
        assert session.get(Ack,claim.run_id).generation==claim.generation
    assert not life.archive.fenced(claim.run_id)


def test_late_claim_is_fenced_by_reclaim_generation(durable):
    _,life,_,carrier,_=durable
    first=life.claim_one(owner=OWNER)
    life.clock=lambda:prior.NOW+timedelta(seconds=181)
    second=life.claim_one(owner=OWNER[:-1]+"b")
    assert second.generation==first.generation+1
    with pytest.raises(PermissionError,match="claim_lost"):
        life.save_verified(first,carrier)
    assert life.status(first.run_id)["state"]=="queued"


def test_claim_unknown_commit_is_durable_and_not_retried(durable):
    db,life,_,carrier,_=durable
    class Lost(Session):
        def commit(self):
            super().commit();raise OSError("private_ack_lost")
    db.factory=sessionmaker(db.engine,class_=Lost,expire_on_commit=False)
    with pytest.raises(BundleCommitUnconfirmed) as error:
        life.claim_one(owner=OWNER)
    assert error.value.phase=="claimed"
    assert life.archive.fenced(carrier.value.run_id)
    assert life.claim_one(owner=OWNER) is None
    bundle,head=life.read_with_head(carrier.value.run_id)
    assert bundle is None and head["state"]=="unconfirmed"
    assert life.reconciliation_state(carrier.value.run_id)=="unknown_commit_not_saved_requires_operator_review"


def test_nojob_unknown_enqueue_fence_never_allows_legacy(durable):
    db,life,_,carrier,_=durable
    with db.session() as session:
        session.execute(delete(Event));session.execute(delete(Job))
    life.archive.exclusive(life.archive.fence_ref(carrier.value.run_id),encoded({
        "run_id":carrier.value.run_id,"phase":"enqueued","request_id":"unknown",
        "generation":0,"bundle_hash":"0"*64}))
    assert life.status(carrier.value.run_id)["state"]=="unconfirmed"
    assert life.read_with_head(carrier.value.run_id)[1]["state"]=="unconfirmed"
    with pytest.raises(ValueError,match="reconciliation"):
        life.enqueue_existing(carrier.value.run_id)


@pytest.mark.parametrize("commit_number,phase",[(1,"prepared"),(2,"confirmed")])
def test_save_ack_unknown_commit_cannot_serve(durable,commit_number,phase):
    db,life,_,carrier,_=durable
    claim=life.claim_one(owner=OWNER)
    class Lost(Session):
        commits=0
        def commit(self):
            super().commit();type(self).commits+=1
            if type(self).commits==commit_number:raise OSError("private_ack_lost")
    db.factory=sessionmaker(db.engine,class_=Lost,expire_on_commit=False)
    with pytest.raises(BundleCommitUnconfirmed) as error:
        life.save_verified(claim,carrier)
    assert error.value.phase==phase and life.archive.fenced(claim.run_id)
    assert life.read_with_head(claim.run_id)[1]["state"]=="unconfirmed"


def test_cancel_after_prepared_readback_wins_ack_cas(durable,monkeypatch):
    db,life,issuer,carrier,_=durable
    claim=life.claim_one(owner=OWNER)
    verify=issuer.verify;calls=0
    def cancel_after_readback(proof):
        nonlocal calls
        verify(proof);calls+=1
        if calls==3:life.cancel_preparation(claim)
    monkeypatch.setattr(issuer,"verify",cancel_after_readback)
    with pytest.raises(PermissionError,match="claim_lost"):
        life.save_verified(claim,carrier)
    with life._ro() as session:
        assert session.get(Job,claim.run_id).state=="cancelled"
        assert session.get(Ack,claim.run_id) is None
        assert session.get(RunRecord,claim.run_id).status in {"completed","degraded"}
    assert not life.archive.fenced(claim.run_id)


def test_revocation_between_read_snapshots_rejects_original(durable,monkeypatch):
    _,life,_,carrier,_=durable
    claim=life.claim_one(owner=OWNER);life.save_verified(claim,carrier)
    ro=life._ro;armed=True
    @contextmanager
    def revoke_on_exit():
        nonlocal armed
        with ro() as session:yield session
        if armed:
            armed=False;life.revoke(claim.run_id,reason="private_review_revocation",expected_version=4)
    monkeypatch.setattr(life,"_ro",revoke_on_exit)
    with pytest.raises(ValueError,match="serving_head_changed"):
        life.read_with_head(claim.run_id)
    assert life.status(claim.run_id)["state"]=="revoked"


def test_fast_read_cannot_claim_capture_write_or_model(durable,monkeypatch):
    db,life,_,carrier,_=durable
    claim=life.claim_one(owner=OWNER);life.save_verified(claim,carrier)
    def forbidden(*args,**kwargs):raise AssertionError("GET invoked a writer/producer")
    monkeypatch.setattr(life,"claim_one",forbidden)
    monkeypatch.setattr(life,"_attempt",forbidden)
    monkeypatch.setattr(life.preparer,"verify",forbidden)
    monkeypatch.setattr(db,"factory",forbidden)
    assert life.read_with_head(claim.run_id)[0].report()==carrier.value.report()


def test_material_corruption_returns_integrity_failure_no_legacy(durable):
    _,life,_,carrier,_=durable
    claim=life.claim_one(owner=OWNER);life.save_verified(claim,carrier)
    path=life.archive._path(carrier.value.material_ref)
    raw=path.read_bytes();path.write_bytes(b" "+raw[1:])
    with pytest.raises(ValueError,match="material_bytes_changed"):
        life.read_with_head(claim.run_id)


def test_unknown_claim_worker_does_not_run_producer(durable):
    db,life,_,carrier,_=durable
    class Lost(Session):
        def commit(self):super().commit();raise OSError("private_ack_lost")
    db.factory=sessionmaker(db.engine,class_=Lost,expire_on_commit=False)
    def forbidden(claim):raise AssertionError("unknown claim must not produce")
    assert PublicationBundleWorker(life,forbidden).process_one() is False


def test_failure_preserves_published_run_and_explicit_reason(durable):
    db,life,_,carrier,_=durable
    claim=life.claim_one(owner=OWNER)
    life.fail_claim(claim,reason="research_usage_terminal_result_changed")
    head=life.status(claim.run_id)
    assert head["state"]=="unconfirmed" and head["reason_code"]=="research_usage_terminal_result_changed"
    with life._ro() as session:
        assert session.get(Ack,claim.run_id) is None
        assert session.get(RunRecord,claim.run_id).status in {"completed","degraded"}


def test_fenced_first_page_cannot_starve_an_unfenced_job(durable):
    db,life,_,carrier,_=durable
    with db.session() as session:
        first=session.get(Job,carrier.value.run_id)
        native={column.name:getattr(first,column.name) for column in Job.__table__.columns}
        for number in range(8):
            run_id=f"private_fenced_{number}"
            values={**native,"run_id":run_id,"request_id":f"private_request_{number}",
                "artifact_id":f"private_artifact_{number}","publication_id":f"private_publication_{number}",
                "created_at":prior.NOW-timedelta(seconds=20-number)}
            session.add(Job(**values))
            life.archive.exclusive(life.archive.fence_ref(run_id),encoded({"private_unknown_commit":True}))
    claim=life.claim_one(owner=OWNER)
    assert claim.run_id==carrier.value.run_id
    with life._ro() as session:
        assert all(job.owner is None for job in session.scalars(select(Job).where(Job.run_id.startswith("private_fenced_"))))


def test_fence_arriving_after_ro_scan_prevents_claim_without_killing_worker(durable,monkeypatch):
    _,life,_,carrier,_=durable
    attempt=life._attempt
    def fence_before_writer(claim,*args,**kwargs):
        life.archive.exclusive(life.archive.fence_ref(claim.run_id),encoded({"private_unknown_commit":True}))
        return attempt(claim,*args,**kwargs)
    monkeypatch.setattr(life,"_attempt",fence_before_writer)
    assert life.claim_one(owner=OWNER) is None
    with life._ro() as session:
        assert session.get(Job,carrier.value.run_id).generation==0


def test_nonexistent_run_has_no_legacy_read_grant(durable):
    _,life,_,_,_=durable
    with pytest.raises(KeyError):life.status("private_no_such_run")
    with pytest.raises(KeyError):life.read_with_head("private_no_such_run")
