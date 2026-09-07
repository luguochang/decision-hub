"""Small OTLP/HTTP test receiver used by the DSH observability canary.

It intentionally does not decode or persist trace content. The canary only
needs to prove that the official plugin emitted protobuf requests and that a
backend outage is isolated from DSH execution. Request sizes and paths are
written as JSONL metadata under ``tmp/`` (which is gitignored).
"""

from __future__ import annotations

import argparse
import gzip
import json
import signal
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("truncated protobuf varint")


def _fields(data: bytes):
    offset = 0
    while offset < len(data):
        key, offset = _varint(data, offset)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire == 1:
            value, offset = data[offset : offset + 8], offset + 8
        elif wire == 2:
            length, offset = _varint(data, offset)
            value, offset = data[offset : offset + length], offset + length
        elif wire == 5:
            value, offset = data[offset : offset + 4], offset + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield number, value


def _span_summaries(body: bytes) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for number, resource_spans in _fields(body):
        if number != 1 or not isinstance(resource_spans, bytes):
            continue
        for scope_number, scope_spans in _fields(resource_spans):
            if scope_number != 2 or not isinstance(scope_spans, bytes):
                continue
            for span_number, span in _fields(scope_spans):
                if span_number != 2 or not isinstance(span, bytes):
                    continue
                values = {field: value for field, value in _fields(span)}
                name = values.get(5, b"")
                trace_id = values.get(1, b"")
                span_id = values.get(2, b"")
                parent_id = values.get(4, b"")
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                attribute_keys: list[str] = []
                for attribute_number, attribute in _fields(span):
                    if attribute_number != 9 or not isinstance(attribute, bytes):
                        continue
                    key = next(
                        (
                            value.decode("utf-8", errors="replace")
                            for field, value in _fields(attribute)
                            if field == 1 and isinstance(value, bytes)
                        ),
                        None,
                    )
                    if key is not None:
                        attribute_keys.append(key)
                summaries.append(
                    {
                        "name": name,
                        "trace_id": trace_id.hex() if isinstance(trace_id, bytes) else "",
                        "span_id": span_id.hex() if isinstance(span_id, bytes) else "",
                        "parent_span_id": parent_id.hex() if isinstance(parent_id, bytes) else "",
                        "attribute_keys": attribute_keys,
                    }
                )
    return summaries


class CaptureHandler(BaseHTTPRequestHandler):
    server: CaptureServer

    def _read_body(self) -> bytes:
        if self.headers.get("transfer-encoding", "").lower() == "chunked":
            chunks: list[bytes] = []
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    continue
                size = int(line.split(b";", 1)[0], 16)
                if size == 0:
                    while self.rfile.readline().strip():
                        pass
                    break
                chunks.append(self.rfile.read(size))
                self.rfile.read(2)
            return b"".join(chunks)
        length = int(self.headers.get("content-length", "0"))
        return self.rfile.read(length)

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        body = self._read_body()
        if self.headers.get("content-encoding", "").lower() == "gzip":
            body = gzip.decompress(body)
        try:
            spans = _span_summaries(body) if self.path.endswith("/traces") else []
        except (ValueError, IndexError):
            spans = []
        record = {
            "observed_at": datetime.now(UTC).isoformat(),
            "path": self.path,
            "content_type": self.headers.get("content-type"),
            "bytes": len(body),
            "span_count": len(spans),
            "spans": spans,
            "contains_plaintext_prompt_marker": any(
                marker in body
                for marker in (
                    b"captureContent",
                    b"DSH Native partial_failure acceptance",
                    b"verify a central-bank event",
                )
            ),
        }
        with self.server.write_lock:
            with self.server.output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.send_response(200)
        self.send_header("content-type", "application/x-protobuf")
        self.send_header("content-length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/health":
            self.send_response(200)
            self.send_header("content-length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        self.send_error(404)

    def log_message(self, *_args: object) -> None:
        return


class CaptureServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], output: Path) -> None:
        super().__init__(address, CaptureHandler)
        self.output = output
        self.write_lock = threading.Lock()


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture OTLP/HTTP request metadata")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    server = CaptureServer((args.host, args.port), args.output)
    server.timeout = 0.5

    def stop(_signum: int, _frame: object) -> None:
        # ``shutdown`` must run from a thread other than ``serve_forever``;
        # invoking it directly from the signal handler can deadlock the
        # receiver and leak a background process after the canary exits.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server.serve_forever()
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
