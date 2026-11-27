"""텔레그램 봇 시험(가짜 HTTP) — 확인 번호 · 본인 판정 · 옛 메시지 · 설정 · 문에 신호 · 예외에 안 죽음 · 429 · 묶어 보내기.
실제 텔레그램 서버를 부르지 않는다. 모든 파일은 임시 폴더에."""
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from control.common import journal_today  # noqa: E402
from notify import telegram  # noqa: E402
from tests.test_control import cfg_text, fake_running  # noqa: E402

spec = importlib.util.spec_from_file_location("bot_script_t", ROOT / "scripts" / "bot.py")
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)
FAKE = "1234567890:" + "B" * 35
MON10 = datetime(2098, 3, 3, 10, 0)                       # 휴장일 표에 없는 해의 월요일 10:00


class R:
    def __init__(self, d):
        self.d = d

    def json(self):
        return self.d


class FakeHTTP:
    """getUpdates 는 넣어 둔 목록을 차례로, sendMessage 는 계획(plan)대로 답하고 보낸 글을 모은다."""

    def __init__(self):
        self.updates, self.sent, self.plan, self.boom_updates = [], [], [], None

    def post(self, url, json=None, timeout=None):
        method = url.rsplit("/", 1)[1]
        if method == "getUpdates":
            if self.boom_updates:
                e, self.boom_updates = self.boom_updates, None
                raise e
            return R({"ok": True, "result": self.updates.pop(0) if self.updates else []})
        d = self.plan.pop(0) if self.plan else {"ok": True, "result": {}}
        if isinstance(d, Exception):
            raise d
        if d.get("ok"):
            self.sent.append(json["text"])
        return R(d)


def msg(uid, text, fid=777, typ="private", date=None):
    return {"update_id": uid, "message": {"from": {"id": fid}, "chat": {"type": typ}, "text": text,
                                          "date": int(time.time()) + 5 if date is None else date}}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make_bot(tmp_path, **kw):
    launched, clk = [], Clock()
    b = bot.Bot(tmp_path, launcher=lambda a, cwd: launched.append(a), market_now=lambda: MON10, clock=clk,
                code_fn=lambda: "123456", run_async=False, **kw)
    return b, launched, clk


# ---------- 확인 번호 ----------
def test_confirm_right_wrong_reuse(tmp_path):
    b, launched, clk = make_bot(tmp_path)
    assert "/확인 123456" in b.handle("/켜기")
    assert "틀렸습니다(1/3)" in b.handle("/확인 000000")
    assert "띄웠습니다" in b.handle("/확인 123456") and len(launched) == 1
    assert "기다리는 명령이 없습니다" in b.handle("/확인 123456") and len(launched) == 1     # 한 번 쓰면 버림


def test_confirm_expires_after_60s(tmp_path):
    b, launched, clk = make_bot(tmp_path)
    b.handle("/켜기 시뮬")
    clk.t += 61
    assert "지나" in b.handle("/확인 123456") and launched == []
    assert "기다리는 명령이 없습니다" in b.handle("/확인 123456")


def test_confirm_three_wrong_discards(tmp_path):
    b, launched, clk = make_bot(tmp_path)
    b.handle("/켜기")
    b.handle("/확인 1")
    b.handle("/확인")
    assert "3번 틀려" in b.handle("/확인 2")
    assert "기다리는 명령이 없습니다" in b.handle("/확인 123456") and launched == []
    b.handle("/켜기")
    assert "버렸습니다" in b.handle("/취소") and "없습니다" in b.handle("/확인 123456")


def test_start_sim_target_args(tmp_path):
    b, launched, clk = make_bot(tmp_path)
    b.handle("/켜기 시뮬")
    b.handle("/확인 123456")
    assert "-Sim" in launched[0]
    assert "대상은" in b.handle("/켜기 실전")


# ---------- 받기 · 본인 · 옛 메시지 · 죽지 않기 ----------
def loop_for(tmp_path, b=None, http=None):
    http = http or FakeHTTP()
    tg = telegram.Telegram(FAKE, 777, session=http, min_interval=0, sleep=lambda s: None)
    b = b or make_bot(tmp_path)[0]
    slept = []
    lp = bot.BotLoop(tg, b, bot.BotLog(tmp_path / "logs"), sleep=slept.append)
    return lp, http, slept


def bot_log(tmp_path):
    p = tmp_path / "logs" / f"bot_{datetime.now():%Y%m%d}.jsonl"
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()]


