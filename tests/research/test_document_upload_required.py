"""Required multipart upload through the real API and isolated offline SQLite."""

from __future__ import annotations

import hashlib
import socket
import sys
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from packages.kernel.decision_hub_kernel.application.research_library import (
        ResearchLibraryService,
    )
    from packages.kernel.decision_hub_kernel.persistence.db import Database


ROUTE = "/v1/research-library/documents"
OWNER = "upload-required-owner"


@dataclass(frozen=True)
class UploadInputs:
    client: TestClient
    database: Database
    library: ResearchLibraryService


@pytest.fixture
def inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[UploadInputs]:
    # Apply isolation before importing main: its module-level app initializes
    # a Database. That initialization must also stay in this fresh directory.
    monkeypatch.setenv("DECISION_HUB_DATA_DIR", str(tmp_path / "isolated-data"))
    monkeypatch.setenv("DECISION_HUB_DOCUMENT_BUNDLE_ROOT", str(tmp_path / "bundles"))
    for name in (
        "LLM_ENABLED",
        "DOCUMENT_CORRECTION_LLM_ENABLED",
        "DECISION_HUB_LLM_ENABLED",
        "DECISION_HUB_DOCUMENT_CORRECTION_LLM_ENABLED",
        "DECISION_HUB_SOURCES_ENABLED",
        "DECISION_HUB_MARKET_ENABLED",
        "DECISION_HUB_CALENDAR_DISCOVERY_ENABLED",
        "DECISION_HUB_EVENT_WINDOW_LIVE_ENABLED",
    ):
        monkeypatch.setenv(name, "0")

    def no_network(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("network is forbidden in the required-upload controls")

    original_connect = socket.socket.connect
    fallback = getattr(socket, "_fallback_socketpair", None)
    fallback_code = getattr(fallback, "__code__", None)

    def guarded_connect(connection: socket.socket, address: Any) -> None:
        caller = sys._getframe(1)  # pyright: ignore[reportPrivateUsage] -- exact stdlib IPC
        listener = caller.f_locals.get("lsock")
        # Same exact stdlib self-pipe exception as the read-command fixture.
        # Other loopback connections, all remote connections and DNS-based
        # create_connection remain forbidden; this is not an HTTP allowance.
        if (
            fallback_code is not None
            and caller.f_code is fallback_code
            and caller.f_locals.get("csock") is connection
            and isinstance(listener, socket.socket)
            and isinstance(address, tuple)
            and len(address) == 2
            and address[0] in {"127.0.0.1", "::1"}
            and isinstance(address[1], int)
            and 0 < address[1] <= 65535
            and connection.family in {socket.AF_INET, socket.AF_INET6}
            and connection.type == socket.SOCK_STREAM
            and listener.getsockname()[:2] == address
        ):
            return original_connect(connection, address)
        no_network()

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    from apps.hub_api.main import create_app
    from packages.kernel.decision_hub_kernel.persistence.db import Database

    database = Database(f"sqlite+pysqlite:///{tmp_path / 'upload.sqlite3'}")
    app = create_app(database, source_connectors=[], sources_enabled=False)
    # Deliberately do not enter TestClient's context/lifespan: the only parse
    # is the upload route's actual bounded background task for the tiny TXT.
    client = TestClient(app)
    try:
        yield UploadInputs(client, database, app.state.research_library)
    finally:
        client.close()
        database.engine.dispose()


def _metadata() -> str:
    from packages.contracts_py.decision_hub_contracts import DocumentUpload

    return DocumentUpload(
        schema_version="document-upload.v1",
        title="上传必填文件离线对照",
        source_type="private_file",
        market_scopes=["crypto_macro"],
        instrument_scopes=["BTC-USDT"],
        privacy_mode="local_only",
        visibility="private",
        notes="保留原文本；不调用 Provider",
    ).model_dump_json()


def _counts(database: Database) -> tuple[int, int, int]:
    from packages.kernel.decision_hub_kernel.persistence.db import (
        ProductCommandRecord,
        ResearchDocumentRecord,
        ResearchDocumentVersionRecord,
    )

    with database.session() as session:
        return (
            session.query(ResearchDocumentRecord).count(),
            session.query(ResearchDocumentVersionRecord).count(),
            session.query(ProductCommandRecord).filter_by(kind="library_upload").count(),
        )


def test_actual_multipart_missing_file_is_422_before_upload(inputs: UploadInputs) -> None:
    assert _counts(inputs.database) == (0, 0, 0)
    response = inputs.client.post(
        ROUTE,
        # A filename-less form part makes this real multipart, not urlencoded.
        files={"metadata": (None, _metadata(), "application/json")},
        headers={"X-Owner-Id": OWNER},
    )
    assert response.status_code == 422
    assert any(
        error["loc"] == ["body", "file"] and error["type"] == "missing"
        for error in response.json()["detail"]
    )
    assert _counts(inputs.database) == (0, 0, 0)
    assert not list(inputs.library.store.root.rglob("original*"))


def test_actual_openapi_requires_multipart_file_and_metadata(inputs: UploadInputs) -> None:
    response = inputs.client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()
    request_body = document["paths"][ROUTE]["post"]["requestBody"]
    assert request_body["required"] is True
    schema = request_body["content"]["multipart/form-data"]["schema"]
    if "$ref" in schema:
        prefix = "#/components/schemas/"
        assert schema["$ref"].startswith(prefix)
        schema = document["components"]["schemas"][schema["$ref"][len(prefix):]]
    assert {"file", "metadata"}.issubset(schema["required"])
    assert schema["properties"]["file"]["type"] == "string"
    assert schema["properties"]["file"]["contentMediaType"] == "application/octet-stream"
    assert schema["properties"]["metadata"]["type"] == "string"
    assert _counts(inputs.database) == (0, 0, 0)


def test_actual_small_txt_upload_retains_source_and_returns_version(
    inputs: UploadInputs,
) -> None:
    from packages.contracts_py.decision_hub_contracts import ResearchDocumentVersion

    source = "# 离线原文\nBTC 材料仅用于上传和文本解析对照。\n".encode()
    response = inputs.client.post(
        ROUTE,
        files={"file": ("source.txt", source, "text/plain")},
        data={"metadata": _metadata()},
        headers={"X-Owner-Id": OWNER},
    )
    assert response.status_code == 202
    receipt = ResearchDocumentVersion.model_validate_json(response.text)
    assert receipt.status == "received"
    assert receipt.original_file_hash == hashlib.sha256(source).hexdigest()
    assert inputs.library.store.read(receipt.original_file_ref) == source
    assert _counts(inputs.database) == (1, 1, 1)

    detail = inputs.client.get(f"/v1/research-library/versions/{receipt.version_id}")
    assert detail.status_code == 200
    parsed = ResearchDocumentVersion.model_validate_json(detail.text)
    assert parsed.version_id == receipt.version_id
    assert parsed.document_id == receipt.document_id
    assert parsed.status == "parsed"
    assert parsed.correction_status == "not_requested"
    assert parsed.original_file_ref == receipt.original_file_ref
    assert parsed.original_file_hash == receipt.original_file_hash
    assert parsed.block_count > 0

    original = inputs.client.get(
        f"/v1/research-library/versions/{receipt.version_id}/original"
    )
    assert original.status_code == 200
    assert original.content == source
    assert _counts(inputs.database) == (1, 1, 1)
