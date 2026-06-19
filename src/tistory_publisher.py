"""티스토리 관리자 내부 API로 글을 게시합니다 (비공식)."""

from __future__ import annotations

import json
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


def _normalize_cookie(cookie: str) -> str:
    """Secrets/복붙 시 줄바꿈이 섞인 쿠키 문자열을 한 줄로 정리합니다."""
    cleaned = cookie.strip().replace("\r\n", "; ").replace("\n", "; ").replace("\r", "; ")
    cleaned = re.sub(r";\s*;", "; ", cleaned)
    return cleaned.strip("; ")


def _request_headers() -> dict[str, str]:
    cookie = os.getenv("TISTORY_COOKIE", "")
    blog_host = _blog_host()
    return {
        "Cookie": _normalize_cookie(cookie),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Origin": f"https://{blog_host}",
        "Referer": f"https://{blog_host}/manage/newpost/?type=post",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "post"


def _looks_like_login_page(text: str, final_url: str) -> bool:
    lowered_url = final_url.lower()
    if any(token in lowered_url for token in ("login", "accounts.kakao", "auth")):
        return True
    snippet = text[:500].lower()
    return "<html" in snippet and ("login" in snippet or "로그인" in snippet)


def _parse_tistory_json(response: requests.Response) -> dict:
    body = response.text.strip()
    if not body:
        raise RuntimeError(
            f"티스토리가 빈 응답을 반환했습니다 (HTTP {response.status_code}). "
            "TISTORY_COOKIE 만료 또는 GitHub Secrets 쿠키 값을 확인하세요."
        )

    if body.startswith("<") or _looks_like_login_page(body, response.url):
        raise RuntimeError(
            f"티스토리 로그인 세션이 유효하지 않습니다 (HTTP {response.status_code}). "
            "브라우저에서 다시 로그인한 뒤 TISTORY_COOKIE를 .env와 GitHub Secrets에 갱신하세요."
        )

    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        preview = body[:200].replace("\n", " ")
        raise RuntimeError(
            f"티스토리 JSON 파싱 실패 (HTTP {response.status_code}): {exc}. "
            f"응답 시작: {preview!r}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"티스토리 응답 형식 오류: {type(data).__name__}")

    return data


def validate_tistory_session() -> None:
    """게시 전 쿠키·세션 유효성을 확인합니다."""
    cookie = os.getenv("TISTORY_COOKIE")
    if not cookie:
        raise RuntimeError(
            "TISTORY_COOKIE 환경변수가 없습니다. "
            "브라우저 개발자도구 → Network → manage/newpost 요청의 Cookie 헤더를 복사하세요."
        )

    blog_host = _blog_host()
    url = f"https://{blog_host}/manage/newpost/?type=post"
    response = requests.get(
        url,
        headers=_request_headers(),
        timeout=30,
        verify=_verify_ssl(),
        allow_redirects=True,
    )

    if _looks_like_login_page(response.text, response.url):
        raise RuntimeError(
            "티스토리 세션 검증 실패: 로그인 페이지로 이동했습니다. "
            "TISTORY_COOKIE를 새로 복사해 GitHub Secrets에 넣어주세요."
        )

    if response.status_code >= 400:
        raise RuntimeError(
            f"티스토리 관리자 페이지 접근 실패 (HTTP {response.status_code}). "
            "쿠키·블로그 이름(TISTORY_BLOG)을 확인하세요."
        )


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

    headers = _request_headers()
    headers["Content-Type"] = "application/json;charset=UTF-8"

    url = f"https://{blog_host}/manage/post.json"
    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=60,
        verify=_verify_ssl(),
    )
    response.raise_for_status()

    data = _parse_tistory_json(response)
    if data.get("success") is False:
        raise RuntimeError(f"티스토리 게시 실패: {data}")

    return data


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    print(f"블로그: {_blog_host()}")
    print("티스토리 세션 검증 중...")
    validate_tistory_session()
    print("✅ TISTORY_COOKIE 유효합니다.")
