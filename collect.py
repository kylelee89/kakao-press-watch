#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import math
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ARTICLES_JSON = DATA_DIR / "articles.json"
ARTICLES_JS = DATA_DIR / "articles.js"
LOG_FILE = DATA_DIR / "collect.log"
RULES = json.loads((ROOT / "rules.json").read_text(encoding="utf-8"))

KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"

QUERIES = [
    "카카오 인적분할",
    "카카오AI 카카오X",
    "카카오 분할 주가",
    "카카오 쪼개기 상장",
    "카카오 분할 증권가",
    "카카오 목표주가",
    "카카오AI 재상장",
    "카카오X 주주환원",
    "카카오 임시주주총회 분할",
    "카카오 인적분할 논란",
]

# 공시일 컷오프: 이 시각 이전에 발행된 기사는 이번 사안과 무관한 과거 기사로 보고 제외한다.
# (구글뉴스 RSS 가 "카카오 쪼개기 상장" 등 쿼리에 2021년 물적분할 논란 기사까지 섞어서 반환함)
CUTOFF_KST = "2026-08-21T00:00:00+09:00"

# 구글뉴스는 볼륨이 낮은 쿼리의 뒤쪽을 도박 SEO 스팸으로 채운다.
# ("카카오X 주주환원", "카카오 임시주주총회 분할" 은 결과의 33% 가 스팸이었다.)
# 해외 사이트 도메인을 탈취해 한국어 도박 키워드를 박아 넣은 것들이라 매체명으로 먼저 거른다.
SPAM_OUTLETS = (
    "calgary roughnecks", "calgaryroughnecks",
    "histoire pour tous", "histoire-pour-tous",
    "platea magazine", "actt.org.tt",
)
# 도박 어휘가 있으면서 카카오 관련 어휘가 하나도 없으면 이번 사안과 무관한 기사다.
# 두 조건을 AND 로 묶어야 "'쪼개기·먹튀' 카카오" 같은 정상 기사가 살아남는다.
SPAM_TERMS = re.compile(
    r"카지노|토토|블랙\s?잭|블랙잭|바카라|슬롯|경마|홀덤|먹튀|배팅|베팅|파워볼|강원랜드|섯다|맞고"
)
TOPIC_TERMS = re.compile(r"카카오|kakao|카나나|카톡|네카오|픽코마|김범수|정신아", re.IGNORECASE)


def is_offtopic(title: str, outlet: str) -> bool:
    """도박 SEO 스팸과 제목이 매체명뿐인 쓰레기 항목을 걸러낸다."""
    low = (outlet or "").lower()
    if any(bad in low for bad in SPAM_OUTLETS):
        return True
    if SPAM_TERMS.search(title) and not TOPIC_TERMS.search(title):
        return True
    # "조세금융신문 | 조세금융신문" 처럼 제목 자리에 매체명만 들어온 항목
    if outlet and normalize_title(title) == normalize_title(outlet):
        return True
    return False


OUTLET_GROUPS = [
    ("회사발표", ["카카오 뉴스룸", "kakaocorp", "카카오톡 뉴스룸"]),
    ("매체 미상", ["v.daum.net", "daum.net", "네이트", "네이버", "n.news", "주달", "zum.com", "nate.com"]),
    ("통신사", ["연합뉴스", "뉴시스", "뉴스1", "연합인포맥스"]),
    ("경제지", ["한국경제", "매일경제", "서울경제", "머니투데이", "이데일리", "파이낸셜뉴스", "헤럴드경제", "아시아경제", "전자신문", "디지털타임스", "아주경제", "조세일보", "비즈니스포스트", "더벨", "인베스트조선", "뉴스웨이", "아이뉴스24", "뉴스토마토", "글로벌이코노믹", "알파경제", "이코노미스트", "대한경제", "경제타임스", "fetv", "FETV", "오피니언뉴스", "뉴스웍스", "스마트투데이", "핀포인트뉴스", "한국금융", "이투데이", "프라임경제", "데일리안", "머니S", "시사저널e", "브릿지경제", "EBN", "뉴데일리", "금융소비자뉴스", "KB Think", "데일리브리프"]),
    ("종합일간", ["조선일보", "중앙일보", "동아일보", "한겨레", "경향신문", "국민일보", "세계일보", "서울신문", "한국일보", "문화일보", "아시아투데이", "국제신문", "천지일보", "매일신문", "강원일보", "경인일보", "노컷뉴스", "오마이뉴스", "프레시안", "시사IN", "주간조선", "신동아"]),
    ("방송", ["KBS", "MBC", "SBS", "YTN", "JTBC", "서울경제TV", "한국경제TV", "MTN", "채널A", "TV조선", "BBS", "티제이비", "TJB", "CBS", "OBS", "연합뉴스TV", "머니투데이방송"]),
    ("IT전문", ["지디넷코리아", "블로터", "바이라인네트워크", "IT조선", "디지털데일리", "테크M", "전자신문", "테크월드", "디일렉", "플래텀", "Platum", "정보통신신문", "전자부품", "AI타임스", "더구루", "인공지능신문", "테크튜브", "벤처스퀘어"]),
    ("증권투자", ["Investing.com", "인포스탁", "데이터투자", "팍스넷", "토마토증권통", "인포스탁데일리", "이데일리마켓", "한경컨센서스", "버핏연구소", "팍스경제TV"]),
]

