"""
매장 설문 AI 자동작성 - 업종 트렌드 뉴스 검색 (구글 뉴스 RSS)

- API 키/결제 없이 쓸 수 있는 구글 뉴스 RSS를 사용한다.
- "업종 + 트렌드"로 최근 60일 기사를 찾고, 범죄 기사(절도/도난 등)는 검색어에서 제외한다.
- 같은 보도자료가 여러 언론사에 반복되므로 비슷한 제목은 하나로 합친다.
- 실패해도 예외를 올리지 않고 빈 목록을 돌려준다 (트렌드는 부가 기능).
"""

import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from urllib.parse import quote

import requests


RSS_URL = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

# 범죄/사건 기사 제외 (무인 매장 관련 기사는 대부분 절도 사건이라)
EXCLUDE_WORDS = ["절도", "도난", "경찰", "검거", "범행", "사건"]

SEARCH_DAYS = 60
MAX_ARTICLES = 10
TIMEOUT_SECONDS = 8

# 제목 유사도가 이 값 이상이면 같은 기사로 본다
SIMILAR_TITLE_RATIO = 0.6


def search_trend_news(industry: str) -> list[dict]:
    """업종 트렌드 기사 목록 [{"title", "link", "source", "date"}] (중복 제거, 최대 10건)"""

    industry = (industry or "").strip()
    if not industry:
        return []

    query = f"{industry} 트렌드 " + " ".join(f"-{w}" for w in EXCLUDE_WORDS) + f" when:{SEARCH_DAYS}d"

    try:
        res = requests.get(RSS_URL.format(query=quote(query)), timeout=TIMEOUT_SECONDS)
        res.raise_for_status()
        root = ET.fromstring(res.content)
    except Exception as e:
        print(f"[shopsurvey] 트렌드 뉴스 검색 실패: {e}")
        return []

    articles: list[dict] = []
    seen_titles: list[str] = []

    for item in root.iter("item"):
        title = _clean_title(item.findtext("title") or "")
        if not title:
            continue

        if any(SequenceMatcher(None, title, seen).ratio() >= SIMILAR_TITLE_RATIO for seen in seen_titles):
            continue
        seen_titles.append(title)

        articles.append({
            "title": title,
            "link": item.findtext("link"),
            "source": item.findtext("source"),
            "date": item.findtext("pubDate"),
        })

        if len(articles) >= MAX_ARTICLES:
            break

    return articles


def _clean_title(title: str) -> str:
    """'기사 제목 - 언론사' 형식에서 마지막 ' - 언론사' 부분을 떼어낸다. (언론사명에 '-'가 있어도 처리)"""

    title = title.strip()
    head, sep, tail = title.rpartition(" - ")
    if sep and head and len(tail) <= 40:
        return head.strip()
    return title