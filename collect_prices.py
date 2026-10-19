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
import xml.etree.ElementTree as ET
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
# 공공데이터포털(금융위원회) 주식시세정보. 거래소 원본이라 코스피 상장시가총액과
# 종목별 시가총액/상장주식수를 그대로 준다. 키는 환경변수 DATA_GO_KR_KEY
# (로컬은 ~/.config/kmw/datagokr.key, Actions 는 저장소 시크릿).
GOV_BASE = "https://apis.data.go.kr/1160100/service"


def _gov_key() -> str:
    """환경변수(Actions 시크릿) 우선, 없으면 로컬 키 파일."""
    env = os.environ.get("DATA_GO_KR_KEY", "").strip()
    if env:
        return env
    local = Path.home() / ".config/kmw/datagokr.key"
    try:
        return local.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


GOV_KEY = _gov_key()
DAILY_PATH = DATA_DIR / "kospi_daily.json"


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


def fetch_official(day: str):
    """해당 거래일의 (코스피 상장시총, 제외 종목별 시총) 을 거래소 공식값으로 받는다.

    휴장일이거나 아직 발표 전이면 None 을 돌려준다(장중에는 당일치가 아직 없다).
    키가 없으면 조용히 포기한다 — 그 경우 직전 행에서 굴려 쓴다.
    """
    if not GOV_KEY:
        return None
    basDt = day.replace("-", "")

    def items(url: str):
        try:
            return ET.fromstring(run_curl(url)).findall(".//item")
        except (RuntimeError, ET.ParseError, OSError, subprocess.TimeoutExpired):
            return []

    found = items(f"{GOV_BASE}/GetMarketIndexInfoService/getStockMarketIndex"
                  f"?serviceKey={GOV_KEY}&idxNm=%EC%BD%94%EC%8A%A4%ED%94%BC&basDt={basDt}&numOfRows=20")
    total = next((it.findtext("lstgMrktTotAmt") for it in found
                  if it.findtext("idxNm") == "코스피" and it.findtext("lstgMrktTotAmt")), None)
    if not total:
        return None

    caps: dict[str, float] = {}
    for code in EX_CODES:
        time.sleep(0.2)
        hit = items(f"{GOV_BASE}/GetStockSecuritiesInfoService/getStockPriceInfo"
                    f"?serviceKey={GOV_KEY}&likeSrtnCd={code}&basDt={basDt}&numOfRows=10")
        value = next((it.findtext("mrktTotAmt") for it in hit
                      if it.findtext("srtnCd") == code and it.findtext("mrktTotAmt")), None)
        if not value:
            return None
        caps[code] = float(value)
    return float(total), caps


def load_daily() -> dict:
    if not DAILY_PATH.exists():
        return {}
    try:
        data = json.loads(DAILY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = data.get("rows")
    return rows if isinstance(rows, dict) else {}


def save_daily(rows: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": ("코스피 전체 시총(t)과 제외 종목 시총(m)의 일별 실측 기록. "
                 "approx=true 는 기록을 시작하기 전 구간을 지수비율로 되돌린 근사치이고, "
                 "한 번 만든 뒤에는 고치지 않는다. final=true 는 장 마감 뒤 확정값."),
        "unit": "원",
        "rows": dict(sorted(rows.items())),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=DATA_DIR,
                                     delete=False, prefix=".kospi_daily.", suffix=".tmp") as handle:
        temporary = Path(handle.name)
        handle.write(text + "\n")
    os.replace(temporary, DAILY_PATH)


