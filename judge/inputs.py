"""입력 모으개 — 판단 하나가 쓸 자료를 모은다. 프롬프트 함수는 여기서 만든 parts 만 받는다.

입력 종류(정의 inputs 의 kind)
  bars          실행기가 문 폴더에 내놓은 닫힌 1분봉(state/ext/bars/<종목>.csv). 판단은 봉을 조회하지 않는다(한투 호출 0).
                form: derived(기본) = 등락률 · 평균 대비 % · 고저 폭 % · 거래량 배수 같은 가공값만.
                form: raw = 시각 · 종가 · 거래량 원값 — judge.yaml send.bars_raw 를 켜야 보낸다.
  signals       실행기 기록의 signal 사건(시각 · 종목 · 신호 이름). 지표 값(원 단위가 섞임)은 send.bars_raw 일 때만.
  news_titles   한투 뉴스 제목(kis.news.recent_titles — 각자 실전 키). 기본 꺼짐(send.news_titles).
  disclosures   OpenDART 오늘 공시 목록(dart.opendart.today_for_codes — 각자 키). 출처 표시를 붙인다.
  browser       browser-reader 가 읽어 둔 값(out/<사이트>/seen.csv). 기본 꺼짐(send.browser).
  positions     실행기 상태(보유 종목 · 수량 · 오늘 진입 수 — control.state). 평균가(원)는 send.bars_raw 일 때만.
  text          봇 · 화면이 넘긴 글(사용자 폴더 경로 · 계좌 꼴 · 토큰 꼴은 가린다).
스위치가 꺼진 입력: optional 이면 그 입력만 빼고, 아니면 판단 전체를 묻지 않는다(이유를 기록).
조회는 켜 둔 판단이 쓰는 만큼만(뉴스 · 공시는 짧게 기억해 같은 바퀴 안에서 두 번 부르지 않는다). 받은 원문을 따로 쌓지 않는다.
"""
from __future__ import annotations

import csv
import hashlib
import io
import time
from datetime import datetime
from pathlib import Path

from control.common import scrub

from . import record

LABELS = {"bars": "1분봉 가공값", "bars_raw": "1분봉", "signals": "전략 신호", "news_titles": "뉴스 제목",
          "disclosures": "공시 목록", "browser": "웹 페이지에서 읽은 값", "positions": "실행기 상태", "text": "넘겨받은 글"}
DART_SOURCE = "금융감독원 전자공시(OpenDART)"


class InputError(RuntimeError):
    pass


# ---------------- 바깥 조회(각자 키) ----------------
class Sources:
    """뉴스 제목 · 공시 목록 조회. 같은 요청은 cache_s 초 안에 다시 부르지 않는다(같은 바퀴의 방아쇠 · 입력이 함께 씀)."""

    def __init__(self, root: Path, opendart_cap: int = 500, cache_s: float = 10.0, clock=time.time):
        self.root, self.cap, self.cache_s, self.clock = Path(root), opendart_cap, cache_s, clock
        self._cache: dict = {}
        self.calls = {"news": 0, "disclosures": 0}

    def _cached(self, key, fn):
        hit = self._cache.get(key)
        if hit and self.clock() - hit[0] < self.cache_s:
            return hit[1]
        rows = fn()
        self._cache[key] = (self.clock(), rows)
        return rows

    def news(self, code: str, limit: int) -> list[dict]:
        def go():
            from kis import news
            self.calls["news"] += 1
            try:
                return news.recent_titles(code or "", limit)
            except Exception as e:
                raise InputError(f"뉴스 제목 조회 실패({type(e).__name__})") from None
        return self._cached(("news", code, limit), go)

    def disclosures(self, codes: list[str]) -> list[dict]:
        """오늘 전체 목록을 한 번 받아 두고 종목으로 거른다 — 종목마다 묻는 판단(per_code)이 같은 목록을 종목 수만큼
        다시 받지 않게(2026-11-26 장중: 다섯 종목 · 10분 간격으로 하루 몫 500건이 오후에 바닥남)."""
        def go():
            from dart import opendart
            self.calls["disclosures"] += 1
            try:
                return opendart.today_all(self.root / "state", daily_cap=self.cap)
            except Exception as e:
                raise InputError(f"공시 목록 조회 실패({type(e).__name__})") from None
        keep = {str(c).strip().upper() for c in codes}
        if not keep:
            return []
        return [r for r in self._cached(("disc_all",), go) if r.get("stock_code") in keep]


