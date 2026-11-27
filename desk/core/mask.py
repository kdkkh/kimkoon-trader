"""화면에 내기 전 거르기 — 빼는 칸(원응답 · 주문번호 · 계좌 …) · 문구 가림(사용자 폴더 · 계좌 꼴 · 토큰 꼴) · 녹화 가림(?mask=1).

녹화 가림(?mask=1)이 바꾸는 것
  이름 · 제목 칸(MASK_KEYS)   → 이름표(종목 1 · 제목 1 · 공급사 1 …). 단 AI 공급자 · 모델 이름(MASK_PASS 꼴)은 그대로 —
                              칸 이름이 같아도(provider · name) 뉴스 공급사 · 종목 이름이 아니기 때문.
  원 단위 가격 · 금액 칸(PRICE_KEYS)의 숫자 → MASKED 글. 손익(pnl · realized · gross …)과 수량 · 비율은 그대로 둔다.

서버는 모든 API 응답을 clean() 에 한 번 더 통과시킨다. 탭이 빠뜨려도 여기서 걸러지게 하려는 두 번째 그물이다.
"""
from __future__ import annotations

import re
from pathlib import Path

try:                                    # control 이 있는 판(내 화면 · 공개판 모두)에서는 같은 가림 함수를 쓴다
    from control.common import scrub as _base_scrub
except Exception:                       # pragma: no cover — control 없이 화면만 쓸 때
    _HOME_RX = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'`]+", re.I)

    def _base_scrub(text) -> str:
        s = str(text)
        home = str(Path.home())
        for h in (home, home.replace("\\", "/")):
            if h and len(h) > 3:
                s = s.replace(h, "~")
        return _HOME_RX.sub("~", s)

# 화면에 내지 않는 칸 — 주문번호(odno · 취소 주문번호) · 조직번호 · 원응답 · 계좌 · 키 · 토큰 · 텔레그램 id
DROP_KEYS = frozenset({
    "raw", "odno", "orgno", "cancel_odno", "orgn_odno", "ord_gno_brno", "ord_orgno", "ord_tmd", "infm_tmd",
    "cano", "acnt_prdt_cd", "account", "acnt", "token", "access_token", "appkey", "appsecret", "secret",
    "chat_id", "msg",
})
# ?mask=1 일 때 가리는 칸(종목 이름 · 기사 제목 · 공급사 · 회사 이름). 종목 코드는 예시라 그대로 둔다.
MASK_KEYS = frozenset({"name", "title", "company", "corp_name", "provider", "dorg"})
# 이름 칸이어도 가리지 않는 값 — AI 판단의 공급자(fake · anthropic) · 모델 이름(claude-…). 종목 · 기사 · 회사 이름이 아니다.
MASK_PASS = re.compile(r"^(fake|anthropic|claude-[a-z0-9.-]+)$")
# ?mask=1 일 때 숫자를 가리는 칸 — 원 단위 가격 · 체결가 · 평균가 · 봉 값 · 주문 금액(손익 합계는 남긴다)
PRICE_KEYS = frozenset({"price", "avg", "close", "open", "high", "low", "current", "last", "entry", "exit", "diff",
                        "diff_won", "est_price", "px", "buy_amt", "sell_amt", "진입가", "청산가", "시가", "고가", "저가", "종가"})
MASKED = "•••"


def scrub(text, root: Path | None = None) -> str:
    """문구 가림: 뿌리 폴더 경로 → '.', 사용자 폴더 → '~', 계좌 꼴 · 봇 토큰 꼴 → 표시."""
    s = str(text)
    if root is not None:
        r = str(Path(root).resolve())
        for v in (r, r.replace("\\", "/")):
            if v and len(v) > 3:
                s = s.replace(v, ".")
    return _base_scrub(s)


class Aliases:
    """가림 이름표 — 같은 값은 같은 '종목 1' 로(한 응답 안에서)."""

    def __init__(self):
        self.maps: dict[str, dict[str, str]] = {}

    def get(self, kind: str, value: str) -> str:
        m = self.maps.setdefault(kind, {})
        if value not in m:
            m[value] = f"{_ALIAS_HEAD.get(kind, '종목')} {len(m) + 1}"
        return m[value]


_ALIAS_HEAD = {"title": "제목", "provider": "공급사", "dorg": "공급사", "company": "회사", "corp_name": "회사"}


def _is_number(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str) and v.strip():
        try:
            float(v.replace(",", ""))
            return True
        except ValueError:
            return False
    return False


def clean(obj, root: Path | None = None, mask: bool = False, _al: Aliases | None = None):
    """dict · list · 글을 따라가며 빼는 칸을 지우고 글을 가린다. mask 면 이름 · 제목 칸을 이름표로 바꾼다."""
    al = _al or Aliases()
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            ks = str(k)
            if ks in DROP_KEYS:
                continue
            if mask and ks in MASK_KEYS and isinstance(v, str) and v and not MASK_PASS.match(v):
                out[ks] = al.get(ks, v)
                continue
            if mask and ks in PRICE_KEYS and _is_number(v):
                out[ks] = MASKED
                continue
            out[ks] = clean(v, root, mask, al)
        return out
    if isinstance(obj, (list, tuple)):
        return [clean(v, root, mask, al) for v in obj]
    if isinstance(obj, str):
        return scrub(obj, root)
    return obj