NEGATIVE = [
    ("불만", -1),
    ("반대", -1),
    ("매도", -1),
    ("편법", -1),
]

POSITIVE = [
    ("기대", 2),
    ("밸류업", 2),
    ("매수", 1),
    ("긍정", 1),
]

FRAME_KEYWORDS = {
    "주가/시장반응": ["주가", "급락", "급등", "하락", "상승", "시총", "거래정지", "약세", "강세", "투자자"],
    "중복상장/주주가치": ["중복상장", "쪼개기", "주주가치", "소액주주", "개미", "주주환원", "자사주"],
    "AI전략": ["AI", "인공지능", "카나나", "에이전트", "카카오AI"],
    "지배구조": ["지배구조", "지분", "김범수", "경영권", "오너", "정신아"],
    "규제/거래소": ["거래소", "금융당국", "금감원", "카카오뱅크", "대주주", "적격성", "심사", "조회공시"],
    "밸류에이션": ["목표주가", "밸류에이션", "저평가", "디스카운트", "증권가", "리포트", "애널리스트"],
    "분할구조": ["분할비율", "존속", "신설", "재상장", "임시주총", "카카오X"],
}


def _unique_push(items: list[str], value: str) -> None:
    if value and value not in items:
        items.append(value)


def _strip_prefixes(title: str) -> str:
    text = title or ""
    changed = True
    while changed:
        changed = False
        for pattern in RULES.get("stripPrefixes", []):
            match = re.match(rf"^(?:{pattern})\s*", text)
            if match:
                text = text[match.end() :].lstrip()
                changed = True
    return text


def _find_pivot(text: str) -> tuple[int, str] | None:
    markers = RULES.get("pivot", {}).get("markers", [])
    ordered = sorted(enumerate(markers), key=lambda item: (-len(item[1]), item[0]))
    best: tuple[int, str] | None = None
    for _, marker in ordered:
        idx = text.find(marker)
        if idx == -1:
            continue
        if best is None or idx < best[0] or (idx == best[0] and len(marker) > len(best[1])):
            best = (idx, marker)
    return best


def _first_pattern_match(patterns: list[str], text: str) -> re.Match[str] | None:
    best: re.Match[str] | None = None
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        if best is None or match.start() < best.start() or (
            match.start() == best.start() and len(match.group(0)) > len(best.group(0))
        ):
            best = match
    return best


def _negated(text: str, start: int, end: int) -> bool:
    window = int(RULES.get("negation", {}).get("window", 0))
    if window <= 0:
        return False
    tail = text[end : end + window]
    patterns = RULES.get("negation", {}).get("patterns", [])
    return any(re.search(pattern, tail) for pattern in patterns)


def _score_term_clause(
    clause: str,
    terms: list[list[Any]],
    hits_pos: list[str],
    hits_neg: list[str],
    signals: list[str],
) -> float:
    score = 0.0
    seen: set[str] = set()
    text = clause or ""
    # "재평가 기대감" 안의 "재평가 기대", "기대감" 처럼 겹치는 표현이 3중으로 가산되던 문제가 있었다.
    # 긴 표현부터 매칭하고, 이미 점수를 먹은 구간과 겹치면 건너뛴다.
    taken: list[tuple[int, int]] = []

    for term, weight in sorted(terms, key=lambda t: -len(str(t[0] or ""))):
        if not term or term in seen:
            continue
        idx = text.find(term)
        if idx == -1:
            continue
        end = idx + len(term)
        if any(idx < te and ts < end for ts, te in taken):
            continue
        taken.append((idx, end))
        seen.add(term)
        actual_weight = float(weight)
        if _negated(text, idx, idx + len(term)):
            actual_weight = -actual_weight
            _unique_push(signals, f"부정반전:{term}")
        score += actual_weight
        if actual_weight >= 0:
            _unique_push(hits_pos, term)
        else:
            _unique_push(hits_neg, term)
        tag = "적대어" if actual_weight < 0 else "호재어"
        _unique_push(signals, f"{tag}:{term}")

    return score