def test_not_owner_group_old_ignored(tmp_path):
    lp, http, _ = loop_for(tmp_path)
    http.updates = [[msg(1, "/상태"), msg(2, "/상태")]]                    # 켜기 전에 쌓인 것
    assert lp.drop_old() == 2 and lp.offset == 3
    http.updates = [[msg(3, "/끄기", fid=778), msg(4, "/끄기", typ="group"), msg(5, "/끄기", date=lp.started - 60)]]
    lp.tick(wait_s=0)
    reasons = [r["reason"] for r in bot_log(tmp_path) if r["event"] == "ignored"]
    assert reasons[0] == "본인 아님" and reasons[1] == "개인 대화 아님" and "옛 명령" in reasons[2]
    assert http.sent == [] and not (tmp_path / "state" / "stop").exists()
    http.updates = [[msg(6, "/끄기")]]
    lp.tick(wait_s=0)
    assert (tmp_path / "state" / "stop").exists() and "꺼져" in http.sent[0]


def test_loop_survives_exceptions(tmp_path):
    class Boom(bot.Bot):
        def handle(self, text):
            if text == "/터짐":
                raise KeyError("가짜")
            return super().handle(text)
    b = Boom(tmp_path, run_async=False)
    lp, http, slept = loop_for(tmp_path, b)
    http.updates = [[msg(1, "/터짐"), msg(2, "/도움")]]
    lp.tick(wait_s=0)
    assert "[오류]" in http.sent[0] and http.sent[1] == bot.HELP
    http.plan = [ConnectionError("끊김")]                                   # 보내기 실패
    http.updates = [[msg(3, "/도움")]]
    lp.tick(wait_s=0)
    http.boom_updates = ConnectionError("받기 실패")                          # 받기 실패
    lp.tick(wait_s=0)
    http.updates = [[msg(4, "/도움")]]
    lp.tick(wait_s=0)
    ev = [r["event"] for r in bot_log(tmp_path)]
    assert "handle_error" in ev and "send_error" in ev and "poll_error" in ev and slept == [5]
    assert http.sent[-1] == bot.HELP                                        # 그 뒤에도 계속 돈다


def test_status_with_broken_files(tmp_path):
    st = tmp_path / "state"
    st.mkdir()
    (st / "oms_paper.json").write_text('{"positions": {"005930": {"qty": 3}}}', encoding="utf-8")   # 평균가 없음
    (st / "heartbeat.json").write_text('{"at": "깨짐"}', encoding="utf-8")
    lg = tmp_path / "logs"
    lg.mkdir()
    (lg / f"strategy_live_{datetime.now():%Y%m%d}.jsonl").write_bytes("{\"event\": \"반".encode("utf-8")[:-1])
    b, _, _ = make_bot(tmp_path)
    assert "005930 3주" in b.handle("/상태") and "[요약]" in b.handle("/요약")


# ---------- 설정 ----------
def test_settings_goes_through_editor(tmp_path):
    (tmp_path / "config.yaml").write_text(cfg_text(), encoding="utf-8")
    b, _, _ = make_bot(tmp_path)
    assert "amount" in b.handle("/설정")
    r = b.handle("/설정 amount 500,000")
    assert "amount: 1000000 → 500000" in r and "/확인 123456" in r
    assert "amount: 1000000" in (tmp_path / "config.yaml").read_text(encoding="utf-8")        # 확인 전엔 그대로
    r = b.handle("/확인 123456")
    assert "다음 시작 때 반영" in r and "amount: 500000" in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    tries = [j for j in journal_today(tmp_path) if j["action"] == "settings_edit"]
    assert len(tries) == 1 and tries[0]["actor"] == "telegram" and tries[0]["status"] == "saved"
    assert "바꾸지 않음" in b.handle("/설정 strategy evil.py") and "바꿀 수 없는 칸" in b.handle("/설정 tf 5")
    assert b.pending is None


# ---------- 사고팔기 ----------
def test_buy_writes_door_signal(tmp_path):
    fake_running(tmp_path, "paper", codes=("005930",))
    b, _, _ = make_bot(tmp_path, opts={"trade_enabled": True})
    r = b.handle("/사기 005930 500000")
    assert "보유 30분" in r and "손절 2.0%" in r and "/확인 123456" in r
    assert not list((tmp_path / "state" / "ext" / "inbox").glob("*.json"))
    assert "썼습니다" in b.handle("/확인 123456")
    f = list((tmp_path / "state" / "ext" / "inbox").glob("*.json"))
    d = json.loads(f[0].read_text(encoding="utf-8"))
    assert (d["source"], d["action"], d["amount"], d["max_hold_min"], d["stop_pct"]) == ("telegram", "entry", 500000, 30, 2.0)
    b.handle("/팔기 005930")
    b.handle("/확인 123456")
    acts = sorted(json.loads(p.read_text(encoding="utf-8"))["action"]
                  for p in (tmp_path / "state" / "ext" / "inbox").glob("*.json"))
    assert acts == ["entry", "exit"]
    assert "목록 밖" in b.handle("/사기 000660 500000")


