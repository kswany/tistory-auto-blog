"""SerpApi 트렌드 후보 중 줍줍토리 주제에 맞는 키워드만 남깁니다."""

from __future__ import annotations

import json
from pathlib import Path


def load_topic_filter_config(config_path: Path | None = None) -> dict:
    path = config_path or Path(__file__).resolve().parents[1] / "config" / "topic_filter.json"
    if not path.exists():
        return {"enabled": False}
    return json.loads(path.read_text(encoding="utf-8"))


def _category_ids(item: dict) -> set[int]:
    ids: set[int] = set()
    for category in item.get("categories") or []:
        if not isinstance(category, dict):
            continue
        raw_id = category.get("id")
        if raw_id is not None:
            ids.add(int(raw_id))
    return ids


def _text_blob(item: dict) -> str:
    parts: list[str] = [str(item.get("keyword", ""))]
    parts.extend(str(x) for x in item.get("trend_breakdown") or [])
    parts.extend(str(x) for x in item.get("news_titles") or [])
    return " ".join(parts).lower()


def _match_hint(blob: str, hints: list[str]) -> bool:
    blob_lower = blob.lower()
    for hint in hints:
        if hint.lower() in blob_lower:
            return True
    return False


def topic_match_reason(item: dict, config: dict | None = None) -> tuple[bool, str]:
    """후보가 블로그 주제에 맞는지 판별합니다."""
    config = config or load_topic_filter_config()
    if not config.get("enabled", True):
        return True, "filter_disabled"

    blocked_ids = {int(x) for x in config.get("blocked_category_ids", [])}
    allowed_ids = {int(x) for x in config.get("allowed_category_ids", [])}
    cat_ids = _category_ids(item)
    blob = _text_blob(item)

    if _match_hint(blob, config.get("blocked_hints", [])):
        return False, "blocked_hint"

    if cat_ids & blocked_ids:
        return False, "blocked_category"

    if _match_hint(blob, config.get("allowed_hints", [])):
        return True, "allowed_hint"

    if cat_ids & allowed_ids:
        return True, "allowed_category"

    return False, "no_match"


def filter_blog_topics(candidates: list[dict], config: dict | None = None) -> list[dict]:
    config = config or load_topic_filter_config()
    if not config.get("enabled", True):
        return candidates

    filtered: list[dict] = []
    for item in candidates:
        allowed, _reason = topic_match_reason(item, config)
        if allowed:
            filtered.append(item)
    return filtered


def format_trend_context(item: dict) -> str:
    """Gemini 프롬프트용 트렌드 배경 텍스트."""
    lines: list[str] = []

    categories = [str(c.get("name", "")).strip() for c in item.get("categories") or [] if c.get("name")]
    if categories:
        lines.append(f"트렌드 분류: {', '.join(categories)}")

    volume = item.get("search_volume")
    if isinstance(volume, int) and volume > 0:
        lines.append(f"검색량(대략): {volume:,}")

    news_titles = [str(t).strip() for t in item.get("news_titles") or [] if str(t).strip()]
    if news_titles:
        lines.append("관련 뉴스 (이 이슈 중심으로 작성):")
        for title in news_titles[:3]:
            lines.append(f"- {title}")

    breakdown = [str(t).strip() for t in item.get("trend_breakdown") or [] if str(t).strip()]
    if breakdown:
        lines.append(f"연관 검색어: {', '.join(breakdown[:5])}")

    return "\n".join(lines)
