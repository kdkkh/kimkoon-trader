"""텔레그램 시험 — 실제로 보내지 않는다(가짜 HTTP)."""
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from notify import telegram  # noqa: E402

spec = importlib.util.spec_from_file_location("bot_script", ROOT / "scripts" / "bot.py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
FAKE = "1234567890:" + "A" * 35


class FakeHTTP:
    def __init__(self, result=None, ok=True, boom=None):
        self.calls, self.result, self.ok, self.boom = [], result, ok, boom

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if self.boom:
            raise self.boom

        class R:
            def __init__(s, d):
                s.d = d

            def json(s):
                return s.d
        return R({"ok": self.ok, "result": self.result, "error_code": 401, "description": "Unauthorized"})


def test_token_never_in_errors():
    h = FakeHTTP(boom=ConnectionError(f"https://api.telegram.org/bot{FAKE}/sendMessage 실패"))
    tg = telegram.Telegram(FAKE, 1, session=h)
    with pytest.raises(RuntimeError) as e:
        tg.send("x")
    assert FAKE not in str(e.value) and "<봇토큰>" in str(e.value)


def test_send_only_to_owner_redacted():
    h = FakeHTTP(result={})
    telegram.Telegram(FAKE, 777, session=h).send(f"토큰 {FAKE} 가 섞여도")
    url, body = h.calls[0]
    assert body["chat_id"] == 777 and FAKE not in body["text"]


def test_owner_private_new_only():
    tg = telegram.Telegram(FAKE, 777, session=FakeHTTP())
    msg = lambda fid, typ, date: {"message": {"from": {"id": fid}, "chat": {"type": typ}, "date": date, "text": "/상태"}}
    assert tg.is_owner_message(msg(777, "private", 200), 100) == (True, "")
    assert tg.is_owner_message(msg(778, "private", 200), 100)[1] == "본인 아님"
    assert tg.is_owner_message(msg(777, "group", 200), 100)[1] == "개인 대화 아님"
    assert "옛 명령" in tg.is_owner_message(msg(777, "private", 50), 100)[1]


def test_load_config_requires_both(tmp_path):
    p = tmp_path / "telegram.yaml"
    p.write_text('bot_token: "여기에 토큰"\nowner_id: 123\n', encoding="utf-8")
    assert telegram.load_config(p) is None
    p.write_text(f'bot_token: "{FAKE}"\nowner_id: 123\n', encoding="utf-8")
    assert telegram.load_config(p) == {"token": FAKE, "owner_id": 123}


def test_format_events():
    assert "체결" in telegram.format_event({"event": "fill", "code": "005930", "side": "buy", "qty": 3, "price": 61300,
                                           "cum_filled": 3})
    assert telegram.format_event({"event": "order_done", "state": "filled"}) is None
    s = telegram.format_event({"event": "stop", "reason": "end", "summary": {"positions": {"005930": {"qty": 3}},
                                                                            "realized": -1200, "unrealized": 0}})
    assert "005930 3주" in s


def test_bot_off_on_status(tmp_path):
    """끄기 → (장 밖) 켜기는 정지 파일만 지움 → (장 중) 켜기는 띄움 → 돌고 있으면 '이미' → 상태 · 꺼짐 · 도움.
    켜기는 이제 확인 번호(/확인) 뒤에만 실행된다 — 예전 시험의 뜻(장 밖이면 안 띄우고 정지 파일만 지움 · 장 중이면 한 번 띄움 ·
    돌고 있으면 안 띄움)은 그대로 두고, 확인 단계를 한 번씩 거치게 바꿨다."""
    st = tmp_path / "state"
    started, now = [], {"t": datetime(2098, 3, 3, 7, 0)}                       # 휴장일 표에 없는 해의 월요일 07:00(장 밖)
    b = bot.Bot(tmp_path, launcher=lambda a, cwd: started.append(1), market_now=lambda: now["t"],
                code_fn=lambda: "123456", run_async=False)
    r = b.handle("/끄기")
    assert (st / "stop").exists() and "꺼져" in r
    r = b.handle("/켜기")
    assert "장 시간" in r and "/확인 123456" in r and (st / "stop").exists()      # 확인 전에는 아무것도 안 바뀜
    r = b.handle("/확인 123456")
    assert not (st / "stop").exists() and started == [] and "장 시간" in r
    now["t"] = datetime(2098, 3, 3, 10, 0)
    assert "/확인 123456" in b.handle("/on")
    b.handle("/확인 123456")
    assert started == [1]
    (st / "heartbeat.json").write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
    r = b.handle("/켜기")
    assert started == [1] and "이미" in r and "/확인" not in r
    (st / "oms_paper.json").write_text(json.dumps({"positions": {"005930": {"qty": 3, "avg": 61300}}, "orders": [],
                                                   "entries_today": 1, "realized": 0}), encoding="utf-8")
    assert "005930 3주" in b.handle("/상태")
    old = (datetime.now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    (st / "heartbeat.json").write_text(json.dumps({"at": old}), encoding="utf-8")
    assert "[모의] 실행기 꺼짐" in b.handle("/status")
    assert b.handle("아무 말") == bot.HELP