def _score_price_move(clause: str, hits_pos: list[str], hits_neg: list[str], signals: list[str]) -> float:
    text = clause or ""
    down_patterns = RULES.get("priceMove", {}).get("downPatterns", [])
    up_patterns = RULES.get("priceMove", {}).get("upPatterns", [])
    down_match = _first_pattern_match(down_patterns, text)
    up_match = _first_pattern_match(up_patterns, text)

    score = 0.0
    chosen: re.Match[str] | None = None
    direction = None
    if down_match and up_match:
        if down_match.start() <= up_match.start():
            chosen = down_match
            direction = "down"
        else:
            chosen = up_match
            direction = "up"
    elif down_match:
        chosen = down_match
        direction = "down"
    elif up_match:
        chosen = up_match
        direction = "up"

    if chosen is None:
        return 0.0

    price_rules = RULES.get("priceMove", {})
    if direction == "down":
        score += float(price_rules.get("downScore", 0.0))
        _unique_push(hits_neg, chosen.group(0).strip())
        _unique_push(signals, f"등락:{chosen.group(0).strip()}")
        number_match = re.search(r"(\d+(?:\.\d+)?)", chosen.group(0))
        if number_match and float(number_match.group(1)) >= float(price_rules.get("bigMoveThreshold", 0)):
            score += float(price_rules.get("bigMoveBonus", 0.0))
            _unique_push(signals, f"대형하락:{number_match.group(1)}")
    else:
        score += float(price_rules.get("upScore", 0.0))
        _unique_push(hits_pos, chosen.group(0).strip())
        _unique_push(signals, f"등락:{chosen.group(0).strip()}")

    return score


def _score_rhetorical_question(clause: str, hits_neg: list[str], signals: list[str]) -> float:
    text = clause or ""
    patterns = RULES.get("rhetoricalQuestion", {}).get("loadedPatterns", [])
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            value = match.group(0).strip()
            _unique_push(hits_neg, value)
            _unique_push(signals, f"수사의문:{value}")
            return float(RULES.get("rhetoricalQuestion", {}).get("loadedScore", 0.0))
    if "?" in text:
        _unique_push(hits_neg, "?")
        _unique_push(signals, "물음표:?")
        return float(RULES.get("rhetoricalQuestion", {}).get("bareQuestionScore", 0.0))
    return 0.0


def _score_scare_quote(clause: str, hits_neg: list[str], signals: list[str]) -> float:
    text = clause or ""
    rule = RULES.get("scareQuote", {})
    pattern = rule.get("quotePattern", "")
    pejoratives = rule.get("pejoratives", [])
    score = 0.0
    for match in re.finditer(pattern, text):
        quoted = match.group(1).strip()
        if any(pejorative in quoted for pejorative in pejoratives):
            score += float(rule.get("score", 0.0))
            _unique_push(hits_neg, quoted)
            _unique_push(signals, f"스케어쿼트:{quoted}")
    return score


def _score_clause(clause: str, clause_weight: float, hits_pos: list[str], hits_neg: list[str], signals: list[str]) -> float:
    score = 0.0
    score += _score_price_move(clause, hits_pos, hits_neg, signals)
    score += _score_rhetorical_question(clause, hits_neg, signals)
    # 회사 프레임 어휘("두 개의 엔진", "승부수", "제2의 도약" 등)는 기자의 긍정 평가가 아니라
    # 보도자료 프레임을 실은 것이다. 논조 점수에 절대 넣지 않는다. frame 축에서만 센다.
    # (이 경로가 살아 있어서 "'AI 지각생'...승부수 던졌지만...하락" 같은 기사가 +2 를 먹었다)
    score += _score_term_clause(clause, RULES.get("hostileFraming", {}).get("terms", []), hits_pos, hits_neg, signals)
    score += _score_term_clause(clause, RULES.get("hostileFraming", {}).get("favorableTerms", []), hits_pos, hits_neg, signals)
    score += _score_scare_quote(clause, hits_neg, signals)
    return score * clause_weight


