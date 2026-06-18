"""키워드 후보를 Google Trends·자동완성 API에서 수집합니다."""

from __future__ import annotations

import json
import os
import time
import warnings
from pathlib import Path

import requests

try:
    from pytrends.request import TrendReq

    HAS_PYTRENDS = True
except ImportError:
    HAS_PYTRENDS = False


def load_seed_keywords(config_path: Path | None = None) -> tuple[list[str], str]:
    path = config_path or Path(__file__).resolve().parents[1] / "config" / "seed_keywords.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("keywords", []), data.get("geo", "KR")


def _ssl_verify_enabled() -> bool:
    return os.getenv("SKIP_SSL_VERIFY", "").lower() not in {"1", "true", "yes"}


def _request_get(url: str, params: dict | None = None) -> requests.Response:
    return requests.get(
        url,
        params=params,
        timeout=20,
        verify=_ssl_verify_enabled(),
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
            )
        },
    )


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def _extract_news_titles(item: dict) -> list[str]:
    titles: list[str] = []
    news = item.get("news_item") or item.get("ht:news_item")
    if isinstance(news, list):
        for entry in news:
            if not isinstance(entry, dict):
                continue
            title = entry.get("news_item_title") or entry.get("ht:news_item_title")
            if title:
                titles.append(str(title).strip())
    elif isinstance(news, dict):
        title = news.get("news_item_title") or news.get("ht:news_item_title")
        if title:
            titles.append(str(title).strip())
    return titles


def _extract_categories(item: dict) -> list[dict]:
    categories: list[dict] = []
    for category in item.get("categories") or []:
        if not isinstance(category, dict):
            continue
        categories.append(
            {
                "id": category.get("id"),
                "name": category.get("name"),
            }
        )
    return categories


def _extract_trend_breakdown(item: dict) -> list[str]:
    breakdown = item.get("trend_breakdown") or []
    return [str(term).strip() for term in breakdown if str(term).strip()]


def fetch_serpapi_trending(geo: str | None = None) -> list[dict]:
    """SerpApi Google Trends Trending Now — 한국 급상승 검색어 (시드 불필요)."""
    api_key = os.getenv("SERPAPI_KEY")
    if not api_key:
        return []

    geo_code = geo or os.getenv("SERPAPI_GEO", "KR")
    only_active = os.getenv("SERPAPI_ONLY_ACTIVE", "1").lower() in {"1", "true", "yes"}
    params: dict[str, str] = {
        "engine": "google_trends_trending_now",
        "geo": geo_code,
        "api_key": api_key,
        "hl": "ko",
        "hours": os.getenv("SERPAPI_HOURS", "24"),
    }
    if only_active:
        params["only_active"] = "true"

    response = _request_get("https://serpapi.com/search", params)
    response.raise_for_status()
    data = response.json()

    if data.get("error"):
        raise RuntimeError(f"SerpApi 오류: {data['error']}")

    candidates: list[dict] = []
    for item in data.get("trending_searches", []):
        if only_active and not item.get("active", True):
            continue
        keyword = str(item.get("query", "")).strip()
        if len(keyword) < 2:
            continue
        volume = int(item.get("search_volume") or 0)
        candidates.append(
            {
                "keyword": keyword,
                "source": "serpapi_trending",
                "score": volume,
                "search_volume": volume,
                "increase_percentage": item.get("increase_percentage"),
                "categories": _extract_categories(item),
                "trend_breakdown": _extract_trend_breakdown(item),
                "news_titles": _extract_news_titles(item),
            }
        )

    candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
    return candidates


def _fallback_from_seeds(seed_keywords: list[str], reason: str) -> list[dict]:
    warnings.warn(f"외부 키워드 수집 실패 → 시드 키워드로 대체 ({reason})")
    return [
        {"keyword": seed, "source": "seed_fallback", "score": 50 - index}
        for index, seed in enumerate(seed_keywords)
    ]


def _fetch_google_autocomplete(keyword: str) -> list[str]:
    response = _request_get(
        "https://suggestqueries.google.com/complete/search",
        {"client": "firefox", "q": keyword, "hl": "ko"},
    )
    response.raise_for_status()
    data = response.json()
    if len(data) < 2:
        return []
    return [str(item) for item in data[1] if str(item).strip()]


def _fetch_naver_autocomplete(keyword: str) -> list[str]:
    response = _request_get(
        "https://ac.search.naver.com/nx/ac",
        {
            "q": keyword,
            "con": "1",
            "frm": "nv",
            "ans": "2",
            "r_format": "json",
            "r_enc": "UTF-8",
            "r_unicode": "0",
            "t_koreng": "1",
            "run": "2",
            "rev": "4",
            "q_enc": "UTF-8",
        },
    )
    response.raise_for_status()
    data = response.json()
    items = data.get("items", [[]])
    if not items or not items[0]:
        return []
    return [str(row[0]) for row in items[0] if row and str(row[0]).strip()]