class FakeSources:
    """--fake 용: 바깥을 부르지 않고 '(가짜)' 표시가 붙은 견본 자료를 준다. 뉴스는 부를 때마다 일련번호가 하나씩 는다
    (새 제목 방아쇠를 시험할 수 있게). 공시는 요청한 첫 종목에만 한 건."""

    def __init__(self, today: str | None = None):
        self.today = today or datetime.now().strftime("%Y%m%d")
        self.n = 0
        self.calls = {"news": 0, "disclosures": 0}

    def news(self, code: str, limit: int) -> list[dict]:
        self.calls["news"] += 1
        self.n += 1
        c = code or "000000"
        rows = []
        for k in range(min(limit, 3)):
            sr = f"{self.today}0900{self.n:02d}{k:04d}"
            rows.append({"srno": sr, "date": self.today, "time": f"0900{(self.n + k) % 60:02d}", "provider_code": "0",
                         "provider": "가짜", "codes": [c] if code else [], "title": f"(가짜 제목 {self.n}-{k}) 시험용 뉴스"})
        return rows

    def disclosures(self, codes: list[str]) -> list[dict]:
        self.calls["disclosures"] += 1
        if not codes:
            return []
        c = codes[0]
        return [{"rcept_no": f"{self.today}9{c[-5:]}", "rcept_dt": self.today, "corp_code": "", "corp_name": "(가짜 회사)",
                 "stock_code": c, "corp_cls": "Y", "report_nm": "(가짜) 주요사항보고서(유상증자결정)", "flr_nm": "(가짜)",
                 "rm": "", "viewer_url": "", "source": DART_SOURCE}]


# ---------------- 가공 ----------------
def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_bars(p: Path) -> list[dict]:
    try:
        text = Path(p).read_text(encoding="utf-8")
    except OSError:
        return []
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        c, v = _f(r.get("close")), _f(r.get("volume"))
        if c is None or c <= 0:
            continue
        rows.append({"time": str(r.get("time", "")), "close": c, "volume": v or 0.0,
                     "high": _f(r.get("high")) or c, "low": _f(r.get("low")) or c})
    return rows


def _hhmm(t: str) -> str:
    try:
        return datetime.fromisoformat(t).strftime("%H:%M")
    except ValueError:
        return t[11:16] if len(t) >= 16 else t


def derived_line(code: str, rows: list[dict]) -> str:
    """원 단위 값 없이 비율만: n봉 등락 · 직전 봉 대비 · n봉 평균 대비 · 고저 폭 · 거래량 배수."""
    c = [r["close"] for r in rows]
    v = [r["volume"] for r in rows]
    n = len(c)
    chg_n = (c[-1] / c[0] - 1) * 100
    chg_1 = (c[-1] / c[-2] - 1) * 100
    dev = (c[-1] / (sum(c) / n) - 1) * 100
    lo, hi = min(r["low"] for r in rows), max(r["high"] for r in rows)
    rng = (hi / lo - 1) * 100 if lo > 0 else 0.0
    prev = v[:-1]
    vx = (v[-1] / (sum(prev) / len(prev))) if prev and sum(prev) > 0 else None
    vtxt = f"{vx:.2f}" if vx is not None else "알 수 없음"
    return (f"{code} · 마지막 봉 {_hhmm(rows[-1]['time'])} · 봉 {n}개 · {n}봉 등락 {chg_n:+.2f}% · "
            f"직전 봉 대비 {chg_1:+.2f}% · {n}봉 평균 대비 {dev:+.2f}% · 고저 폭 {rng:.2f}% · "
            f"거래량 배수(마지막 봉 ÷ 앞 봉 평균) {vtxt}")


def raw_lines(code: str, rows: list[dict]) -> str:
    body = "\n".join(f"{_hhmm(r['time'])} {r['close']:.0f} {r['volume']:.0f}" for r in rows)
    return f"[{code} 1분봉: 시각 종가 거래량]\n{body}"


def _codes_for(x: dict, ctx: dict) -> list[str]:
    if x.get("codes") == "trigger":
        return list(ctx.get("trigger", {}).get("codes") or [])
    return list(ctx.get("codes") or [])


def _news_rows(x: dict, ctx: dict) -> list[dict]:
    src, last = ctx["sources"], int(x["last"])
    if x.get("codes", "all") == "all":
        rows = src.news("", last)
    else:
        rows, seen = [], set()
        for c in _codes_for(x, ctx):
            for r in src.news(c, last):
                k = (r.get("srno"), r.get("provider_code"), r.get("title"))
                if k not in seen:
                    seen.add(k)
                    rows.append(r)
    rows = sorted(rows, key=lambda r: (str(r.get("date", "")), str(r.get("time", "")), str(r.get("srno", ""))),
                  reverse=True)
    return rows[:last]


