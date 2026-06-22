"""Gemini API로 티스토리 블로그 글을 생성합니다."""

from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import google.generativeai as genai
from google.api_core import exceptions as google_exceptions

from post_quality import append_quality_footer

MAX_SENTENCES_PER_PARAGRAPH = 1
KST = timezone(timedelta(hours=9))

GREETING_HINTS = [
    "안녕하세요, 줍줍토리예요.",
    "반가워요, 오늘 한입 정보 들고 왔어요.",
    "안녕하세요! 오늘도 핫한 이슈 하나 짚어볼게요.",
    "줍줍토리입니다, 잠깐만요 — 이 이슈 정리해 드릴게요.",
]

CLOSING_HINTS = [
    "오늘도 끝까지 읽어주셔서 고마워요, 다음 이슈에서 또 만나요.",
    "도움이 되셨다면 주변에도 한번 공유해 주세요.",
    "궁금한 점은 글 아래 공식 링크에서 꼭 한번 더 확인해 보세요.",
    "여기까지 오늘의 한입, 다음에도 쓸모 있는 정보로 올게요.",
]

INTRO_STYLES = [
    "인사 다음 문장: 독자가 검색창에 칠 법한 가려운 질문으로 이어가기",
    "인사 다음 문장: 이 이슈가 지금 뉴스·검색에 뜬 배경 한 줄",
    "인사 다음 문장: 이 글이 특히 도움될 대상(누구 해당) 짚기",
    "인사 다음 문장: 일상 장면 한 줄로 키워드와 연결하기",
]

STRUCTURE_VARIANTS = ["narrative", "qa", "checklist", "timeline"]


def _load_blog_config() -> dict:
    path = Path(__file__).resolve().parents[1] / "config" / "blog_config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.IGNORECASE)
        text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))

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


