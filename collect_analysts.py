#!/usr/bin/env python3
"""카카오(035720) 증권사 리포트 자동 수집기.

FnGuide 기업모니터가 화면을 그릴 때 쓰는 내부 ajax 엔드포인트를 그대로 호출한다.
브라우저 없이 curl 만으로 투자의견/목표주가/직전 대비 액션/리포트 논지 불릿까지 다 받아온다.

  https://comp.wisereport.co.kr/company/ajax/c1080001_data.aspx?cmp_cd=035720&perPage=N&curPage=1

외국계(HSBC 등)는 이 엔드포인트에 안 잡힌다. Investing.com 이 JS 렌더링이라 자동화가 안 되므로
data/analysts_foreign.json 에 수기로 두고 여기서 병합만 한다.

주의: python.org 빌드는 CA 번들이 없어 SSL 검증이 깨진다. HTTP 는 전부 curl 서브프로세스로 우회한다.
"""
import json, re, html, subprocess, sys, pathlib
from datetime import datetime, timezone, timedelta

BASE = pathlib.Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
CUTOFF = "26/08/21"            # 인적분할 공시일. 이 날짜 이상만 "분할 반응 리포트"로 본다.
PRICE_REF_LABEL = "8/21 종가"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
URL = ("https://comp.wisereport.co.kr/company/ajax/c1080001_data.aspx"
       "?cmp_cd=035720&perPage=100&curPage=1")


