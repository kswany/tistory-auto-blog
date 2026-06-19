"""티스토리 자동 블로그 파이프라인."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from blog_writer import format_readable_html, write_blog_post
from category_mapper import get_category_list, preview_category_name, resolve_category_id
from keyword_fetcher import fetch_keyword_candidates, has_external_keywords, load_seed_keywords
from keyword_filter import save_posted_keywords, select_top_candidate_items
from topic_filter import filter_blog_topics, format_trend_context, topic_match_reason
from tistory_publisher import publish_to_tistory, validate_tistory_session

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def _is_dry_run() -> bool:
    return _env_flag("DRY_RUN")


def _post_limit(default: int = 2) -> int:
    return max(1, int(os.getenv("POST_LIMIT", str(default))))


def _save_post_log(post: dict) -> None:
    today = datetime.now().strftime("%Y-%m-%d")
    day_dir = LOG_DIR / today
    day_dir.mkdir(parents=True, exist_ok=True)
    safe_name = post["keyword"].replace("/", "-")
    path = day_dir / f"{safe_name}.json"
    path.write_text(json.dumps(post, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_posts_from_logs() -> list[dict]:
    log_date = os.getenv("LOG_DATE")
    if log_date:
        day_dir = LOG_DIR / log_date
    else:
        day_dirs = sorted([p for p in LOG_DIR.iterdir() if p.is_dir()], reverse=True)
        if not day_dirs:
            raise SystemExit("logs/ 폴더에 저장된 글이 없습니다.")
        day_dir = day_dirs[0]

    posts = []
    for path in sorted(day_dir.glob("*.json")):
        post = json.loads(path.read_text(encoding="utf-8"))
        if post.get("body_html"):
            post["body_html"] = format_readable_html(post["body_html"])
        posts.append(post)

    if not posts:
        raise SystemExit(f"{day_dir} 에 JSON 글이 없습니다.")

    limit = _post_limit(len(posts))
    return posts[:limit]


def _publish_with_retry(post: dict) -> dict:
    delay = int(os.getenv("POST_DELAY_SECONDS", "30"))
    max_retries = max(1, int(os.getenv("PUBLISH_RETRIES", "3")))

    for attempt in range(1, max_retries + 1):
        try:
            return publish_to_tistory(post)
        except Exception as exc:
            if attempt >= max_retries:
                raise
            wait = delay * attempt
            print(f"  게시 실패 ({attempt}/{max_retries}): {exc}")
            print(f"  {wait}초 후 재시도...")
            time.sleep(wait)


def _publish_posts(posts: list[dict]) -> None:
    delay = int(os.getenv("POST_DELAY_SECONDS", "30"))
    published_keywords: list[str] = []
    failed_posts: list[str] = []

    for index, post in enumerate(posts, start=1):
        print(f"\n[{index}/{len(posts)}] '{post.get('keyword', '')}' 처리 중...")
        print(f"  제목: {post['title']}")

        if _is_dry_run():
            keyword = str(post.get("keyword") or "")
            try:
                category_id, category_name = resolve_category_id(
                    keyword,
                    post.get("tags"),
                    category_name=post.get("category_name"),
                )
                print(
                    f"  DRY_RUN=1 → 게시 생략 (카테고리: {category_name}, id={category_id})"
                )
            except Exception:
                category_name = preview_category_name(keyword, post.get("tags"))
                print(f"  DRY_RUN=1 → 게시 생략 (예상 카테고리: {category_name})")
            continue

        print("  티스토리 게시 중...")
        try:
            result = _publish_with_retry(post)
        except Exception as exc:
            label = post.get("keyword") or post["title"]
            failed_posts.append(label)
            print(f"  ❌ 최종 게시 실패: {exc}")
            continue

        print(f"  게시 완료 (카테고리: {result.get('category_name', '?')})")
        if post.get("keyword"):
            save_posted_keywords([post["keyword"]])
            published_keywords.append(post["keyword"])

        if index < len(posts):
            time.sleep(delay)

    if failed_posts:
        print(f"\n⚠️ {len(published_keywords)}편 성공, {len(failed_posts)}편 실패")
        print(f"   실패: {failed_posts}")
        raise SystemExit(1)

    if published_keywords:
        print(f"\n완료: {len(published_keywords)}편 게시")
    elif _is_dry_run():
        print("\n완료: DRY_RUN 모드 (게시 안 함)")
    else:
        print("\n완료")


def main() -> None:
    print("=== 티스토리 자동 블로그 파이프라인 시작 ===", flush=True)
    if _env_flag("PUBLISH_ONLY"):
        print("PUBLISH_ONLY=1 → logs/ 에 저장된 글만 게시 (AI 새로 작성 안 함)")
        posts = _load_posts_from_logs()
        print(f"불러온 글: {len(posts)}편")
        _publish_posts(posts)
        return

    limit = _post_limit()
    seeds, geo = load_seed_keywords()
    print("키워드 수집 중 (SerpApi)...", flush=True)
    candidates = fetch_keyword_candidates(seeds, geo)

    source_counts: dict[str, int] = {}
    for item in candidates:
        source = str(item.get("source", "unknown"))
        source_counts[source] = source_counts.get(source, 0) + 1

    print(f"시드 키워드: {seeds}")
    print(f"키워드 수집 출처: {source_counts}")

    if not has_external_keywords(candidates):
        print("❌ 외부 키워드 수집 실패 → 시드 대체 모드")
        print("   같은 키워드 반복 작성을 막기 위해 이번 실행은 중단합니다.")
        if not _env_flag("ALLOW_SEED_FALLBACK"):
            raise SystemExit(1)
        print("   ALLOW_SEED_FALLBACK=1 → 시드 키워드로 계속 진행")

    print("✅ 외부 API에서 후보 키워드 수집 성공")

    filtered = filter_blog_topics(candidates)
    allowed_keywords = {item["keyword"] for item in filtered}
    excluded = [item for item in candidates if item["keyword"] not in allowed_keywords]
    if excluded:
        print(f"주제 필터: {len(candidates)}개 → {len(filtered)}개 (제외 {len(excluded)}개)")
        for item in excluded[:8]:
            _ok, reason = topic_match_reason(item)
            print(f"  제외: {item.get('keyword')} ({reason})")
        if len(excluded) > 8:
            print(f"  ... 외 {len(excluded) - 8}개")

    top_items = select_top_candidate_items(filtered, limit=limit)
    print(f"필터 후 상위 {limit}개:")
    for i, item in enumerate(top_items, start=1):
        volume = item.get("search_volume")
        vol_label = f" (vol={volume:,})" if isinstance(volume, int) and volume else ""
        print(f"{i}. {item['keyword']}{vol_label}")

    if len(filtered) < limit:
        raise SystemExit(
            f"주제 필터 후 키워드가 {limit}개 미만입니다: {len(filtered)}개 → 글 작성·게시 중단"
        )

    if len(top_items) < limit:
        raise SystemExit(
            f"게시 이력 제외 후 키워드가 {limit}개 미만입니다: {len(top_items)}개 → 중단"
        )

    if not _is_dry_run():
        print("티스토리 세션 확인 중...", flush=True)
        validate_tistory_session()
        print("✅ 티스토리 쿠키 유효", flush=True)
        categories = get_category_list()
        print(f"✅ 티스토리 카테고리 {len(categories)}개 로드", flush=True)

    posts: list[dict] = []
    for index, item in enumerate(top_items, start=1):
        keyword = item["keyword"]
        trend_context = format_trend_context(item)
        print(f"\n[{index}/{limit}] '{keyword}' 글 작성 중...", flush=True)
        if trend_context:
            print(f"  트렌드 맥락:\n{trend_context.replace(chr(10), chr(10) + '  ')}", flush=True)
        print("  Gemini API 호출 중...", flush=True)
        post = write_blog_post(keyword, trend_context=trend_context or None)
        _save_post_log(post)
        posts.append(post)
        print(f"  제목: {post['title']}")
        if index < limit:
            time.sleep(int(os.getenv("GEMINI_DELAY_SECONDS", "45")))

    _publish_posts(posts)
    if _is_dry_run():
        print("(logs/ 폴더에 저장됨)")


if __name__ == "__main__":
    main()
