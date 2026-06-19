"""Gemini API로 티스토리 블로그 글을 생성합니다."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

P_STYLE = "margin: 0 0 22px 0; line-height: 1.95; font-size: 16px;"
H2_STYLE = "margin: 52px 0 24px 0; line-height: 1.45; font-size: 22px; font-weight: 700;"
UL_STYLE = "margin: 10px 0 28px 0; padding-left: 22px; line-height: 1.9;"
LI_STYLE = "margin-bottom: 18px;"
SPACER = '<p style="margin:0;padding:0;height:18px;line-height:18px;font-size:0;">&nbsp;</p>'
BLOCK_SPACER = '<p style="margin:0;padding:0;height:28px;line-height:28px;font-size:0;">&nbsp;</p>'
MAX_SENTENCES_PER_PARAGRAPH = 1


def _load_blog_config() -> dict:
    path = Path(__file__).resolve().parents[1] / "config" / "blog_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Gemini 응답이 JSON 객체가 아닙니다.")
    return data


def _default_tags(keyword: str) -> list[str]:
    candidates = [keyword.strip(), "재테크", "정부정책", "줍줍토리", "생활정보"]
    tags: list[str] = []
    seen: set[str] = set()
    for tag in candidates:
        normalized = tag.lower()
        if tag and normalized not in seen:
            seen.add(normalized)
            tags.append(tag)
    return tags[:5]


def _normalize_tags(raw_tags: object, keyword: str) -> list[str]:
    if isinstance(raw_tags, list):
        tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
        if tags:
            return tags[:5]
    if isinstance(raw_tags, str) and raw_tags.strip():
        parts = re.split(r"[,，\s]+", raw_tags.strip())
        tags = [part for part in parts if part]
        if tags:
            return tags[:5]
    return _default_tags(keyword)


def _build_post_data(data: dict, keyword: str) -> dict:
    title = str(data.get("title") or "").strip()
    body_html_raw = str(data.get("body_html") or data.get("body") or "").strip()

    if not title:
        title = f"{keyword}, 지금 꼭 알아야 할 정보"
    if not body_html_raw:
        raise ValueError("Gemini 응답에 body_html이 없습니다.")

    return {
        "keyword": keyword,
        "title": title,
        "body_html": format_readable_html(body_html_raw),
        "tags": _normalize_tags(data.get("tags"), keyword),
    }


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _chunk_sentences(sentences: list[str], max_per_paragraph: int = MAX_SENTENCES_PER_PARAGRAPH) -> list[str]:
    chunks: list[str] = []
    for index in range(0, len(sentences), max_per_paragraph):
        chunk = " ".join(sentences[index : index + max_per_paragraph])
        if chunk:
            chunks.append(chunk)
    return chunks


def _split_long_paragraph(text: str, max_chars: int = 70) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= max_chars:
        return [text]

    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return [text]

    return _chunk_sentences(sentences, max_per_paragraph=MAX_SENTENCES_PER_PARAGRAPH)


def _is_spacer_html(html: str) -> bool:
    return "font-size:0" in html and "height:" in html


def _expand_plain_paragraph(inner: str) -> list[str]:
    plain = _strip_tags(inner)
    if not plain:
        return []

    sentences = _split_sentences(plain)
    if len(sentences) <= MAX_SENTENCES_PER_PARAGRAPH:
        if "<strong>" in inner:
            return [_make_paragraph(inner)]
        return [_make_paragraph(plain)]

    return [_make_paragraph(sentence) for sentence in sentences]


def _expand_labeled_paragraph(inner: str) -> list[str]:
    match = re.match(
        r"^<strong>([^<]+)</strong>\s*[：:]?\s*(.*)$",
        inner.strip(),
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return []

    label = match.group(1).strip()
    rest = match.group(2).strip()
    blocks = [_make_paragraph(f"<strong>{label}</strong>")]
    if not rest:
        return blocks

    plain = _strip_tags(rest)
    sentences = _split_sentences(plain)
    if len(sentences) <= 1:
        blocks.append(_make_paragraph(rest if rest.startswith("<") else plain))
        return blocks

    blocks.extend(_make_paragraph(sentence) for sentence in sentences)
    return blocks


def _make_paragraph(content: str) -> str:
    return f'<p style="{P_STYLE}">{content}</p>'


def _fix_intro_spacing(html: str) -> str:
    match = re.search(r"<p(?:\s[^>]*)?>(.*?)</p>", html, flags=re.DOTALL | re.IGNORECASE)
    if not match:
        return html

    inner_html = match.group(1).strip()
    plain = _strip_tags(inner_html)
    if "안녕" not in plain and "줍줍토리" not in plain:
        return html

    sentences = _split_sentences(plain)
    if len(sentences) < 2:
        return html

    greeting = sentences[0]
    if "안녕" not in greeting and "줍줍토리" not in greeting:
        return html

    rebuilt = [_make_paragraph(greeting), SPACER]
    rebuilt.extend(_make_paragraph(chunk) for chunk in _chunk_sentences(sentences[1:], 1))
    replacement = "".join(rebuilt)
    return html[: match.start()] + replacement + html[match.end() :]


def _add_block_spacers_before_topics(html: str) -> str:
    pattern = re.compile(
        r"(<p style=\"[^\"]*\">)(\s*<strong>[^<]+</strong>)",
        flags=re.IGNORECASE,
    )
    count = 0

    def replacer(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        if count == 1:
            return match.group(0)
        return BLOCK_SPACER + match.group(0)

    return pattern.sub(replacer, html)


def _apply_paragraph_styles(html: str) -> str:
    def replace_p(match: re.Match[str]) -> str:
        full_tag = match.group(0)
        inner = match.group(1).strip()

        if _is_spacer_html(full_tag) or not inner or inner == "&nbsp;":
            return SPACER

        if re.match(r"^<strong>[^<]+</strong>", inner, flags=re.IGNORECASE):
            blocks = _expand_labeled_paragraph(inner)
            if blocks:
                return SPACER.join(blocks)

        if inner.startswith("<") and "<strong>" not in inner:
            return f'<p style="{P_STYLE}">{inner}</p>'

        return SPACER.join(_expand_plain_paragraph(inner))

    html = re.sub(r"<p(?:\s[^>]*)?>(.*?)</p>", replace_p, html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<h2(?:\s[^>]*)?>", f'<h2 style="{H2_STYLE}">', html, flags=re.IGNORECASE)
    html = re.sub(r"<ul(?:\s[^>]*)?>", f'<ul style="{UL_STYLE}">', html, flags=re.IGNORECASE)
    html = re.sub(r"<li(?:\s[^>]*)?>", f'<li style="{LI_STYLE}">', html, flags=re.IGNORECASE)
    return html


def _split_dense_list_items(html: str) -> str:
    def replace_li(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        plain = _strip_tags(inner)
        sentences = _split_sentences(plain)
        if len(sentences) <= MAX_SENTENCES_PER_PARAGRAPH:
            return match.group(0)
        return "".join(f'<li style="{LI_STYLE}">{sentence}</li>' for sentence in sentences)

    return re.sub(r"<li(?:\s[^>]*)?>(.*?)</li>", replace_li, html, flags=re.DOTALL | re.IGNORECASE)


def _enhance_heading_spacing(html: str) -> str:
    html = re.sub(
        r"(<h2 style=\"[^\"]+\">.*?</h2>)",
        rf"{BLOCK_SPACER}\1{SPACER}",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return html


def _insert_breathing_room(html: str) -> str:
    html = re.sub(r"<p(?:\s[^>]*)?>\s*</p>", SPACER, html, flags=re.IGNORECASE)
    html = re.sub(
        r"(</p>)\s*(<p style=\"margin: 0 0 22px)",
        rf"\1{SPACER}\2",
        html,
        flags=re.IGNORECASE,
    )
    return html


def _collapse_duplicate_spacers(html: str) -> str:
    while SPACER + SPACER in html:
        html = html.replace(SPACER + SPACER, SPACER)
    while BLOCK_SPACER + SPACER + SPACER in html:
        html = html.replace(BLOCK_SPACER + SPACER + SPACER, BLOCK_SPACER + SPACER)
    return html


def format_readable_html(html: str) -> str:
    html = re.sub(r"<div[^>]*>|</div>", "", html.strip())
    html = _apply_paragraph_styles(html)
    html = _split_dense_list_items(html)
    html = _fix_intro_spacing(html)
    html = _add_block_spacers_before_topics(html)
    html = _enhance_heading_spacing(html)
    html = _insert_breathing_room(html)
    html = _collapse_duplicate_spacers(html)
    return f'<div style="line-height:1.95; word-break:keep-all;">{html}</div>'


def _gemini_retry_wait(exc: Exception, attempt: int) -> float:
    match = re.search(r"retry in ([0-9.]+)s", str(exc), flags=re.IGNORECASE)
    if match:
        return float(match.group(1)) + 3
    return min(120.0, 20.0 * attempt)


def write_blog_post(keyword: str, trend_context: str | None = None) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")

    config = _load_blog_config()
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    model = genai.GenerativeModel(model_name)

    trend_section = ""
    if trend_context and trend_context.strip():
        trend_section = f"""
