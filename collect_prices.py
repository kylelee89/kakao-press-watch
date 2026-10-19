# 네이버 금융에서 카카오 그룹·피어·지수의 일별 종가를 수집한다.
# 인적분할 발표 전 기준일 대비 수익률과 함께 JSON 및 브라우저용 JS로 저장한다.
# 전체 수집 실패 시 기존 파일을 보존하고 비정상 종료한다.
from __future__ import annotations

import ast
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
KST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
START_DATE = "20260814"
BASE_DATE = "2026-08-20"
ANN_DATE = "2026-08-21"
SYMBOLS = [
    ("kakao", "카카오", "035720", "group"),
    ("kbank", "카카오뱅크", "323410", "group"),
    ("kpay", "카카오페이", "377300", "group"),
    ("sm", "에스엠", "041510", "group"),
    ("kgames", "카카오게임즈", "293490", "group"),
    ("naver", "NAVER", "035420", "peer"),
    ("krafton", "크래프톤", "259960", "peer"),
    ("nc", "엔씨소프트", "036570", "peer"),
    ("netmarble", "넷마블", "251270", "peer"),
    ("nhn", "NHN", "181710", "peer"),
    ("hybe", "하이브", "352820", "peer"),
    ("pearl", "펄어비스", "263750", "peer"),
    ("KOSPI", "KOSPI", "KOSPI", "index"),
    ("KOSDAQ", "KOSDAQ", "KOSDAQ", "index"),
]

# 코스피에서 삼성전자(보통주/우선주)와 SK하이닉스를 뺀 지수.
# 2026-10 기준 이 세 종목이 코스피 시가총액의 절반 가까이를 차지해서,
# 코스피와만 비교하면 "반도체가 끌어올린 지수"와 비교하는 셈이 된다.
EX_ID = "KOSPI_EXSEMI"
EX_NAME = "코스피(삼전/하이닉스 제외)"
EX_CODES = ("005930", "005935", "000660")
RANK_URL = "https://m.stock.naver.com/api/stocks/marketValue/KOSPI?page={page}&pageSize=100"


def run_curl(url: str) -> str:
    proc = subprocess.run(
        ["curl", "-fsSL", "--max-time", "20", "-A", UA, url],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    if proc.returncode:
        # 환경별 프록시 주소 등이 포함될 수 있는 stderr는 저장하지 않는다.
        raise RuntimeError(f"curl failed with code {proc.returncode}")
    return proc.stdout


def parse_closes(response: str, end_date: str) -> dict[str, int | float]:
    try:
        rows = ast.literal_eval(response.strip())
    except (SyntaxError, ValueError) as exc:
        raise ValueError("네이버 응답 형식 오류") from exc
    if (
        not isinstance(rows, list)
        or not rows
        or not isinstance(rows[0], list)
        or len(rows[0]) < 5
        or rows[0][0] != "날짜"
        or rows[0][4] != "종가"
    ):
        raise ValueError("네이버 응답 헤더 오류")

    closes: dict[str, int | float] = {}
    for row in rows[1:]:
        if not isinstance(row, list) or len(row) < 5:
            raise ValueError("네이버 가격 행 형식 오류")
        date, close = row[0], row[4]
        if not isinstance(date, str) or len(date) != 8 or not date.isdigit():
            raise ValueError("네이버 거래일 형식 오류")
        day = datetime.strptime(date, "%Y%m%d").date().isoformat()
        if type(close) not in (int, float) or not math.isfinite(close) or close < 0:
            raise ValueError("네이버 종가 값 오류")
        if START_DATE <= date <= end_date:
            if day in closes and closes[day] != close:
                raise ValueError("네이버 거래일 중복 값 오류")
            closes[day] = close
    if not closes:
        raise ValueError("요청 기간의 종가 데이터 없음")
    return closes


def fetch_kospi_anchor() -> tuple[float, dict[str, float]]:
    """코스피 전체 시가총액과 제외 종목의 상장주식수를 지금 시점 기준으로 구한다.

    네이버 시가총액 순위 API 는 ETF/ETN 까지 섞어 주므로 `stockEndType == "stock"` 만
    더해야 지수 시가총액이 된다(2026-10 기준 942종목 4,642조. ETF 1,176종목 419조는 제외).
    상장주식수는 시가총액/현재가로 역산한다. 두 달 창에서 주식수는 사실상 고정이라
    과거 종가에 그대로 곱해 그날의 시가총액을 복원할 수 있다.
    """
    rows: list[dict] = []
    page = 1
    while page <= 40:
        if page > 1:
            time.sleep(0.2)
        payload = json.loads(run_curl(RANK_URL.format(page=page)))
        chunk = payload.get("stocks") or []
        rows.extend(chunk)
        if not chunk or len(rows) >= int(payload.get("totalCount") or 0):
            break
        page += 1
    if not rows:
        raise ValueError("코스피 시가총액 순위 응답 없음")

    total = 0.0
    shares: dict[str, float] = {}
    for row in rows:
        if row.get("stockEndType") != "stock":
            continue
        raw = row.get("marketValueRaw")
        if raw is None:
            continue
        cap = float(raw)
        if not math.isfinite(cap) or cap < 0:
            raise ValueError("시가총액 값 오류")
        total += cap
        code = row.get("itemCode")
        if code in EX_CODES:
            price = float(str(row.get("closePrice", "")).replace(",", "") or 0)
            if price <= 0:
                raise ValueError(f"{code} 현재가 오류")
            shares[code] = cap / price
    missing = [c for c in EX_CODES if c not in shares]
    if missing:
        raise ValueError(f"제외 종목 미발견: {', '.join(missing)}")
    if total <= 0:
        raise ValueError("코스피 시가총액 합계 오류")
    return total, shares


def build_ex_index(dates: list[str], kospi: dict[str, int | float], end_date: str):
    """코스피 시가총액에서 세 종목을 빼고, 기준일에 코스피와 같은 값이 되도록 환산한다.

    과거 시가총액은 `코스피 전체 시총(현재) x 코스피지수(그날)/코스피지수(현재)` 로 되돌린다.
    이 창(두 달) 안의 신규상장/상폐는 무시한다 — 지수 대비 효과가 1% 미만이다.
    """
    if not kospi:
        raise ValueError("코스피 지수 데이터 없음")
    total_now, shares = fetch_kospi_anchor()
    ref_day = max(kospi)
    ref_level = kospi[ref_day]
    if not ref_level:
        raise ValueError("코스피 기준 지수 오류")

    closes: dict[str, float] = {}
    for code in EX_CODES:
        query = urlencode({
            "symbol": code, "requestType": 1, "startTime": START_DATE,
            "endTime": end_date, "timeframe": "day",
        })
        time.sleep(0.3)
        closes[code] = parse_closes(run_curl(f"https://api.finance.naver.com/siseJson.naver?{query}"), end_date)

    raw: dict[str, float] = {}
    for day in dates:
        level = kospi.get(day)
        if level is None or any(closes[c].get(day) is None for c in EX_CODES):
            continue
        market = total_now * (level / ref_level)
        value = market - sum(shares[c] * closes[c][day] for c in EX_CODES)
        if value > 0:
            raw[day] = value
    if not raw:
        raise ValueError("제외 지수 계산 가능한 날짜 없음")

    # 기준일(없으면 가장 이른 날)에 코스피와 같은 포인트가 되도록 맞춘다.
    pin = BASE_DATE if BASE_DATE in raw and BASE_DATE in kospi else min(raw)
    scale = kospi[pin] / raw[pin]
    return {day: round(value * scale, 2) for day, value in raw.items()}


def pct(value: int | float | None, base: int | float | None) -> float | None:
    if value is None or base is None or base == 0:
        return None
    return round((value / base - 1) * 100, 2)


def write_outputs(data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    outputs = [
        (DATA_DIR / "prices.json", payload + "\n"),
        (DATA_DIR / "prices.js", f"window.KMW_PRICES = {payload};\n"),
    ]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    staged: list[tuple[Path, Path]] = []
    try:
        for target, content in outputs:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=DATA_DIR, delete=False,
                prefix=f".{target.name}.", suffix=".tmp",
            ) as handle:
                temporary = Path(handle.name)
                staged.append((temporary, target))
                handle.write(content)
        for temporary, target in staged:
            os.replace(temporary, target)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)


