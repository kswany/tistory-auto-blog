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
    """시드 키워드 기반으로 후보 키워드를 수집합니다."""
    if not seed_keywords:
        return []

    candidates: list[dict] = []
    candidates.extend(_fetch_autocomplete_sources(seed_keywords))
    candidates.extend(_fetch_pytrends(seed_keywords, geo))

    if not candidates:
        return _fallback_from_seeds(seed_keywords, "모든 수집 API 실패")

    return candidates


if __name__ == "__main__":
    seeds, geo = load_seed_keywords()
    results = fetch_google_trends(seeds, geo)
    source_counts: dict[str, int] = {}
    for item in results:
        source = item["source"]
        source_counts[source] = source_counts.get(source, 0) + 1
    print("수집 출처:", source_counts)
    for item in results[:20]:
        print(f"{item['score']:>3} | {item['keyword']} ({item['source']})")