def _classify_tone_axis(title: str) -> tuple[str, float, dict[str, list[str]], list[str]]:
    text = _strip_prefixes(title)
    pivot = _find_pivot(text)
    clause_specs: list[tuple[str, float]] = []
    signals: list[str] = []

    if pivot is None:
        clause_specs.append((text, 1.0))
    else:
        idx, marker = pivot
        pre = text[:idx].strip()
        post = text[idx + len(marker) :].strip()
        if pre:
            clause_specs.append((pre, float(RULES.get("pivot", {}).get("preWeight", 1.0))))
        if post:
            clause_specs.append((post, float(RULES.get("pivot", {}).get("postWeight", 1.0))))
        _unique_push(signals, f"역접:{marker}")

    hits_pos: list[str] = []
    hits_neg: list[str] = []
    score = 0.0
    for clause, clause_weight in clause_specs:
        score += _score_clause(clause, clause_weight, hits_pos, hits_neg, signals)

    # 전언/인용 헤드라인은 기자 본인의 평가가 아니다. 부정 점수만 감쇠한다.
    # ("증권가 '카카오 우려'" 는 사실 전달이고, "카카오, 우려 자초" 는 기자의 평가다)
    attr = RULES.get("attribution", {})
    if score < 0:
        marker = next((m for m in attr.get("markers", []) if m and m in text), None)
        # 내용 자체가 단정적 부정 판단이면 인용이라도 감쇠하지 않는다.
        hard = any(t in text for t in attr.get("noDampTerms", []))
        if marker and not hard:
            score *= float(attr.get("negMultiplier", 1.0))
            _unique_push(signals, f"전언감쇠:{marker.strip()}")

    # 파이썬 round() 는 짝수 반올림, JS Math.round() 는 절상이라 -1.65 에서 값이 갈렸다.
    # 두 엔진 판정이 완전히 일치해야 하므로 JS 쪽 규칙(절상)에 맞춘다.
    tone_score = math.floor(score * 10 + 0.5) / 10
    thresholds = RULES.get("thresholds", {})
    if tone_score >= float(thresholds.get("favorable", 0.0)):
        tone = "긍정"
    elif tone_score <= float(thresholds.get("critical", 0.0)):
        tone = "우려"
    else:
        tone = "중립"

    return tone, tone_score, {"pos": hits_pos, "neg": hits_neg}, signals


def _classify_frame_axis(title: str, tone_score: float = 0.0) -> str:
    text = _strip_prefixes(title)
    company_terms = RULES.get("companyFrame", {}).get("terms", [])
    critical_terms = RULES.get("criticalFrame", {}).get("terms", [])
    c = 0
    k = 0
    for term in company_terms:
        if term and term in text:
            c += 1
    for term in critical_terms:
        if term and term in text:
            k += 1
    if k > c:
        return "비판프레임"
    if c > k:
        return "회사프레임"
    # 회사 프레임과 비판 프레임이 같은 수로 맞붙은 경우.
    # 개수로는 안 갈리지만 기사의 실제 논지는 논조가 말해준다.
    # 예: "'AI 지각생' ... 승부수 던졌지만...11% 하락" -> 회사어1/비판어1 이지만 명백히 비판.
    if c > 0:
        th = RULES.get("thresholds", {})
        if tone_score <= th.get("critical", -1.5):
            return "비판프레임"
        if tone_score >= th.get("favorable", 1.5):
            return "회사프레임"
        return "혼합"
    return "중립전달"


def _extract_messages(title: str) -> list[str]:
    text = _strip_prefixes(title)
    messages: list[str] = []
    for item in RULES.get("messages", {}).get("items", []):
        keywords = item.get("keywords", [])
        if any(keyword and keyword in text for keyword in keywords):
            messages.append(item.get("key", ""))
    return [message for message in messages if message]


def now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone(KST)


def iso_kst(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).isoformat(timespec="seconds")


def run_curl(url: str) -> str:
    proc = subprocess.run(
        ["curl", "-sL", "--max-time", "20", "-A", UA, url],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"curl failed with code {proc.returncode}")
    return proc.stdout