def _news_time(r: dict) -> str:
    d, t = str(r.get("date", "")), str(r.get("time", ""))
    dd = f"{d[4:6]}-{d[6:8]}" if len(d) == 8 else d
    tt = f"{t[0:2]}:{t[2:4]}:{t[4:6]}" if len(t) == 6 else t
    return f"{dd} {tt}".strip()


def _signal_events(ctx: dict) -> list[dict]:
    from control.common import log_path, read_events
    out = []
    for t in ctx.get("targets") or []:
        try:
            out += [dict(e, _target=t) for e in read_events(log_path(ctx["root"], t), {"signal"})]
        except Exception:
            continue
    return sorted(out, key=lambda e: int(e.get("ts_ms") or 0))


# ---------------- 모으기 ----------------
def gather(d: dict, ctx: dict) -> dict:
    """→ {parts, summary, skip, notes, titles, input_codes, disclosures_new}.
    skip 이 있으면 묻지 않는다(이유). parts 의 각 항목 = {kind, label, text, masked?, source?}."""
    send, raw_on = ctx["send"], bool(ctx["send"].get("bars_raw"))
    out = {"parts": [], "summary": [], "skip": None, "notes": [], "titles": [], "input_codes": [],
           "disclosures_new": []}

    def drop(x, why):
        if x.get("optional"):
            out["notes"].append(f"{x['kind']}: {why} — 이 입력만 빼고 물음")
            out["summary"].append({"kind": x["kind"], "dropped": why})
            return False
        out["skip"] = f"{x['kind']}: {why}"
        return True

    for x in d["inputs"]:
        k = x["kind"]
        sw = {"bars": "bars_raw" if x.get("form") == "raw" else None, "signals": "signals", "news_titles": "news_titles",
              "disclosures": "disclosures", "browser": "browser", "positions": "positions", "text": "text"}[k]
        if sw and not send.get(sw):
            if drop(x, f"AI 로 보내기 스위치 꺼짐(judge.yaml send.{sw})"):
                return out
            continue
        try:
            if k == "bars":
                lines, used = [], []
                for code in _codes_for(x, ctx):
                    rows = []
                    for bd in ctx.get("bars_dirs") or []:
                        rows = read_bars(Path(bd) / f"{code}.csv")
                        if rows:
                            break
                    rows = rows[-int(x["last"]):]
                    if len(rows) < 2:
                        continue
                    used.append({"code": code, "n": len(rows), "last": rows[-1]["time"][:19]})
                    lines.append(raw_lines(code, rows) if x["form"] == "raw" else derived_line(code, rows))
                if not lines:
                    if drop(x, "봉 없음(실행기가 문을 켜고 봉을 내놓아야 함)"):
                        return out
                    continue
                out["parts"].append({"kind": k, "label": LABELS["bars_raw" if x["form"] == "raw" else "bars"],
                                     "text": "\n".join(lines), "source": "실행기가 받은 1분봉"})
                out["summary"].append({"kind": k, "form": x["form"], "codes": used})
            elif k == "signals":
                want = set(_codes_for(x, ctx))
                ev = [e for e in _signal_events(ctx) if not want or e.get("code") in want][-int(x["last"]):]
                if not ev:
                    if drop(x, "오늘 전략 신호 없음"):
                        return out
                    continue
                lines = []
                for e in ev:
                    hm = datetime.fromtimestamp(int(e.get("ts_ms") or 0) / 1000).strftime("%H:%M:%S")
                    line = f"{hm} {e.get('code')} {e.get('kind')}"
                    if raw_on and isinstance(e.get("indicators"), dict):
                        line += " " + ", ".join(f"{a}={b}" for a, b in list(e["indicators"].items())[:8])
                    lines.append(line)
                out["parts"].append({"kind": k, "label": LABELS[k], "text": "\n".join(lines)})
                out["summary"].append({"kind": k, "n": len(ev)})
            elif k == "news_titles":
                rows = _news_rows(x, ctx)
                if not rows:
                    if x.get("skip_if_empty") or not x.get("optional"):
                        out["skip"] = "news_titles: 뉴스 제목 없음"
                        return out
                    drop(x, "뉴스 제목 없음")
                    continue
                lines, masked, items = [], [], []
                for r in rows:
                    cs = ",".join(r.get("codes") or [])
                    head = f"{_news_time(r)}" + (f" [{cs}]" if cs else "")
                    h = record.title_hash(r.get("title", ""))
                    lines.append(f"{head} {r.get('title', '')}")
                    masked.append(f"{head} #{r.get('srno', '')} 제목 해시 {h}")
                    out["titles"].append((str(r.get("title", "")), f"#{r.get('srno', '')}"))
                    it = {"srno": r.get("srno"), "time": f"{r.get('date', '')}{r.get('time', '')}",
                          "provider_code": r.get("provider_code"), "hash": h, "codes": r.get("codes") or []}
                    if ctx.get("record_titles"):
                        it["title"] = r.get("title")
                    items.append(it)
                    out["input_codes"] += [c for c in (r.get("codes") or []) if c not in out["input_codes"]]
                out["parts"].append({"kind": k, "label": LABELS[k], "text": "\n".join(lines), "masked": "\n".join(masked),
                                     "source": "한국투자증권 뉴스(제목만)"})
                out["summary"].append({"kind": k, "n": len(rows), "items": items})
            elif k == "disclosures":
                codes = _codes_for(x, ctx)
                rows = ctx["sources"].disclosures(codes) if codes else []
                seen = ctx.get("seen_disclosures")
                if x.get("only_new") and seen is not None:
                    rows = [r for r in rows if r.get("rcept_no") not in seen]
                if not rows:
                    if x.get("skip_if_empty") or not x.get("optional"):
                        out["skip"] = "disclosures: " + ("새 공시 없음" if x.get("only_new") else "오늘 공시 없음")
                        return out
                    drop(x, "오늘 공시 없음")
                    continue
                lines = [f"{r.get('rcept_dt', '')} {r.get('corp_name', '')}({r.get('stock_code', '')}) "
                         f"{r.get('report_nm', '')} · 제출인 {r.get('flr_nm', '')}" + (f" · 비고 {r['rm']}" if r.get("rm") else "")
                         for r in rows]
                out["parts"].append({"kind": k, "label": LABELS[k], "text": f"출처: {DART_SOURCE}\n" + "\n".join(lines),
                                     "source": DART_SOURCE})
                out["summary"].append({"kind": k, "n": len(rows), "source": DART_SOURCE,
                                       "items": [{"rcept_no": r.get("rcept_no"), "stock_code": r.get("stock_code"),
                                                  "report_nm": r.get("report_nm")} for r in rows]})
                out["disclosures_new"] = [r.get("rcept_no") for r in rows]
                out["input_codes"] += [r["stock_code"] for r in rows if r.get("stock_code") and r["stock_code"] not in out["input_codes"]]
            elif k == "browser":
                f = Path(ctx.get("browser_out") or ".") / x["site"] / "seen.csv"
                try:
                    rows = list(csv.DictReader(io.StringIO(f.read_text(encoding="utf-8-sig"))))[-int(x["last"]):]
                except OSError:
                    rows = []
                if not rows:
                    if x.get("skip_if_empty") or not x.get("optional"):
                        out["skip"] = f"browser: {x['site']} 읽은 값 없음"
                        return out
                    drop(x, "읽은 값 없음")
                    continue
                lines = [" ".join(str(r.get(c, "")) for c in ("pub_time", "company", "title", "extra") if r.get(c)) for r in rows]
                out["parts"].append({"kind": k, "label": LABELS[k], "text": scrub("\n".join(lines)),
                                     "source": f"browser-reader {x['site']}"})
                out["summary"].append({"kind": k, "site": x["site"], "n": len(rows)})
            elif k == "positions":
                from control import state
                lines = []
                for t in ctx.get("targets") or []:
                    st = state.runner_status(ctx["root"], t, pid_alive=ctx.get("pid_alive"))
                    pos = ", ".join(f"{c} {v['qty']}주" + (f"(평균 {v['avg']:,.0f})" if raw_on else "")
                                    for c, v in st["positions"].items()) or "없음"
                    lines.append(f"{st['label']}: 보유 {pos} · 오늘 진입 {st['entries_today']}건 · 진행 중 주문 {len(st['orders'])}건")
                if not lines:
                    if drop(x, "돌고 있는 실행기 없음"):
                        return out
                    continue
                out["parts"].append({"kind": k, "label": LABELS[k], "text": "\n".join(lines)})
                out["summary"].append({"kind": k, "targets": list(ctx.get("targets") or [])})
            elif k == "text":
                t = scrub(str(ctx.get("trigger", {}).get("text") or "")).strip()[: int(x["max_chars"])]
                if not t:
                    if drop(x, "넘겨받은 글 없음"):
                        return out
                    continue
                out["parts"].append({"kind": k, "label": LABELS[k], "text": t})
                out["summary"].append({"kind": k, "chars": len(t), "sha": hashlib.sha1(t.encode("utf-8")).hexdigest()[:10]})
        except InputError as e:
            if drop(x, str(e)):
                return out
    if not out["parts"]:
        out["skip"] = "보낼 자료가 하나도 없음"
    return out
