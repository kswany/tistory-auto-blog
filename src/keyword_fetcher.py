"""Google Trends에서 시드 키워드 기반 후보 키워드를 수집합니다."""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

from pytrends.request import TrendReq


def load_seed_keywords(config_path: Path | None = None) -> tuple[list[str], str]:
    path = config_path or Path(__file__).resolve().parents[1] / "config" / "seed_keywords.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("keywords", []), data.get("geo", "KR")


def _ssl_verify_enabled() -> bool:
    return os.getenv("SKIP_SSL_VERIFY", "").lower() not in {"1", "true", "yes"}


def _create_trend_client() -> TrendReq:
    requests_args: dict = {}
    if not _ssl_verify_enabled():
        requests_args["verify"] = False
        try:
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

    return TrendReq(hl="ko-KR", tz=540, requests_args=requests_args)


def _fallback_from_seeds(seed_keywords: list[str], reason: str) -> list[dict]:
    warnings.warn(f"Google Trends 사용 불가 → 시드 키워드로 대체 ({reason})")
    return [
        {"keyword": seed, "source": "seed_fallback", "score": 50 - index}
        for index, seed in enumerate(seed_keywords)
    ]


def fetch_google_trends(seed_keywords: list[str], geo: str = "KR") -> list[dict]:
    """시드 키워드별 연관 검색어와 급상승 키워드를 수집합니다."""
    if not seed_keywords:
        return []

    try:
        pytrends = _create_trend_client()
    except Exception as exc:
        return _fallback_from_seeds(seed_keywords, str(exc))

    candidates: list[dict] = []

    try:
        trending_df = pytrends.trending_searches(pn="south_korea")
        for keyword in trending_df[0].head(20).tolist():
            candidates.append({"keyword": keyword, "source": "google_trending", "score": 100})
    except Exception:
        pass

    for seed in seed_keywords[:5]:
        try:
            pytrends.build_payload([seed], timeframe="now 7-d", geo=geo)
            related = pytrends.related_queries()
            top = related.get(seed, {}).get("top")
            if top is not None and not top.empty:
                for _, row in top.head(10).iterrows():
                    candidates.append(
                        {
                            "keyword": str(row["query"]),
                            "source": "google_related",
                            "score": int(row["value"]),
                        }
                    )
        except Exception:
            continue

    if not candidates:
        return _fallback_from_seeds(seed_keywords, "수집 결과 없음 (회사망 SSL 또는 Trends 차단 가능)")

    return candidates


if __name__ == "__main__":
    seeds, geo = load_seed_keywords()
    results = fetch_google_trends(seeds, geo)
    for item in results[:20]:
        print(f"{item['score']:>3} | {item['keyword']} ({item['source']})")