def fetch_rss(url: str) -> str:
    return run_curl(url)


def fetch_json(url: str) -> Any:
    return json.loads(run_curl(url))


def normalize_title(title: str) -> str:
    return re.sub(r"[\s\W_]+", "", title, flags=re.UNICODE).casefold()


def sha1_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def classify_outlet(outlet: str) -> str:
    # 카카오 뉴스룸 보도자료는 언론 보도가 아니므로 별도 분리한다.
    # 참고: "매체 미상" 그룹(다음/네이트 등)은 재배포 중복이 아니다.
    # 구글뉴스가 포털 사본만 색인해 원 매체명을 못 준 실제 고유 기사다.
    # 집계에는 포함하고, 매체별 표에서만 제외한다.
    if outlet.strip() in ("카카오", "카카오 뉴스룸", "Kakao"):
        return "회사발표"
    for group, names in OUTLET_GROUPS:
        for name in names:
            if name and name in outlet:
                return group
    return "기타"


def score_tone(title: str) -> dict[str, Any]:
    tone, tone_score, hits, signals = _classify_tone_axis(title)
    clean_title = _strip_prefixes(title)
    return {
        "tone": tone,
        "toneScore": tone_score,
        "frame": _classify_frame_axis(clean_title, tone_score),
        "hits": hits,
        "signals": signals,
        "messages": _extract_messages(clean_title),
    }


def classify_frames(title: str) -> list[str]:
    text = _strip_prefixes(title)
    frames = [name for name, keywords in FRAME_KEYWORDS.items() if any(keyword in text for keyword in keywords)]
    return frames or ["기타"]


def parse_pubdate(value: str) -> str:
    dt = parsedate_to_datetime(value)
    return iso_kst(dt)


def extract_title_outlet(raw_title: str, source_text: str | None) -> tuple[str, str]:
    title = html.unescape(raw_title or "").strip()
    outlet = html.unescape((source_text or "").strip())
    if " - " in title:
        base, suffix = title.rsplit(" - ", 1)
        title = base.strip()
        if not outlet:
            outlet = suffix.strip()
    return title, outlet


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        raw_title = item.findtext("title", default="")
        link = html.unescape(item.findtext("link", default="").strip())
        pub_date = item.findtext("pubDate", default="").strip()
        source_el = item.find("source")
        source_text = source_el.text if source_el is not None else ""
        title, outlet = extract_title_outlet(raw_title, source_text)
        if not title or not link:
            continue
        if is_offtopic(title, outlet or ""):
            continue
        published_at = parse_pubdate(pub_date) if pub_date else iso_kst(now_kst())
        tone_result = score_tone(title)
        frames = classify_frames(title)
        items.append(
            {
                "id": sha1_id(link),
                "title": title,
                "outlet": outlet or "기타",
                "outletGroup": classify_outlet(outlet or ""),
                "url": link,
                "publishedAt": published_at,
                "tone": tone_result["tone"],
                "toneScore": tone_result["toneScore"],
                "frame": tone_result["frame"],
                "messages": tone_result["messages"],
                "signals": tone_result["signals"],
                "frames": frames,
                "hits": tone_result["hits"],
                "_titleNorm": normalize_title(title),
            }
        )
    return items


def load_existing() -> dict[str, Any]:
    if not ARTICLES_JSON.exists():
        return {
            "generatedAt": iso_kst(now_kst()),
            "runCount": 0,
            "price": None,
            "articles": [],
        }
    try:
        with ARTICLES_JSON.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {
            "generatedAt": iso_kst(now_kst()),
            "runCount": 0,
            "price": None,
            "articles": [],
        }
    if not isinstance(data, dict):
        data = {}
    data.setdefault("generatedAt", iso_kst(now_kst()))
    data.setdefault("runCount", 0)
    data.setdefault("price", None)
    data.setdefault("articles", [])
    if not isinstance(data["articles"], list):
        data["articles"] = []
    return data


def build_lookups(articles: Iterable[dict[str, Any]]) -> tuple[set[str], set[str]]:
    ids: set[str] = set()
    norms: set[str] = set()
    for article in articles:
        if isinstance(article, dict):
            aid = article.get("id")
            if isinstance(aid, str):
                ids.add(aid)
            title = article.get("title")
            if isinstance(title, str):
                norms.add(normalize_title(title))
    return ids, norms


