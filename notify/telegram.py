"""텔레그램 — 알림 보내기와 명령 받기(본인만). 선택 기능: 설정 파일이 없거나 비어 있으면 아무것도 하지 않는다.

설정: 홈 폴더 KIS/config/telegram.yaml (사용자가 직접 넣는다 — 이 프로그램은 값을 출력하지 않는다)
    bot_token: <BotFather 가 준 토큰>   # 숫자:글자 꼴
    owner_id: 123456789           # 본인 텔레그램 숫자 id(이 사람의 개인 대화 명령만 받는다)
지키는 것
  - 보내지 않는 것: 계좌번호, 앱키·시크릿·접근 토큰, 봇 토큰, 비밀번호.
  - 시세를 채널·그룹으로 방송하지 않는다(한투 약관 5조③ "개인의 업무에 한하여") → 본인 개인 대화로만.
  - 토큰은 요청 주소에 들어가므로, 오류 메시지·기록에서 토큰을 가린다.
  - 보내는 글에서 사용자 폴더 경로 · 계좌번호 꼴을 한 번 더 가린다(오류 문구에 섞여 들어올 수 있어서).
보내기 속도: 한 프로세스 안에서 SEND_INTERVAL_S 마다 한 건(여러 스레드가 함께 써도). 알림이 몰리면 같은 종류를 묶어 한 글로.
  429(너무 많이 보냄)를 받으면 응답의 retry_after 초만큼 쉬고 다시 보낸다.
몰림 줄이기: 거르기에 막힌 진입(entry_filtered)은 (날 · 거르기 · 종목)마다, 검토 답 마감(review_expired)은 (날 · 종목)마다,
  버린 바깥 신호(ext_rejected)는 (날 · 이유 · 출처)마다 한 번만 보낸다(사람이 보낸 신호의 버림은 늘 보냄).
  같은 거르기에 여러 종목이 막히면 종목을 모아 한 줄로.
"""
from __future__ import annotations

import os
import queue
import re
import threading
import time
from pathlib import Path

import requests

CONFIG_FILE = (Path(os.environ["KIS_CONFIG_DIR"]) if os.environ.get("KIS_CONFIG_DIR")      # kis/config.py 와 같은 규칙
               else Path.home() / "KIS" / "config") / "telegram.yaml"
API = "https://api.telegram.org"
_TOKEN_RX = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{20,}")
_HOME_RX = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s\"'`]+", re.I)
_ACCOUNT_RX = re.compile(r"(?<!\d)\d{8}-\d{2}(?!\d)")

# 보내기 한도 — Telegram Bot FAQ 'My bot is hitting limits, how do I avoid this?'(https://core.telegram.org/bots/faq,
#   2026-11-23 확인) 원문: "In a single chat, avoid sending more than one message per second." ·
#   "For bulk notifications, bots are not able to broadcast more than about 30 messages per second".
#   이 프로그램은 본인 대화 하나에만 보내므로 앞 문장(대화 하나당 초당 1건)을 따른다. 짧은 몰림은 허용되나
#   넘으면 429 를 받는다는 문장도 같은 항목에 있다 → RETRY_429.
SEND_INTERVAL_S = 1.0          # 한 대화에 보내는 최소 간격(초) — 위 원문의 '초당 1건'
MAX_TEXT = 4000                # 글 한 건 최대 글자(본문 한도보다 작게 둠)
RETRY_429 = 3                  # 429 를 받았을 때 retry_after 만큼 쉬고 다시 보내는 횟수


def redact(text: str, token: str = "") -> str:
    s = str(text)
    if token:
        s = s.replace(token, "<봇토큰>")
    s = _TOKEN_RX.sub("<봇토큰>", s)
    home = str(Path.home())
    for h in (home, home.replace("\\", "/")):
        if len(h) > 3:
            s = s.replace(h, "~")
    s = _HOME_RX.sub("~", s)
    return _ACCOUNT_RX.sub("<계좌>", s)


