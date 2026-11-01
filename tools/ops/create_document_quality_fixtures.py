"""Create non-market PC document quality fixtures in a fresh isolated directory."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.ops.serve_document_recovery_probe import validate_output  # noqa: E402

UPLOAD_LIMIT_BYTES = 20 * 1024 * 1024


def readable_pdf_bytes() -> bytes:
    """Two real PDF text layers, independently verified in the manifest."""
    writer = PdfWriter()
    for text in (
        "PC isolated readable PDF control. No AI research or market advice.",
        "Second actual PDF text page. Original bytes and page locator are preserved.",
    ):
        page = writer.add_blank_page(width=600, height=300)
        font = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        })
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 11 Tf 20 150 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def write_boundary_text(path: Path, size: int) -> None:
    """Exact byte length with 321 lines, below the 100000-char block contract."""
    header = b"PC ISOLATED UPLOAD BOUNDARY. NON MARKET. NO AI RESEARCH.\n"
    remaining = size - len(header)
    chunk_size = 64 * 1024
    chunk = b"PC isolated bytes. " * (chunk_size // 19 + 1)
    with path.open("wb") as output:
        output.write(header)
        while remaining:
            line_size = min(remaining, chunk_size)
            if line_size == 1:
                output.write(b"\n")
            else:
                output.write(chunk[:line_size - 1] + b"\n")
            remaining -= line_size


def upload_fixtures(root: Path) -> dict[str, dict]:
    data = readable_pdf_bytes()
    (root / "pc-readable-two-pages.pdf").write_bytes(data)
    (root / "pc-corrupt-nine-bytes.pdf").write_bytes(b"not a PDF")
    (root / "pc-truncated-64-bytes.pdf").write_bytes(data[:64])
    (root / "pc-empty.txt").write_bytes(b"")
    cases = {
        "pc-readable-two-pages.pdf": {"case": "readable_pdf", "expected_status": "parsed"},
        "pc-corrupt-nine-bytes.pdf": {
            "case": "corrupt_pdf", "expected_status": "failed", "intended_corruption": True,
        },
        "pc-truncated-64-bytes.pdf": {
            "case": "truncated_pdf", "expected_status": "failed", "intended_corruption": True,
        },
        "pc-empty.txt": {"case": "empty", "expected_http_status": 400},
    }
    for delta, label in ((-1, "limit-minus-one"), (0, "limit"), (1, "limit-plus-one")):
        name = f"pc-{label}.txt"
        write_boundary_text(root / name, UPLOAD_LIMIT_BYTES + delta)
        cases[name] = {
            "case": label.replace("-", "_"), "limit_bytes": UPLOAD_LIMIT_BYTES,
            "expected_http_status": 413 if delta > 0 else 202,
        }
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=("quality", "upload"), default="quality")
    args = parser.parse_args()
    try:
        root = validate_output(args.output)
    except ValueError as exc:
        parser.error(str(exc))
    root.mkdir(parents=True, exist_ok=False)
    if args.profile == "upload":
        cases = upload_fixtures(root)
    else:
        cases = quality_fixtures(root)
    result = {
        "purpose": "PC isolated document acceptance", "profile": args.profile,
        "paid_provider": False, "production_data": False,
        "limit_bytes": UPLOAD_LIMIT_BYTES if args.profile == "upload" else None, "files": [],
    }
    for path in sorted(root.iterdir()):
        data = path.read_bytes()
        entry = {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                 "bytes": len(data), **cases.get(path.name, {})}
        if path.suffix == ".pdf":
            try:
                pages = PdfReader(io.BytesIO(data)).pages
                texts = [page.extract_text() or "" for page in pages]
            except Exception as exc:
                if not entry.get("intended_corruption"):
                    raise
                entry.update({"pdf_read_error_type": type(exc).__name__,
                              "expected_read_failure_observed": True})
            else:
                if entry.get("intended_corruption"):
                    raise RuntimeError("corrupt fixture unexpectedly parsed")
                entry.update({
                    "page_count": len(pages), "extracted_text": texts,
                    "extracted_text_lengths": [len(text) for text in texts],
                })
        elif path.suffix == ".txt":
            entry["text_line_count"] = len(data.splitlines())
        result["files"].append(entry)
    (root / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))


def quality_fixtures(root: Path) -> dict[str, dict]:
    image = Image.new("RGB", (900, 620), "white")
    draw = ImageDraw.Draw(image)
    for line, text in enumerate([
        "PC ISOLATED DOCUMENT QUALITY ACCEPTANCE",
        "This page contains pixels only. There is no selectable PDF text.",
        "No OCR provider is configured in the local acceptance probe.",
        "This fixture is not market evidence or investment advice.",
    ]):
        draw.text((40, 60 + line * 55), text, fill="black")
    image.save(root / "pc-scanned-image.png")
    image.save(root / "pc-scanned-document.pdf", "PDF")
    second = Image.new("RGB", image.size, "white")
    ImageDraw.Draw(second).text((40, 60), "SECOND PIXEL PAGE - NO TEXT LAYER", fill="black")
    image.save(root / "pc-scanned-two-pages.pdf", "PDF", save_all=True, append_images=[second])
    (root / "pc-readable-control.txt").write_text(
        "PC isolated readable document control. This is non-market acceptance material.\n"
        "A local parser can read these actual words. No AI research is requested.\n",
        encoding="utf-8",
    )
    return {}


if __name__ == "__main__":
    main()