def _fetch_pytrends(seed_keywords: list[str], geo: str) -> list[dict]:
    if not HAS_PYTRENDS:
        return []

    requests_args: dict = {}
    if not _ssl_verify_enabled():
        requests_args["verify"] = False

    pytrends = TrendReq(hl="ko-KR", tz=540, retries=2, backoff_factor=0.5, requests_args=requests_args)
    candidates: list[dict] = []

    for method_name, pn in [("realtime_trending_searches", "KR"), ("trending_searches", "south_korea")]:
        try:
            method = getattr(pytrends, method_name)
            trending_df = method(pn=pn)
            for index, keyword in enumerate(trending_df[0].head(15).tolist()):
                candidates.append(
                    {"keyword": str(keyword), "source": f"google_{method_name}", "score": 100 - index}
                )
            if candidates:
                break
        except Exception:
            continue

    for seed in seed_keywords[:5]:
        try:
            suggestions = pytrends.suggestions(keyword=seed)
            for index, item in enumerate(suggestions[:8]):
                title = str(item.get("title", "")).strip()
                if title:
                    candidates.append(
                        {"keyword": title, "source": "google_suggest", "score": 80 - index}
                    )
        except Exception:
            pass

        try:
            pytrends.build_payload([seed], timeframe="now 7-d", geo=geo)
            related = pytrends.related_queries()
            top = related.get(seed, {}).get("top")
            if top is not None and not top.empty:
                for _, row in top.head(8).iterrows():
                    candidates.append(
                        {
                            "keyword": str(row["query"]),
                            "source": "google_related",
                            "score": int(row["value"]),
                        }
                    )
        except Exception:
            continue

        time.sleep(1)

    return candidates


def _fetch_autocomplete_sources(seed_keywords: list[str]) -> list[dict]:
    candidates: list[dict] = []

    for seed in seed_keywords[:7]:
        try:
            for index, keyword in enumerate(_fetch_naver_autocomplete(seed)[:8]):
                candidates.append(
                    {"keyword": keyword, "source": "naver_autocomplete", "score": 90 - index}
                )
        except Exception:
            pass
        time.sleep(0.3)

        try:
            for index, keyword in enumerate(_fetch_google_autocomplete(seed)[:8]):
                candidates.append(
                    {"keyword": keyword, "source": "google_autocomplete", "score": 85 - index}
                )
        except Exception:
            pass
        time.sleep(0.3)

    return candidates


def has_external_keywords(candidates: list[dict]) -> bool:
    """시드 fallback 이외의 출처에서 키워드를 가져왔는지 확인합니다."""
    return any(item.get("source") != "seed_fallback" for item in candidates)


def fetch_google_trends(seed_keywords: list[str], geo: str = "KR") -> list[dict]:
    """시드 키워드 기반으로 후보 키워드를 수집합니다 (자동완성·pytrends)."""
    if not seed_keywords:
        return []

    candidates: list[dict] = []
    candidates.extend(_fetch_autocomplete_sources(seed_keywords))
    candidates.extend(_fetch_pytrends(seed_keywords, geo))

    if not candidates:
        return _fallback_from_seeds(seed_keywords, "모든 수집 API 실패")

    return candidates


def fetch_keyword_candidates(seed_keywords: list[str], geo: str = "KR") -> list[dict]:
    """SerpApi 급상승 우선. SERPAPI_FALLBACK=1 일 때만 자동완성·pytrends."""
    api_key = os.getenv("SERPAPI_KEY")
    if api_key:
        try:
            serpapi = fetch_serpapi_trending(geo)
            if serpapi:
                return serpapi
            if not _env_flag("SERPAPI_FALLBACK"):
                return []
        except Exception as exc:
            warnings.warn(f"SerpApi 수집 실패 ({exc})")
            if not _env_flag("SERPAPI_FALLBACK"):
                return []

    if _env_flag("SERPAPI_FALLBACK") or not api_key:
        return fetch_google_trends(seed_keywords, geo)

    return []


def _print_candidate_report(results: list[dict], limit: int = 20, verbose: bool = False) -> None:
    source_counts: dict[str, int] = {}
    for item in results:
        source = str(item.get("source", "unknown"))
        source_counts[source] = source_counts.get(source, 0) + 1
    print(f"수집 출처: {source_counts}")
    print(f"후보 {len(results)}개 (상위 {limit}개):")
    for index, item in enumerate(results[:limit], start=1):
        volume = item.get("search_volume")
        extra = f" vol={volume:,}" if isinstance(volume, int) and volume else ""
        print(f"{index:>2}. [{item.get('score', 0):>7}] {item['keyword']} ({item.get('source')}){extra}")
        if verbose:
            categories = [c.get("name") for c in item.get("categories") or [] if c.get("name")]
            if categories:
                print(f"     분류: {', '.join(categories)}")
            news_titles = item.get("news_titles") or []
            if news_titles:
                print(f"     뉴스: {news_titles[0][:60]}...")
            breakdown = item.get("trend_breakdown") or []
            if breakdown:
                print(f"     연관: {', '.join(breakdown[:4])}")


if __name__ == "__main__":
    import sys

    from dotenv import load_dotenv

    load_dotenv()

    if "--test-serpapi" in sys.argv:
        from topic_filter import filter_blog_topics, topic_match_reason

        print("=== SerpApi Trending Now (geo=KR) 테스트 ===")
        if not os.getenv("SERPAPI_KEY"):
            raise SystemExit("SERPAPI_KEY 가 .env 또는 환경변수에 없습니다.")
        results = fetch_serpapi_trending()
        if not results:
            raise SystemExit("SerpApi 결과가 비어 있습니다.")
        _print_candidate_report(results, limit=15, verbose=True)

        if "--filter" in sys.argv:
            print("\n=== 주제 필터 적용 (줍줍토리) ===")
            allowed_keywords = {item["keyword"] for item in filter_blog_topics(results)}
            filtered = filter_blog_topics(results)
            print(f"필터 전 {len(results)}개 → 후 {len(filtered)}개")
            for item in results:
                if item["keyword"] not in allowed_keywords:
                    _ok, reason = topic_match_reason(item)
                    print(f"  제외: {item['keyword']} ({reason})")
            _print_candidate_report(filtered, limit=10, verbose=True)
        sys.exit(0)

    seeds, geo = load_seed_keywords()
    results = fetch_keyword_candidates(seeds, geo)
    _print_candidate_report(results)
