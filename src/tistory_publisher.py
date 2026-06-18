"""티스토리 관리자 내부 API로 글을 게시합니다 (비공식)."""

from __future__ import annotations

import os
import re
import unicodedata

import requests


def _blog_host() -> str:
    blog = os.getenv("TISTORY_BLOG", "happy-life-smile").strip()
    if ".tistory.com" not in blog:
        blog = f"{blog}.tistory.com"
    return blog


def _verify_ssl() -> bool:
    return os.getenv("SKIP_SSL_VERIFY", "").lower() not in {"1", "true", "yes"}


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "post"


def publish_to_tistory(post: dict) -> dict:
    cookie = os.getenv("TISTORY_COOKIE")
    if not cookie:
        raise RuntimeError(
            "TISTORY_COOKIE 환경변수가 없습니다. "
            "브라우저 개발자도구에서 로그인 쿠키를 복사해 .env에 넣어주세요."
        )

    blog_host = _blog_host()
    category_id = int(os.getenv("TISTORY_CATEGORY_ID", "0"))
    tags = ",".join(post.get("tags", []))

    payload = {
        "id": "0",
        "title": post["title"],
        "content": post["body_html"],
        "contentType": "html",
        "category": category_id,
        "published": 1,
        "visibility": 20,
        "tag": tags,
        "slogan": _slugify(post["title"])[:80],
        "password": "",
        "attachments": [],
        "uselessMarginForEntry": 1,
        "type": "post",
    }

    headers = {
        "Cookie": cookie,
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "application/json, text/plain, */*",
        "Origin": f"https://{blog_host}",
        "Referer": f"https://{blog_host}/manage/newpost/?type=post",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
    }

    url = f"https://{blog_host}/manage/post.json"
    response = requests.post(url, headers=headers, json=payload, timeout=60, verify=_verify_ssl())
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, dict) or data.get("success") is False:
        raise RuntimeError(f"티스토리 게시 실패: {data}")

    return data
