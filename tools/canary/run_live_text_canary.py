from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from packages.contracts_py.decision_hub_contracts.models import ObservationCreate
from packages.kernel.decision_hub_kernel.application.analyze import AnalyzeTextService
from packages.kernel.decision_hub_kernel.persistence.db import Database
from packages.runtime_adapters.langgraph_agent.runtime import LangGraphAgentRuntime

DEFAULT_TEXT = (
    "Live canary fixture: the central bank says policy remains data dependent, "
    "inflation is still elevated, and no immediate policy change is promised."
)
_SENSITIVE = re.compile(
    r"(?i)(bearer\s+|api[_-]?key\s*[=:]\s*|sk-[a-z0-9_-]+|authorization\s*[=:]\s*)[^\s,;]+"
)


def _secret() -> str | None:
    return (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("SUB2API_API_KEY")
    )


def redact_for_output(value: object, secret: str | None) -> str:
    rendered = str(value)
    if secret:
        rendered = rendered.replace(secret, "<redacted>")
    return _SENSITIVE.sub(r"\1<redacted>", rendered)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run an opt-in OpenAI-compatible text canary through the full Decision Hub path."
        )
    )
    parser.add_argument(
        "--text",
        default=DEFAULT_TEXT,
        help="Synthetic fixture text. Do not pass secrets or sensitive production text.",
    )
    return parser.parse_args()


async def run_canary(text: str) -> dict[str, object]:
    secret = _secret()
    if os.getenv("DECISION_HUB_LLM_ENABLED") != "1":
        raise RuntimeError(
            "Live canary is opt-in: set DECISION_HUB_LLM_ENABLED=1 in the current process."
        )
    if not secret:
        raise RuntimeError(
            "Live canary requires OPENAI_API_KEY, DEEPSEEK_API_KEY, or SUB2API_API_KEY; "
            "inject it temporarily and never write it to a file."
        )
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("SUB2API_BASE_URL")
    if not base_url:
        raise RuntimeError("Set OPENAI_BASE_URL (for example, https://codexai.club/v1).")
    model = os.getenv("DECISION_HUB_MODEL")
    if not model:
        raise RuntimeError("Set DECISION_HUB_MODEL (for example, gpt-5.5).")
    if not text.strip():
        raise ValueError("Canary text must not be empty.")

    with tempfile.TemporaryDirectory(prefix="decision-hub-live-canary-") as temp_dir:
        database = Database(f"sqlite+pysqlite:///{Path(temp_dir) / 'canary.sqlite3'}")
        database.create_all()
        service = AnalyzeTextService(database, LangGraphAgentRuntime())
        event_id, run_id, admitted = await service.submit_and_run(
            ObservationCreate(
                text=text,
                source_id="live-canary",
                language="en",
                event_hint="provider_compatibility_canary",
            )
        )
        run = database.get_run_view(run_id)
        if run is None:
            raise RuntimeError("Canary completed without a RunView.")
        artifact = database.get_artifact_view(run.artifact_id) if run.artifact_id else None
        if artifact is None:
            raise RuntimeError(f"Canary completed without an artifact; status={run.status.value}")
        result_payload: dict[str, object] = {
            "event_id": event_id,
            "run_id": run_id,
            "admitted": admitted,
            "status": run.status.value,
            "gate_status": artifact.gate_status.value,
            "forecast_horizons": [forecast.horizon for forecast in artifact.forecasts],
            "input_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "result_sha256": hashlib.sha256(
                artifact.model_dump_json(exclude={"created_at"}).encode("utf-8")
            ).hexdigest(),
            "model": model,
            "base_url": base_url,
        }
        return result_payload


def main() -> int:
    args = _parse_args()
    secret = _secret()
    try:
        result = asyncio.run(run_canary(args.text))
    except Exception as exc:
        print(
            json.dumps(
                {"status": "error", "error": redact_for_output(exc, secret)},
                ensure_ascii=False,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
