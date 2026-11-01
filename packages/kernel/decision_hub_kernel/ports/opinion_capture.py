"""Closed, versioned retained-excerpt originals; no declaration grants source rights."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .market_path import aware_utc, canonical_hash
from .research_signal import PublicationTime

CAPTURE_SCHEMA = "opinion-capture.v1"
CAPTURE_PARSER = "public-opinion-excerpt.verbatim.v1"
IMPORT_SCHEMA_V2 = "public-opinion-import.v2"


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class OpinionCapturePermission(Closed):
    schema_version: Literal["opinion-capture-permission.v1"]
    source_id: str = Field(min_length=1, max_length=128)
    source_ref: str = Field(min_length=1, max_length=128)
    exact_urls: tuple[str, ...] = Field(min_length=1, max_length=16)
    rights_url: str
    rights_notice: str = Field(min_length=1, max_length=512)
    rights_notice_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    attribution_template: str = Field(min_length=1, max_length=512)
    permitted_use: Literal["personal_noncommercial_research"]
    remote_research_allowed: bool
    capture_modes: tuple[Literal["excerpt", "metadata_only"], ...] = Field(min_length=1)
    max_quote_words: int = Field(strict=True, ge=1, le=200)
    max_quote_chars: int = Field(strict=True, ge=1, le=3200)
    title_selector: str = Field(min_length=1, max_length=256)
    author_selector: str = Field(min_length=1, max_length=256)
    publication_selector: str = Field(min_length=1, max_length=256)
    paragraph_selector: str = Field(min_length=1, max_length=256)
    reviewed_at: AwareDatetime

    @model_validator(mode="after")
    def coherent(self):
        for url in (*self.exact_urls, self.rights_url):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
            ):
                raise ValueError("opinion_capture_permission_url_invalid")
        if len(set(self.exact_urls)) != len(self.exact_urls):
            raise ValueError("opinion_capture_permission_url_duplicate")
        if hashlib.sha256(self.rights_notice.encode()).hexdigest() != self.rights_notice_hash:
            raise ValueError("opinion_capture_rights_notice_hash_invalid")
        if "{author}" not in self.attribution_template or "{url}" not in self.attribution_template:
            raise ValueError("opinion_capture_attribution_incomplete")
        return self


class OpinionExcerpt(Closed):
    text: str = Field(min_length=1, max_length=3200)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    selector: str = Field(min_length=1, max_length=256)
    paragraph_index: int = Field(strict=True, ge=0)
    paragraph_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    char_start: int = Field(strict=True, ge=0)
    char_end: int = Field(strict=True, ge=1)
    boundary: Literal["complete_sentence", "complete_paragraph"]

    @model_validator(mode="after")
    def coherent(self):
        if (
            hashlib.sha256(self.text.encode()).hexdigest() != self.sha256
            or self.char_end - self.char_start != len(self.text)
            or self.text != self.text.strip()
            or "\n" in self.text
            or "\x00" in self.text
            or "\ufffd" in self.text
        ):
            raise ValueError("opinion_capture_excerpt_invalid")
        if self.boundary == "complete_sentence" and self.text[-1] not in ".!?。！？":
            raise ValueError("opinion_capture_sentence_incomplete")
        return self


class OpinionCaptureManifest(Closed):
    schema_version: Literal["opinion-capture.v1"]
    capture_mode: Literal["excerpt", "metadata_only"]
    coverage: Literal["partial"]
    source_id: str
    canonical_url: str
    title: str = Field(min_length=1, max_length=512)
    author: str | None = Field(max_length=256)
    language: str | None = Field(max_length=32)
    publication: PublicationTime
    # Calendar date is retained separately when the page states no timezone.
    publication_date: str | None = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    observed_at: AwareDatetime
    extraction_version: Literal["public-opinion-excerpt.verbatim.v1"]
    source_policy_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    permission: OpinionCapturePermission
    permission_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    attribution: str = Field(min_length=1, max_length=1024)
    excerpts: tuple[OpinionExcerpt, ...] = Field(max_length=8)
    original_full_content_hash: None
    syndication_cluster: str | None = Field(max_length=128)

    @model_validator(mode="after")
    def coherent(self):
        permission = self.permission
        if (
            self.source_id != permission.source_id
            or self.canonical_url not in permission.exact_urls
            or self.capture_mode not in permission.capture_modes
            or self.permission_hash != canonical_hash(permission.model_dump(mode="json"))
            or self.attribution
            != permission.attribution_template.format(
                author=self.author or "Author not identified", url=self.canonical_url
            )
        ):
            raise ValueError("opinion_capture_permission_binding_invalid")
        if permission.reviewed_at > self.observed_at:
            raise ValueError("opinion_capture_permission_after_capture")
        if bool(self.excerpts) != (self.capture_mode == "excerpt"):
            raise ValueError("opinion_capture_mode_content_mismatch")
        if (
            sum(len(x.text) for x in self.excerpts) > permission.max_quote_chars
            or sum(len(x.text.split()) for x in self.excerpts) > permission.max_quote_words
        ):
            raise ValueError("opinion_capture_quote_budget_exceeded")
        if len(
            {(x.selector, x.paragraph_index, x.char_start, x.char_end) for x in self.excerpts}
        ) != len(self.excerpts):
            raise ValueError("opinion_capture_excerpt_duplicate")
        if self.publication_date is not None:
            datetime.strptime(self.publication_date, "%Y-%m-%d")
        aware_utc(self.observed_at)
        return self


def capture_json(manifest: OpinionCaptureManifest) -> bytes:
    return json.dumps(
        manifest.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def parse_capture(data: bytes) -> OpinionCaptureManifest:
    if len(data) > 64 * 1024:
        raise ValueError("opinion_capture_size_exceeded")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("opinion_capture_duplicate_key")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("opinion_capture_nonfinite")

    value = json.loads(data, object_pairs_hook=unique, parse_constant=reject_constant)
    manifest = OpinionCaptureManifest.model_validate(value)
    if capture_json(manifest) != data:
        raise ValueError("opinion_capture_noncanonical")
    return manifest


def capture_summary(manifest: OpinionCaptureManifest) -> dict:
    return {
        "schema_version": CAPTURE_SCHEMA,
        "capture_mode": manifest.capture_mode,
        "coverage": manifest.coverage,
        "retained_capture_hash": hashlib.sha256(capture_json(manifest)).hexdigest(),
        "original_full_content_hash": None,
        "permission_hash": manifest.permission_hash,
        "source_policy_hash": manifest.source_policy_hash,
        "original_url": manifest.canonical_url,
        "attribution": manifest.attribution,
        "publication_date": manifest.publication_date,
        "publication_precision": manifest.publication.precision,
        "observed_at": aware_utc(manifest.observed_at).isoformat(),
        "syndication_cluster": manifest.syndication_cluster,
        "excerpt_hashes": [x.sha256 for x in manifest.excerpts],
        "limitation": "retained_quote_only_author_thesis_requires_independent_evidence",
    }
