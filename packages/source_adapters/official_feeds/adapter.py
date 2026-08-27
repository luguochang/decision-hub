from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter

from packages.contracts_py.decision_hub_contracts.models import SourceType, TextEnvelope
from packages.kernel.decision_hub_kernel.ports.sources import SourceManifest, SourcePollResult


class FeedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    summary: str = ""
    published_at: datetime | None = None
    updated_at: datetime | None = None
    url: str | None = None
    revision_of: str | None = None


Fetcher = Callable[[str], Awaitable[tuple[int, str]]]


async def _http_fetch(url: str) -> tuple[int, str]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(url, follow_redirects=True)
        return response.status_code, response.text


class OfficialFeedSource:
    def __init__(
        self,
        manifest: SourceManifest,
        endpoint: str,
        *,
        fetcher: Fetcher | None = None,
        format_hint: str = "auto",
        include_document_body: bool = False,
        document_fetcher: Fetcher | None = None,
    ) -> None:
        self.manifest = manifest
        self.endpoint = endpoint
        self.fetcher = fetcher or _http_fetch
        self.format_hint = format_hint
        self.include_document_body = include_document_body
        self.document_fetcher = document_fetcher or self.fetcher

    async def poll(self, cursor: str | None = None) -> SourcePollResult:
        status_code, body = await self.fetcher(self.endpoint)
        if status_code == 429:
            raise RuntimeError("source_rate_limited")
        if status_code >= 400:
            raise RuntimeError(f"source_unavailable:{status_code}")
        fetched_at = datetime.now(UTC)
        items = sorted(parse_feed(body, self.format_hint), key=_cursor_for)
        selected = _after_cursor(items, cursor)
        envelopes_list: list[TextEnvelope] = []
        for item in selected[: self.manifest.max_batch]:
            text = (f"{item.title}\n{item.summary}").strip()
            if self.include_document_body and item.url:
                article_status, article_body = await self.document_fetcher(item.url)
                if article_status == 429:
                    raise RuntimeError("source_rate_limited")
                if article_status >= 400:
                    raise RuntimeError(f"source_document_unavailable:{article_status}")
                extracted = extract_document_text(article_body)
                if extracted:
                    text = f"{item.title}\n{extracted}".strip()
            envelopes_list.append(
                TextEnvelope(
                    source_id=self.manifest.source_id,
                    source_type=SourceType.official_feed,
                    observed_at=item.published_at or item.updated_at or fetched_at,
                    published_at=item.published_at,
                    received_at=fetched_at,
                    raw_text=text,
                    language="en",
                    event_hint=self.manifest.source_id,
                    source_url=_url(item.url),
                    revision_of=item.revision_of,
                    content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                )
            )
        envelopes = tuple(envelopes_list)
        # Do not advance past unprocessed entries when a source batch is capped.
        cursor_after = _cursor_for(selected[len(envelopes) - 1]) if envelopes else cursor
        return SourcePollResult(
            source_id=self.manifest.source_id,
            cursor_before=cursor,
            cursor_after=cursor_after,
            envelopes=envelopes,
            fetched_at=fetched_at,
        )


def parse_feed(body: str, format_hint: str = "auto") -> list[FeedItem]:
    stripped = body.lstrip()
    if format_hint == "json" or (format_hint == "auto" and stripped.startswith("{")):
        import json

        raw = json.loads(body)
        items = raw.get("items", raw.get("data", [])) if isinstance(raw, dict) else []
        return [_json_item(item) for item in items if isinstance(item, dict)]
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise ValueError("feed xml parse failed") from exc
    result: list[FeedItem] = []
    for node in root.iter():
        if _local_name(node.tag) not in {"item", "entry"}:
            continue
        values = {_local_name(child.tag): (child.text or "").strip() for child in node}
        item_id = values.get("guid") or values.get("id") or values.get("link")
        title = values.get("title")
        if not item_id or not title:
            continue
        link_node = next(
            (child for child in node if _local_name(child.tag) == "link"),
            None,
        )
        link = values.get("link") or (
            link_node.attrib.get("href") if link_node is not None else None
        )
        result.append(
            FeedItem(
                item_id=item_id,
                title=title,
                summary=values.get("description", values.get("summary", "")),
                published_at=_parse_date(values.get("pubDate") or values.get("published")),
                updated_at=_parse_date(values.get("updated")),
                url=link,
            )
        )
    return result


def extract_document_text(body: str) -> str:
    soup = BeautifulSoup(body, "html.parser")
    for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
        element.decompose()
    container = soup.find("main") or soup.find("article") or soup.body
    if container is None:
        return ""
    lines = [line.strip() for line in container.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def _json_item(item: dict[str, Any]) -> FeedItem:
    item_id = str(item.get("id") or item.get("guid") or item.get("url") or "")
    title = str(item.get("title") or "")
    if not item_id or not title:
        raise ValueError("feed item requires id and title")
    return FeedItem(
        item_id=item_id,
        title=title,
        summary=str(item.get("summary") or item.get("description") or ""),
        published_at=_parse_date(str(item.get("published_at") or item.get("published") or "")),
        updated_at=_parse_date(str(item.get("updated_at") or item.get("updated") or "")),
        url=item["url"] if item.get("url") else None,
        revision_of=str(item["revision_of"]) if item.get("revision_of") else None,
    )


def _url(value: str | None) -> HttpUrl | None:
    return TypeAdapter(HttpUrl).validate_python(value) if value else None


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        try:
            return parsedate_to_datetime(value).astimezone(UTC)
        except (TypeError, ValueError):
            return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _cursor_for(item: FeedItem) -> str:
    timestamp = item.updated_at or item.published_at
    return f"{timestamp.isoformat() if timestamp else ''}|{item.item_id}"


def _after_cursor(items: list[FeedItem], cursor: str | None) -> list[FeedItem]:
    if not cursor:
        return items
    return [_item for _item in items if _cursor_for(_item) > cursor]
