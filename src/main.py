"""티스토리 자동 블로그 파이프라인 (1단계: 키워드 수집/선정까지)."""

from keyword_fetcher import fetch_google_trends, load_seed_keywords
from keyword_filter import select_top_keywords


def main() -> None:
    seeds, geo = load_seed_keywords()
    candidates = fetch_google_trends(seeds, geo)
    top5 = select_top_keywords(candidates, limit=5)

    print(f"시드 키워드: {seeds}")
    print(f"후보 {len(candidates)}개 중 상위 5개:")
    for i, keyword in enumerate(top5, start=1):
        print(f"{i}. {keyword}")

    if len(top5) < 5:
        raise SystemExit(f"키워드가 5개 미만입니다: {len(top5)}개")


if __name__ == "__main__":
    main()
