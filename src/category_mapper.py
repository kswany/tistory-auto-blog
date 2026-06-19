"""티스토리 카테고리 ID 조회 및 키워드 기반 분류."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import requests

from tistory_publisher import _blog_host, _request_headers, _verify_ssl

ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = ROOT / "config" / "tistory_categories.json"

_category_list_cache: list[dict[str, int | str]] | None = None


def _load_category_config() -> dict:
    return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))


def _normalize_label(text: str) -> str:
    cleaned = text.strip().lower()
    cleaned = cleaned.replace("·", "").replace("•", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned


def fetch_tistory_categories() -> list[dict[str, int | str]]:
    """티스토리 /manage/category.json 에서 카테고리 목록을 가져옵니다."""
    from tistory_publisher import _parse_tistory_json

    blog_host = _blog_host()
    url = f"https://{blog_host}/manage/category.json"
    response = requests.get(
        url,
        headers=_request_headers(),
        timeout=30,
        verify=_verify_ssl(),
        allow_redirects=True,
    )
    response.raise_for_status()
    data = _parse_tistory_json(response)

    categories: list[dict[str, int | str]] = []
    for item in data.get("categories") or []:
        if not isinstance(item, dict):
            continue
        category_id = item.get("id")
        name = str(item.get("name") or item.get("label") or "").strip()
        if not name or category_id in (None, 0):
            continue
        categories.append({"id": int(category_id), "name": name})

    if not categories:
        raise RuntimeError(
            "티스토리 category.json 에 카테고리가 없습니다. "
            "TISTORY_COOKIE를 갱신하거나 scripts/list_tistory_categories.py 를 실행하세요."
        )

    return categories


def get_category_list(*, refresh: bool = False) -> list[dict[str, int | str]]:
    global _category_list_cache
    if _category_list_cache is None or refresh:
        _category_list_cache = fetch_tistory_categories()
    return _category_list_cache


def _find_id_by_name(name: str) -> tuple[int, str] | None:
    target = _normalize_label(name)
    for item in get_category_list():
        raw_name = str(item["name"])
        if _normalize_label(raw_name) == target:
            return int(item["id"]), raw_name
    return None


def _resolve_category_name(keyword: str, tags: list[str] | None = None) -> str:
    config = _load_category_config()
    text = f"{keyword} {' '.join(tags or [])}".lower()

    best_name = str(config.get("default_category_name") or "오늘의 한입 뉴스")
    best_score = 0

    for entry in config.get("categories") or []:
        if entry.get("disabled"):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue

        score = 0
        for hint in entry.get("hints") or []:
            hint_lower = str(hint).lower()
            if hint_lower and hint_lower in text:
                score += len(hint_lower)

        if score > best_score:
            best_score = score
            best_name = name

    return best_name


def preview_category_name(keyword: str, tags: list[str] | None = None) -> str:
    """쿠키 없이 키워드만으로 예상 카테고리명을 반환합니다."""
    return _resolve_category_name(keyword, tags)


def resolve_category_id(
    keyword: str,
    tags: list[str] | None = None,
    *,
    category_name: str | None = None,
) -> tuple[int, str]:
    """키워드·태그로 카테고리 ID를 결정합니다. (id, name) 반환."""
    env_default = os.getenv("TISTORY_CATEGORY_ID", "").strip()
    force_env = os.getenv("TISTORY_CATEGORY_FORCE", "").lower() in {"1", "true", "yes"}
    if force_env and env_default and env_default != "0":
        return int(env_default), f"env:{env_default}"

    name = (category_name or "").strip() or _resolve_category_name(keyword, tags)
    found = _find_id_by_name(name)
    if found:
        return found

    default_name = str(_load_category_config().get("default_category_name") or "")
    if default_name:
        found = _find_id_by_name(default_name)
        if found:
            return found

    categories = get_category_list()
    if categories:
        return int(categories[0]["id"]), str(categories[0]["name"])

    return 0, name
