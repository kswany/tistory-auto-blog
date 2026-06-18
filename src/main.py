"""티스토리 자동 블로그 파이프라인."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from blog_writer import format_readable_html, write_blog_post
from keyword_fetcher import fetch_google_trends, load_seed_keywords
from keyword_filter import save_posted_keywords, select_top_keywords
from tistory_publisher import publish_to_tistory

load_dotenv()

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes"}


def _is_dry_run() -> bool:
    return _env_flag("DRY_RUN")


def _post_limit(default: int = 5) -> int:
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


def _publish_posts(posts: list[dict]) -> None:
    delay = int(os.getenv("POST_DELAY_SECONDS", "30"))
    published_keywords: list[str] = []

    for index, post in enumerate(posts, start=1):
        print(f"\n[{index}/{len(posts)}] '{post.get('keyword', '')}' 처리 중...")
        print(f"  제목: {post['title']}")

        if _is_dry_run():
            print("  DRY_RUN=1 → 티스토리 게시 생략")
            continue

        print("  티스토리 게시 중...")
        publish_to_tistory(post)
        print("  게시 완료")
        if post.get("keyword"):
            published_keywords.append(post["keyword"])

        if index < len(posts):
            time.sleep(delay)

    if published_keywords:
        save_posted_keywords(published_keywords)
        print(f"\n완료: {len(published_keywords)}편 게시")
    elif _is_dry_run():
        print("\n완료: DRY_RUN 모드 (게시 안 함)")
    else:
        print("\n완료")


def main() -> None:
    if _env_flag("PUBLISH_ONLY"):
        print("PUBLISH_ONLY=1 → logs/ 에 저장된 글만 게시 (AI 새로 작성 안 함)")
        posts = _load_posts_from_logs()
        print(f"불러온 글: {len(posts)}편")
        _publish_posts(posts)
        return

    limit = _post_limit()
    seeds, geo = load_seed_keywords()
    candidates = fetch_google_trends(seeds, geo)
    top5 = select_top_keywords(candidates, limit=limit)

    print(f"시드 키워드: {seeds}")
    print(f"후보 {len(candidates)}개 중 상위 {limit}개:")
    for i, keyword in enumerate(top5, start=1):
        print(f"{i}. {keyword}")

    if len(top5) < limit:
        raise SystemExit(f"키워드가 {limit}개 미만입니다: {len(top5)}개")

    posts: list[dict] = []
    for index, keyword in enumerate(top5, start=1):
        print(f"\n[{index}/{limit}] '{keyword}' 글 작성 중...")
        post = write_blog_post(keyword)
        _save_post_log(post)
        posts.append(post)
        print(f"  제목: {post['title']}")
        if index < limit:
            time.sleep(3)

    _publish_posts(posts)
    if _is_dry_run():
        print("(logs/ 폴더에 저장됨)")


if __name__ == "__main__":
    main()
