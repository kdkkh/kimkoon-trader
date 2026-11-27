"""간단 주문 방식(execution/modes.py — limit · market 만) 시험. 가격 · 종목 코드는 가짜."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from execution import modes  # noqa: E402


def test_only_two_modes():
    assert modes.MODES == ("limit", "market")
    for bad in ("best", "escalate", "capped", "chase"):
        with pytest.raises(ValueError):
            modes.OrderSettings(bad, 1_000_000, 50)


def test_entry_qty_by_amount():
    lim, _ = modes.make_intent("long_entry", "999901", 61_234, 0, modes.OrderSettings("limit", 1_000_000, 50))
    mkt, _ = modes.make_intent("long_entry", "999901", 61_234, 0, modes.OrderSettings("market", 1_000_000, 50))
    assert lim.price == 61_300 and lim.qty == 1_000_000 // 61_300 and lim.current == 61_234
    assert mkt.price == 61_400 and mkt.qty == 1_000_000 // 61_400 and mkt.wait_s == 0


def test_exit_sells_all_and_skips():
    i, _ = modes.make_intent("long_exit", "999901", 61_234, 7, modes.OrderSettings())
    assert i.side == "sell" and i.qty == 7 and i.price == 61_200
    assert modes.make_intent("long_exit", "999901", 61_234, 0, modes.OrderSettings())[0] is None
    assert modes.make_intent("long_entry", "999901", 0, 0, modes.OrderSettings())[1] == "현재가 없음"


def test_check_kinds_and_intent_roundtrip():
    s = modes.OrderSettings("market", 500_000, 50)
    assert modes.check_kinds(s, "kis-paper") is None and modes.check_kinds(s, "sim") is None
    i, _ = modes.make_intent("long_entry", "999901", 20_000, 0, s)
    assert modes.Intent.from_dict(i.to_dict()) == i


def test_public_has_no_ladder_or_oms():
    import importlib.util
    for m in ("execution.ladder", "execution.venues", "oms.manager", "sim", "kis.realtime"):
        assert importlib.util.find_spec(m) is None, m                  # 공개판에 싣지 않은 것
