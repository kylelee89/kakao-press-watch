#!/usr/bin/env python3
"""카카오 국내 리포트 원문을 무인증 공개 소스에서 순서대로 수집한다.

수기 논지와 이미 확보한 전문은 보존하고, 실패 및 요약 건은 매 실행 재시도한다.
Python CA 번들 문제를 피하려 HTTP는 시스템 curl로 실행하며 TLS 검증과
실행 환경의 프록시 설정은 그대로 사용한다.
"""
import json, re, html, subprocess, sys, pathlib, tempfile
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.parse import urlencode

BASE = pathlib.Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
YEAR = 2026
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36")
HANKYUNG = "http://consensus.hankyung.com/analysis/"
NAVER = "https://m.stock.naver.com/api/research/stock/035720?page=1&pageSize=40"
BROKER_APIS = {"현대차": "https://mprod.hmsec.com/api/v1/searchResearch"}
TELEGRAM_HANDLES = {"현대차": "ssi_game"}
SOURCES = ("hankyung", "naver", "broker", "telegram")


def now():
    return datetime.now(KST).replace(microsecond=0).isoformat()


def curl(url, payload=None, referer=None):
    # curl 자체 CA 저장소를 쓰되 -k, proxy 재설정, 쿠키/인증은 사용하지 않는다.
    with tempfile.TemporaryDirectory(prefix="report-fetch-") as tmp:
        body = pathlib.Path(tmp) / "body"
        headers = pathlib.Path(tmp) / "headers"
        cmd = ["curl", "-sS", "-L", "--max-time", "30", "-A", UA,
               "-D", str(headers), "-o", str(body), "-w", "%{http_code}"]
        if referer:
            cmd += ["-H", "Referer: " + referer]
        if payload is not None:
            cmd += ["-H", "Content-Type: application/json", "--data-binary",
                    json.dumps(payload, ensure_ascii=False)]
        result = subprocess.run(cmd + [url], capture_output=True, timeout=35)
        status = result.stdout.decode("ascii", errors="replace").strip()
        header = headers.read_text(errors="replace") if headers.exists() else ""
        # 헤더 전문에는 인프라 정보가 있을 수 있어 상태와 Squid 오류 코드만 남긴다.
        squid = re.findall(r"(?im)^X-Squid-Error:\s*(ERR_[A-Z_]+(?:\s+\d+)?)", header)
        stderr = result.stderr.decode("utf-8", errors="replace")
        connect_451 = "CONNECT tunnel failed, response 451" in stderr
        if result.returncode or not status.isdigit() or int(status) >= 400:
            detail = f"curl rc={result.returncode}, HTTP {status or '없음'}"
            reason = {5: "프록시 이름 해석 실패", 6: "호스트 이름 해석 실패",
                      7: "연결 실패", 28: "요청 시간 초과", 60: "TLS 인증서 검증 실패"}
            if result.returncode in reason:
                detail += ", " + reason[result.returncode]
            if squid:
                detail += ", X-Squid-Error: " + "; ".join(squid)
            if connect_451 or (status == "451" and squid):
                detail += ", 프록시 정책 차단 (Unavailable For Legal Reasons)"
            elif status == "451":
                detail += ", HTTP 451 (발신 주체 확인 필요)"
            if "ERR_CONNECT_FAIL" in detail:
                detail += ", 프록시 연결 실패"
            raise RuntimeError(detail)
        data = body.read_bytes()
        if not data:
            raise RuntimeError("빈 응답")
        return data


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = TextParser()
    parser.feed(str(value or ""))
    text = "".join(parser.parts).replace("\xa0", " ")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def similarity(left, right):
    left, right = (re.sub(r"\s+", "", str(x or "")) for x in (left, right))
    if not left or not right:
        return 0
    if left in right or right in left:
        return 1
    return SequenceMatcher(None, left, right).ratio()


class HankyungParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.row = None
        self.cell = None
        self.rows = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody":
            self.in_body = True
        if not self.in_body:
            return
        if tag == "tr":
            self.row = {"cells": [], "ids": []}
        if self.row is None:
            return
        if tag == "td":
            self.cell = []
        if tag == "br" and self.cell is not None:
            self.cell.append(" ")
        for value in attrs.values():
            self.row["ids"].extend(re.findall(r"downpdf\?report_idx=(\d+)", value or ""))

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.cell is not None and self.row is not None:
            self.row["cells"].append(" ".join("".join(self.cell).split()))
            self.cell = None
        if tag == "tr" and self.row is not None:
            cells = self.row["cells"]
            if len(cells) >= 5 and self.row["ids"]:
                # 목록 6번째 칸에 증권사명이 그대로 들어온다("LS증권", "대신증권").
                # 제목 유사도보다 이쪽이 훨씬 정확해서 매칭 1순위로 쓴다.
                self.rows.append({"date": cells[0], "title": cells[1],
                                  "target": cells[2], "rating": cells[3],
                                  "analyst": cells[4],
                                  "broker": cells[5] if len(cells) >= 6 else "",
                                  "id": self.row["ids"][0]})
            self.row = None
            self.cell = None
        if tag == "tbody":
            self.in_body = False


class TelegramParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.messages = []
        self.current = None
        self.depth = 0
        self.text_depth = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "div":
            self.depth += 1
        if attrs.get("data-post"):
            match = re.search(r"/(\d+)$", attrs["data-post"])
            if match:
                self.current = {"id": int(match[1]), "date": "", "parts": [], "depth": self.depth}
        if self.current is None:
            return
        if tag == "time":
            self.current["date"] = attrs.get("datetime", "")[:10]
        if tag == "div" and "tgme_widget_message_text" in attrs.get("class", "").split():
            self.text_depth = self.depth
        if tag == "br" and self.text_depth is not None:
            self.current["parts"].append("\n")

    def handle_data(self, data):
        if self.current is not None and self.text_depth is not None:
            self.current["parts"].append(data)

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if self.text_depth == self.depth:
            self.text_depth = None
        if self.current is not None and self.current["depth"] == self.depth:
            self.current["text"] = "".join(self.current.pop("parts")).replace("\xa0", " ").strip()
            self.messages.append(self.current)
            self.current = None
        self.depth -= 1


