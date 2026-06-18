"""Gemini API로 티스토리 블로그 글을 생성합니다."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import google.generativeai as genai

P_STYLE = "margin: 0 0 18px 0; line-height: 1.95; font-size: 16px;"
H2_STYLE = "margin: 44px 0 22px 0; line-height: 1.45; font-size: 22px; font-weight: 700;"
UL_STYLE = "margin: 10px 0 26px 0; padding-left: 22px; line-height: 1.9;"
LI_STYLE = "margin-bottom: 16px;"
SPACER = '<p style="margin:0;padding:0;height:16px;line-height:16px;font-size:0;">&nbsp;</p>'
BLOCK_SPACER = '<p style="margin:0;padding:0;height:22px;line-height:22px;font-size:0;">&nbsp;</p>'


def _load_blog_config() -> dict:
    path = Path(__file__).resolve().parents[1] / "config" / "blog_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _strip_tags(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [part.strip() for part in parts if part.strip()]


def _chunk_sentences(sentences: list[str], max_per_paragraph: int = 2) -> list[str]:
    chunks: list[str] = []
    for index in range(0, len(sentences), max_per_paragraph):
        chunk = " ".join(sentences[index : index + max_per_paragraph])
        if chunk:
            chunks.append(chunk)
    return chunks


def _split_long_paragraph(text: str, max_chars: int = 100) -> list[str]:
    text = re.sub(r"\s+", " ", text.strip())
    if len(text) <= max_chars:
        return [text]

    sentences = _split_sentences(text)
    if len(sentences) <= 1:
        return [text]

    return _chunk_sentences(sentences, max_per_paragraph=2)


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
        inner = match.group(1).strip()
        if not inner or inner == "&nbsp;" or "height:" in match.group(0):
            return match.group(0)
        if inner.startswith("<"):
            return f'<p style="{P_STYLE}">{inner}</p>'

        parts = _split_long_paragraph(_strip_tags(inner))
        return "".join(_make_paragraph(part) for part in parts)

    html = re.sub(r"<p(?:\s[^>]*)?>(.*?)</p>", replace_p, html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<h2(?:\s[^>]*)?>", f'<h2 style="{H2_STYLE}">', html, flags=re.IGNORECASE)
    html = re.sub(r"<ul(?:\s[^>]*)?>", f'<ul style="{UL_STYLE}">', html, flags=re.IGNORECASE)
    html = re.sub(r"<li(?:\s[^>]*)?>", f'<li style="{LI_STYLE}">', html, flags=re.IGNORECASE)
    return html


def format_readable_html(html: str) -> str:
    html = re.sub(r"<div[^>]*>|</div>", "", html.strip())
    html = _apply_paragraph_styles(html)
    html = _fix_intro_spacing(html)
    html = _add_block_spacers_before_topics(html)
    return f'<div style="line-height:1.95; word-break:keep-all;">{html}</div>'


def write_blog_post(keyword: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")

    config = _load_blog_config()
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    model = genai.GenerativeModel(model_name)

    prompt = f"""
당신은 한국어 블로그 '{config["blog_name"]}'의 작가입니다.
독자에게 {config["tone"]} 톤으로 실용 정보를 전달합니다.

키워드: {keyword}
카테고리 방향: {config["categories_hint"]}

아래 JSON 형식만 출력하세요. 다른 설명은 금지합니다.
{{
  "title": "SEO에 맞는 제목 (35자 내외)",
  "body_html": "HTML 본문",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"]
}}

[여백·줄바꿈 규칙 - 반드시 지키기]

1) 서론
- 1문장: "안녕하세요, 줍줍토리입니다!" 만 단독 p
- 빈 줄 1개 (spacer p)
- 다음 문장 각각 별도 p (1~2문장씩)
- 서론 끝에도 spacer p 1개

2) h2 큰 소제목
- h2 위쪽 여백 = 공백 2줄 느낌 (h2 태그만 사용, 문단과 분리)
- h2 아래 공백 1줄 느낌 후 본문 p 시작
- 예: <h2>정부지원금, 왜 놓치지 말아야 할까요?</h2>

3) 유형·항목 나열 (청년/취약계층/육아/소상공인 등)
- 각 항목: <p><strong>항목명</strong> 설명 1문장.</p> + <p>추가 설명 1문장.</p>
- 항목과 항목 사이 spacer p 1개 (숨 쉴 공간)
- ul/li 사용 시 li도 2문장 이내

4) 일반 본문
- p 태그당 1~2문장만
- 3문장 이상 한 p에 넣지 말 것
- 핵심어·숫자·날짜는 <strong>

5) 금지
- 긴 벽돌 문단
- h2 없이 긴 줄글만 이어 쓰기

[내용]
- 본문 {config["min_chars"]}~{config["max_chars"]}자
- 신청 방법, 조건, 주의사항 포함
- 허위·과장 금지
- tags 한국어 5개
"""

    response = model.generate_content(prompt)
    raw = response.text or ""
    data = _extract_json(raw)
    body_html = format_readable_html(str(data["body_html"]).strip())

    return {
        "keyword": keyword,
        "title": str(data["title"]).strip(),
        "body_html": body_html,
        "tags": [str(tag).strip() for tag in data["tags"][:5]],
    }
