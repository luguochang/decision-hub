from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = [
    ROOT / "packages/kernel/README.md",
    ROOT / "packages/orchestration/langgraph/README.md",
    ROOT / "packages/runtime_adapters/README.md",
    ROOT / "packages/source_adapters/README.md",
    ROOT / "packages/query_views/README.md",
    ROOT / "packages/contracts_ts/README.md",
    ROOT / "apps/hub_api/README.md",
    ROOT / "apps/hub_worker/README.md",
    ROOT / "apps/decision-desk/README.md",
]


def main() -> int:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED if not path.exists()]
    if missing:
        print("missing module README:")
        print("\n".join(missing))
        return 1
    print(f"module docs: ok ({len(REQUIRED)} modules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
