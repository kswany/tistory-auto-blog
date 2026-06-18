"""수집된 키워드 후보에서 블로그용 상위 N개를 선택합니다."""

from __future__ import annotations

import json
from pathlib import Path


def load_posted_keywords(data_path: Path | None = None) -> set[str]:
    path = data_path or Path(__file__).resolve().parents[1] / "data" / "posted_keywords.json"
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k.strip().lower() for k in data.get("keywords", [])}


def select_top_keywords(candidates: list[dict], limit: int = 5) -> list[str]:
    posted = load_posted_keywords()
    seen: set[str] = set()
    ranked: list[tuple[int, str]] = []

    for item in sorted(candidates, key=lambda x: x.get("score", 0), reverse=True):
        keyword = str(item.get("keyword", "")).strip()
        normalized = keyword.lower()
        if len(keyword) < 2:
            continue
        if normalized in posted or normalized in seen:
            continue
        seen.add(normalized)
        ranked.append((int(item.get("score", 0)), keyword))

    return [keyword for _, keyword in ranked[:limit]]
