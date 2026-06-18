"""수집된 키워드 후보에서 블로그용 상위 N개를 선택합니다."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def load_posted_keywords(data_path: Path | None = None) -> set[str]:
    path = data_path or Path(__file__).resolve().parents[1] / "data" / "posted_keywords.json"
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k.strip().lower() for k in data.get("keywords", [])}


def select_top_candidate_items(candidates: list[dict], limit: int = 5) -> list[dict]:
    """게시 이력·중복 제외 후 상위 N개 후보(dict)를 반환합니다."""
    posted = load_posted_keywords()
    seen: set[str] = set()
    selected: list[dict] = []

    for item in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
        keyword = str(item.get("keyword", "")).strip()
        normalized = keyword.lower()
        if len(keyword) < 2:
            continue
        if normalized in posted or normalized in seen:
            continue
        seen.add(normalized)
        selected.append(item)
        if len(selected) >= limit:
            break

    return selected


def select_top_keywords(candidates: list[dict], limit: int = 5) -> list[str]:
    return [item["keyword"] for item in select_top_candidate_items(candidates, limit=limit)]


def save_posted_keywords(keywords: list[str], data_path: Path | None = None) -> None:
    path = data_path or Path(__file__).resolve().parents[1] / "data" / "posted_keywords.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"keywords": [], "last_updated": None}

    existing = {k.strip().lower(): k for k in data.get("keywords", [])}
    for keyword in keywords:
        existing[keyword.strip().lower()] = keyword.strip()

    data["keywords"] = list(existing.values())
    data["last_updated"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