[현재 트렌드 배경 — 반드시 반영]
아래는 실제 급상승 검색·뉴스 맥락입니다. 키워드만 아는 일반론·백과사전식 소개는 금지합니다.
이 이슈가 지금 뜨는 이유, 관련 뉴스·연관 검색어를 본문 전반에 자연스럽게 녹여 작성하세요.

{trend_context.strip()}
"""

    prompt = f"""
당신은 한국어 블로그 '{config["blog_name"]}'의 작가입니다.
독자에게 {config["tone"]} 톤으로 실용 정보를 전달합니다.

키워드: {keyword}
카테고리 방향: {config["categories_hint"]}
{trend_section}

아래 JSON 형식만 출력하세요. 다른 설명은 금지합니다.
{{
  "title": "SEO에 맞는 제목 (35자 내외)",
  "body_html": "HTML 본문",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"]
}}

[여백·줄바꿈 규칙 - 가독성 최우선, 반드시 지키기]

★ 핵심: 한 p 태그 = 최대 1문장. 2문장 이상 한 p에 넣으면 실패.

1) 서론
- 1문장: "안녕하세요, 줍줍토리입니다!" 만 단독 p
- spacer p 1개
- 다음 문장 각각 별도 p (1문장씩)
- 서론 끝 spacer p 1개

