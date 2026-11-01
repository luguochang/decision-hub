"""Capture one configured, permitted quote for the existing acquisition API.

This is a bounded adapter command, not a polling service. It makes ordinary
unauthenticated HTTP requests and retains only the canonical partial manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup
from packages.kernel.decision_hub_kernel.ports.opinion_capture import capture_json
from packages.provider_adapters.research.opinion_capture import (
    OpinionCaptureRegistry,
    capture_html_excerpt,
)

from packages.contracts_py.decision_hub_contracts import SourceAcquisition

USER_AGENT = "DecisionHubOpinionResearch/1.0"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("opinion_capture_redirect_not_permitted")


def _get(url: str) -> bytes:
    with build_opener(NoRedirect()).open(
        Request(url, headers={"User-Agent": USER_AGENT}), timeout=15
    ) as response:
        data = response.read(1024 * 1024 + 1)
        if response.status != 200 or len(data) > 1024 * 1024:
            raise ValueError("opinion_capture_http_response_invalid")
        return data


def capture_one(
    *,
    registry: OpinionCaptureRegistry,
    source_id: str,
    url: str,
    quotes: tuple[str, ...],
    artifact_root: Path,
    use: str,
    get=_get,
) -> SourceAcquisition:
    permission, _ = registry.require(source_id, url)
    if use != permission.permitted_use:
        raise ValueError("opinion_capture_use_not_permitted")
    target = urlsplit(url)
    robot_url = f"{target.scheme}://{target.netloc}/robots.txt"
    robot = RobotFileParser(robot_url)
    try:
        robot.parse(get(robot_url).decode("utf-8").splitlines())
        if not robot.can_fetch(USER_AGENT, url):
            raise ValueError("opinion_capture_robots_denied")
    except HTTPError as error:
        if error.code != 404:
            raise
    # Retain only the reviewed notice hash, not full terms/article response bytes.
    rights = BeautifulSoup(get(permission.rights_url).decode("utf-8"), "html.parser").get_text(
        " ", strip=True
    )
    if " ".join(permission.rights_notice.split()) not in " ".join(rights.split()):
        raise ValueError("opinion_capture_permission_notice_changed")
    started = datetime.now(UTC)
    html = get(url)
    observed = datetime.now(UTC)
    capture = capture_html_excerpt(
        html,
        source_id=source_id,
        url=url,
        observed_at=observed,
        registry=registry,
        exact_quotes=quotes,
    )
    raw = capture_json(capture)
    digest = hashlib.sha256(raw).hexdigest()
    artifact_root = artifact_root.resolve()
    artifact_root.mkdir(parents=True, exist_ok=True)
    path = artifact_root / (digest + ".opinion.json")
    if path.exists() and path.read_bytes() != raw:
        raise ValueError("opinion_capture_original_conflict")
    if not path.exists():
        with path.open("xb") as stream:
            stream.write(raw)
    return SourceAcquisition.model_validate(
        {
            "schema_version": "acquisition-receipt.v1",
            "receipt_id": "opcap_" + digest,
            "source_id": source_id,
            "url": url,
            "access_mode": "http",
            "status": "succeeded",
            "request_started_at": started,
            "request_completed_at": observed,
            "observed_at": observed,
            "status_code": 200,
            "error_code": None,
            "artifacts": [{"kind": "json", "path": str(path), "content_hash": digest}],
        }
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--use", choices=["personal_noncommercial_research"], required=True)
    parser.add_argument("--quote", action="append", default=[])
    arguments = parser.parse_args()
    result = capture_one(
        registry=OpinionCaptureRegistry.from_pack(arguments.pack),
        source_id=arguments.source_id,
        url=arguments.url,
        quotes=tuple(arguments.quote),
        artifact_root=arguments.artifact_root,
        use=arguments.use,
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))


if __name__ == "__main__":
    main()
