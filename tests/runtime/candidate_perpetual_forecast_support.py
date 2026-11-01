"""Real candidate modules and synthetic OKX bytes; no DB/model/network side effects."""

import hashlib
import importlib
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

MAIN = Path("E:/project/study/codex/dsh/decision-hub")
ROOT = Path(__file__).resolve().parents[2]


def load(relative: Any) -> Any:
    sys.path.insert(0, str(MAIN))
    path = ROOT / relative
    assert path.is_file(), "new cross-horizon scoring source is not implemented"
    name = relative.removesuffix(".py").replace("/", ".")
    importlib.import_module(name.rpartition(".")[0])
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    assert module.__file__ is not None
    assert Path(module.__file__).resolve() == path
    return module


def truth_module() -> Any:
    load("packages/kernel/decision_hub_kernel/ports/perpetual_forecast_scoring.py")
    return load("packages/kernel/decision_hub_kernel/application/perpetual_forecast_truth.py")


def parsed_pages(
    horizon: Any = "4h",
    instrument: Any = "BTC-USDT-SWAP",
    *,
    missing: Any = False,
    attack: Any = None,
    end: Any = None,
    entries: Any = None,
):
    """Use the new explicit bounded truth codec with original capture/parse primitives."""
    sys.path.insert(0, str(MAIN))
    from packages.kernel.decision_hub_kernel.application.market_original_parse import (
        MarketOriginalParse,
    )
    from packages.kernel.decision_hub_kernel.ports.action_quote import CaptureOriginal
    from packages.kernel.decision_hub_kernel.ports.perpetual_close import millis, public_url

    codec = truth_module()
    version = "okx-perp-forecast-truth.v1"

    minutes = {"4h": 240, "24h": 1440, "72h": 4320}[horizon]
    end = end or datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes)
    start = end - timedelta(minutes=minutes)
    stamps = [end - timedelta(minutes=i) for i in range(minutes + 1)]
    if missing:
        stamps.pop(90)
    cursor, pages = millis(end), []
    cutoff = end + timedelta(minutes=1)
    parser = MarketOriginalParse()
    for sequence in range((len(stamps) + 299) // 300):
        rows = []
        for stamp in stamps[sequence * 300 : (sequence + 1) * 300]:
            price = "110" if stamp == end else "100"
            rows.append(
                [str(millis(stamp) - 60000), price, price, price, price, "7", "0.07", "7", "1"]
            )
        if sequence == 0 and attack == "unconfirmed":
            rows[0][-1] = "0"
        if sequence == 0 and attack == "invalid_ohlc":
            rows[0][2] = "1"
        if sequence == 0 and attack == "duplicate_minute":
            rows[1][0] = rows[0][0]
        raw = json.dumps({"code": "0", "data": rows}, separators=(",", ":")).encode()
        digest = hashlib.sha256(raw).hexdigest()
        at = end + timedelta(seconds=sequence + 1)
        capture = CaptureOriginal(
            f"synthetic-scoring-{instrument}-{sequence}",
            f"{version}:{instrument}:{'3' * 32}:base:{sequence}",
            public_url(instrument, cursor),
            200,
            "raw://sha256/" + digest,
            "0" * 64 if attack == "body_hash" else digest,
            len(raw),
            version,
            None,
            at,
            at + timedelta(milliseconds=10),
            at + timedelta(milliseconds=20),
        )
        if entries is not None:
            entries.append((capture, raw))
        pages.append(
            codec.parse_truth_page(
                capture, raw, inst_id=instrument, as_of=cutoff, selected_rows=parser.selected_rows
            )
        )
        cursor = int(rows[-1][0])
    return start, end, cutoff, tuple(pages)


def publication_case(
    tmp_path: Any,
    monkeypatch: Any,
    instrument: Any,
    horizon: Any,
    *,
    probability_mode: Any = "finite",
) -> Any:
    """Original services publish a synthetic fixture; no Graph/native qualification."""
    from dataclasses import asdict

    from packages.contracts_py.decision_hub_contracts import ObservationCreate
    from packages.contracts_py.decision_hub_contracts.generated.decision_product_v2 import (
        DecisionSettingsCommandV1,
    )
    from packages.contracts_py.decision_hub_contracts.models import SourceType
    from packages.kernel.decision_hub_kernel.application.admission import AdmissionService
    from packages.kernel.decision_hub_kernel.application.decision_settings import (
        DecisionSettingsService,
    )
    from packages.kernel.decision_hub_kernel.application.decision_settings_binding import (
        DecisionSettingsBindingReader,
    )
    from packages.kernel.decision_hub_kernel.application.okx_perpetual_source import (
        OKXPerpetualSource,
    )
    from packages.kernel.decision_hub_kernel.application.run import RunService
    from packages.kernel.decision_hub_kernel.application.study import ensure_study
    from packages.kernel.decision_hub_kernel.persistence.db import (
        Database,
        RawCaptureRecord,
        RunRecord,
    )
    from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
    from packages.provider_adapters.research.raw_capture import RawBodyArchive
    from pydantic import HttpUrl
    from tests.decision_product import test_perpetual_close_source as close_fixture
    from tests.decision_product.test_okx_perpetual_original import capture, metadata_row
    from tests.decision_product.test_perpetual_advice_query import persisted_publication
    from tests.decision_product.test_perpetual_research_binding import AT, NOW, OWNER

    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "data"))
    db = Database("sqlite:///" + (tmp_path / "fixture.sqlite3").as_posix())
    db.create_all()  # Existing unit-fixture convention only; no original DB/migration claim.
    archive = RawBodyArchive(tmp_path / "raw")
    metadata, raw = capture("metadata", metadata_row(instrument), instrument)
    assert archive.put(raw) == (metadata.body_hash, metadata.body_ref)
    with db.session() as session:
        session.add(RawCaptureRecord(**asdict(metadata), media_type="application/json"))
    source = OKXPerpetualSource(db, archive)
    with monkeypatch.context() as patch:
        patch.setattr(close_fixture, "INST", instrument)
        ids = close_fixture.pages((db, archive, source, metadata))
    body = json.loads(
        (MAIN / "apps/decision-desk/src/api/decisionSettingsReceiptFixture.json").read_bytes()
    )["intent"]["command"]
    body["partition"]["instrument_id"] = "okx:" + instrument
    body["values"]["position_context"]["instrument_id"] = "okx:" + instrument
    ref = source.metadata_contract_reference(metadata.capture_id).model_dump(mode="json")
    body["values"]["instrument"] = ref
    body["values"]["position_context"]["instrument_ref"] = ref
    body["values"]["account_snapshot"]["as_of"] = AT.isoformat().replace("+00:00", "Z")
    utility = body["values"]["utility_policy"]
    utility["hash"] = canonical_hash({k: v for k, v in utility.items() if k != "hash"})
    settings = DecisionSettingsCommandV1.model_validate(body)
    settings_service = DecisionSettingsService(
        db, now=lambda: AT, binding_reader=DecisionSettingsBindingReader(source, now=lambda: AT)
    )
    settings_service.save(OWNER, settings)
    event_id, _, accepted = AdmissionService(db, clock=lambda: AT).admit(
        ObservationCreate(
            source_id="synthetic-scoring-chain",
            source_type=SourceType.transcript,
            source_url=HttpUrl("https://example.com/synthetic-scoring"),
            text="Synthetic research fixture.",
            observed_at=AT,
            published_at=NOW,
            language="en",
            event_hint="central_bank_speech",
        )
    )
    assert accepted
    run_id, created = RunService(db, clock=lambda: AT).create(
        event_id, strategy_version="research.v1"
    )
    assert created
    with db.session() as session:
        study = ensure_study(
            session,
            question_key="scoring-" + instrument + horizon,
            title="Synthetic scoring fixture",
            market="crypto",
            instrument=instrument,
            horizon_scope=horizon,
            identity_basis="exact_perpetual_decision_v2",
            now=AT,
        )
        run = session.get(RunRecord, run_id)
        assert run is not None
        run.study_id = study
    case: dict[str, Any] = dict(
        db=db,
        archive=archive,
        source=source,
        ids=ids,
        settings=settings,
        settings_service=settings_service,
        event_id=event_id,
        run_id=run_id,
    )
    if probability_mode == "finite":
        case["derived"] = persisted_publication(case)
    else:
        from tests.decision_product import test_perpetual_action_publication as packets

        builder = packets.lawful_packet_for

        def authored(request: Any, materials: Any):
            packet = builder(request, materials)
            change_authored_direction(packet, probability_mode)
            return packet

        with monkeypatch.context() as patch:
            patch.setattr(packets, "lawful_packet_for", authored)
            case["derived"] = persisted_publication(case)
    return case


