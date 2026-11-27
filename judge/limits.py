"""전역 설정(judge.yaml) · 전역 상한(하루 호출 수 · 하루 달러) · 단가표.

judge.yaml 이 없으면 아래 DEFAULTS(모두 보수적 — AI 기능 꺼짐 · 뉴스 제목 · 봉 원값 · 브라우저 자료 꺼짐)로 돈다.
단가는 코드에 박지 않는다 — judge.yaml 의 prices 표(확인 날짜 칸 포함)에서만 읽는다. 표에 없는 모델이면 '비용 어림 없음'.
하루 사용량은 그날 판단 기록(logs/judge_YYYYMMDD.jsonl)에서 센다 — 프로세스를 다시 띄워도 이어진다.
"""
from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

import yaml

DEFAULTS = {
    "ai_enabled": False,          # AI 로 보내는 기능 전체(공급자 anthropic). 가짜 공급자는 이 스위치와 상관없이 돈다.
    "ack": False,                 # 고지를 읽었다는 표시. ai_enabled 인데 이것이 없으면 고지만 보여 주고 부르지 않는다.
    "send": {"bars_raw": False, "news_titles": False, "disclosures": True, "signals": True, "positions": True,
             "text": True, "browser": False},
    "record_titles": False,       # 뉴스 제목 원문을 판단 기록에 남길지(꺼짐 = 일련번호 · 시각 · 해시만)
    "limits": {"daily_calls": 200, "daily_usd": 1.0},
    "news": {"poll_s": 60, "limit": 40},
    "opendart": {"daily_cap": 500},
    "browser_out": "../browser-reader/out",
    "prices": {"checked": None, "source": None, "unit": "usd_per_million_tokens", "models": {}},
}
TOP = set(DEFAULTS)
SUB = {"send": set(DEFAULTS["send"]), "limits": set(DEFAULTS["limits"]), "news": set(DEFAULTS["news"]),
       "opendart": set(DEFAULTS["opendart"]), "prices": set(DEFAULTS["prices"])}
PRICE_KEYS = {"input", "output", "cache_read", "cache_write", "note"}


class GlobalError(ValueError):
    pass


def load_global(root: Path, path: Path | None = None) -> dict:
    """judge.yaml → 기본값을 채운 dict. 모르는 칸 · 잘못된 값이면 GlobalError(판단 프로세스는 시작하지 않는다)."""
    g = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else Path(root) / "judge.yaml"
    if not p.exists():
        g["_file"] = None
        return g
    try:
        d = yaml.safe_load(p.read_text(encoding="utf-8-sig")) or {}
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as e:
        raise GlobalError(f"{p.name}: 읽지 못함({type(e).__name__})") from None
    if not isinstance(d, dict):
        raise GlobalError(f"{p.name}: 칸 묶음이 아님")
    bad = sorted(k for k in d if k not in TOP)
    if bad:
        raise GlobalError(f"{p.name}: 모르는 칸 {', '.join(bad)}")
    for k, v in d.items():
        if k in SUB:
            if not isinstance(v, dict):
                raise GlobalError(f"{p.name}: {k} 는 칸 묶음")
            badk = sorted(x for x in v if x not in SUB[k])
            if badk:
                raise GlobalError(f"{p.name}: {k} 의 모르는 칸 {', '.join(badk)}")
            g[k].update(v)
        else:
            g[k] = v
    for k in ("ai_enabled", "ack", "record_titles"):
        if not isinstance(g[k], bool):
            raise GlobalError(f"{p.name}: {k} 는 true · false")
    for k, v in g["send"].items():
        if not isinstance(v, bool):
            raise GlobalError(f"{p.name}: send.{k} 는 true · false")
    lim = g["limits"]
    if not isinstance(lim["daily_calls"], int) or isinstance(lim["daily_calls"], bool) or not 1 <= lim["daily_calls"] <= 100000:
        raise GlobalError(f"{p.name}: limits.daily_calls 는 1~100000 정수")
    if not isinstance(lim["daily_usd"], (int, float)) or isinstance(lim["daily_usd"], bool) or not 0 < lim["daily_usd"] <= 1000:
        raise GlobalError(f"{p.name}: limits.daily_usd 는 0 초과 1000 이하")
    if not isinstance(g["news"]["poll_s"], (int, float)) or g["news"]["poll_s"] < 10:
        raise GlobalError(f"{p.name}: news.poll_s 는 10초 이상")
    if not isinstance(g["news"]["limit"], int) or not 1 <= g["news"]["limit"] <= 400:
        raise GlobalError(f"{p.name}: news.limit 는 1~400")
    if not isinstance(g["opendart"]["daily_cap"], int) or not 1 <= g["opendart"]["daily_cap"] <= 20000:
        raise GlobalError(f"{p.name}: opendart.daily_cap 는 1~20000")
    models = g["prices"].get("models") or {}
    if not isinstance(models, dict):
        raise GlobalError(f"{p.name}: prices.models 는 칸 묶음")
    for name, row in models.items():
        if not isinstance(row, dict) or any(x not in PRICE_KEYS for x in row):
            raise GlobalError(f"{p.name}: prices.models.{name} 칸은 {' · '.join(sorted(PRICE_KEYS))}")
        for x in ("input", "output", "cache_read", "cache_write"):
            if row.get(x) is not None and (not isinstance(row[x], (int, float)) or row[x] < 0):
                raise GlobalError(f"{p.name}: prices.models.{name}.{x} 는 0 이상 숫자")
    g["prices"]["models"] = models
    g["_file"] = p.name
    return g