2) h2 큰 소제목 (3~4개 사용)
- h2 위·아래 공백 느낌
- 예: <h2>모두의 카드 정책 개요 및 환급 구조</h2>

3) 소제목·항목 (5~6번 예시처럼)
- <p><strong>[모두의 카드 유형 비교]</strong></p> 처럼 대괄호 소제목은 단독 p
- 또는 <p><strong>1. 정부 공식 웹사이트 활용</strong></p> + 다음 p에 설명 1~2문장
- 항목마다 spacer p 1개

4) ul/li 목록
- li 1개 = 1문장 (길면 분리)
- 목록 전후 spacer p

5) 일반 본문
- p 태그당 1문장만 (절대 벽돌 문단 금지)
- 핵심어·숫자·날짜는 <strong>

6) 금지
- 3문장 이상 한 p에 묶기
- h2 없이 긴 줄글
- spacer 없이 문단 5개 연속

[body_html 작성 예시 - 이 밀도로 작성]
<p>안녕하세요, 줍줍토리입니다!</p>
<p style="margin:0;padding:0;height:18px;line-height:18px;font-size:0;">&nbsp;</p>
<p>오늘은 ○○ 이슈, 한입에 정리해 드릴게요.</p>
<p>최근 검색량이 급증한 배경도 함께 짚어 봅니다.</p>
<p style="margin:0;padding:0;height:18px;line-height:18px;font-size:0;">&nbsp;</p>
<h2>○○, 지금 왜 주목받을까요?</h2>
<p style="margin:0;padding:0;height:18px;line-height:18px;font-size:0;">&nbsp;</p>
<p>첫 번째 핵심 포인트 한 문장.</p>
<p>두 번째 핵심 포인트 한 문장.</p>
<p><strong>[확인 방법]</strong></p>
<p style="margin:0;padding:0;height:18px;line-height:18px;font-size:0;">&nbsp;</p>
<p><strong>1. 공식 사이트 확인</strong></p>
<p>설명 첫 문장.</p>
<p>설명 둘째 문장.</p>

[내용]
- 본문 {config["min_chars"]}~{config["max_chars"]}자
- 트렌드 배경이 있으면 그 이슈·뉴스 맥락 중심으로 작성 (단순 키워드 정의 금지)
- 신청 방법, 조건, 주의사항 포함
- 허위·과장 금지
- tags 한국어 5개
"""

    generation_config = genai.types.GenerationConfig(response_mime_type="application/json")
    gemini_timeout = max(30, int(os.getenv("GEMINI_TIMEOUT_SECONDS", "180")))
    parse_attempts = 2
    quota_attempts = max(1, int(os.getenv("GEMINI_QUOTA_RETRIES", "3")))
    last_error: Exception | None = None

    for quota_try in range(1, quota_attempts + 1):
        prompt_body = prompt
        for attempt in range(1, parse_attempts + 1):
            try:
                response = model.generate_content(
                    prompt_body,
                    generation_config=generation_config,
                    request_options={"timeout": gemini_timeout},
                )
                raw = response.text or ""
                data = _extract_json(raw)
                return _build_post_data(data, keyword)
            except google_exceptions.ResourceExhausted as exc:
                last_error = exc
                if quota_try >= quota_attempts:
                    raise RuntimeError(
                        f"Gemini API 일일/분당 한도 초과 ({keyword}). "
                        "내일 다시 시도하거나 GEMINI_MODEL 변경·유료 플랜을 확인하세요."
                    ) from exc
                wait = _gemini_retry_wait(exc, quota_try)
                print(
                    f"  Gemini 한도 초과, {wait:.0f}초 후 재시도 ({quota_try}/{quota_attempts})...",
                    flush=True,
                )
                time.sleep(wait)
                break
            except (json.JSONDecodeError, ValueError, KeyError) as exc:
                last_error = exc
                if attempt >= parse_attempts:
                    raise RuntimeError(f"Gemini 글 생성 실패 ({keyword}): {last_error}") from last_error
                prompt_body += (
                    "\n\n이전 응답에 title, body_html, tags 필드가 빠졌습니다. "
                    "세 필드를 모두 포함한 JSON만 다시 출력하세요."
                )

    raise RuntimeError(f"Gemini 글 생성 실패 ({keyword}): {last_error}") from last_error