def load_config(path: Path = CONFIG_FILE) -> dict | None:
    """채워져 있으면 {"token", "owner_id"}, 아니면 None(기능 꺼짐)."""
    if not path.exists():
        return None
    import yaml
    d = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    tok, owner = str(d.get("bot_token") or "").strip(), d.get("owner_id")
    if not _TOKEN_RX.fullmatch(tok) or not str(owner or "").strip().lstrip("-").isdigit():
        return None
    return {"token": tok, "owner_id": int(owner)}


def status() -> str:
    """채워짐/비어 있음만 알려 준다(값은 돌려주지 않음)."""
    if not CONFIG_FILE.exists():
        return "telegram.yaml 없음(텔레그램 꺼짐)"
    return "telegram.yaml 채워짐" if load_config() else "telegram.yaml 비었거나 형식이 맞지 않음(텔레그램 꺼짐)"


class Telegram:
    def __init__(self, token: str, owner_id: int, session=None, timeout: float = 10.0,
                 min_interval: float = SEND_INTERVAL_S, sleep=time.sleep, clock=time.monotonic):
        self.token = token
        self.owner_id = int(owner_id)
        self.http = session or requests.Session()
        self.timeout = timeout
        self.min_interval = min_interval
        self.sleep, self.clock = sleep, clock
        self._send_lock = threading.Lock()
        self._last_send = None
        self.waited_429: list[float] = []          # 429 로 쉰 초(시험 · 기록용)

    def _call(self, method: str, http_timeout: float | None = None, **params) -> dict:
        for attempt in range(RETRY_429 + 1):
            try:
                body = {k: v for k, v in params.items() if v is not None}
                r = self.http.post(f"{API}/bot{self.token}/{method}", json=body, timeout=http_timeout or self.timeout)
                d = r.json()
            except Exception as e:                     # 주소에 토큰이 들어 있으므로 메시지를 가린다
                raise RuntimeError(redact(f"{type(e).__name__}: {e}", self.token)) from None
            if d.get("ok"):
                return d.get("result")
            if d.get("error_code") == 429 and attempt < RETRY_429:
                try:
                    wait = float((d.get("parameters") or {}).get("retry_after") or 1)
                except (TypeError, ValueError):
                    wait = 1.0
                wait = min(max(wait, 0.5), 300.0)
                self.waited_429.append(wait)
                self.sleep(wait)
                continue
            raise RuntimeError(redact(f"텔레그램 {method} 실패: {d.get('error_code')} {d.get('description')}", self.token))
        raise RuntimeError(f"텔레그램 {method} 실패: 429 반복")

    def send(self, text: str) -> dict:
        """본인 개인 대화로만. 여러 스레드가 불러도 min_interval 간격을 지킨다."""
        with self._send_lock:
            if self._last_send is not None:
                gap = self._last_send + self.min_interval - self.clock()
                if gap > 0:
                    self.sleep(gap)
            try:
                return self._call("sendMessage", chat_id=self.owner_id, text=redact(text, self.token)[:MAX_TEXT],
                                  disable_web_page_preview=True)
            finally:
                self._last_send = self.clock()

    def updates(self, offset: int | None, wait_s: int = 30) -> list[dict]:
        return self._call("getUpdates", http_timeout=wait_s + 10, offset=offset, timeout=wait_s,
                          allowed_updates=["message"]) or []

    def is_owner_message(self, upd: dict, not_before: int) -> tuple[bool, str]:
        """본인 개인 대화의 새 메시지인지. 반환 (받을지, 이유)."""
        m = upd.get("message") or {}
        frm, chat = m.get("from") or {}, m.get("chat") or {}
        if frm.get("id") != self.owner_id:
            return False, "본인 아님"
        if chat.get("type") != "private":
            return False, "개인 대화 아님"
        if int(m.get("date", 0)) < not_before:
            return False, "봇이 켜지기 전 메시지(옛 명령 버림)"
        return True, ""


