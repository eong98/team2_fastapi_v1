"""
매장 설문 AI 자동작성 - 업종 트렌드 뉴스 검색 (Tavily Search API)

- Tavily 뉴스 검색(topic="news")으로 최근 SEARCH_DAYS일 기사를 찾는다.
- 제목뿐 아니라 본문 요약(content)도 받아서 LLM이 트렌드를 더 정확히 이해하게 한다.
- 무인 매장 관련 기사는 절도 사건이 많아서, 범죄 단어가 들어간 기사는 결과에서 뺀다.
- 같은 보도자료가 여러 언론사에 반복되므로 비슷한 제목은 하나로 합친다.
- 키가 없거나 실패해도 예외를 올리지 않고 빈 목록을 돌려준다 (트렌드는 부가 기능).

필요한 설정: .env 에 TAVILY_API_KEY=tvly-xxxx  (app.tavily.com 에서 무료 발급, 월 1,000회)
요청 1번 = 1크레딧 (search_depth="basic")
"""

import os
from datetime import date, timedelta
from difflib import SequenceMatcher
from urllib.parse import urlparse

import requests


TAVILY_URL = "https://api.tavily.com/search"

# 범죄/사건 기사 제외 (제목이나 요약에 들어 있으면 버림)
EXCLUDE_WORDS = ["절도", "도난", "경찰", "검거", "범행", "사건", "체포", "구속"]

SEARCH_DAYS = 30
MAX_RESULTS = 20          # Tavily에서 받아올 개수 (걸러낸 뒤 MAX_ARTICLES개만 사용)
MAX_ARTICLES = 10
MAX_CONTENT_LENGTH = 300  # 프롬프트에 넣을 요약 길이
TIMEOUT_SECONDS = 15

# 제목 유사도가 이 값 이상이면 같은 기사로 본다
SIMILAR_TITLE_RATIO = 0.6


def search_trend_news(industry: str) -> list[dict]:
    """업종 트렌드 기사 목록 [{"title", "link", "source", "date", "content"}] (중복 제거, 최대 10건)"""

    industry = (industry or "").strip()
    if not industry:
        return []

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("[shopsurvey] TAVILY_API_KEY가 .env에 없어 트렌드 검색을 건너뜁니다.")
        return []

    payload = {
        "query": f"{industry} 트렌드 신제품 인기",
        "topic": "news",
        "search_depth": "basic",
        "start_date": (date.today() - timedelta(days=SEARCH_DAYS)).isoformat(),
        "max_results": MAX_RESULTS,
        "include_published_date": True,
    }

    try:
        res = requests.post(
            TAVILY_URL,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=TIMEOUT_SECONDS,
        )
        res.raise_for_status()
        results = res.json().get("results") or []
    except Exception as e:
        print(f"[shopsurvey] 트렌드 뉴스 검색 실패: {e}")
        return []

    articles: list[dict] = []
    seen_titles: list[str] = []

    for item in results:
        title = _clean_title(item.get("title") or "")
        content = " ".join((item.get("content") or "").split())
        if not title:
            continue

        # 범죄 기사 제외
        if any(w in title or w in content for w in EXCLUDE_WORDS):
            continue

        # 비슷한 제목은 하나로
        if any(SequenceMatcher(None, title, seen).ratio() >= SIMILAR_TITLE_RATIO for seen in seen_titles):
            continue
        seen_titles.append(title)

        link = item.get("url")
        articles.append({
            "title": title,
            "link": link,
            "source": _domain(link),
            "date": item.get("published_date"),
            "content": content[:MAX_CONTENT_LENGTH],
        })

        if len(articles) >= MAX_ARTICLES:
            break

    return articles


def _clean_title(title: str) -> str:
    """'기사 제목 - 언론사' / '기사 제목 | 언론사' 형식이면 마지막 언론사 부분을 떼어낸다."""

    title = " ".join(title.split())
    for sep in (" - ", " | "):
        head, found, tail = title.rpartition(sep)
        if found and head and len(tail) <= 40:
            return head.strip()
    return title


def _domain(url: str | None) -> str | None:
    """https://www.hankyung.com/... → hankyung.com"""

    if not url:
        return None
    host = urlparse(url).netloc
    return host[4:] if host.startswith("www.") else host or None