def test_buy_refused_door_off_or_disabled(tmp_path):
    b, _, _ = make_bot(tmp_path)
    assert "꺼져 있습니다(bot.yaml" in b.handle("/사기 005930 500000") and b.pending is None
    b2, _, _ = make_bot(tmp_path, opts={"trade_enabled": True})
    assert "문이 꺼져 있습니다" in b2.handle("/사기 005930 500000") and b2.pending is None
    fake_running(tmp_path, "paper", ext=False)
    assert "--ext 없이" in b2.handle("/팔기 005930")


def test_read_placeholder_and_opts(tmp_path):
    b, _, _ = make_bot(tmp_path)
    assert "모듈 없음" in b.handle("/읽기 sample")
    p = tmp_path / "bot.yaml"
    p.write_text("trade_enabled: true\nbuy_hold_min: abc\nbogus: 1\ntrade_target: live\n", encoding="utf-8")
    o, warn = bot.load_opts(p)
    assert o["trade_enabled"] is True and o["buy_hold_min"] == 30 and o["trade_target"] == "paper" and len(warn) == 3


# ---------- 보내기 ----------
def test_send_429_waits_retry_after():
    http = FakeHTTP()
    http.plan = [{"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 3}}]
    slept = []
    tg = telegram.Telegram(FAKE, 777, session=http, sleep=slept.append, min_interval=0)
    tg.send("안녕")
    assert slept == [3.0] and http.sent == ["안녕"] and tg.waited_429 == [3.0]


def test_send_interval_one_per_second():
    http, slept, clk = FakeHTTP(), [], Clock()
    tg = telegram.Telegram(FAKE, 777, session=http, sleep=lambda s: (slept.append(s), setattr(clk, "t", clk.t + s)),
                           clock=clk)
    tg.send("하나")
    clk.t += 0.3
    tg.send("둘")
    assert len(http.sent) == 2 and abs(slept[0] - telegram.SEND_INTERVAL_S + 0.3) < 1e-9


def test_notifier_batches_same_kind():
    class TG:
        def __init__(self):
            self.out = []

        def send(self, t):
            self.out.append(t)
    tg = TG()
    n = telegram.Notifier(tg, prefix="[시뮬] ", start=False)
    for code in ("005930", "000660", "035420"):
        n({"event": "fill", "code": code, "side": "buy", "qty": 3, "price": 61300, "cum_filled": 3})
    n({"event": "order_sent", "code": "005930", "side": "buy", "qty": 3, "mode": "market"})
    n({"event": "bar", "code": "005930"})                                   # 알림 대상 아님
    msgs = n.drain_batch()
    assert len(msgs) == 2 and msgs[0].count("[시뮬] [체결]") == 3 and "[시뮬] [주문]" in msgs[1]
    n.send_batch(msgs)
    assert tg.out == msgs and n.sent == 2


def test_outgoing_has_no_secrets():
    odno, acct = "0000" + "917355", "4321" + "8765" + "-01"
    home = str(Path.home())
    extra = {"odno": odno, "raw": {"CANO": acct, "appkey": "PS" + "x" * 30}, "account": acct, "code": "005930",
             "side": "buy", "qty": 1, "price": 61300, "state": "cancelled", "reason": "stop file",
             "error": f"{home}\\KIS\\config 읽기 실패 {acct}", "detail": acct, "changes": {"amount": [1, 2]},
             "summary": {"orders_done": {"filled": 1}, "positions": {"005930": {"qty": 1, "odno": odno}}}}
    for e in telegram.Notifier.EVENTS:
        s = telegram.format_event({"event": e, **extra}) or ""
        assert odno not in s and acct not in s and "PSxxxx" not in s and home not in s, e


def test_start_refused_reason_texts():
    f = telegram.format_event
    assert "정지 파일" in f({"event": "start_refused", "reason": "stop file"})
    assert "다른 실행기" in f({"event": "start_refused", "reason": "lock held by pid 42"})
    assert "상한" in f({"event": "start_refused", "reason": "amount over cap", "detail": "1회 금액이 상한을 넘음"})
    s = f({"event": "stop", "reason": "end", "summary": {"orders_done": {"filled": 6, "cancelled": 2}}})
    assert "체결 6 · 취소 2" in s and "{" not in s


def test_sim_log_follower_new_lines_only(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(json.dumps({"event": "fill"}) + "\n", encoding="utf-8")
    f = bot.LogFollower(lambda: p)
    assert f.new() == []                                                    # 켜기 전 것은 보내지 않음
    with p.open("ab") as fh:
        fh.write((json.dumps({"event": "order_sent", "code": "005930"}) + "\n").encode("utf-8") + b'{"event": "fi')
    assert [r["event"] for r in f.new()] == ["order_sent"]
    with p.open("ab") as fh:
        fh.write(b'll"}\n')
    assert [r["event"] for r in f.new()] == ["fill"]                        # 반쯤 쓴 줄은 다음 번에 온전히
