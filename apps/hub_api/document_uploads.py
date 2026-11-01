"""Bounded application reads, after Starlette has parsed/spooled multipart.

This caps the file bytes copied by the route. It is not a transport, multipart
aggregate, or spool-disk limit: multipart parsing occurs before route execution.
"""

from fastapi import HTTPException, UploadFile

from packages.kernel.decision_hub_kernel.application.research_library import (
    MAX_DOCUMENT_FILE_SIZE_BYTES,
)


async def read_document_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while size <= MAX_DOCUMENT_FILE_SIZE_BYTES:
        # Read at most one byte over the boundary to distinguish exactly L from L+1.
        chunk = await file.read(min(64 * 1024, MAX_DOCUMENT_FILE_SIZE_BYTES + 1 - size))
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > MAX_DOCUMENT_FILE_SIZE_BYTES:
            raise HTTPException(413, "document_file_too_large")
        chunks.append(chunk)
    raise HTTPException(413, "document_file_too_large")
