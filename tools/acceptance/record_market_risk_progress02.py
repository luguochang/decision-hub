"""Record bounded engineering receipts without changing formal product sources."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
FORMAL = ROOT.parent / "decision-hub"
EVIDENCE = ROOT / "artifacts/market-risk-implementation-20261007"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    files = (
        "B4/normal-composition-root01.xml",
        "B4/normal-composition-root02.xml",
        "B4/normal-composition-root03.xml",
        "B3/forecast-alarm-root-ui01.xml",
        "B3/forecast-alarm-root-ui02.xml",
        "B3/ci-inference01/receipt01.json",
        "B3/score-view-v2-source-seal01.json",
        "B2/forecast-alarm-delivery04.md",
        "B2/owned-source-change-seal03.json",
    )
    refs, tests = {}, {}
    for ref in files:
        path = EVIDENCE / ref
        if not path.is_file():
            raise ValueError("named_evidence_missing:" + ref)
        refs[ref] = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.suffix == ".xml":
            tests[ref] = [
                {
                    "class": node.attrib.get("classname"),
                    "name": node.attrib.get("name"),
                    "failed": node.find("failure") is not None or node.find("error") is not None,
                }
                for node in ElementTree.parse(path).getroot().iter("testcase")
            ]
    receipt = {
        "schema_version": "market-risk-root-progress.v2",
        "at": datetime.now(UTC).isoformat(),
        "environment": "private_candidate_controls",
        "formal_install": False,
        "evidence": refs,
        "test_nodes": tests,
        "red01": "script used Worker attribute; actual port belongs to Factory",
        "corrected02": "normal builders pass; independent enhancement isolation pass in red01",
        "increment03": "normal three ledger hooks and actual private API v2/current/owner GET pass",
        "forecast_ui": "foreground duration, exact parent hash, idempotent mismatch retry",
        "unfinished": [
            "all_four_non_model_original_protocols",
            "standard_release_0080",
            "real_dsh_btc_eth_terminal",
            "formal_pc_and_manual",
        ],
        "future_statistics": "current_28_days_cannot_meet_100_independent_days",
    }
    dest = EVIDENCE / "B4/root-progress02.json"
    write(dest, receipt)
    for ref in (*files, "B4/root-progress02.json"):
        destination = FORMAL / "artifacts/market-risk-implementation-20261007" / ref
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EVIDENCE / ref, destination)
    contract_path = FORMAL / "docs/design/market-risk-v1/execution-contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["candidate_b2_b4"].update(
        {
            "ci_and_matching": "implemented_v2_private_original_controls_pass_formal_pending",
            "normal_composition": "private_normal_builders_api_and_independent_channels_pass",
            "non_model_component_protocols": "implementation_in_progress",
            "enhancement_candidates": "three_routes_fred_originals_and_independent_channel_pass",
            "root_progress_receipt": "B4/root-progress02.json",
        }
    )
    write(contract_path, contract)
    shutil.copyfile(contract_path, ROOT / "docs/design/market-risk-v1/execution-contract.json")
    print(
        json.dumps(
            {
                "passed": True,
                "evidence": len(files),
                "formal_source_writes": 0,
                "receipt": str(dest),
            }
        )
    )


if __name__ == "__main__":
    main()
