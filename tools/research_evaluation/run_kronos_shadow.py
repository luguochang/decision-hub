"""Isolated, finite Kronos trial. Export runs in Hub Python; infer in D: Python.

No production imports on the inference path; no database writes, Action or model
registration. The input and label files are deliberately separate.
"""
# Python 3.10 is intentional for the cached isolated CUDA runtime.
# ruff: noqa: UP017

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PIN = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
ORIGINS = ("2026-10-13T00:00:00+00:00", "2026-10-14T00:00:00+00:00")
FEATURES = ["open", "high", "low", "close", "volume", "amount"]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value: object) -> None:
    data = (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Immutable trial identities: an existing result is an error, not overwritten.
    with path.open("xb") as stream:
        stream.write(data)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def hourly(bars: tuple, start: datetime, count: int) -> list[dict]:
    if len(bars) != count * 60:
        raise ValueError(f"incomplete_minute_window:{len(bars)}:{count * 60}")
    for index, bar in enumerate(bars):
        if bar.open_at != start + timedelta(minutes=index) or not bar.closed:
            raise ValueError("non_contiguous_or_open_minute")
        if bar.volume_base is None or bar.volume_quote is None:
            raise ValueError("real_volume_and_amount_required")
    rows = []
    for index in range(count):
        batch = bars[index * 60 : (index + 1) * 60]
        rows.append(
            {
                "timestamp": batch[0].open_at.isoformat(),
                "close_at": batch[-1].close_at.isoformat(),
                "open": str(batch[0].open),
                "high": str(max(b.high for b in batch)),
                "low": str(min(b.low for b in batch)),
                "close": str(batch[-1].close),
                "volume": str(sum(b.volume_base for b in batch)),
                "amount": str(sum(b.volume_quote for b in batch)),
            }
        )
    return rows


def export(args: argparse.Namespace) -> None:
    # Import only in the Hub environment; inference never imports the kernel.
    sys.path.insert(0, str(ROOT))
    from sqlalchemy import event

    from packages.kernel.decision_hub_kernel.application.market_path_store import MarketPathStore
    from packages.kernel.decision_hub_kernel.persistence.db import Database
    from packages.kernel.decision_hub_kernel.ports.market_path import path_hashes, spot_scope
    from packages.provider_adapters.research.raw_capture import RawBodyArchive

    db_path = args.database.resolve()
    db = Database("sqlite+pysqlite:///file:" + db_path.as_posix() + "?mode=ro&uri=true")

    @event.listens_for(db.engine, "connect")
    def readonly(connection, _record):
        connection.execute("PRAGMA query_only=ON")

    archive = RawBodyArchive(args.raw_root.resolve())
    store = MarketPathStore(db, archive)
    exported_at = datetime.now(timezone.utc)
    protocol = {
        "schema_version": "kronos-shadow-protocol.v1",
        "upstream_commit": PIN,
        "kind": "retrospective_candidate_trial_not_historical_published_prediction",
        "context_hours": 512,
        "prediction_hours": 24,
        "sample_count": 16,
        "origins": list(ORIGINS),
        "instruments": ["BTC-USDT", "ETH-USDT"],
        "seed": 20261015,
        "temperature": 1.0,
        "top_k": 0,
        "top_p": 0.9,
        "input_frequency": "1h_from_60_authenticated_closed_1m_bars",
        "baselines": ["last_close", "empirical_iid_hourly_logreturn_bootstrap_16_paths"],
        "selection_rule": (
            "fixed_two_latest_nonoverlapping_UTC_midnight_24h_origins_before_export_date"
        ),
        "target": "spot_USDT_terminal_return_and_intrahorizon_low_24h.v1",
        "asset_join": "independent_series_not_joint_distribution",
        "no_formal_admission": True,
        "no_training": True,
        "known_time_policy": "input_known_at_lte_decision_cutoff_labels_known_at_lte_exported_at",
        "decision_lag_seconds": 60,
        "anchor_semantics": (
            "hourly model anchor precedes decision cutoff by 60s to admit actual "
            "just-closed candle receipts; no prediction was published historically"
        ),
    }
    write_json(args.output / "protocol.json", protocol)
    cases, labels = [], []
    with db.session() as session, session.no_autoflush:
        session.connection().exec_driver_sql("PRAGMA query_only=ON")
        for instrument in protocol["instruments"]:
            for origin_text in ORIGINS:
                origin = datetime.fromisoformat(origin_text)
                cutoff = origin + timedelta(seconds=60)
                start = origin - timedelta(hours=512)
                scope = spot_scope(instrument)
                past = store.load_in_session(
                    session, scope, start_at=start, end_at=origin, as_of=cutoff
                )
                future = store.load_in_session(
                    session,
                    scope,
                    start_at=origin,
                    end_at=origin + timedelta(hours=24),
                    as_of=exported_at,
                )
                semantic, manifest = path_hashes(scope, past)
                label_semantic, label_manifest = path_hashes(scope, future)
                case_id = instrument + "-" + origin.strftime("%Y%m%dT%H%MZ")
                source_refs = sorted(set((b.capture_ref, b.capture_hash) for b in past))
                cases.append(
                    {
                        "case_id": case_id,
                        "instrument": instrument,
                        "origin": origin.isoformat(),
                        "decision_cutoff": cutoff.isoformat(),
                        "scope": scope.model_dump(mode="json"),
                        "input_semantic_hash": semantic,
                        "input_manifest_hash": manifest,
                        "input_max_known_at": max(b.known_at for b in past).isoformat(),
                        "source_originals": [{"ref": r, "sha256": h} for r, h in source_refs],
                        "bars": hourly(past, start, 512),
                    }
                )
                labels.append(
                    {
                        "case_id": case_id,
                        "label_semantic_hash": label_semantic,
                        "label_manifest_hash": label_manifest,
                        "label_max_known_at": max(b.known_at for b in future).isoformat(),
                        "bars": hourly(future, origin, 24),
                    }
                )
                print("exported", case_id, len(past), len(future), flush=True)
    write_json(
        args.output / "inputs.json",
        {
            "schema_version": "kronos-shadow-input.v1",
            "exported_at": exported_at.isoformat(),
            "protocol_sha256": digest((args.output / "protocol.json").read_bytes()),
            "cases": cases,
        },
    )
    write_json(
        args.output / "labels.json",
        {
            "schema_version": "kronos-shadow-label.v1",
            "exported_at": exported_at.isoformat(),
            "cases": labels,
        },
    )
    write_json(
        args.output / "export-receipt.json",
        {
            "database": str(db_path),
            "database_access": "sqlite mode=ro + query_only",
            "exported_at": exported_at.isoformat(),
            "files": {
                n: digest((args.output / n).read_bytes())
                for n in ("protocol.json", "inputs.json", "labels.json")
            },
        },
    )


def import_upstream(source: Path):
    """No copy/fork: preserve the reference tree, load only its model package."""
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != PIN:
        raise ValueError("upstream_commit_mismatch")
    sys.path.insert(0, str(source))
    import model.kronos as upstream

    return upstream


def raw_function(upstream):
    """Thin raw-path exit. All generation code is the locked upstream function.

    The sole source edit removes sample averaging. Slicing/denormalization below
    explicitly retain [batch,sample,time,channel]; the public default is untouched.
    """
    source = inspect.getsource(upstream.auto_regressive_inference)
    old = "        preds = np.mean(preds, axis=1)"
    if source.count(old) != 1:
        raise ValueError("raw_path_patch_anchor_mismatch")
    patched = source.replace("def auto_regressive_inference(", "def infer_raw(", 1).replace(
        old, "        # raw samples retained by Decision Hub shadow adapter"
    )
    namespace = dict(upstream.__dict__)
    exec(compile(patched, "<kronos-shadow-raw-paths>", "exec"), namespace)
    return namespace["infer_raw"], {
        "upstream_function_sha256": digest(source.encode()),
        "adapted_function_sha256": digest(patched.encode()),
        "patch": "remove sample-axis mean only; keep upstream untouched",
    }


def infer(args: argparse.Namespace) -> None:
    os.environ["HF_HOME"] = str(args.runtime / "hf-cache")
    os.environ["HF_HUB_OFFLINE"] = "1"
    import importlib.metadata

    import numpy as np
    import pandas as pd
    import torch

    started = time.perf_counter()
    torch.set_num_threads(4)
    upstream = import_upstream(args.source)
    raw, patch = raw_function(upstream)
    tokenizer = (
        upstream.KronosTokenizer.from_pretrained(str(args.runtime / "tokenizer"))
        .eval()
        .to(args.device)
    )
    model = upstream.Kronos.from_pretrained(str(args.runtime / "model")).eval().to(args.device)
    if args.device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats()
    load_seconds = time.perf_counter() - started
    data = read_json(args.input)
    protocol_path = args.input.with_name("protocol.json")
    protocol = read_json(protocol_path)
    if data["protocol_sha256"] != digest(protocol_path.read_bytes()):
        raise ValueError("input_protocol_hash_mismatch")
    output = {
        "schema_version": "kronos-shadow-paths.v1",
        "input_sha256": digest(args.input.read_bytes()),
        "protocol_sha256": digest(protocol_path.read_bytes()),
        "patch": patch,
        "environment": {
            "python": sys.version,
            "device": args.device,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "dependencies": {
                n: importlib.metadata.version(n)
                for n in ["torch", "numpy", "pandas", "einops", "huggingface_hub", "safetensors"]
            },
        },
        "model_load_seconds": load_seconds,
        "cases": [],
    }
    for case in data["cases"]:
        origin = datetime.fromisoformat(case["origin"])
        if datetime.fromisoformat(case["input_max_known_at"]) > datetime.fromisoformat(
            case["decision_cutoff"]
        ):
            raise ValueError("future_input_known_at")
        if (
            len(case["bars"]) != 512
            or datetime.fromisoformat(case["bars"][-1]["close_at"]) != origin
        ):
            raise ValueError("input_origin_or_context_mismatch")
        x = np.asarray([[float(b[n]) for n in FEATURES] for b in case["bars"]], dtype=np.float32)
        if not np.isfinite(x).all() or (x[:, :4] <= 0).any() or (x[:, 4:] < 0).any():
            raise ValueError("invalid_input_values")
        xt = pd.Series(pd.to_datetime([b["timestamp"] for b in case["bars"]], utc=True))
        yt = pd.Series(pd.date_range(origin, periods=24, freq="h"))
        means, stds = x.mean(axis=0), x.std(axis=0)
        normalized = np.clip((x - means) / (stds + 1e-5), -5, 5)
        stamps = [upstream.calc_time_stamps(t).values.astype(np.float32)[None, :] for t in (xt, yt)]
        tensors = [torch.from_numpy(v).to(args.device) for v in [normalized[None, :], *stamps]]
        random.seed(protocol["seed"])
        np.random.seed(protocol["seed"])
        torch.manual_seed(protocol["seed"])
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(protocol["seed"])
        case_start = time.perf_counter()
        with torch.inference_mode():
            generated = raw(
                tokenizer,
                model,
                *tensors,
                512,
                24,
                clip=5,
                T=1.0,
                top_k=0,
                top_p=0.9,
                sample_count=16,
                verbose=False,
            )
        paths = generated[0, :, -24:, :] * (stds + 1e-5) + means
        if paths.shape != (16, 24, 6) or not np.isfinite(paths).all():
            raise ValueError("raw_output_shape_or_values_invalid")
        # Same random state proves the untouched upstream mean exit matches these
        # samples. One 4-step call is enough to verify the changed axis contract.
        compatibility = None
        if not output["cases"]:
            short = tensors[:2] + [tensors[2][:, :4, :]]
            torch.manual_seed(protocol["seed"])
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(protocol["seed"])
            with torch.inference_mode():
                a = raw(
                    tokenizer,
                    model,
                    *short,
                    512,
                    4,
                    clip=5,
                    T=1.0,
                    top_k=0,
                    top_p=0.9,
                    sample_count=16,
                    verbose=False,
                )
            torch.manual_seed(protocol["seed"])
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(protocol["seed"])
            with torch.inference_mode():
                b = upstream.auto_regressive_inference(
                    tokenizer,
                    model,
                    *short,
                    512,
                    4,
                    clip=5,
                    T=1.0,
                    top_k=0,
                    top_p=0.9,
                    sample_count=16,
                    verbose=False,
                )
            delta = float(np.max(np.abs(a.mean(axis=1) - b)))
            compatibility = {
                "steps": 4,
                "samples": 16,
                "same_sample_mean_max_abs_difference": delta,
                "passed": delta <= 1e-6,
            }
            if delta > 1e-6:
                raise ValueError("default_mean_compatibility_failure")
        elapsed = time.perf_counter() - case_start
        bad_geometry = (
            (paths[:, :, 2] > np.minimum(paths[:, :, 0], paths[:, :, 3]))
            | (paths[:, :, 1] < np.maximum(paths[:, :, 0], paths[:, :, 3]))
            | (paths[:, :, 2] > paths[:, :, 1])
        )
        row = {
            "case_id": case["case_id"],
            "origin": case["origin"],
            "timestamps": [t.isoformat() for t in yt],
            "features": FEATURES,
            "paths": paths.tolist(),
            "path_shape": list(paths.shape),
            "elapsed_seconds": elapsed,
            "negative_price_values": int((paths[:, :, :4] <= 0).sum()),
            "negative_volume_amount_values": int((paths[:, :, 4:] < 0).sum()),
            "invalid_ohlc_bars": int(bad_geometry.sum()),
            "default_mean_compatibility": compatibility,
        }
        output["cases"].append(row)
        print(
            "inferred",
            case["case_id"],
            "seconds",
            round(elapsed, 3),
            "invalid_ohlc",
            row["invalid_ohlc_bars"],
            flush=True,
        )
    output["total_seconds"] = time.perf_counter() - started
    output["cuda_peak_allocated_bytes"] = (
        torch.cuda.max_memory_allocated() if args.device.startswith("cuda") else None
    )
    output["cuda_peak_reserved_bytes"] = (
        torch.cuda.max_memory_reserved() if args.device.startswith("cuda") else None
    )
    write_json(args.output, output)


def evaluate(args: argparse.Namespace) -> None:
    import numpy as np

    inputs, labels, paths = [read_json(p) for p in (args.input, args.labels, args.paths)]
    if paths["input_sha256"] != digest(args.input.read_bytes()):
        raise ValueError("scoring_input_mismatch")
    cases = []

    def score(samples, actual):
        # Empirical CRPS; lower is better. Samples are terminal fractional returns.
        return float(
            np.mean(abs(samples - actual)) - 0.5 * np.mean(abs(samples[:, None] - samples[None, :]))
        )

    for source, label, pred in zip(inputs["cases"], labels["cases"], paths["cases"], strict=True):
        if source["case_id"] != label["case_id"] or source["case_id"] != pred["case_id"]:
            raise ValueError("case_order_mismatch")
        anchor = float(source["bars"][-1]["close"])
        actual = float(label["bars"][-1]["close"]) / anchor - 1
        observed_low = min(float(b["low"]) for b in label["bars"]) / anchor - 1
        observed_high = max(float(b["high"]) for b in label["bars"]) / anchor - 1
        arr = np.asarray(pred["paths"])
        close_paths = arr[:, :, 3] / anchor - 1
        historical = np.log(np.asarray([float(b["close"]) for b in source["bars"]]))
        rng = np.random.default_rng(20261015)
        simple = (
            np.exp(np.cumsum(rng.choice(np.diff(historical), size=(16, 24), replace=True), axis=1))
            - 1
        )
        row = {
            "case_id": source["case_id"],
            "actual_terminal_return": actual,
            "actual_intrahorizon_low_return": observed_low,
            "actual_intrahorizon_high_return": observed_high,
            "models": {},
        }
        for name, values in (
            ("kronos", close_paths),
            ("empirical_iid_bootstrap", simple),
            ("last_close", np.zeros((16, 24))),
        ):
            terminal = values[:, -1]
            lo, med, hi = np.quantile(terminal, [0.05, 0.5, 0.95])
            row["models"][name] = {
                "mean_terminal_return": float(terminal.mean()),
                "terminal_q05_q50_q95": [float(lo), float(med), float(hi)],
                "terminal_mean_absolute_error": float(abs(terminal.mean() - actual)),
                "terminal_crps": score(terminal, actual),
                "actual_in_q05_q95": bool(lo <= actual <= hi),
                "p_terminal_down_3pct": float((terminal <= -0.03).mean()),
                "terminal_down_3pct_brier": float(
                    ((terminal <= -0.03).mean() - int(actual <= -0.03)) ** 2
                ),
                "p_terminal_up_3pct": float((terminal >= 0.03).mean()),
                "p_any_hour_close_down_3pct": float((values.min(axis=1) <= -0.03).mean()),
            }
        row["kronos_model_low_threshold_estimates"] = {
            "p_low_down_3pct": float((arr[:, :, 2].min(axis=1) / anchor - 1 <= -0.03).mean()),
            "p_low_down_5pct": float((arr[:, :, 2].min(axis=1) / anchor - 1 <= -0.05).mean()),
        }
        row["kronos_invalid_ohlc_bars"] = pred["invalid_ohlc_bars"]
        cases.append(row)
    result = {
        "schema_version": "kronos-shadow-paired-score.v1",
        "kind": "retrospective_candidate_trial_no_historical_prediction_or_profit_claim",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": digest(args.input.read_bytes()),
        "labels_sha256": digest(args.labels.read_bytes()),
        "paths_sha256": digest(args.paths.read_bytes()),
        "case_count": len(cases),
        "cases": cases,
        "mean_terminal_crps": {
            n: float(np.mean([r["models"][n]["terminal_crps"] for r in cases]))
            for n in ["kronos", "empirical_iid_bootstrap", "last_close"]
        },
        "limitations": [
            "two nonoverlapping origins per asset, correlated asset exposures; "
            "no statistical performance claim",
            "16 raw samples imply 6.25 percentage point empirical probability resolution, "
            "not calibrated confidence",
            "hourly low and close are different targets; only close threshold is comparable "
            "to simple close-path baseline",
            "no strategy, execution, fees, leverage or win-rate admission",
            "model pretraining provenance is not fully reproducible; "
            "known-time export constrains product inputs only",
        ],
    }
    write_json(args.output, result)
    print(
        json.dumps({"case_count": len(cases), "mean_terminal_crps": result["mean_terminal_crps"]}),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("export")
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--raw-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(run=export)
    p = sub.add_parser("infer")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--source", type=Path, required=True)
    p.add_argument("--runtime", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--device", default="cuda:0")
    p.set_defaults(run=infer)
    p = sub.add_parser("evaluate")
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--paths", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.set_defaults(run=evaluate)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