def _build_post_data(data: dict, keyword: str, today: str) -> dict:
    title = str(data.get("title") or "").strip()
    body_html_raw = str(data.get("body_html") or data.get("body") or "").strip()

    if not title:
        title = f"{keyword}, 지금 꼭 알아야 할 정보"
    if not body_html_raw:
        raise ValueError("Gemini 응답에 body_html이 없습니다.")

    body_html = format_readable_html(body_html_raw)
    body_html = append_quality_footer(
        body_html,
        keyword=keyword,
        today=today,
        raw_sources=data.get("sources"),
    )

    return {
        "keyword": keyword,
        "title": title,
        "body_html": body_html,
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


def _is_spacer_paragraph(full_tag: str, inner: str) -> bool:
    if "font-size:0" in full_tag and "height:" in full_tag:
        return True
    plain = _strip_tags(inner).strip()
    if not plain:
        return True
    return plain in {"&nbsp;", "\u00a0", "&#160;"}


def _normalize_tag(html: str, tag: str) -> str:
    pattern = rf"<{tag}\s[^>]*>"
    return re.sub(pattern, f"<{tag}>", html, flags=re.IGNORECASE)


def _normalize_markup(html: str) -> str:
    for tag in ("p", "h2", "h3", "ul", "ol", "li"):
        html = _normalize_tag(html, tag)
    return html


def _remove_spacer_paragraphs(html: str) -> str:
    def replace_p(match: re.Match[str]) -> str:
        full_tag = match.group(0)
        inner = match.group(1).strip()
        if _is_spacer_paragraph(full_tag, inner):
            return ""
        return full_tag

    return re.sub(r"<p(?:\s[^>]*)?>(.*?)</p>", replace_p, html, flags=re.DOTALL | re.IGNORECASE)


def _strip_spacers_adjacent_to_blocks(html: str) -> str:
    html = re.sub(
        r"<p>\s*(?:&nbsp;|\u00a0|&#160;)?\s*</p>\s*(?=<(?:ul|ol|h2|h3))",
        "",
        html,
        flags=re.IGNORECASE,
    )
    html = re.sub(
        r"(?<=</(?:ul|ol|h2|h3)>)\s*<p>\s*(?:&nbsp;|\u00a0|&#160;)?\s*</p>",
        "",
        html,
        flags=re.IGNORECASE,
    )
    return html


def _collapse_excessive_breaks(html: str) -> str:
    html = re.sub(r"(<br\s*/?>\s*){3,}", "<br><br>", html, flags=re.IGNORECASE)
    return html


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
    return f"<p>{content}</p>"


def _apply_paragraph_styles(html: str) -> str:
    def replace_p(match: re.Match[str]) -> str:
        full_tag = match.group(0)
        inner = match.group(1).strip()

        if _is_spacer_paragraph(full_tag, inner):
            return ""

        if re.match(r"^<strong>[^<]+</strong>", inner, flags=re.IGNORECASE):
            blocks = _expand_labeled_paragraph(inner)
            if blocks:
                return "".join(blocks)

        if inner.startswith("<") and "<strong>" not in inner:
            return f"<p>{inner}</p>"

        return "".join(_expand_plain_paragraph(inner))

    return re.sub(r"<p(?:\s[^>]*)?>(.*?)</p>", replace_p, html, flags=re.DOTALL | re.IGNORECASE)


def _split_dense_list_items(html: str) -> str:
    def replace_li(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        plain = _strip_tags(inner)
        sentences = _split_sentences(plain)
        if len(sentences) <= MAX_SENTENCES_PER_PARAGRAPH:
            return f"<li>{inner}</li>"
        return "".join(f"<li>{sentence}</li>" for sentence in sentences)

    return re.sub(r"<li(?:\s[^>]*)?>(.*?)</li>", replace_li, html, flags=re.DOTALL | re.IGNORECASE)


def format_readable_html(html: str) -> str:
    """본문 HTML 정리: 1문장 1문단, spacer/인라인 여백 패턴 제거."""
    html = re.sub(r"<div[^>]*>|</div>", "", html.strip())
    html = _normalize_markup(html)
    html = _remove_spacer_paragraphs(html)
    html = _apply_paragraph_styles(html)
    html = _split_dense_list_items(html)
    html = _remove_spacer_paragraphs(html)
    html = _strip_spacers_adjacent_to_blocks(html)
    html = _collapse_excessive_breaks(html)
    return f'<div style="line-height:1.85; word-break:keep-all;">{html}</div>'


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
    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")
    model = genai.GenerativeModel(model_name)

    today = datetime.now(KST).strftime("%Y-%m-%d")
    greeting_hint = random.choice(GREETING_HINTS)
    closing_hint = random.choice(CLOSING_HINTS)
    intro_style = random.choice(INTRO_STYLES)
    structure_variant = random.choice(STRUCTURE_VARIANTS)

    trend_section = ""
    if trend_context and trend_context.strip():
        trend_section = f"""
[현재 트렌드 배경 — 반드시 반영]
아래는 실제 급상승 검색·뉴스 맥락입니다. 키워드만 아는 일반론·백과사전식 소개는 금지합니다.
이 이슈가 지금 뜨는 이유, 관련 뉴스·연관 검색어를 본문 전반에 자연스럽게 녹여 작성하세요.

★ 뉴스·트렌드 재해석 (필수)
- 아래 뉴스 제목·문장을 그대로 복사·인용·베끼지 마세요 (유사 문서·저품질 판정 위험).
- 사실 관계만 참고하고, 완전히 당신의 언어로 재해석(Paraphrasing)해 설명하세요.
- 언론사명·기사 문장 3단어 이상 연속 사용 금지.

{trend_context.strip()}
"""

    prompt = f"""
당신은 한국어 블로그 '{config["blog_name"]}'의 작가입니다.
독자에게 {config["tone"]} 톤으로 실용 정보를 전달합니다.
뉴스 기사·백과사전·공문서처럼 딱딱하게 쓰지 마세요.

키워드: {keyword}
카테고리 방향: {config["categories_hint"]}
작성 기준일: {today}
글 구조 유형: {structure_variant}
서론 전개: {intro_style}
{trend_section}

[출력 형식 — 엄수]
- 마크다운 코드블록(```) 사용 금지. 순수 JSON 객체 한 개만 출력.
- 설명 문장, 주석, JSON 앞뒤 여백 텍스트 금지.

{{
  "title": "검색 의도 반영 제목 (28~40자, 핵심 키워드 앞쪽 배치, 과장·클릭베이트 금지)",
  "body_html": "HTML 본문 (면책·출처 목록은 넣지 말 것 — 시스템이 자동 추가)",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"],
  "sources": [
    {{"name": "기관명", "url": "https://www.go.kr/..."}}
  ],
  "ymyl": true
}}

[톤·콘텐츠 — 읽을 맛 나게]
1) 친구에게 썰 풀듯: "~에 대해 알아보겠습니다", "결론적으로" 같은 AI 상투구 금지.
2) 가끔 "솔직히", "사실상", "아하" 같은 자연스러운 리액션 (남발 금지).
3) 어려운 용어·정책어는 일상 비유로 풀기 (예: 독점 → 혼자 시장을 꽉 쥔 상태).
4) h2 소제목은 독자가 검색할 법한 질문형으로 (예: "Q. ○○, 왜 지금 시끄러운 걸까요?").
5) 트렌드·뉴스 맥락을 생생하게, 단 기사 문장 복붙·3단어 이상 연속 인용 금지.
6) 신청 방법·대상·주의사항은 꼭 포함. 확실치 않으면 "공식 발표 기준 확인 필요".
7) 허위·과장·"제가 직접 신청해 봤는데" 같은 가짜 경험 금지.

[서론·마무리 — 매 글 필수]
★ 서론 첫 p: 짧은 인사 1문장 (아래 톤 참고, 문장 그대로 복붙 금지).
  참고 톤: 「{greeting_hint}」
★ 서론 2~4p: 인사 후 {intro_style}
★ 본문 마지막 p(면책·출처 전): 짧은 끝맺음 1문장.
  참고 톤: 「{closing_hint}」

[HTML·가독성]
★ 한 p 태그 = 최대 1문장. 2문장 이상 한 p에 넣으면 실패.

1) 서론
- 첫 p = 짧은 인사 (매번 표현은 다르게, 위 참고 톤만 참고).
- 이후 문장도 각각 별도 p.

2) h2 소제목 (3~4개)
- <h2>제목</h2> 만 사용 (인라인 style 금지).
- 가능하면 질문형·구체적으로, 매 글 표현 다르게.

3) 목록 (ul/li)
- li 1개 = 1문장.
- ul/ol 앞뒤 빈 p, &nbsp; p, spacer p 금지.

4) 일반 본문
- <p>문장</p> (p에 margin·height 등 인라인 style 금지).
- 여백용 <p>&nbsp;</p>, font-size:0 spacer, <br> 연속 남발 금지.
- 핵심어·숫자·날짜만 <strong>.

5) 금지
- 3문장 이상 한 p, h2 없는 장문, 매 글 동일 HTML 패턴

[structure_variant별 골격 — 하나만]
- narrative: 인사 → 배경 썰 → h2 질문 단락 → 실무 팁 → 짧은 마무리
- qa: h2를 "Q. ..." 4~5개, 각 답 2~4문장 (1문장 1p)
- checklist: h2 "체크리스트" + ul, 조건별 h2 2~3개
- timeline: h2 "순서·일정" + ul/li 단계

[body_html 작성 예시]
<p>안녕하세요, 오늘도 한입 정보 들고 왔어요.</p>
<p>요즘 ○○ 검색 많이 하시더라고요, 저도 처음엔 헷갈렸어요.</p>
<p>핵심만 쉽게 풀어 드릴게요.</p>
<h2>Q. ○○, 왜 지금 이렇게 떠오른 걸까요?</h2>
<p>솔직히 최근 뉴스에서 ○○ 얘기가 줄줄 이어지면서 검색량이 확 튀었거든요.</p>
<p>쉽게 말하면 ○○ 조건을 <strong>동시에</strong> 맞춰야 하는 경우가 많아서 그래요.</p>
<ul>
<li>먼저 본인 해당 여부부터 확인하세요.</li>
<li>신청 전 필요 서류를 미리 챙기면 덜 헤매요.</li>
</ul>
<p>오늘도 끝까지 읽어주셔서 고마워요, 다음 이슈에서 또 만나요.</p>

[분량·출처]
- 본문 {config["min_chars"]}~{config["max_chars"]}자
- tags 한국어 5개
- sources: https 공식 사이트 1~3개 (정부24, 복지로, 국세청, 금융감독원 등)
- ymyl: 세금·지원금·대출·금융·고용 관련이면 true, 아니면 false
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
                return _build_post_data(data, keyword, today)
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
                    "\n\n이전 응답이 JSON 규칙을 위반했습니다. "
                    "마크다운 ``` 없이 title, body_html, tags 필드를 모두 포함한 "
                    "순수 JSON 객체 하나만 다시 출력하세요."
                )

    raise RuntimeError(f"Gemini 글 생성 실패 ({keyword}): {last_error}") from last_error
