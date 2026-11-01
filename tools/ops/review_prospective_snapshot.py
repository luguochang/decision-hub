"""Local HTTP read-only review of an explicit experimental snapshot."""

import argparse
import os
from pathlib import Path

import uvicorn
from dotenv import dotenv_values
from fastapi.responses import JSONResponse

from packages.kernel.decision_hub_kernel.persistence.db import Database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--frontend-dir", type=Path, required=True)
    parser.add_argument("--provider-env", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8057)
    parser.add_argument("--allow-promotion-reviews", action="store_true",
                        help="Also allow the query-only crypto macro promotion review POST")
    args = parser.parse_args()
    if not args.database.is_file() or not (args.frontend_dir / "index.html").is_file():
        parser.error("existing snapshot and review frontend required")
    config = dotenv_values(args.provider_env)
    for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "DECISION_HUB_MODEL",
                "DECISION_HUB_LLM_API_MODE"):
        if config.get(key):
            os.environ[key] = config[key]
    os.environ.update(DECISION_HUB_DATA_DIR=str(args.data_root.resolve()),
                      DECISION_HUB_LLM_ENABLED="1", DECISION_HUB_MAX_RETRIES="0",
                      DECISION_HUB_TOKEN_BUDGET="2500", DECISION_HUB_TIMEOUT_SECONDS="60")
    # main has a module-level application; contain its initialization in the
    # explicitly selected review data directory, never a caller's default DB.
    from apps.hub_api.main import create_app

    database = Database(f"sqlite+pysqlite:///{args.database.resolve().as_posix()}")
    app = create_app(database, sources_enabled=False, frontend_dir=args.frontend_dir.resolve())

    @app.middleware("http")
    async def read_only(request, call_next):
        review_query = (
            args.allow_promotion_reviews and request.method == "POST"
            and request.url.path == "/v1/evolution/crypto_macro.v1/promotion-reviews"
        )
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not review_query:
            return JSONResponse({"detail": "review_snapshot_read_only"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Decision-Hub-Review-Only"] = "true"
        return response

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
