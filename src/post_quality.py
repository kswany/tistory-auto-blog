"""게시 전 YMYL 면책·출처 검증·본문 푸터 처리."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "tistory-auto-blog/1.0 (source-url-check)"
FOOTER_MARKER = "참고 및 면책"
MIN_SOURCES = 1
MAX_SOURCES = 3


def _load_official_sources_config() -> dict:
    path = ROOT / "config" / "official_sources.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes"}


def _normalize_host(url: str) -> str:
    return urlparse(url).netloc.lower().split(":")[0]


def _domain_allowed(url: str, config: dict) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme != "https" or not parsed.netloc:
        return False

    host = _normalize_host(url)
    allowed_hosts = {h.lower() for h in config.get("allowed_hosts") or []}
    if host in allowed_hosts:
        return True

    for suffix in config.get("allowed_domain_suffixes") or []:
        if host.endswith(suffix.lstrip(".")) or host.endswith(suffix):
            return True

    return False


def _url_responds(url: str, timeout: float) -> bool:
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.head(url, timeout=timeout, allow_redirects=True, headers=headers)
        if response.status_code < 400:
            return True
        if response.status_code in {405, 501}:
            response = requests.get(
                url,
                timeout=timeout,
                allow_redirects=True,
                headers=headers,
                stream=True,
            )
            response.close()
            return response.status_code < 400
        return False
    except requests.RequestException:
        return False


def validate_source_url(url: str, config: dict | None = None) -> bool:
    config = config or _load_official_sources_config()
    if not _domain_allowed(url, config):
        return False
    if not _env_flag("VALIDATE_SOURCE_URLS", default=False):
        return True
    timeout = max(3.0, float(os.getenv("SOURCE_URL_TIMEOUT_SECONDS", "8")))
    return _url_responds(url, timeout)


def _pick_fallback_sources(keyword: str, config: dict) -> list[dict[str, str]]:
    keyword_lower = keyword.lower()
    for entry in config.get("keyword_fallbacks") or []:
        hints = [str(h).lower() for h in entry.get("hints") or []]
        if any(hint in keyword_lower for hint in hints):
            return list(entry.get("sources") or [])[:MAX_SOURCES]

    return list(config.get("default_fallbacks") or [])[:MAX_SOURCES]


def normalize_sources(
    raw_sources: object,
    keyword: str,
    *,
    config: dict | None = None,
) -> list[dict[str, str]]:
    config = config or _load_official_sources_config()
    candidates: list[dict[str, str]] = []

    if isinstance(raw_sources, list):
        for item in raw_sources:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("url") or "").strip()
            if name and url:
                candidates.append({"name": name, "url": url})

    validated: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for source in candidates:
        url = source["url"]
        if url in seen_urls:
            continue
        if validate_source_url(url, config):
            validated.append(source)
            seen_urls.add(url)
        if len(validated) >= MAX_SOURCES:
            break

    if len(validated) < MIN_SOURCES:
        for fallback in _pick_fallback_sources(keyword, config):
            url = fallback["url"]
            if url in seen_urls:
                continue
            if validate_source_url(url, config):
                validated.append({"name": fallback["name"], "url": url})
                seen_urls.add(url)
            if len(validated) >= MIN_SOURCES:
                break

    return validated[:MAX_SOURCES]


def build_disclaimer_footer(today: str, sources: list[dict[str, str]]) -> str:
    items = "".join(
        (
            f'<li><a href="{source["url"]}" rel="noopener noreferrer" '
            f'target="_blank">{source["name"]}</a></li>'
        )
        for source in sources
    )
    return (
        f"<h2>{FOOTER_MARKER}</h2>"
        f"<p>이 글은 <strong>{today}</strong> 기준 공개 정보를 바탕으로 정리한 참고용 콘텐츠입니다.</p>"
        "<p>신청 조건·금액·기간·자격 요건은 정책 변경될 수 있습니다.</p>"
        "<p>반드시 아래 공식 출처에서 최종 확인하세요.</p>"
        f"<ul>{items}</ul>"
    )


def append_quality_footer(
    body_html: str,
    *,
    keyword: str,
    today: str,
    raw_sources: object,
) -> str:
    if FOOTER_MARKER in body_html:
        return body_html

    sources = normalize_sources(raw_sources, keyword)
    if not sources:
        return body_html

    footer = build_disclaimer_footer(today, sources)
    inner = re.sub(r"</div>\s*$", "", body_html.strip())
    if inner.startswith('<div style="'):
        return f"{inner}{footer}</div>"
    return f"{body_html.strip()}{footer}"


def is_ymyl_topic(keyword: str, raw_flag: object = None) -> bool:
    if isinstance(raw_flag, bool):
        return raw_flag
    ymyl_hints = (
        "세금",
        "연말",
        "대출",
        "금리",
        "지원",
        "혜택",
        "연금",
        "주식",
        "ETF",
        "펀드",
        "코인",
        "보험",
        "청년",
        "고용",
        "최저임금",
        "부동산",
        "적금",
    )
    keyword_lower = keyword.lower()
    return any(hint in keyword_lower for hint in ymyl_hints)
