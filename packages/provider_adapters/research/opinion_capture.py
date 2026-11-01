"""Permission-scoped metadata/quote capture over the existing source boundary."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import yaml
from bs4 import BeautifulSoup
from packages.kernel.decision_hub_kernel.ports.opinion_capture import (
    CAPTURE_PARSER,
    OpinionCaptureManifest,
    OpinionCapturePermission,
    OpinionExcerpt,
    parse_capture,
)

from packages.kernel.decision_hub_kernel.ports.market_path import canonical_hash
from packages.kernel.decision_hub_kernel.ports.research_signal import PublicationTime

from .source_registry import ResearchSourceRegistry


class OpinionCaptureRegistry:
    def __init__(
        self, sources: ResearchSourceRegistry, permissions: tuple[OpinionCapturePermission, ...]
    ):
        self.sources = sources
        self.permissions = {item.source_id: item for item in permissions}
        if len(self.permissions) != len(permissions):
            raise ValueError("opinion_capture_source_duplicate")

    @classmethod
    def from_pack(cls, root: Path):
        payload = yaml.safe_load(
            (root / "evidence/opinion_capture_policies.yaml").read_text(encoding="utf-8")
        )
        if (
            set(payload) != {"schema_version", "permissions"}
            or payload["schema_version"] != "opinion-capture-registry.v1"
        ):
            raise ValueError("opinion_capture_registry_invalid")
        return cls(
            ResearchSourceRegistry.from_pack(root),
            tuple(OpinionCapturePermission.model_validate(item) for item in payload["permissions"]),
        )

    def require(self, source_id: str, url: str):
        permission = self.permissions.get(source_id)
        if permission is None or url not in permission.exact_urls:
            raise ValueError("opinion_capture_source_not_permitted")
        locator = self.sources.classify_locator(url)
        policy = next(
            (entry for entry in self.sources.entries if entry.source_ref == permission.source_ref),
            None,
        )
        if (
            policy is None
            or locator.source_ref != permission.source_ref
            or locator.status != "approved_locator"
            or policy.retention_policy not in {"hash_excerpt", "full_text"}
            or "market.narrative" not in policy.requirement_ids
            or not permission.remote_research_allowed
        ):
            raise ValueError("opinion_capture_use_not_permitted")
        return permission, policy

    def validate(self, data: bytes, *, source_id: str, url: str) -> OpinionCaptureManifest:
        permission, policy = self.require(source_id, url)
        capture = parse_capture(data)
        if (
            capture.source_id != source_id
            or capture.canonical_url != url
            or capture.permission.model_dump(mode="json") != permission.model_dump(mode="json")
            or capture.source_policy_hash != canonical_hash(policy.model_dump(mode="json"))
        ):
            raise ValueError("opinion_capture_source_policy_changed")
        return capture


def capture_html_excerpt(
    html: bytes,
    *,
    source_id: str,
    url: str,
    observed_at: datetime,
    registry: OpinionCaptureRegistry,
    exact_quotes: tuple[str, ...],
) -> OpinionCaptureManifest:
    """Extract exact text from an authorized in-memory response; retain no full page.

    The selector is trusted source configuration. Each quote must occupy a whole
    sentence or paragraph. This preserves local sentence conditions; partial
    context remains explicitly unverified and may still require the full source.
    """
    permission, policy = registry.require(source_id, url)
    if len(html) > 1024 * 1024:
        raise ValueError("opinion_capture_response_size_exceeded")
    soup = BeautifulSoup(html.decode("utf-8", errors="strict"), "html.parser")
    titles = soup.select(permission.title_selector)
    times = soup.select(permission.publication_selector)
    if len(titles) != 1 or len(times) > 1:
        raise ValueError("opinion_capture_metadata_ambiguous")
    title = titles[0].get_text(" ", strip=True)
    authors = tuple(
        dict.fromkeys(
            tag.get_text(" ", strip=True) for tag in soup.select(permission.author_selector)
        )
    )
    author = "; ".join(authors) if authors else None
    display = str(times[0].get("datetime") or times[0].get_text(" ", strip=True)) if times else None
    publication_date = None
    if display:
        for pattern in ("%d %b %Y", "%Y-%m-%d", "%B %d, %Y"):
            try:
                publication_date = datetime.strptime(display, pattern).date().isoformat()
                break
            except ValueError:
                continue
    # No source offset means no fabricated UTC publication timestamp.
    publication = PublicationTime(
        precision="unknown",
        published_at=None,
        window_start=None,
        window_end=None,
        display_text=display,
        source_timezone=None,
    )
    paragraphs = [
        tag.get_text(" ", strip=True) for tag in soup.select(permission.paragraph_selector)
    ]
    excerpts = []
    for quote in exact_quotes:
        matches = [(i, text) for i, text in enumerate(paragraphs) if quote in text]
        if len(matches) != 1 or matches[0][1].count(quote) != 1:
            raise ValueError("opinion_capture_quote_not_unique_original")
        index, text = matches[0]
        start, end = text.index(quote), text.index(quote) + len(quote)
        whole = quote == text
        if not whole and (
            not quote
            or quote[-1] not in ".!?。！？"
            or (start and text[:start].rstrip()[-1] not in ".!?。！？")
            or (end < len(text) and not text[end].isspace())
        ):
            raise ValueError("opinion_capture_quote_boundary_incomplete")
        excerpts.append(
            OpinionExcerpt(
                text=quote,
                sha256=hashlib.sha256(quote.encode()).hexdigest(),
                selector=permission.paragraph_selector,
                paragraph_index=index,
                paragraph_sha256=hashlib.sha256(text.encode()).hexdigest(),
                char_start=start,
                char_end=end,
                boundary="complete_paragraph" if whole else "complete_sentence",
            )
        )
    return OpinionCaptureManifest(
        schema_version="opinion-capture.v1",
        capture_mode="excerpt" if excerpts else "metadata_only",
        coverage="partial",
        source_id=source_id,
        canonical_url=url,
        title=title,
        author=author,
        language=str(soup.html.get("lang")) if soup.html and soup.html.get("lang") else None,
        publication=publication,
        publication_date=publication_date,
        observed_at=observed_at,
        extraction_version=CAPTURE_PARSER,
        source_policy_hash=canonical_hash(policy.model_dump(mode="json")),
        permission=permission,
        permission_hash=canonical_hash(permission.model_dump(mode="json")),
        attribution=permission.attribution_template.format(
            author=author or "Author not identified", url=url
        ),
        excerpts=tuple(excerpts),
        original_full_content_hash=None,
        syndication_cluster=None,
    )
