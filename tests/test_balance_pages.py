"""잔고조회 이어 받기 — 보유 종목이 한 쪽을 넘을 때 뒤쪽 종목이 빠지지 않는지. HTTP 와 토큰은 가짜."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from kis import account, client  # noqa: E402

CFG = SimpleNamespace(rest_url="https://example.invalid", app_key="AK-fake", app_secret="AS-fake", user_agent="t",
                      account="00000000", product="01", tr_prefix="V")


class _Resp:
    def __init__(self, body, cont=""):
        self.status_code, self._b, self.headers = 200, body, {"tr_cont": cont}

    def json(self):
        return self._b


def _page(codes, nk=""):
    return {"rt_cd": "0", "output1": [{"pdno": c, "hldg_qty": "3"} for c in codes], "output2": [{"tot": "1"}],
            "ctx_area_fk100": "FK" if nk else "", "ctx_area_nk100": nk}


def _fake(monkeypatch, pages):
    calls = []

    def get(url, headers=None, params=None, timeout=None):
        calls.append((dict(headers), dict(params)))
        return pages[len(calls) - 1]
    monkeypatch.setattr(client, "get_token", lambda cfg: "TOKEN-fake")
    monkeypatch.setattr(client.requests, "get", get)
    return calls


def test_balance_follows_next_pages(monkeypatch):
    calls = _fake(monkeypatch, [_Resp(_page(["999901", "999902"], nk="NK1"), "M"), _Resp(_page(["999903"]), "D")])
    rows, summary = account.balance(CFG)
    assert [r["pdno"] for r in rows] == ["999901", "999902", "999903"] and summary == {"tot": "1"}
    assert "tr_cont" not in calls[0][0] and calls[1][0]["tr_cont"] == "N"
    assert calls[1][1]["CTX_AREA_NK100"] == "NK1" and calls[1][1]["CTX_AREA_FK100"] == "FK"


def test_balance_single_page_unchanged(monkeypatch):
    calls = _fake(monkeypatch, [_Resp(_page(["999901"]), "")])
    assert len(account.balance(CFG)[0]) == 1 and len(calls) == 1


def test_balance_partial_is_error(monkeypatch):
    _fake(monkeypatch, [_Resp(_page(["999901"], nk=""), "M")])            # 다음 쪽이 있다는데 이어 받기 값이 없다
    with pytest.raises(client.KisError):
        account.balance(CFG)