class Notifier:
    """실행기 기록(JsonlLog)에 붙어 중요한 사건만 텔레그램으로 보낸다. 보내기는 별도 스레드 — 매매 흐름을 막지 않는다."""

    EVENTS = {"start", "stop", "start_refused", "order_sent", "order_unknown", "fill", "order_done", "cancel_stuck",
              "reconcile", "error", "risk_block", "ext_signal", "ext_rejected", "entry_filtered", "ext_rule_exit",
              "ext_owned_left", "review_unavailable", "review_expired", "settings_changed", "settings_rejected",
              "check_needed"}
    GATHER_S = 0.5                                  # 첫 알림 뒤 이만큼 모았다가 같은 종류를 묶어 보낸다
    ONCE_PER_DAY = ("entry_filtered", "review_expired", "ext_rejected")   # 같은 열쇠는 하루 한 번만
    MERGE = ("entry_filtered", "review_expired")    # 한 번에 모인 것은 종목을 모아 한 줄로
    MERGE_MAX_CODES = 30                            # 한 줄에 적는 종목 수(넘으면 '외 N')

    def __init__(self, tg: Telegram | None, on_error=None, prefix: str = "", start: bool = True):
        """prefix = 글 머리(예: 시뮬레이션 알림 '[시뮬] '). start=False 면 보내는 스레드를 띄우지 않는다(시험용)."""
        self.tg = tg
        self.q: queue.Queue = queue.Queue(maxsize=200)
        self.on_error = on_error
        self.prefix = prefix
        self.sent = 0
        self.dropped = 0
        self.suppressed = 0                         # 하루 한 번 규칙으로 보내지 않은 수
        self._said: set = set()
        self._said_day = ""
        self._busy = False
        if tg and start:
            threading.Thread(target=self._loop, daemon=True).start()

    @staticmethod
    def _gate_name(gate) -> str:
        """entry_filtered 의 gate 칸('이름: 이유')에서 이름만 — 이유 글이 바뀌어도 같은 거르기로 센다."""
        return str(gate or "").split(":", 1)[0].strip()[:60]

    def once_key(self, rec: dict) -> tuple | None:
        """하루 한 번 규칙의 열쇠(날 빼고). None = 늘 보냄."""
        e = rec.get("event")
        if e == "entry_filtered":
            return e, self._gate_name(rec.get("gate")), str(rec.get("code", ""))
        if e == "review_expired":
            return e, str(rec.get("code", "")), str(rec.get("on_timeout", ""))
        if e == "ext_rejected":
            src = str(rec.get("source") or "")
            if src.startswith(("telegram", "manual")):
                return None                         # 사람이 보낸 신호가 버려진 것은 매번 알린다
            return e, str(rec.get("reason", ""))[:80], src[:40]
        return None

    def _first_today(self, rec: dict) -> bool:
        key = self.once_key(rec)
        if key is None:
            return True
        day = str(rec.get("at") or "")[:10] or time.strftime("%Y-%m-%d")
        if day != self._said_day:                   # 날이 바뀌면 처음부터
            self._said_day, self._said = day, set()
        if key in self._said:
            self.suppressed += 1
            return False
        self._said.add(key)
        return True

    def __call__(self, rec: dict) -> None:
        if not self.tg or rec.get("event") not in self.EVENTS:
            return
        if rec.get("event") in self.ONCE_PER_DAY and not self._first_today(rec):
            return
        msg = format_event(rec)
        if msg:
            e = str(rec.get("event"))
            merge = None
            if e in self.MERGE:                     # (묶을 열쇠, 원 사건) — 꺼낼 때 종목을 모아 다시 글로 만든다
                gk = self._gate_name(rec.get("gate")) if e == "entry_filtered" else str(rec.get("on_timeout", ""))
                merge = (gk, rec)
            try:
                self.q.put_nowait((e, self.prefix + msg, merge))
            except queue.Full:
                self.dropped += 1

    def _merged_text(self, recs: list[dict]) -> str:
        codes = []
        for r in recs:
            c = str(r.get("code", ""))
            if c and c not in codes:
                codes.append(c)
        shown = ", ".join(codes[:self.MERGE_MAX_CODES]) + (f" 외 {len(codes) - self.MERGE_MAX_CODES}"
                                                           if len(codes) > self.MERGE_MAX_CODES else "")
        return self.prefix + (format_event({**recs[0], "code": shown}) or "")

    def drain_batch(self, first=None) -> list[str]:
        """쌓인 알림을 모두 꺼내 같은 종류끼리 한 글로 묶는다(종류는 처음 나온 순서, 글 한 건 MAX_TEXT 이하).
        거르기 · 검토 마감은 같은 거르기(같은 처리) 안의 종목을 모아 한 줄로."""
        items = [first] if first else []
        while True:
            try:
                items.append(self.q.get_nowait())
            except queue.Empty:
                break
        if self.dropped:
            items.append(("dropped", f"{self.prefix}[알림 일부 버림] 몰려서 {self.dropped}건을 보내지 못함 — 기록 파일 확인", None))
            self.dropped = 0
        groups: dict[str, list] = {}
        for it in items:
            kind, text, merge = (tuple(it) + (None,))[:3]
            g = groups.setdefault(kind, [])
            if merge is None:
                g.append(text)
                continue
            gk, rec = merge
            slot = next((x for x in g if isinstance(x, list) and x[0] == gk), None)
            if slot is None:
                g.append([gk, [rec]])
            else:
                slot[1].append(rec)
        out = []
        for parts in groups.values():
            texts = [self._merged_text(p[1]) if isinstance(p, list) else p for p in parts]
            buf = ""
            for t in texts:
                if buf and len(buf) + 1 + len(t) > MAX_TEXT:
                    out.append(buf)
                    buf = ""
                buf = f"{buf}\n{t}" if buf else t
            if buf:
                out.append(buf)
        return out

    def send_batch(self, msgs: list[str]) -> None:
        for m in msgs:
            try:
                self.tg.send(m)
                self.sent += 1
            except Exception as e:
                if self.on_error:
                    self.on_error(redact(str(e))[:200])

    def _loop(self):
        while True:
            first = self.q.get()
            self._busy = True
            try:
                time.sleep(self.GATHER_S)
                self.send_batch(self.drain_batch(first))
            except Exception:
                pass
            finally:
                self._busy = False

    def flush(self, wait_s: float = 5.0) -> None:
        end = time.time() + wait_s
        while (not self.q.empty() or self._busy) and time.time() < end:
            time.sleep(0.1)


