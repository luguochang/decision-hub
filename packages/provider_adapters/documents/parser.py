from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader

from packages.contracts_py.decision_hub_contracts import (
    DocumentBlock,
    DocumentParseReceipt,
    DocumentQuality,
)
from packages.kernel.decision_hub_kernel.ports.documents import (
    DocumentInput,
    DocumentParserPort,
    ParsedDocument,
)
from packages.provider_adapters.research.document_bundle_store import DocumentBundleStore

_NUMERIC_RE = re.compile(r"(?:[-+]?\d+(?:[.,]\d+)?%?|\b\d{1,2}[/:.-]\d{1,2}[/:.-]\d{2,4}\b)")
_REPLACEMENT = "\ufffd"


class DocumentParserRouter:
    """Ordered routing; parse receipts retain the selected parser's identity."""

    parser_ref = "document-parser-router.v1"
    parser_mode = "router"

    def __init__(self, parsers: tuple[DocumentParserPort, ...]) -> None:
        self.parsers = parsers

    def can_parse(self, file_path: Path) -> bool:
        return any(parser.can_parse(file_path) for parser in self.parsers)

    def parse(self, document: DocumentInput) -> ParsedDocument:
        for parser in self.parsers:
            if parser.can_parse(document.file_path):
                return parser.parse(document)
        raise ValueError("document_parser_unavailable")


class MineruCliDocumentParser:
    """Optional MinerU 4.x adapter.

    The adapter is deliberately process based so MinerU's model/runtime
    dependencies do not enter the Kernel. It only becomes active when the
    configured executable is present.
    """

    parser_ref = "mineru-cli.v4"
    parser_mode = "mineru_local"

    def __init__(self, store: DocumentBundleStore, executable: str | None = None) -> None:
        self.store = store
        self.executable = executable or os.getenv("DECISION_HUB_MINERU_BIN", "mineru")

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg", ".webp"} and bool(
            shutil.which(self.executable)
        )

    def parse(self, document: DocumentInput) -> ParsedDocument:
        with tempfile.TemporaryDirectory(prefix="decision-hub-mineru-") as raw_output:
            output_dir = Path(raw_output)
            command = [
                self.executable,
                "parse",
                str(document.file_path),
                "--pages",
                "all",
                "-o",
                str(output_dir),
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=900)
            candidates = sorted(output_dir.rglob("*.md"))
            if completed.returncode != 0 or not candidates:
                raise ValueError(f"mineru_parse_failed:{completed.returncode}")
            text = candidates[0].read_text(encoding="utf-8", errors="replace")
            return _build_parsed_document(
                document,
                text=text,
                parser_ref=self.parser_ref,
                parser_mode=self.parser_mode,
                store=self.store,
                page_count=_page_count_from_text(text),
            )


class PypdfTextDocumentParser:
    parser_ref = "pypdf-text.v1"
    parser_mode = "pypdf_text"

    def __init__(self, store: DocumentBundleStore) -> None:
        self.store = store

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".pdf", ".txt", ".md", ".html"}

    def parse(self, document: DocumentInput) -> ParsedDocument:
        suffix = document.file_path.suffix.lower()
        page_texts = None
        if suffix == ".pdf":
            reader = PdfReader(str(document.file_path))
            page_texts = tuple(page.extract_text() or "" for page in reader.pages)
            # Page labels are metadata, never substitute text for a missing layer.
            text = "\n\n".join(page.strip() for page in page_texts if page.strip())
            page_count = len(page_texts)
        else:
            text = document.file_path.read_text(encoding="utf-8", errors="replace")
            page_count = max(1, text.count("\f") + 1)
        return _build_parsed_document(
            document,
            text=text,
            parser_ref=self.parser_ref,
            parser_mode=self.parser_mode,
            store=self.store,
            page_count=page_count,
            page_texts=page_texts,
        )


class ImagePlaceholderDocumentParser:
    """Fail-closed image path when OCR is not installed.

    The UI still receives an immutable image block and a quality receipt. It
    never fabricates text from a screenshot; installing MinerU (or another
    OCR adapter) is an explicit deployment choice.
    """

    parser_ref = "image-placeholder.v1"
    parser_mode = "replay"

    def __init__(self, store: DocumentBundleStore) -> None:
        self.store = store

    def can_parse(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}

    def parse(self, document: DocumentInput) -> ParsedDocument:
        placeholder = "[图片内容待 OCR：未安装可用的本地 OCR 解析器]"
        block = DocumentBlock(
            schema_version="document-block.v1",
            block_id=f"{document.version_id}:block:1",
            version_id=document.version_id,
            block_type="image",
            page_no=1,
            content=placeholder,
            content_hash=hashlib.sha256(placeholder.encode("utf-8")).hexdigest(),
            locator=f"document://{document.version_id}/page/1/block/1",
            bbox=None,
            confidence=0.0,
            has_numeric_content=False,
            needs_review=True,
        )
        quality = DocumentQuality(
            layout_coverage=1.0,
            text_readability=0.0,
            table_integrity=0.0,
            image_coverage=1.0,
            numeric_suspicion=0,
            encoding_errors=0,
            locator_completeness=1.0,
            quality_status="blocked_low_quality",
            issues=["ocr_required", "no_readable_text"],
        )
        normalized_ref = self.store.write_text("", name="normalized.md")
        receipt = DocumentParseReceipt.model_validate(
            {
                "schema_version": "document-parse-receipt.v1",
                "version_id": document.version_id,
                "parser_ref": self.parser_ref,
                "parser_mode": self.parser_mode,
                "original_file_hash": document.original_file_hash,
                "parsed_bundle_ref": normalized_ref,
                "page_count": 1,
                "block_count": 1,
                "quality": quality,
                "errors": quality.issues,
                "completed_at": datetime.now(UTC),
            }
        )
        return ParsedDocument(
            receipt=receipt,
            blocks=(block,),
            normalized_text="",
            normalized_content_ref=normalized_ref,
        )


