from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from packages.contracts_py.decision_hub_contracts import (
    AnalystClaim,
    DocumentBlock,
    DocumentCorrection,
    DocumentParseReceipt,
)


@dataclass(frozen=True)
class DocumentInput:
    version_id: str
    file_path: Path
    original_file_ref: str
    original_file_hash: str
    title: str
    privacy_mode: str


@dataclass(frozen=True)
class ParsedDocument:
    receipt: DocumentParseReceipt
    blocks: tuple[DocumentBlock, ...]
    normalized_text: str
    normalized_content_ref: str | None


@dataclass(frozen=True)
class CorrectionResult:
    correction: DocumentCorrection
    corrected_blocks: tuple[DocumentBlock, ...]
    corrected_text: str


class DocumentParserPort(Protocol):
    parser_ref: str
    parser_mode: str

    def can_parse(self, file_path: Path) -> bool: ...

    def parse(self, document: DocumentInput) -> ParsedDocument: ...


class DocumentCorrectionPort(Protocol):
    correction_model_ref: str

    async def correct(
        self,
        document: ParsedDocument,
        *,
        title: str,
        market_scopes: tuple[str, ...],
        instrument_scopes: tuple[str, ...],
        privacy_mode: str = "local_only",
    ) -> CorrectionResult: ...


class AnalystClaimExtractorPort(Protocol):
    async def extract(
        self,
        *,
        version_id: str,
        corrected_blocks: tuple[DocumentBlock, ...],
        market_scopes: tuple[str, ...],
        instrument_scopes: tuple[str, ...],
        author: str | None,
        published_at: object,
    ) -> tuple[AnalystClaim, ...]: ...