def _side(s: str) -> str:
    return "매수" if s == "buy" else "매도" if s == "sell" else s


_DONE_WORDS = {"filled": "체결", "cancelled": "취소", "rejected": "거부", "expired": "만료"}


def _won(v) -> str:
    try:
        return f"{float(v):,.0f}원"
    except (TypeError, ValueError):
        return "-"


def summary_line(s: dict) -> str:
    """하루 요약(실행기 stop 사건의 summary) → 한 줄. 주문번호 · 계좌는 summary 에 없다."""
    s = s if isinstance(s, dict) else {}
    od = s.get("orders_done") if isinstance(s.get("orders_done"), dict) else {}
    done = " · ".join(f"{_DONE_WORDS.get(k, k)} {v}" for k, v in od.items()) or "없음"
    pos = s.get("positions") if isinstance(s.get("positions"), dict) else {}
    held = ", ".join(f"{k} {(v or {}).get('qty')}주" for k, v in pos.items()) or "없음"
    return (f"끝난 주문 {done} · 오늘 진입 {s.get('entries_today', 0)}회 · 실현 {_won(s.get('realized', 0))} · "
            f"평가 {_won(s.get('unrealized', 0))}(모의 · 수수료 전) · 보유 {held}")


def format_event(r: dict) -> str | None:
    """사건 하나 → 짧은 알림 문장(가린 뒤). 정해 둔 칸만 쓰므로 주문번호(odno) · 원응답(raw) · 계좌는 들어가지 않는다. 종목 코드는 예시."""
    try:
        msg = _format(r)
    except Exception:
        msg = f"[알림] {str(r.get('event', ''))[:30]}" if isinstance(r, dict) else None
    return redact(msg) if msg else None


