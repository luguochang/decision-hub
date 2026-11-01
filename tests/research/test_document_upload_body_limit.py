"""Exercise the request stream boundary independently of parser buffering."""

import asyncio
import json
from collections.abc import Iterable, Sequence
from typing import Annotated

import pytest
from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from apps.hub_api.document_upload_limit import (
    MAX_DOCUMENT_REQUEST_BYTES,
    DocumentUploadRequestLimitMiddleware,
)


def run_stream(
    chunks: Iterable[tuple[bytes, bool]], *, path: str = "/v1/research-library/uploads",
    headers: Sequence[tuple[bytes, bytes]] = (),
) -> tuple[list[int], list[Message], list[int]]:
    consumed: list[int] = []
    sent: list[Message] = []
    reads: list[int] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        while True:
            message = await receive()
            consumed.append(len(message.get("body", b"")))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    stream = iter(chunks)

    async def receive() -> Message:
        item = next(stream)
        reads.append(len(item[0]))
        return {"type": "http.request", "body": item[0], "more_body": item[1]}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": "POST", "path": path, "headers": list(headers)}
    asyncio.run(DocumentUploadRequestLimitMiddleware(downstream)(scope, receive, send))
    return consumed, sent, reads


def test_exact_request_boundary_is_forwarded_without_reassembly():
    consumed, sent, _ = run_stream([(b"a" * MAX_DOCUMENT_REQUEST_BYTES, False)])
    assert consumed == [MAX_DOCUMENT_REQUEST_BYTES]
    assert sent[0]["status"] == 202


@pytest.mark.parametrize("headers", [(), ((b"content-length", b"1"),)])
def test_actual_chunked_body_cannot_bypass_limit_with_absent_or_false_length(
    headers: Sequence[tuple[bytes, bytes]],
):
    consumed, sent, reads = run_stream(
        [(b"a" * MAX_DOCUMENT_REQUEST_BYTES, True), (b"b", False)], headers=headers,
    )
    assert consumed == [MAX_DOCUMENT_REQUEST_BYTES]
    assert reads == [MAX_DOCUMENT_REQUEST_BYTES, 1]
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"]) == {"detail": "document_upload_request_too_large"}


def test_known_oversized_request_is_rejected_before_any_parser_read():
    consumed, sent, reads = run_stream(
        [], headers=((b"content-length", str(MAX_DOCUMENT_REQUEST_BYTES + 1).encode()),),
    )
    assert consumed == reads == []
    assert sent[0]["status"] == 413


def test_other_post_routes_are_not_limited_by_document_policy():
    consumed, sent, _ = run_stream(
        [(b"a" * (MAX_DOCUMENT_REQUEST_BYTES + 1), False)], path="/other",
    )
    assert consumed == [MAX_DOCUMENT_REQUEST_BYTES + 1]
    assert sent[0]["status"] == 202


def test_framework_form_parser_preserves_413_for_an_oversized_actual_stream():
    app = FastAPI()
    app.add_middleware(DocumentUploadRequestLimitMiddleware)
    invoked = []

    @app.post("/v1/research-library/uploads")
    async def upload(file: Annotated[UploadFile, File()]):
        invoked.append(file.filename)
        return {"ok": True}

    prefix = (b'--boundary\r\nContent-Disposition: form-data; name="file"; '
              b'filename="fixture.txt"\r\nContent-Type: text/plain\r\n\r\n')
    suffix = b"\r\n--boundary--\r\n"
    with TestClient(app) as client:
        accepted = client.post("/v1/research-library/uploads", content=prefix + b"small" + suffix,
                               headers={"Content-Type": "multipart/form-data; boundary=boundary"})
        assert accepted.status_code == 200
        assert invoked == ["fixture.txt"]
        invoked.clear()
        response = client.post(
            "/v1/research-library/uploads",
            content=iter([prefix, b"a" * MAX_DOCUMENT_REQUEST_BYTES, suffix]),
            headers={"Content-Type": "multipart/form-data; boundary=boundary",
                     "Content-Length": "1"},
        )
        assert response.status_code == 413
        assert response.json() == {"detail": "document_upload_request_too_large"}
        assert invoked == []