def _build_parsed_document(
    document: DocumentInput,
    *,
    text: str,
    parser_ref: str,
    parser_mode: str,
    store: DocumentBundleStore,
    page_count: int,
    page_texts: tuple[str, ...] | None = None,
) -> ParsedDocument:
    normalized = "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()
    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    blocks: list[DocumentBlock] = []
    positioned_lines = (
        [(page, line.strip()) for page, content in enumerate(page_texts, 1)
         for line in content.splitlines() if line.strip()]
        if page_texts is not None
        else [(_page_for_line(line, index, page_count), line)
              for index, line in enumerate(raw_lines, 1)]
    )
    for index, (page, line) in enumerate(positioned_lines, 1):
        block_type = "heading" if line.startswith("#") else "paragraph"
        block_hash = hashlib.sha256(line.encode("utf-8")).hexdigest()
        numeric = bool(_NUMERIC_RE.search(line))
        suspicious = _REPLACEMENT in line or (
            numeric and any(token in line for token in ("??", "###", "�"))
        )
        blocks.append(
            DocumentBlock(
                schema_version="document-block.v1",
                block_id=f"{document.version_id}:block:{index}",
                version_id=document.version_id,
                block_type=block_type,
                page_no=page,
                content=line,
                content_hash=block_hash,
                locator=f"document://{document.version_id}/page/{page}/block/{index}",
                bbox=None,
                confidence=0.82 if line else 0.0,
                has_numeric_content=numeric,
                needs_review=suspicious,
            )
        )
    numeric_suspicion = sum(1 for block in blocks if block.needs_review)
    encoding_errors = text.count(_REPLACEMENT)
    text_readability = min(1.0, len(normalized) / max(1, len(text))) if text else 0.0
    locator_completeness = 1.0 if blocks else 0.0
    issues: list[str] = []
    if not normalized:
        issues.append("no_readable_text")
        if page_texts is not None and page_texts:
            issues.append("ocr_required")
    empty_pages = (
        [str(page) for page, content in enumerate(page_texts, 1) if not content.strip()]
        if page_texts is not None else []
    )
    if empty_pages and normalized:
        issues.append(f"pages_without_readable_text:{','.join(empty_pages)}")
    if encoding_errors:
        issues.append("encoding_replacement_characters")
    if numeric_suspicion:
        issues.append("numeric_or_layout_suspicion")
    if page_count == 0:
        issues.append("page_count_unknown")
    quality_status = "ready_for_correction"
    if not normalized or not blocks:
        quality_status = "blocked_low_quality"
    elif numeric_suspicion or encoding_errors or text_readability < 0.8 or empty_pages:
        quality_status = "needs_review"
    quality = DocumentQuality(
        layout_coverage=1.0 if blocks else 0.0,
        text_readability=text_readability,
        table_integrity=0.0 if not blocks else 1.0,
        image_coverage=0.0 if not blocks else 1.0,
        numeric_suspicion=numeric_suspicion,
        encoding_errors=encoding_errors,
        locator_completeness=locator_completeness,
        quality_status=quality_status,
        issues=issues,
    )
    normalized_ref = store.write_text(normalized)
    receipt = DocumentParseReceipt.model_validate(
        {
            "schema_version": "document-parse-receipt.v1",
            "version_id": document.version_id,
            "parser_ref": parser_ref,
            "parser_mode": parser_mode,
            "original_file_hash": document.original_file_hash,
            "parsed_bundle_ref": normalized_ref,
            "page_count": page_count,
            "block_count": len(blocks),
            "quality": quality,
            "errors": issues,
            "completed_at": datetime.now(UTC),
        }
    )
    return ParsedDocument(
        receipt=receipt,
        blocks=tuple(blocks),
        normalized_text=normalized,
        normalized_content_ref=normalized_ref,
    )


def _page_for_line(line: str, index: int, page_count: int) -> int:
    match = re.search(r"\[Page\s+(\d+)\]", line, flags=re.IGNORECASE)
    if match:
        return max(1, min(page_count or 1, int(match.group(1))))
    return max(1, min(page_count or 1, index))


def _page_count_from_text(text: str) -> int:
    pages = [int(item) for item in re.findall(r"\[Page\s+(\d+)\]", text, flags=re.IGNORECASE)]
    return max(pages, default=1)
