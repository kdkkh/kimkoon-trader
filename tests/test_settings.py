"""설정 파일 읽기 시험."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("strategy_live_script", ROOT / "scripts" / "strategy_live.py")
sl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sl)


def test_example_config_loads():
    s, name = sl.load_settings(ROOT / "config.example.yaml")
    # 예시 값은 판마다 다르다 — 읽히는지와 칸 사이 규칙만 본다
    from execution.modes import MODES
    assert name == "config.example.yaml" and s["order_mode"] in MODES and all(len(c) == 6 for c in s["codes"])
    assert 0 < s["amount"] <= (s["max_amount"] or 3_000_000) and s["loss_limit"] > 0 and s["wait"] > 0


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("codez: [005930]\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        sl.load_settings(p)


def test_codes_string_and_zero_pad(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("codes: 5930, 660\n", encoding="utf-8")
    s, _ = sl.load_settings(p)
    assert s["codes"] == ["005930", "000660"]


def test_unquoted_codes_rejected(tmp_path):
    """YAML 8진수 함정: codes: [000660] → 432 로 읽힘."""
    p = tmp_path / "c.yaml"
    p.write_text("codes: [000660]" + chr(10), encoding="utf-8")
    with pytest.raises(SystemExit):
        sl.load_settings(p)


def test_max_amount_optional_and_checked(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text('codes: ["005930"]' + chr(10), encoding="utf-8")
    assert sl.load_settings(p)[0]["max_amount"] is None                 # 칸이 없어도 예전과 같이 읽힘
    p.write_text('codes: ["005930"]' + chr(10) + "max_amount: 1000000" + chr(10), encoding="utf-8")
    assert sl.load_settings(p)[0]["max_amount"] == 1_000_000
    for bad in ("5000000", "0", "abc", "true"):
        p.write_text("max_amount: " + bad + chr(10), encoding="utf-8")
        with pytest.raises(SystemExit):
            sl.load_settings(p)


def test_example_max_amount_not_reloadable():
    from strategy.live import LiveRunner
    s, _ = sl.load_settings(ROOT / "config.example.yaml")
    assert 0 < s["max_amount"] <= 3_000_000 and "max_amount" not in LiveRunner.RELOADABLE