def main() -> int:
    end_date = datetime.now(KST).strftime("%Y%m%d")
    collected = []
    dates_set: set[str] = set()
    failed = 0
    for index, (series_id, name, code, grp) in enumerate(SYMBOLS):
        if index:
            time.sleep(0.3)
        closes = {}
        error = None
        query = urlencode({
            "symbol": code, "requestType": 1, "startTime": START_DATE,
            "endTime": end_date, "timeframe": "day",
        })
        try:
            closes = parse_closes(run_curl(f"https://api.finance.naver.com/siseJson.naver?{query}"), end_date)
        except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
            error = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
            failed += 1
            print(f"{name}: {error}", file=sys.stderr)
        dates_set.update(closes)
        collected.append((series_id, name, code, grp, closes, error))

    kospi_closes = next((c for sid, _n, _c, _g, c, _e in collected if sid == "KOSPI"), {})
    ex_closes: dict[str, float] = {}
    ex_error = None
    try:
        ex_closes = build_ex_index(sorted(dates_set), kospi_closes, end_date)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        ex_error = str(exc) if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__
        failed += 1
        print(f"{EX_NAME}: {ex_error}", file=sys.stderr)
    collected.append((EX_ID, EX_NAME, "KOSPI", "index", ex_closes, ex_error))

    dates = sorted(dates_set)
    result = 0
    if not dates:
        print("유효한 종가 데이터가 없어 기존 파일을 보존합니다.", file=sys.stderr)
        result = 1
    else:
        series = []
        for series_id, name, code, grp, closes, error in collected:
            base = closes.get(BASE_DATE)
            last = closes[max(closes)] if closes else None
            series.append({
                "id": series_id, "name": name, "code": code, "grp": grp,
                "closes": [closes.get(day) for day in dates],
                "base": base, "last": last, "cumPct": pct(last, base),
                "annPct": pct(closes.get(ANN_DATE), base), "error": error,
            })
        try:
            write_outputs({
                "generatedAt": datetime.now(KST).isoformat(timespec="seconds"),
                "baseDate": BASE_DATE, "annDate": ANN_DATE,
                "dates": dates, "series": series,
            })
        except OSError as exc:
            print(f"출력 저장 실패: {type(exc).__name__}", file=sys.stderr)
            result = 1
    print(f"[{datetime.now(KST):%H:%M:%S}] 종목 {len(SYMBOLS)}개 / 거래일 {len(dates)}일 / 실패 {failed}건")
    return result


if __name__ == "__main__":
    sys.exit(main())
