"""설정 폴더 환경 변수 · 봉 CSV 읽기 시험.

네트워크 호출 없음. 실제 설정 폴더(홈 폴더 KIS/config)는 읽지 않는다(환경 변수로 임시 폴더를 가리킴).
"""
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategy import data  # noqa: E402
from strategy.validate import sample_bars  # noqa: E402


def _py(code: str, env_dir: Path) -> str:
    env = dict(os.environ, KIS_CONFIG_DIR=str(env_dir), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
    return r.stdout


def test_kis_config_dir_moves_all_files(tmp_path):
    (tmp_path / "kis_devlp.yaml").write_text(
        "paper_app: A\npaper_sec: B\nmy_paper_stock: '12345678'\nvps: https://example.invalid\n"
        "vops: wss://example.invalid\nmy_prod: '01'\n", encoding="utf-8")
    out = _py("from kis import config; from notify import telegram\n"
              "c = config.load('paper')\n"
              "print(config.CONFIG_DIR.name, c.token_file.parent.name, telegram.CONFIG_FILE.parent.name, c.is_paper)",
              tmp_path)
    assert out.split() == [tmp_path.name, tmp_path.name, tmp_path.name, "True"]


def test_missing_config_msg_no_user_path(tmp_path):
    out = _py("from kis import config\n"
              "try:\n    config.load('paper')\nexcept FileNotFoundError as e:\n    print(e)", tmp_path)
    assert "kis_devlp.yaml" in out and str(Path.home()) not in out and str(tmp_path) not in out


def test_read_bars_csv_roundtrip_kst(tmp_path):
    b = sample_bars(30)
    p = tmp_path / "bars.csv"
    b.assign(time=b["time"].dt.tz_localize(None)).iloc[::-1].to_csv(p, index=False)     # 시간대 없음 · 거꾸로
    got = data.read_bars_csv(p)
    assert list(got.columns) == data.BAR_COLS and len(got) == 30
    assert str(got["time"].dt.tz) == data.KST and got["time"].is_monotonic_increasing
    assert got["close"].tolist() == b["close"].tolist()


def test_read_bars_csv_rejects_missing(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("time,close\n2026-01-02,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="volume"):
        data.read_bars_csv(p)
    with pytest.raises(FileNotFoundError):
        data.read_bars_csv(tmp_path / "none.csv")


def test_validate_csv_needs_no_network(tmp_path):
    b = sample_bars()
    p = tmp_path / "sample_bars.csv"
    b.to_csv(p, index=False)
    env = dict(os.environ, KIS_CONFIG_DIR=str(tmp_path / "no_config"), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-X", "utf8", "scripts/strategy_validate.py", "supertrend", "--csv", str(p),
                        "--tv-trades", str(ROOT / "tests" / "fixtures" / "tv_trades_sample.csv")],
                       cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-800:]
    assert "[봉] sample_bars.csv" in r.stdout and "판정: 통과" in r.stdout


def test_backtest_needs_code_or_csv(tmp_path):
    """봉 백테스트는 기본 종목이 없다 — --csv 도 --code 도 없으면 사용법 오류(종료 코드 2)로 끝나고 아무것도 조회하지 않는다."""
    env = dict(os.environ, KIS_CONFIG_DIR=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "strategy_backtest.py")], cwd=tmp_path, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 2
    assert "종목 코드(--code) 또는 봉 파일(--csv)을 주세요" in r.stderr
    assert "[백테스트]" not in r.stdout
