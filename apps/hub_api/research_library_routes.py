from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from apps.hub_api.document_uploads import read_document_upload
from packages.contracts_py.decision_hub_contracts import (
    AnalystClaim,
    ClaimReviewCommand,
    DocumentUploadIntent,
    DocumentUploadPolicy,
    DocumentUploadReceipt,
    ResearchDocumentVersion,
)
from packages.kernel.decision_hub_kernel.application.research_library import ResearchLibraryService
from packages.kernel.decision_hub_kernel.application.research_library_commands import (
    DocumentRecoveryCommand,
    ResearchLibraryCommands,
)


from packages.kernel.decision_hub_kernel.application.research_library_provenance import DocumentProvenanceView


def research_library_router(
    library: ResearchLibraryService,
    require_owner: Callable[..., str],
) -> APIRouter:
    router = APIRouter(tags=["research-library-commands"])
    service = ResearchLibraryCommands(library)

    @router.get("/v1/research-library/versions/{version_id}/provenance", response_model=DocumentProvenanceView)
    def document_provenance(version_id: str) -> DocumentProvenanceView:
        result = library.document_provenance(version_id)
        if result is None:
            raise HTTPException(404, "document_version_not_found")
        return result

    @router.get("/v1/research-library/upload-policy", response_model=DocumentUploadPolicy)
    def upload_policy() -> DocumentUploadPolicy:
        return DocumentUploadPolicy(
            schema_version="document-upload-policy.v1",
            max_file_size_bytes=20971520,
        )

    @router.post(
        "/v1/research-library/uploads",
        status_code=202,
        response_model=DocumentUploadReceipt,
    )
    async def upload_document(
        file: Annotated[UploadFile, File()],
        intent: Annotated[str, Form()],
        x_owner_id: Annotated[str | None, Header(max_length=128)] = None,
        idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
    ) -> DocumentUploadReceipt:
        owner = require_owner(x_owner_id)
        if not idempotency_key:
            raise HTTPException(400, "document_upload_request_id_required")
        try:
            payload = DocumentUploadIntent.model_validate_json(intent)
            require_owner(owner, payload.owner_id)
            data = await read_document_upload(file)
            receipt, _ = await run_in_threadpool(
                service.upload,
                owner,
                idempotency_key,
                payload,
                filename=file.filename or "upload.bin",
                data=data,
            )
            # A durable DocumentWorker owns parsing. Acceptance never waits for a
            # parser or treats parser failure as failure to commit this upload.
            return receipt
        except ValueError as exc:
            code = str(exc)
            status = 409 if code == "product_request_reused" else 400
            if code == "document_file_too_large":
                status = 413
            raise HTTPException(status, code) from exc

    @router.get(
        "/v1/research-library/uploads/{request_id}",
        response_model=DocumentUploadReceipt,
    )
    def upload_receipt(
        request_id: str,
        x_owner_id: Annotated[str | None, Header(max_length=128)] = None,
    ) -> DocumentUploadReceipt:
        owner = require_owner(x_owner_id)
        try:
            return service.upload_receipt(request_id, owner)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    def execute(target, payload, owner, key, callback):
        owner = require_owner(owner)
        if key != payload.request_id:
            raise HTTPException(400, "idempotency_key_mismatch")
        try:
            return callback(target, owner, payload)
        except KeyError as exc:
            raise HTTPException(404, exc.args[0]) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post(
        "/v1/research-library/versions/{version_id}/recover",
        status_code=202,
        response_model=ResearchDocumentVersion,
    )
    def recover(
        version_id: str,
        payload: DocumentRecoveryCommand,
        x_owner_id: str | None = Header(None, max_length=128),
        idempotency_key: str | None = Header(None, max_length=128),
    ):
        return execute(version_id, payload, x_owner_id, idempotency_key, service.recover)

    @router.post(
        "/v1/research-library/claims/{claim_id}/review",
        response_model=AnalystClaim,
    )
    def review(
        claim_id: str,
        payload: ClaimReviewCommand,
        x_owner_id: str | None = Header(None, max_length=128),
        idempotency_key: str | None = Header(None, max_length=128),
    ):
        return execute(claim_id, payload, x_owner_id, idempotency_key, service.review_claim)

    return router