def _format(r: dict) -> str | None:
    e, code = r.get("event"), r.get("code", "")
    if e == "start":
        return (f"[시작] {r.get('mode')} · 종목 {','.join(r.get('codes') or [])} · 방식 {r.get('order_mode')} · "
                f"1회 {_won(r.get('amount'))} · 오늘 진입 {r.get('entries_today', 0)}회"
                + (" · 바깥 신호 문 켜짐" if r.get("ext") else ""))
    if e == "start_refused":
        why = str(r.get("reason") or "")
        if why == "stop file":
            return "[시작 안 함] 정지 파일이 있음 — /켜기 로 지우고 시작"
        if why.startswith("lock held"):
            return "[시작 안 함] 다른 실행기가 이미 돌고 있음(잠금) — /상태 로 확인"
        if why == "amount over cap":
            return f"[시작 안 함] 금액이 상한을 넘음 — {r.get('detail', '')}"
        return f"[시작 안 함] {r.get('detail') or why}"
    if e == "stop":
        return f"[끝] {r.get('reason')} · " + summary_line(r.get("summary") or {})
    if e == "settings_changed":
        ch = r.get("changes") if isinstance(r.get("changes"), dict) else {}
        return "[설정 바뀜] " + " · ".join(f"{k}: {v[0]} → {v[1]}" for k, v in ch.items()
                                          if isinstance(v, (list, tuple)) and len(v) == 2)
    if e == "settings_rejected":
        return f"[설정 적용 안 됨] {str(r.get('error', ''))[:150]} — 기존 설정으로 계속"
    if e == "order_sent":
        how = "시장가" if r.get("mode") == "market" else f"지정가 {r.get('price', 0):,}원"
        return f"[주문] {code} {_side(r.get('side'))} {r.get('qty')}주 · {how}"
    if e == "order_unknown":
        return f"[응답 없음] {code} {_side(r.get('side'))} — 다시 보내지 않고 조회로 확인 중"
    if e == "fill":
        return f"[체결] {code} {_side(r.get('side'))} {r.get('qty')}주 × {r.get('price', 0):,.0f}원 (누적 {r.get('cum_filled')})"
    if e == "order_done" and r.get("state") in ("cancelled", "rejected"):
        return f"[{'취소' if r.get('state') == 'cancelled' else '거부'}] {code} {_side(r.get('side'))} 체결 {r.get('filled')}/{r.get('qty')} · {r.get('note', '')}"
    if e == "check_needed":                                 # 간단 장부(공개판)가 체결을 끝까지 확인하지 못함
        return f"[확인 필요] {code} {_side(r.get('side'))} 체결 {r.get('filled')}/{r.get('qty')} · {str(r.get('note', ''))[:120]}"
    if e == "cancel_stuck":
        return f"[확인 필요] {code} 주문 취소가 {r.get('tries')}번 안 됨"
    if e == "reconcile" and r.get("diffs"):
        return f"[잔고 대조] 차이 {r.get('diffs')}"
    if e == "error":
        return f"[오류] {code} {r.get('where', '')}: {str(r.get('error', ''))[:150]}"
    if e == "risk_block":                                   # (날, 종류)마다 한 번 — 정지 파일 · 진입 상한 · 일 손실 한도
        return f"[새 진입 멈춤] {r.get('reason', '')}"
    if e == "ext_signal":
        act = {"entry": "진입", "exit": "청산"}.get(r.get("action"), r.get("action"))
        return f"[바깥 신호] {r.get('source', '')} → {code} {act}"
    if e == "ext_rejected":
        return f"[바깥 신호 버림] {r.get('reason', '')}"
    if e == "entry_filtered":
        return f"[거르기] {code} 새 진입 막음 — {str(r.get('gate', ''))[:80]}"
    if e == "ext_rule_exit":
        return f"[바깥 규칙 청산] {code} {r.get('reason', '')}"
    if e == "ext_owned_left":
        return f"[주의] 바깥이 산 종목 표시가 남은 채 문 없이 시작 — 전략 청산이 팔 수 있음: {','.join(r.get('codes') or [])}"
    if e == "review_unavailable":
        how = "전략대로 진입" if r.get("on_timeout") == "pass" else "진입하지 않음"
        return (f"[검토 판단 응답 없음] 전략 진입: {how} — 검토 표시(review.json)가 만료된 채 남아 있음. "
                "판단을 쓰지 않을 거면 /검토끄기")
    if e == "review_expired":
        how = "전략대로 진입" if r.get("on_timeout") == "pass" else "진입하지 않음"
        return f"[검토 답 마감] {code} {how}"
    return None