def fetch_price() -> dict[str, Any] | None:
    url = "https://polling.finance.naver.com/api/realtime/domestic/stock/035720"
    try:
        data = fetch_json(url)
    except Exception:
        return None

    def find_first(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            if key in obj:
                return obj[key]
            for value in obj.values():
                found = find_first(value, key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = find_first(value, key)
                if found is not None:
                    return found
        return None

    close_price = find_first(data, "closePrice")
    pct = find_first(data, "fluctuationsRatio")
    if close_price is None and pct is None:
        return None

    try:
        if isinstance(close_price, str):
            close_price_str = close_price
        elif close_price is None:
            close_price_str = ""
        else:
            close_price_str = f"{int(float(close_price)):,}"
    except Exception:
        close_price_str = str(close_price) if close_price is not None else ""

    try:
        pct_num = float(pct)
        pct_str = f"{pct_num:+.2f}"
    except Exception:
        pct_str = str(pct) if pct is not None else ""

    return {
        "price": close_price_str,
        "pct": pct_str,
        "at": iso_kst(now_kst()),
    }


def slugify(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", text, flags=re.UNICODE).strip("-")
    return slug or "article"



def write_outputs(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=1)
    ARTICLES_JSON.write_text(payload + "\n", encoding="utf-8")
    ARTICLES_JS.write_text(f"window.KMW_DATA = {payload};\n", encoding="utf-8")


def append_log(line: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    existing = load_existing()
    existing_articles = existing.get("articles", [])
    existing_ids, existing_norms = build_lookups(existing_articles)

    collected: list[dict[str, Any]] = []
    for query in QUERIES:
        url = "https://news.google.com/rss/search?q={q}&hl=ko&gl=KR&ceid=KR:ko".format(q=quote(query))
        try:
            xml_text = fetch_rss(url)
            for _art in parse_rss(xml_text):
                if _art.get("publishedAt", "") >= CUTOFF_KST:
                    collected.append(_art)
        except Exception:
            continue

    new_articles: list[dict[str, Any]] = []
    current_time = iso_kst(now_kst())
    seen_new_ids: set[str] = set()
    seen_new_norms: set[str] = set()
    for article in collected:
        aid = article["id"]
        title_norm = article["_titleNorm"]
        if aid in existing_ids or title_norm in existing_norms:
            continue
        if aid in seen_new_ids or title_norm in seen_new_norms:
            continue
        article.pop("_titleNorm", None)
        article["firstSeenAt"] = current_time
        new_articles.append(article)
        seen_new_ids.add(aid)
        seen_new_norms.add(title_norm)


    # 규칙(rules.json)을 고치면 기존에 쌓인 기사도 새 규칙으로 다시 채점해야 한다.
    # 안 그러면 대시보드에 옛 판정과 새 판정이 섞여 통계가 어긋난다.
    for article in existing_articles:
        if not isinstance(article, dict) or not article.get("title"):
            continue
        r = score_tone(article["title"])
        article["tone"] = r["tone"]
        article["toneScore"] = r["toneScore"]
        article["hits"] = r["hits"]
        article["frame"] = r["frame"]
        article["messages"] = r["messages"]
        article["signals"] = r["signals"]
        article["frames"] = classify_frames(article["title"])

    for article in existing_articles:
        if isinstance(article, dict) and "firstSeenAt" not in article:
            article["firstSeenAt"] = existing.get("generatedAt", current_time)

    merged_articles = list(existing_articles) + new_articles

    price = None
    try:
        price = fetch_price()
    except Exception:
        price = None
    if price is None:
        price = existing.get("price")

    tone_counts = {"긍정": 0, "중립": 0, "우려": 0}
    for article in new_articles:
        tone = article.get("tone")
        if tone in tone_counts:
            tone_counts[tone] += 1

    data = {
        "generatedAt": current_time,
        "runCount": int(existing.get("runCount", 0)) + 1,
        "price": price,
        "articles": merged_articles,
    }
    write_outputs(data)

    cum = {"긍정": 0, "중립": 0, "우려": 0}
    for a in merged_articles:
        t = a.get("tone")
        if t in cum:
            cum[t] += 1
    summary = f"[{datetime.now().strftime('%H:%M:%S')}] 신규 {len(new_articles)}건 / 누적 {len(merged_articles)}건 / 긍정 {cum['긍정']} 중립 {cum['중립']} 우려 {cum['우려']}"
    print(summary)
    append_log(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
