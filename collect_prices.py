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