def cost(model: str, usage: dict, prices: dict) -> tuple[float | None, str]:
    """사용량 × 단가표 → (달러 어림, 설명). 표에 없는 모델 · 빈 칸이 쓰인 사용량이면 None 또는 그 부분을 빼고 설명에 적는다."""
    row = (prices.get("models") or {}).get(model)
    if not row or row.get("input") is None or row.get("output") is None:
        return None, f"비용 어림 없음(단가표에 {str(model)[:40]} 없음)"
    u = usage or {}
    parts, miss = 0.0, []
    for key, price_key in (("input_tokens", "input"), ("output_tokens", "output"),
                           ("cache_read_input_tokens", "cache_read"), ("cache_creation_input_tokens", "cache_write")):
        n = u.get(key) or 0
        if not n:
            continue
        price = row.get(price_key)
        if price is None:
            miss.append(price_key)
            continue
        parts += n * float(price) / 1_000_000
    note = f"단가표 확인 {prices.get('checked') or '날짜 없음'}"
    if miss:
        note += f" · 단가 없는 칸 뺌({', '.join(miss)})"
    return round(parts, 6), note


def today(now_ms: int | None = None) -> str:
    return (datetime.fromtimestamp(now_ms / 1000) if now_ms else datetime.now()).strftime("%Y%m%d")


def usage_of(records: list[dict]) -> dict:
    """그날 기록 → {calls, usd, per_judge{id: calls}, settings{id: n}, limit_hit, limit_reason}."""
    out = {"calls": 0, "usd": 0.0, "per_judge": {}, "limit_hit": False, "limit_reason": ""}
    for r in records:
        ev = r.get("event")
        if ev == "judge":
            n = int(r.get("calls") or 0)
            out["calls"] += n
            out["per_judge"][r.get("judge")] = out["per_judge"].get(r.get("judge"), 0) + n
            if isinstance(r.get("cost_est"), (int, float)):
                out["usd"] += float(r["cost_est"])
        elif ev == "limit":
            out["limit_hit"], out["limit_reason"] = True, str(r.get("reason", ""))
    out["usd"] = round(out["usd"], 6)
    return out


def over(use: dict, g: dict) -> str | None:
    """전역 상한에 닿았으면 이유, 아니면 None."""
    lim = g["limits"]
    if use["calls"] >= lim["daily_calls"]:
        return f"오늘 호출 수 상한({lim['daily_calls']}번)에 닿음"
    if use["usd"] >= float(lim["daily_usd"]):
        return f"오늘 비용 상한(${lim['daily_usd']})에 닿음(어림 ${use['usd']:.4f})"
    return None
