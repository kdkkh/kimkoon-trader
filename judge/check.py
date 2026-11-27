"""답 검증 — 어긋나면 버린다(이유를 기록). 구조화 출력을 써도 여기서 한 번 더 본다(두 번째 그물).

순서: 멈춘 이유(stop_reason) → JSON 꺼내기(코드 울타리 ``` 를 벗긴 뒤 첫 객체) → 보기 안 / 점수 범위 안.
버리는 것: 거부(refusal) · 길이 초과(max_tokens) · 그 밖의 멈춤 · JSON 아님 · 보기 밖 · 정수 아님 · 범위 밖.
"""
from __future__ import annotations

import json
import re

OK_STOPS = ("end_turn",)


def strip_fence(s: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip())


def parse_obj(text: str) -> dict:
    s = strip_fence(text)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("객체 없음")
    o = json.loads(s[i:j + 1])
    if not isinstance(o, dict):
        raise ValueError("객체 아님")
    return o


def validate(answer: dict, resp: dict) -> tuple[dict | None, str]:
    """(검증된 답 {answer|score, reason}, '') 또는 (None, 버린 이유)."""
    stop = resp.get("stop_reason")
    if stop == "refusal":
        cat = resp.get("stop_category")
        return None, "거부 응답(refusal" + (f": {cat}" if cat else "") + ")"
    if stop == "max_tokens":
        return None, "길이 초과(max_tokens) — 답이 끝나기 전에 잘림"
    if stop not in OK_STOPS:
        return None, f"멈춘 이유 {str(stop)[:30]}"
    try:
        o = parse_obj(str(resp.get("text") or ""))
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"JSON 아님({type(e).__name__})"
    reason = o.get("reason", "")
    reason = reason if isinstance(reason, str) else str(reason)
    reason = " ".join(reason.split())[:300]
    if answer["type"] == "choice":
        v = o.get("answer")
        if not isinstance(v, str) or v not in answer["options"]:
            return None, f"보기 밖 답: {str(v)[:20]}"
        return {"answer": v, "reason": reason}, ""
    v = o.get("score")
    if isinstance(v, bool) or not isinstance(v, int):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        else:
            return None, f"점수 형식(정수 아님): {str(v)[:20]}"
    if not answer["min"] <= v <= answer["max"]:
        return None, f"점수 범위 밖: {v}"
    return {"score": v, "reason": reason}, ""


def matches(cond: dict, ans: dict) -> bool:
    """쓰임의 if 조건. 빈 조건 = 늘 참."""
    if not cond:
        return True
    if "answer" in cond and ans.get("answer") != cond["answer"]:
        return False
    if "answer_in" in cond and ans.get("answer") not in cond["answer_in"]:
        return False
    sc = ans.get("score")
    if "score_gte" in cond and not (isinstance(sc, int) and sc >= cond["score_gte"]):
        return False
    if "score_lte" in cond and not (isinstance(sc, int) and sc <= cond["score_lte"]):
        return False
    return True


def show(ans: dict | None) -> str:
    if not ans:
        return "-"
    return str(ans["answer"]) if "answer" in ans else f"{ans['score']}점"