class Collector:
    def __init__(self):
        self.cache = {}

    def fetch(self, url, payload=None):
        # 같은 목록을 여러 리포트에서 공유하므로 장애 시에도 요청을 반복하지 않는다.
        key = (url, json.dumps(payload, sort_keys=True))
        if key not in self.cache:
            try:
                self.cache[key] = curl(url, payload)
            except Exception as exc:
                self.cache[key] = exc
        data = self.cache[key]
        if isinstance(data, Exception):
            raise data
        return data

    def hankyung(self, report, day):
        url = HANKYUNG + "list?" + urlencode({
            "sdate": day.isoformat(), "edate": day.isoformat(), "report_type": "CO",
            "search_text": "카카오", "pagenum": 80, "now_page": 1})
        parser = HankyungParser()
        parser.feed(self.fetch(url).decode("utf-8", errors="replace"))
        # analysts.json 의 house 는 축약형("LS", "대신")이고 한경은 정식명("LS증권")이라
        # 부분문자열로 본다. 증권사가 맞으면 제목이 달라도 같은 리포트로 인정한다.
        house = (report.get("house") or "").strip()
        same_day = [r for r in parser.rows if r["date"] == day.isoformat()]
        matches = [r for r in same_day if house and house in r.get("broker", "")]
        if not matches:
            matches = [r for r in same_day
                       if similarity(r["title"], report.get("title")) >= 0.6]
        matches.sort(key=lambda r: (house and house in r.get("broker", ""),
                                    similarity(r["title"], report.get("title")),
                                    r["analyst"] in (report.get("analyst") or "")), reverse=True)
        if not matches:
            raise RuntimeError("날짜/증권사 일치 리포트 없음")
        errors = []
        for row in matches:
            pdf_url = HANKYUNG + "downpdf?report_idx=" + row["id"]
            try:
                data = curl(pdf_url, referer=HANKYUNG + "list")
                if data[:5] != b"%PDF-":
                    raise RuntimeError("PDF 서명 불일치")
                from pypdf import PdfReader
                with tempfile.TemporaryDirectory(prefix="report-pdf-") as tmp:
                    path = pathlib.Path(tmp) / "report.pdf"
                    path.write_bytes(data)
                    # 일부 리포트의 비표준 Pages 구조는 strict 모드에서 열리지 않는다.
                    reader = PdfReader(path, strict=False)
                    text = "\n".join(page.extract_text() or "" for page in reader.pages[:2]).strip()
                if len(text) < 300:
                    raise RuntimeError("이미지 PDF")
                return text, 3, pdf_url
            except Exception as exc:
                errors.append(f"report_idx={row['id']}: {exc}")
        raise RuntimeError("; ".join(errors))

    def naver(self, report, day):
        # finance.naver.com은 curl 접근이 차단되어 확인된 모바일 API만 사용한다.
        rows = json.loads(self.fetch(NAVER))
        if not isinstance(rows, list):
            raise RuntimeError("예상한 JSON 배열이 아님")
        matches = [r for r in rows if isinstance(r, dict)
                   and str(r.get("writeDate", ""))[:10] == day.isoformat()
                   and report["house"] in str(r.get("brokerName", ""))]
        matches.sort(key=lambda r: similarity(r.get("title"), report.get("title")), reverse=True)
        for row in matches:
            text = plain(row.get("previewContent"))
            if text:
                return text, 2, NAVER
        raise RuntimeError("날짜/증권사 일치 요약 없음")

    def broker(self, report, day):
        url = BROKER_APIS.get(report["house"])
        if not url:
            raise RuntimeError("확인된 공개 API 없음")
        data = json.loads(self.fetch(url, {"query": "카카오 " + report.get("analyst", ""),
                                          "index": "msreport", "pageNo": 1}))
        rows = [item for group in data.get("result", []) for item in group.get("items", [])
                if "035720" in str(item.get("stk_code", ""))
                and str(item.get("date", ""))[:10] == day.isoformat()]
        rows.sort(key=lambda r: (similarity(r.get("title"), report.get("title")),
                                r.get("analyst") == report.get("analyst")), reverse=True)
        for row in rows:
            text = plain(row.get("content"))
            if len(text) < 300:
                text = plain(row.get("coment"))
            if text:
                return text, 3, url
        raise RuntimeError("날짜/종목 일치 원문 없음")

    def telegram(self, report, day):
        handle = TELEGRAM_HANDLES.get(report["house"])
        if not handle:
            raise RuntimeError("확인된 공개 채널 없음")
        base = "https://t.me/s/" + handle
        before = None
        for _ in range(6):
            url = base + ("?before=" + str(before) if before is not None else "")
            parser = TelegramParser()
            parser.feed(self.fetch(url).decode("utf-8", errors="replace"))
            matches = []
            for row in parser.messages:
                try:
                    delta = abs((datetime.strptime(row["date"], "%Y-%m-%d").date() - day).days)
                except ValueError:
                    continue
                text = row["text"]
                if delta <= 2 and len(text) >= 500 and "카카오" in text and ("TP" in text or "목표주가" in text):
                    matches.append((delta, row))
            if matches:
                matches.sort(key=lambda pair: pair[0])
                row = matches[0][1]
                return row["text"], 3, "https://t.me/" + handle + "/" + str(row["id"])
            if not parser.messages:
                break
            oldest = min(row["id"] for row in parser.messages)
            if before is not None and oldest >= before:
                break
            before = oldest
        raise RuntimeError("최대 6페이지 내 날짜/본문 조건 일치 메시지 없음")


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix="." + path.name, delete=False) as stream:
        temporary = pathlib.Path(stream.name)
        try:
            stream.write(text)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def collect(reports):
    override_path = BASE / "data/analysts_override.json"
    # 수기 목록이 손상되면 잘못된 자동 수집으로 덮어쓰지 않도록 이번 실행을 멈춘다.
    overrides = json.loads(override_path.read_text(encoding="utf-8")).get("points", {}) if override_path.exists() else {}
    index_path = BASE / "data/report_sources.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {"items": {}}
    items = index.setdefault("items", {})
    if not isinstance(items, dict) or not isinstance(overrides, dict):
        raise ValueError("기존 인덱스 또는 수기 points 형식 오류")
    collector = Collector()
    targets, seen = [], set()
    for report in reports:
        if not isinstance(report, dict) or report.get("region") != "국내":
            continue
        key = f"{report.get('house', '')} {report.get('date', '')}"
        if key in overrides or key in seen:
            continue
        seen.add(key)
        targets.append((key, report))
    acquired = 0
    for key, report in targets:
        previous = items.get(key, {})
        if previous.get("level") == 3:
            print(f"[건너뜀] {key} / 기존 전문")
            continue
        errors, result = {}, None
        try:
            day = datetime.strptime(f"{YEAR}/{report['date']}", "%Y/%m/%d").date()
            house = report["house"]
            if not house or re.search(r"[/\\\x00]", house) or house in (".", ".."):
                raise ValueError("안전하지 않은 house 파일명")
            for source in SOURCES:
                try:
                    text, level, url = getattr(collector, source)(report, day)
                    result = (source, text, level, url)
                    break
                except Exception as exc:
                    errors[source] = str(exc)
                    print(f"[소스실패] {key} / {source} / {exc}")
            if result:
                source, text, level, url = result
                fetched = now()
                relative = f"report_text/{house}_{day:%m%d}.txt"
                atomic_write(BASE / "data" / relative,
                             f"# source={source} level={level} fetched={fetched}\n{text}\n")
                items[key] = {"source": source, "level": level, "chars": len(text),
                              "file": relative, "fetchedAt": fetched, "url": url}
                acquired += 1
                print(f"[수집] {key} / {source} / level={level} / {len(text)}자")
                continue
        except Exception as exc:
            errors["processing"] = str(exc)
        failure = {"source": None, "level": 0, "error": "모든 소스에서 못 찾음",
                   "tried": [s for s in SOURCES if s in errors], "errors": errors}
        # 일시적인 장애가 이미 받은 요약을 소실시키면 다음 백필까지 정보가 사라진다.
        if previous.get("level") in (1, 2):
            items[key] = dict(previous, lastAttemptAt=now(), lastAttemptError=failure)
        else:
            items[key] = failure
        print(f"[실패] {key} / " + "; ".join(f"{s}: {e}" for s, e in errors.items()))
    index["updatedAt"] = now()
    atomic_write(index_path, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    levels = [items.get(key, {}).get("level", 0) for key, _ in targets]
    print(f"[원문] 대상 {len(targets)}건 / 신규수집 {acquired}건 / 전문 {levels.count(3)}건 "
          f"요약 {sum(level in (1, 2) for level in levels)}건 실패 {levels.count(0)}건")


def main():
    try:
        data = json.loads((BASE / "data/analysts.json").read_text(encoding="utf-8"))
        reports = data["reports"]
        if not isinstance(reports, list):
            raise ValueError("reports가 배열이 아님")
    except Exception as exc:
        print(f"[원문] analysts.json 읽기 실패: {exc}")
        return 1
    try:
        collect(reports)
    except Exception as exc:
        print(f"[원문] 실행 실패: {exc}")
        print("[원문] 대상 0건 / 신규수집 0건 / 전문 0건 요약 0건 실패 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
