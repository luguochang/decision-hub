"""Bound existing evidence and synchronize the short repository delivery state."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORMAL = ROOT.parent / "decision-hub"
FOLDER = ROOT / "docs/design/market-risk-v1"
EVIDENCE = ROOT / "artifacts/market-risk-implementation-20261007"


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    # Formal documentation is authoritative; the candidate's companion machine
    # file was stale while prose was current. Synchronize both before promotion.
    source = FORMAL / "docs/design/market-risk-v1/execution-contract.json"
    contract = json.loads(source.read_text(encoding="utf-8"))
    candidate = contract["candidate_b2_b4"]
    candidate.update({
        "pc_real_public_price_preview": "pass_candidate_not_formal",
        "method_memory_consumer": "implemented_control_tests_pass_real_dsh_pending",
        "component_consumer": "actual_five_boundaries_integrated_scoped_tests_pass",
        "enhancement_candidates": "implementation_in_progress",
        "core_integration_receipt": "B4/component-root-hooks01.xml",
        "pc_preview_receipt": "B2/pc-candidate01/acceptance01.md",
        "preview_services_stopped": True,
    })
    write(source, contract)
    shutil.copyfile(source, FOLDER / "execution-contract.json")
    refs = (
        "B4/component-root-hooks01.xml",
        "B4/case-context-receipt01.md",
        "B2/quality-projection-root01.xml",
        "B2/quality-ui-integration-root01.xml",
        "B2/quality-ui-integration-root02.xml",
        "B2/quality-ui-integration-root04.xml",
        "B2/risk-trigger-exclusion01.xml",
        "B2/pc-candidate01/eth-home01.jpg",
        "B2/pc-candidate01/eth-24h-short10x01.jpg",
        "B2/pc-candidate01/eth-paused-after-refresh01.jpg",
        "B2/pc-candidate01/eth-seven-day-gaps01.jpg",
    )
    write(EVIDENCE / "B4/root-integration-receipt01.json", {
        "schema_version": "market-risk-root-integration.v1",
        "at": datetime.now(UTC).isoformat(), "formal_installed": False,
        "actual_component_hook_tests": {"passed": 5, "seconds": 9.62},
        "real_dsh_spot_accepted": False,
        "evidence": {ref: hashlib.sha256((EVIDENCE / ref).read_bytes()).hexdigest()
                     for ref in refs},
        "source_paths": ["apps/hub_worker/market_component_bootstrap.py",
                         "apps/hub_worker/composition.py",
                         "packages/kernel/decision_hub_kernel/ports/market_component_consumer.py",
                         "packages/kernel/decision_hub_kernel/application/market_component_functions.py",
                         "packages/kernel/decision_hub_kernel/application/market_risk_data.py",
                         "packages/kernel/decision_hub_kernel/application/risk_monitor.py"],
        "remaining": ["standard0080_release", "real_dsh_btc_eth_pc",
                      "actual_enhancement_inputs", "future_statistics"],
        "documentation_fix": "candidate companion machine state synchronized from formal B2",
    })
    # Evidence files only. Never copy preview DB, raw captures or formal data.
    for ref in (*refs, "B4/root-integration-receipt01.json"):
        destination = FORMAL / "artifacts/market-risk-implementation-20261007" / ref
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(EVIDENCE / ref, destination)


if __name__ == "__main__":
    main()