def build_ex_index(dates: list[str], kospi: dict[str, int | float], end_date: str):
    """코스피에서 삼성전자(보통주/우선주)와 SK하이닉스를 뺀 지수를 만든다.

    **수준이 아니라 수익률을 이어 붙인다.** 날마다 그날 실측을 다시 기준으로 삼고 전일 대비
    변화만 곱해 나간다. 그래야 신규상장/상장폐지/유상증자가 섞여도 지수가 오염되지 않는다:

      전체시총(t) 은 쓰지 않는다 -> `전체시총(t-1) x (1 + 코스피 등락률)`
        신규상장은 시총만 늘리고 지수는 안 올린다. 지수 등락률을 쓰면 거래소가 해 주는
        기준시가총액 조정을 그대로 물려받아 그 효과가 빠진다.
      제외분(t) 은 시총이 아니라 `종목별시총(t-1) x 주가(t)/주가(t-1)`
        시총을 그대로 쓰면 유상증자/무상증자가 주가 하락 없이 시총만 늘려 왜곡된다.

    일별 실측은 `data/kospi_daily.json` 에 쌓는다. 공공데이터포털 공식값이 들어온 행은
    `src:"krx"`, `final:true` 로 두고 다시 받지 않는다. 장중이라 당일 공식값이 아직 없으면
    직전 행에서 굴린 임시 행(`src:"roll"`)을 쓰고, 다음 실행에서 공식값으로 교체한다.
    """
    if not kospi:
        raise ValueError("코스피 지수 데이터 없음")
    rows = load_daily()
    changed = False

    closes: dict[str, dict[str, int | float]] = {}
    for code in EX_CODES:
        query = urlencode({"symbol": code, "requestType": 1,
                           "startTime": START_DATE, "endTime": end_date, "timeframe": "day"})
        time.sleep(0.3)
        closes[code] = parse_closes(run_curl(f"https://api.finance.naver.com/siseJson.naver?{query}"), end_date)

    # 1) 공식값이 아직 없는 날만 받아 온다(확정된 행은 건드리지 않는다).
    for day in dates:
        if not kospi.get(day) or (rows.get(day, {}).get("src") == "krx"):
            continue
        official = fetch_official(day)
        if official:
            total, caps = official
            rows[day] = {"k": kospi[day], "t": round(total),
                         "m": {c: round(caps[c]) for c in EX_CODES},
                         "src": "krx", "final": True}
            changed = True

    # 2) 공식값이 아직 안 나온 날(보통 당일)은 직전 확정 행에서 굴려 둔다.
    for prev, cur in zip(dates, dates[1:]):
        if cur in rows and rows[cur].get("src") == "krx":
            continue
        base = rows.get(prev)
        if not base or not kospi.get(cur) or not kospi.get(prev):
            continue
        moved = {}
        for code in EX_CODES:
            p0, p1 = closes[code].get(prev), closes[code].get(cur)
            moved[code] = round(base["m"][code] * (p1 / p0)) if p0 and p1 else base["m"][code]
        rolled = {"k": kospi[cur], "t": round(base["t"] * (kospi[cur] / kospi[prev])),
                  "m": moved, "src": "roll", "final": False}
        if rows.get(cur) != rolled:
            rows[cur] = rolled
            changed = True

    if changed:
        save_daily(rows)

    # 3) 수익률 체인.
    usable = [d for d in dates if d in rows and kospi.get(d)]
    if len(usable) < 2:
        raise ValueError("제외 지수 계산 가능한 날짜 부족")
    pin = BASE_DATE if BASE_DATE in usable else usable[0]
    level: dict[str, float] = {usable[0]: float(kospi[usable[0]])}
    for prev, cur in zip(usable, usable[1:]):
        a = rows[prev]
        rest_prev = a["t"] - sum(a["m"].values())
        moved = 0.0
        for code in EX_CODES:
            p0, p1 = closes[code].get(prev), closes[code].get(cur)
            moved += a["m"][code] * (p1 / p0) if p0 and p1 else rows[cur]["m"][code]
        rest_cur = a["t"] * (kospi[cur] / kospi[prev]) - moved
        level[cur] = level[prev] * (rest_cur / rest_prev) if rest_prev > 0 and rest_cur > 0 else level[prev]
    scale = kospi[pin] / level[pin]
    return {day: round(value * scale, 2) for day, value in level.items()}


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