def change_authored_direction(packet: Any, mode: Any):
    """Synthetic authoring input before original publication; saved originals stay intact."""
    from decimal import Decimal

    from tests.contracts.test_d2_candidate_contract import body_ref

    synthesis = packet["synthesis"]
    market = synthesis["market_distribution"]
    estimate = market["direction_distribution"]
    paths = synthesis["path_distribution"]
    old_path = market["path_distribution_ref"]
    if mode == "unavailable":
        estimate.update(
            probabilities=None,
            kind="unavailable",
            source_ref=None,
            gaps=["synthetic_direction_not_estimated"],
        )
        paths["supports_market_direction"] = False
    elif mode == "infinite":
        for index, row in enumerate(paths["scenarios"]):
            row["probability"] = "1" if index == 0 else "0"
        grouped = {"up": "0", "neutral": "0", "down": "0"}
        threshold = Decimal(synthesis["candidate_target"]["threshold_fraction"])
        for row in paths["scenarios"]:
            value = Decimal(row["terminal_return_fraction"])
            group = "up" if value > threshold else "down" if value < -threshold else "neutral"
            grouped[group] = str(Decimal(grouped[group]) + Decimal(row["probability"]))
        for row in estimate["probabilities"]:
            row["value"] = grouped[row["outcome_id"]]
    else:
        raise ValueError("unsupported synthetic direction mode")
    market["path_distribution_ref"] = body_ref("synthetic-authored-paths", paths)
    synthesis["economic_input_refs"] = [
        market["path_distribution_ref"] if row == old_path else row
        for row in synthesis["economic_input_refs"]
    ]
    synthesis["market_distribution_ref"] = body_ref("synthetic-market", market)
    domain = packet["allowed_domain"]
    domain["market_distribution_ref"] = synthesis["market_distribution_ref"]
    synthesis["allowed_domain_ref"] = body_ref("synthetic-domain", domain)
    packet.update(
        synthesis_ref=body_ref("synthetic-synthesis", synthesis),
        allowed_domain_ref=synthesis["allowed_domain_ref"],
    )
