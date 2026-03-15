from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

TRAILING_BRACKET_RE = re.compile(r"\s*\[(?P<token>[^\]]+)\]\s*$")
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")
SLUG_CANDIDATE_FIELDS = ("track", "title", "fulltitle")


@dataclass(frozen=True)
class SlugSelection:
    base_slug: str
    final_slug: str
    video_id_slug: str | None
    source_label: str


def safe_slug(text: str | None, fallback: str = "track") -> str:
    raw = (text or "").strip()
    if not raw:
        return fallback

    normalized = unicodedata.normalize("NFKD", raw)
    normalized = normalized.replace("&", " and ")
    normalized = normalized.replace("'", "")
    normalized = normalized.replace("’", "")
    normalized = normalized.encode("ascii", "ignore").decode("ascii")
    normalized = normalized.lower()
    normalized = NON_SLUG_RE.sub("_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._- ")
    return normalized or fallback


def strip_trailing_bracket_token(text: str | None) -> str:
    return TRAILING_BRACKET_RE.sub("", (text or "").strip()).strip()


def extract_trailing_bracket_token(text: str | None) -> str | None:
    match = TRAILING_BRACKET_RE.search((text or "").strip())
    if not match:
        return None
    token = safe_slug(match.group("token"), fallback="")
    return token or None


def load_ytdlp_metadata(source_dir: Path) -> dict[str, Any] | None:
    candidates = sorted(
        source_dir.glob("*.info.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for candidate in candidates:
        try:
            loaded = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


def build_slug_candidate_texts(metadata: Mapping[str, Any] | None, source_stem: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    if metadata:
        for field_name in SLUG_CANDIDATE_FIELDS:
            value = metadata.get(field_name)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    candidates.append((field_name, cleaned))
    cleaned_source = strip_trailing_bracket_token(source_stem)
    if cleaned_source:
        candidates.append(("source_filename", cleaned_source))
    return candidates


def metadata_video_id_slug(metadata: Mapping[str, Any] | None, source_stem: str) -> str | None:
    if metadata:
        raw_value = metadata.get("id")
        if raw_value is not None:
            slug = safe_slug(str(raw_value), fallback="")
            if slug:
                return slug
    return extract_trailing_bracket_token(source_stem)


def choose_unique_slug(base_slug: str, existing_slugs: Iterable[str], video_id_slug: str | None = None) -> str:
    taken = {slug for slug in existing_slugs if slug}
    if base_slug not in taken:
        return base_slug

    if video_id_slug:
        candidate = f"{base_slug}_{video_id_slug}"
        if candidate not in taken:
            return candidate

        suffix = 2
        while True:
            numbered = f"{candidate}_{suffix}"
            if numbered not in taken:
                return numbered
            suffix += 1

    suffix = 2
    while True:
        candidate = f"{base_slug}_{suffix}"
        if candidate not in taken:
            return candidate
        suffix += 1


def choose_project_slug(
    metadata: Mapping[str, Any] | None,
    source_stem: str,
    existing_slugs: Iterable[str],
    fallback: str = "track",
) -> SlugSelection:
    source_label = "fallback"
    base_slug = ""

    for source_label, candidate_text in build_slug_candidate_texts(metadata, source_stem):
        candidate_slug = safe_slug(candidate_text, fallback="")
        if candidate_slug:
            base_slug = candidate_slug
            break

    if not base_slug:
        base_slug = fallback

    video_id_slug = metadata_video_id_slug(metadata, source_stem)
    final_slug = choose_unique_slug(base_slug, existing_slugs, video_id_slug=video_id_slug)
    return SlugSelection(
        base_slug=base_slug,
        final_slug=final_slug,
        video_id_slug=video_id_slug,
        source_label=source_label,
    )