def fetch():
    r = subprocess.run(
        ["curl", "-s", "--max-time", "30", "-A", UA,
         "-H", "Referer: https://comp.wisereport.co.kr/company/c1080001.aspx?cmp_cd=035720", URL],
        capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError(f"FnGuide 응답 없음 (curl rc={r.returncode})")
    return json.loads(r.stdout)


def strip_tags(s):
    """COMMENT2 는 <span>▶</span> 로 구분된 불릿 HTML 이다. 평문 리스트로 바꾼다."""
    if not s:
        return []
    s = html.unescape(re.sub(r"<br\s*/?>", "\n", s))
    s = re.sub(r"<[^>]+>", "", s)
    parts = [re.sub(r"\s+", " ", p).strip(" .·") for p in s.split("▶")]
    return [p for p in parts if len(p) > 3]


def no_middot(s):
    """장표/문서에 가운뎃점을 쓰지 않는다는 규칙에 맞춰 치환한다."""
    return (s.replace("테크핀·", "테크핀/").replace("광고·", "광고와 ")
             .replace("의사결정 및 자원배분", "의사결정과 자원배분").replace("·", ", "))


def to_int(v):
    if not v:
        return None
    d = re.sub(r"[^0-9]", "", str(v))
    return int(d) if d else None


def is_english(title):
    """같은 리포트의 영문판이 별건으로 들어온다. 한글이 거의 없으면 영문판으로 보고 버린다."""
    return len(re.findall(r"[가-힣]", title or "")) < 2


def build():
    data = fetch()
    rows = data.get("lists") or []
    if not rows:
        raise RuntimeError("리포트 목록이 비어 있다")

    recent, older = [], []
    for r in rows:
        if is_english(r.get("RPT_TITLE")):
            continue
        (recent if (r.get("ANL_DT") or "") >= CUTOFF else older).append(r)

    # 직전 목표가/의견: 같은 증권사의 컷오프 이전 최신 리포트에서 가져온다.
    prev = {}
    for r in sorted(older, key=lambda x: x.get("ANL_DT") or ""):
        prev[r.get("BRK_NM_SHORT_KOR")] = r

    reports, seen = [], set()
    for r in sorted(recent, key=lambda x: -(to_int(x.get("TARGET_PRC")) or 0)):
        house = r.get("BRK_NM_SHORT_KOR") or r.get("BRK_NM_KOR") or "미상"
        key = (house, r.get("ANL_DT"))
        if key in seen:          # 같은 날 같은 증권사 중복(영문판 잔여 등) 제거
            continue
        seen.add(key)
        p = prev.get(house)
        bullets = strip_tags(r.get("COMMENT2"))
        reports.append({
            "date": r.get("ANL_DT", "")[3:],           # 26/08/24 -> 08/24
            "region": "국내",
            "house": house,
            "analyst": r.get("ANL_NM_KOR") or "-",
            "rating": r.get("RECOMM"),
            "ratingPrev": (p or {}).get("RECOMM"),
            "target": to_int(r.get("TARGET_PRC")),
            "targetPrev": to_int((p or {}).get("TARGET_PRC")),
            "title": no_middot(r.get("RPT_TITLE") or ""),
            "point": no_middot(". ".join(bullets[:3])),
            "extra": no_middot(" / ".join(
                x for x in [r.get("RECOMM_ACTION_TYP_NM"), r.get("PRC_ACTION_TYP_NM")]
                if x and x not in ("변동없음", "투자의견 없음", "목표주가 없음"))),
        })

    # 컷오프 이전에만 리포트가 있는 곳 = 아직 분할 리포트를 안 낸 증권사.
    # 단 2분기 실적 시즌(8월 초) 이후로 갱신이 있는 곳만 "현재 커버리지"로 본다.
    # 안 그러면 2월에 마지막 리포트를 낸 곳까지 "미제출"로 잡혀 숫자가 부풀려진다.
    ACTIVE_SINCE = "26/08/01"
    published = {x["house"] for x in reports}
    pending = [{"house": h, "analyst": p.get("ANL_NM_KOR") or "-",
                "targetPrev": to_int(p.get("TARGET_PRC")), "lastDate": p.get("ANL_DT", "")[3:]}
               for h, p in sorted(prev.items(), key=lambda kv: -(to_int(kv[1].get("TARGET_PRC")) or 0))
               if h not in published
               and h != "해당기업"                       # 회사 IR 자료이지 증권사가 아니다
               and (p.get("ANL_DT") or "") >= ACTIVE_SINCE
               and to_int(p.get("TARGET_PRC"))]

    out = {
        "asOf": datetime.now(KST).replace(microsecond=0).isoformat(),
        "source": "FnGuide 기업모니터(국내, 자동수집) / 리포트 원문 PDF(외국계, 수기)",
        "note": f"20{CUTOFF.replace('/', '-')} 인적분할 공시 이후 발간된 리포트만. 국내 직전 목표가는 공시 전 마지막 리포트 기준, 외국계는 리포트에 표기된 값 기준.",
        "priceRef": {"label": PRICE_REF_LABEL, "value": (rows[0].get("CLOSE_PRC") or "")},
        "reports": reports,
        "pending": pending,
    }

    # FnGuide 요약이 부실한 국내 리포트는 원문 PDF 기준 수기 요약으로 덮어쓴다.
    op = BASE / "data" / "analysts_override.json"
    if op.exists():
        ov = json.loads(op.read_text(encoding="utf-8")).get("points", {})
        for r in reports:
            o = ov.get(r["house"])
            if o:
                r.update({k: v for k, v in o.items() if k in ("point", "extra", "title")})

    # 외국계는 수기 파일에서 병합한다(자동수집 불가 소스라 덮어쓰지 않는다).
    fp = BASE / "data" / "analysts_foreign.json"
    if fp.exists():
        f = json.loads(fp.read_text(encoding="utf-8"))
        out["reports"] = f.get("reports", []) + out["reports"]
        out["reports"].sort(key=lambda x: -(x.get("target") or 0))
        out["foreignPending"] = f.get("foreignPending", [])
    return out


def chain_prev_targets(reports):
    """같은 하우스가 두 번 이상 냈으면 직전 '리포트'의 목표가를 targetPrev 로 쓴다.

    FnGuide 의 TARGET_PRC / RECOMM 은 하우스별로 '분할 발표 전 원래 값'을 계속 들고 있어서,
    두 번째 리포트부터 직전 대비 변동이 과장돼 보인다(키움 10/07 이 70,000 이 아니라
    110,000 대비로 찍혀 -60% 로 표시되던 문제, 삼성 10/14 가 08/24 에 이미 끝난
    BUY->HOLD 강등을 다시 한 것처럼 보이던 문제). 각 하우스의 첫 리포트는 FnGuide 값을
    그대로 두고, 두 번째부터만 우리 데이터 안의 직전 리포트 값으로 바꾼다.
    """
    by_house = {}
    for r in reports:
        by_house.setdefault(r.get("house", ""), []).append(r)
    for house, group in by_house.items():
        group.sort(key=lambda r: str(r.get("date", "")))
        # 덮어쓰기 전에 '분할 발표 전' 기준값을 떼어 둔다. FnGuide 가 첫 리포트에 붙여 보낸
        # 직전 값이 곧 발표 전 스탠스라, 이걸 살려둬야 "발표 이후 누적으로 얼마나 꺾였나"를
        # 따로 보여줄 수 있다(리포트 단위 변화만 남기면 8월 강등이 화면에서 사라진다).
        base_rating = group[0].get("ratingPrev")
        base_target = group[0].get("targetPrev")
        for r in group:
            r["ratingBase"] = base_rating
            r["targetBase"] = base_target
        if len(group) < 2:
            continue
        for prev, cur in zip(group, group[1:]):
            if prev.get("target") is not None:
                cur["targetPrev"] = prev["target"]
            # 의견도 목표가와 같이 직전 '리포트' 기준으로 덮어쓴다. None 일 때만 채우면
            # FnGuide 가 들고 있는 분할 전 의견이 그대로 남아 이미 끝난 강등이 반복돼 보인다.
            if prev.get("rating"):
                cur["ratingPrev"] = prev["rating"]
    return reports


def main():
    try:
        out = build()
    except Exception as e:
        print(f"[증권사] 수집 실패: {e}", file=sys.stderr)
        return 1

    chain_prev_targets(out["reports"])

    d = BASE / "data"
    prev_n = 0
    if (d / "analysts.json").exists():
        try:
            prev_n = len(json.loads((d / "analysts.json").read_text(encoding="utf-8")).get("reports", []))
        except Exception:
            pass

    (d / "analysts.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    (d / "analysts.js").write_text(
        "window.KMW_ANALYSTS = " + json.dumps(out, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")

    n = len(out["reports"])
    dom = sum(1 for x in out["reports"] if x["region"] == "국내")
    cut = sum(1 for x in out["reports"] if x.get("target") and x.get("targetPrev") and x["target"] < x["targetPrev"])
    delta = f" (신규 {n - prev_n}건)" if n > prev_n else ""
    print(f"[증권사] 리포트 {n}건{delta} / 국내 {dom} 외국계 {n-dom} / 목표가 하향 {cut} / 미제출 {len(out['pending'])}곳")
    return 0


if __name__ == "__main__":
    sys.exit(main())
