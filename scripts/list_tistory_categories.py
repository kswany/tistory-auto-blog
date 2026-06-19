"""티스토리 블로그 카테고리 ID 목록을 출력합니다.

사용법:
  python scripts/list_tistory_categories.py

.env 에 TISTORY_COOKIE, TISTORY_BLOG 가 설정되어 있어야 합니다.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from category_mapper import fetch_tistory_categories  # noqa: E402


def main() -> None:
    categories = fetch_tistory_categories()
    print("티스토리 카테고리 목록 (config/tistory_categories.json 의 name 과 맞추세요)\n")
    for item in categories:
        print(f"  ID={item['id']:>8}  {item['name']}")
    print(f"\n총 {len(categories)}개")


if __name__ == "__main__":
    main()
