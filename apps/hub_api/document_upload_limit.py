"""Bound upload request streams before the framework parses multipart files."""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_DOCUMENT_FILE_BYTES = 20 * 1024 * 1024
MAX_DOCUMENT_REQUEST_BYTES = MAX_DOCUMENT_FILE_BYTES + 64 * 1024
UPLOAD_PATHS = frozenset({"/v1/research-library/documents", "/v1/research-library/uploads"})


class _UploadRequestTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="document_upload_request_too_large")


class DocumentUploadRequestLimitMiddleware:
    """Count actual bytes, including chunked requests without Content-Length."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (scope["type"] != "http" or scope.get("method") != "POST"
                or scope.get("path") not in UPLOAD_PATHS):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        declared = headers.get(b"content-length")
        if declared and declared.isdigit() and int(declared) > MAX_DOCUMENT_REQUEST_BYTES:
            await self._reject(scope, receive, send)
            return
        size = 0
        started = False

        async def bounded_receive() -> Message:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > MAX_DOCUMENT_REQUEST_BYTES:
                    raise _UploadRequestTooLarge
            return message

        async def track_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, track_send)
        except _UploadRequestTooLarge:
            if started:
                raise
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        await JSONResponse(
            {"detail": "document_upload_request_too_large"}, status_code=413,
        )(scope, receive, send)
