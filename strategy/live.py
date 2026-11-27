"""실시간 실행기 — 모의 전용. 기본은 dry-run(주문을 보내지 않고 계획 가격에 바로 체결된 것으로 침).

흐름(종목 1~N개, 봉 tf 분):
  봉이 닫히면(+ settle 초) → 당일 1분봉 받기(처음엔 lookback 개, 그다음엔 최신 30개만 이어 붙임) → 진행 중인 봉 빼기
  → tf 분봉으로 묶기 → 전략 signals → '방금 닫힌 봉'의 신호만 본다
  → long_entry: 위험 점검(정지 파일·진입 상한·일 손실 한도) → 주문 방식(execution.modes: limit/market, 금액 기준 수량) → OMS
  → long_exit : 이 실행기가 들고 있는 수량(체결된 것) 전부 → OMS
  봉과 봉 사이에는 poll_s 초마다 진행 중 주문의 상태를 조회한다(체결 반영·기다림 상한 뒤 취소·청산 시장가).
주문: 장부(기본 oms.manager 주문 관리, 없는 판은 oms.simple 간단 장부) → 증권사 연결(oms.broker). paper_order=True 면 한국투자 **모의** 계좌, 아니면 DryRunBroker.
  실전 주문 경로는 없다(kis.order 가 모의 설정이 아니면 거부).
포지션 = 체결된 수량만(보내자마자 산 것으로 적지 않는다). state/oms_{paper|dry}.json 에 저장 → 다음 날·재시작에도 이어 감,
  모의는 시작·끝에 잔고와 대조(실행기가 산 것만 관리, 기존 보유분은 건드리지 않음).
멈추기: state/stop 파일을 만들면 새 주문을 멈추고 진행 중 주문을 취소한 뒤 끝낸다. 시작할 때 이 파일이 있으면 시작하지 않는다.
기록: logs/strategy_live_YYYYMMDD.jsonl — 모든 단계에 ms 시각(봉 마감·조회·신호·주문 보냄/응답·체결 확인·취소). 계좌·키·토큰은 적지 않는다.
체결통보(선택): notice=True 면 H0STCNI9(HTS ID 필요)로 받아 기록만 한다(상태 판정은 조회로).
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from execution.modes import OrderSettings, make_intent
from execution.modes import round_tick as _round_tick
from kis import config, order, quote
from oms.broker import DryRunBroker, KisPaperBroker
from oms.risk import RiskGate
from strategy import data
from strategy import ext as extdoor
from strategy.base import Strategy

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
STATE_DIR = ROOT / "state"
PAPER_GAP = 0.5      # 모의 호출 간격(초) — 공식 예제 kis_auth.py 모의 smart_sleep


def now_ms() -> int:
    return int(time.time() * 1000)


round_tick = _round_tick     # 옛 이름(시험 · 앞선 도구 호환) — 정의는 execution.modes


def _err(e: BaseException) -> str:
    """오류 문구를 기록용으로 — 사용자 폴더 경로(윈도우 사용자 이름이 들어 있음)는 ~ 로 가린다."""
    s = str(e)
    home = str(Path.home())
    for h in (home, home.replace("\\", "/")):
        s = s.replace(h, "~")
    return f"{type(e).__name__}: {s[:200]}"


def pid_alive(pid: int) -> bool:
    """윈도우에서 그 pid 프로세스가 살아 있는지(ctypes, 다른 패키지 없이)."""
    if pid <= 0:
        return False
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))         # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    except Exception:
        return False


def paper_lock_pid(path: Path) -> int:
    """그 잠금 파일을 살아 있는 프로세스가 쥐고 있으면 그 pid, 아니면 0(읽기만 — 잠금을 건드리지 않는다)."""
    try:
        other = int(Path(path).read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0
    return other if other and pid_alive(other) else 0


def acquire_lock(path: Path) -> int:
    """잠금 파일(pid)을 잡는다. 0 = 잡음, 그 밖 = 이미 돌고 있는 pid. 죽은 프로세스의 잠금은 넘겨받는다."""
    import os
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return 0
        except FileExistsError:
            try:
                other = int(path.read_text(encoding="utf-8").strip() or 0)
            except (OSError, ValueError):
                other = 0
            if other and other != os.getpid() and pid_alive(other):
                return other
            try:
                path.unlink()
            except OSError:
                return other or -1
    return -1


def release_lock(path: Path) -> None:
    import os
    try:
        if path.read_text(encoding="utf-8").strip() == str(os.getpid()):
            path.unlink()
    except OSError:
        pass


class JsonlLog:
    def __init__(self, day: str | None = None):
        LOG_DIR.mkdir(exist_ok=True)
        self.path = LOG_DIR / f"strategy_live_{day or datetime.now().strftime('%Y%m%d')}.jsonl"
        self.lock = threading.Lock()
        self.listeners: list = []          # 기록 한 줄마다 불림(텔레그램 알림 등) — 실패해도 기록은 남는다

    def write(self, event: str, **kw) -> dict:
        rec = {"ts_ms": now_ms(), "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], "event": event, **kw}
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        for fn in self.listeners:
            try:
                fn(rec)
            except Exception:
                pass
        return rec

    def today_events(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


class LiveRunner:
    def __init__(self, codes: list[str], strategy: Strategy, tf_minutes: int = 1, amount: float = 1_000_000,
                 max_orders: int = 5, lookback: int = 120, paper_order: bool = False, data_mode: str = "real",
                 settle_sec: float = 3.0, notice: bool = False, printer=print, order_mode: str = "limit",
                 wait_s: float = 50.0, daily_loss_limit: float = 30_000, poll_s: float = 2.0, broker=None,
                 notify: bool = False, book=None, ext: bool = False, max_amount: float | None = None):
        """book = 주문 장부 클래스(기본 oms.manager.OrderManager). ext = 바깥 신호 문을 연다(기본 꺼짐, strategy/ext.py).
        max_amount = 1회 금액 상한(설정값, 시작할 때만) — 코드의 절대 상한 kis.order.MAX_ORDER_WON 보다 높일 수 없다.
        금액이 상한을 넘으면 깎아서 돌지 않고 run() 이 시작을 거부한다."""
        self.codes = codes
        self.strategy = strategy
        self.tf = tf_minutes
        self.max_amount_asked = None if max_amount is None else float(max_amount)
        self.max_amount = min(self.max_amount_asked or float(order.MAX_ORDER_WON), float(order.MAX_ORDER_WON))
        self.amount_asked = float(amount)
        self.amount = min(float(amount), self.max_amount)
        self.settings = OrderSettings(order_mode, self.amount, wait_s)
        self.max_orders = max_orders
        self.lookback = lookback
        self.paper_order = paper_order
        self.data_mode = data_mode
        self.settle = settle_sec
        self.notice = notice
        self.poll_s = poll_s
        self.say = printer
        self.log = JsonlLog()
        self.bars: dict[str, pd.DataFrame] = {}
        self.last_bar: dict[str, pd.Timestamp] = {}
        self.order_cfg = None
        if broker is None:
            if paper_order:
                cfg = config.load("paper")
                if not cfg.is_paper:                       # 이중 확인 — 모의 설정이 아니면 멈춘다
                    raise PermissionError("모의 설정이 아닙니다 — 주문하지 않습니다")
                self.order_cfg = cfg
                broker = KisPaperBroker(cfg, data.Pacer(PAPER_GAP))
            else:
                broker = DryRunBroker()
        self.broker = broker
        if broker.dry:                                    # dry-run 기록은 따로 — 같은 날 모의 운행 기록에 섞이지 않게
            self.log.path = LOG_DIR / f"strategy_live_dry_{datetime.now():%Y%m%d}.jsonl"
        STATE_DIR.mkdir(exist_ok=True)
        if book is None:                                  # 주문 관리(OMS)가 없는 판(공개판)은 간단 장부로
            import importlib.util
            if importlib.util.find_spec("oms.manager") is not None:
                from oms.manager import OrderManager as book
            else:
                from oms.simple import SimpleBook as book
        self.oms = book(broker, self.log, STATE_DIR / f"oms_{'dry' if broker.dry else 'paper'}.json", say=printer)
        self.oms.restore()
        self.risk = RiskGate(STATE_DIR / "stop", max_orders, daily_loss_limit)
        # dry-run 은 따로(모의와 같은 state 폴더 — 함께 돌면 서로 덮어쓰고, 끝날 때 상대 것을 지웠다)
        self.heartbeat_path = STATE_DIR / ("heartbeat_dry.json" if broker.dry else "heartbeat.json")
        self.lock_path = STATE_DIR / f"run_{'dry' if broker.dry else 'paper'}.lock"
        self._hb_last = 0.0
        self.notifier = None
        if notify:                                          # 텔레그램(선택) — telegram.yaml 이 채워져 있을 때만
            from notify import telegram
            tc = telegram.load_config()
            if tc:
                self.notifier = telegram.Notifier(telegram.Telegram(tc["token"], tc["owner_id"]),
                                                  on_error=lambda m: self.log.write("notify_error", error=m))
                self.log.listeners.append(self.notifier)
        self.oms.price_fn = self._price_safe              # 사다리 다음 단계 가격은 그 순간 현재가로
        self.oms.switch_fn = self._switch_intent
        self.settings_path = None                         # 실행 중 설정 바꾸기(watch_settings)
        self.settings_loader = None
        self._settings_mtime = None
        self._settings_cur: dict = {}
        self._risk_said: set = set()                      # risk_block 은 (날, 종류)마다 한 번만 기록
        self.ext = extdoor.FileDoor(STATE_DIR / "ext", self.log) if ext else None   # None = 문 꺼짐(예전과 같은 동작)

    # ---------- 상태 ----------
    @property
    def positions(self) -> dict[str, dict]:
        return self.oms.positions

    def entries_today(self) -> int:
        """오늘 보낸 새 진입(매수) 수 — 하루 상한은 이것만 센다(청산은 상한 밖)."""
        return self.oms.entries_today

    orders_today = entries_today   # 옛 이름(앞선 호출부 호환)

    # ---------- 봉 ----------
    def _update_bars(self, code: str) -> tuple[pd.DataFrame, int, int]:
        t0 = now_ms()
        first = code not in self.bars
        new = data.fetch_minute_today(code, bars=self.lookback if first else 30, mode=self.data_mode)
        t1 = now_ms()
        if first:
            hist = data.read_cache(code, "1m")            # 앞 날짜 1분봉 캐시가 있으면 앞에 붙임(지표 예열용, 호출 없음)
            today = datetime.now().strftime("%Y%m%d")
            hist = hist[hist["time"].dt.strftime("%Y%m%d") < today]
            parts = [x for x in (hist, new) if not x.empty]
            base = pd.concat(parts) if parts else data.empty_bars()
        else:
            base = pd.concat([self.bars[code], new])
        base = base.drop_duplicates("time", keep="last").sort_values("time").reset_index(drop=True)
        self.bars[code] = base
        return base, t0, t1

    def closed_bars(self, code: str, now: datetime | None = None) -> pd.DataFrame:
        one = data.drop_unfinished_minute(self.bars.get(code, data.empty_bars()), now, 1)
        one = one[one["volume"] > 0]      # 거래 없는 분(장 마감 뒤 채움 봉 등)은 뺀다 — 트레이딩뷰는 체결 없는 분에 봉을 그리지 않음(추정)
        tf = data.resample_minutes(one, self.tf)
        return data.drop_unfinished_minute(tf, now, self.tf)

    # ---------- 한 번 돌기 ----------
    def step(self, bar_close_ms: int | None = None) -> list[dict]:
        """모든 종목에 대해 방금 닫힌 봉을 처리. 돌려주는 값 = 이번에 만든 계획/주문 목록."""
        actions = []
        self.reload_settings()
        if self.ext is not None:
            actions += extdoor.step(self)                     # 바깥 신호는 봉 처리 첫머리에서만(최대 tf 분 늦음)
        for code in self.codes:
            try:
                _, t0, t1 = self._update_bars(code)
                bars = self.closed_bars(code, self.now_dt())
                if self.ext is not None and len(bars):
                    self.ext.publish_bars(code, bars)
                if len(bars) < 2:
                    self.log.write("bar", code=code, note="봉 부족", n_bars=len(bars), t_fetch_start_ms=t0, t_fetch_end_ms=t1)
                    self.say(f"  {code}: 닫힌 봉 {len(bars)}개 — 부족")
                    continue
                last_t = bars["time"].iloc[-1]
                if self.last_bar.get(code) == last_t:
                    continue                                  # 이미 처리한 봉
                self.last_bar[code] = last_t
                self.oms.current[code] = int(bars["close"].iloc[-1])
                sig = self.strategy.run(bars)
                row = sig.iloc[-1]
                t_sig = self.clock_ms()
                close_ms = bar_close_ms or int((last_t + pd.Timedelta(minutes=self.tf)).timestamp() * 1000)
                self.log.write("bar", code=code, bar_time=last_t.isoformat(), close=float(bars["close"].iloc[-1]),
                               n_bars=len(bars), t_bar_close_ms=close_ms, t_fetch_start_ms=t0, t_fetch_end_ms=t1)
                kind = "long_entry" if row["long_entry"] else "long_exit" if row["long_exit"] else None
                extra = {k: (float(row[k]) if pd.notna(row[k]) else None) for k in sig.columns
                         if k not in ("long_entry", "long_exit", "short_entry", "short_exit")}
                if kind is None:
                    self.say(f"  {code} {last_t:%H:%M} 봉: 신호 없음 (종가 {bars['close'].iloc[-1]:,.0f}, {extra})")
                    continue
                self.log.write("signal", code=code, bar_time=last_t.isoformat(), kind=kind, t_signal_ms=t_sig,
                               ms_after_bar_close=t_sig - close_ms, indicators=extra)
                self.say(f"  {code} {last_t:%H:%M} 봉: 신호 {kind} ({extra})")
                if kind == "long_exit" and self.ext is not None and extdoor.strategy_exit(self, code):
                    continue                                     # 바깥 규칙으로 파는 종목 — 전략 청산으로는 팔지 않음
                if kind == "long_exit" and self.oms.held(code):
                    self.oms.pending_exit.add(code)              # 다 팔릴 때까지 다음 봉에도 다시 낸다
                elif kind == "long_entry":
                    self.oms.pending_exit.discard(code)          # 전략이 다시 매수 쪽 — 대기 중 청산은 거둔다
                act = self._act(code, kind, t_sig)
                if act:
                    actions.append(act)
            except Exception as e:                            # 한 종목 오류가 다른 종목을 멈추지 않게
                self.log.write("error", code=code, where="step", error=f"{type(e).__name__}: {str(e)[:200]}")
                self.say(f"  {code}: 오류 {type(e).__name__}: {str(e)[:120]}")
        for code in sorted(self.oms.pending_exit):           # 청산 대기: 들고 있고 진행 중 주문이 없으면 다시 낸다
            if code not in self.codes or self.oms.active_for(code):
                continue
            if not self.oms.held(code):
                self.oms.pending_exit.discard(code)
                continue
            try:
                self.log.write("exit_retry", code=code, held=self.oms.held(code))
                act = self._act(code, "long_exit", self.clock_ms())
                if act:
                    actions.append(act)
            except Exception as e:
                self.log.write("error", code=code, where="exit_retry", error=f"{type(e).__name__}: {str(e)[:200]}")
        return actions

    def _act(self, code: str, kind: str, t_sig: int, amount: float | None = None,
             source: dict | None = None) -> dict | None:
        """amount · source 는 바깥 신호(문)일 때만 — 없으면 예전과 같다. amount 는 설정 금액 이하로만 쓴다."""
        held = self.oms.held(code)
        if self.oms.active_for(code):
            return self._skip(code, "이 종목에 진행 중인 주문이 있음")
        if kind == "long_entry" and held:
            return self._skip(code, "이미 포지션 있음(종목당 1개)")
        if kind == "long_exit" and not held:
            return self._skip(code, "이 실행기가 들고 있는 수량 없음 — 청산할 것 없음")
        if kind == "long_entry":
            why = self.risk.check_entry(self.oms)
            if why:
                self._risk_block(code, why, source)
                return self._skip(code, why)
            if self.ext is not None and extdoor.entry_held(self, code, t_sig, source):
                return None                                  # 거르기(모든 진입) · 검토(전략 진입) — 위험 점검 뒤
        t_q0 = self.clock_ms()
        px_now = self.price_now(code)
        t_q1 = self.clock_ms()
        self.oms.current[code] = px_now
        st = self.settings
        if amount is not None:
            from dataclasses import replace as _r
            st = _r(st, amount=min(float(amount), float(self.amount)))
        intent, why = make_intent(kind, code, px_now, held, st, t_sig)
        if intent is None:
            return self._skip(code, why)
        extra = {"source": source.get("source"), "ext_id": source.get("id")} if source else {}
        self.log.write("order_intent", **intent.to_dict(), t_quote_start_ms=t_q0, t_quote_end_ms=t_q1, **extra)
        o = self.oms.submit(intent)
        if o is None:
            return None
        if source and kind == "long_entry" and self.ext is not None:
            self.ext.mark_owned(code, source, self.clock_ms())
        side = "매수" if intent.side == "buy" else "매도"
        how = "시장가" if intent.mode == "market" else f"지정가 {intent.price:,}원"
        tag = "[계획만 · dry-run]" if self.broker.dry else "[모의 주문]"
        self.say(f"  {tag} {code} {side} {intent.qty}주 · {how} (현재가 {px_now:,}) → {o.state}"
                 + (f" 주문번호 {o.odno}" if o.odno and not self.broker.dry else ""))
        return {"code": code, "side": intent.side, "qty": intent.qty, "price": intent.price, "mode": intent.mode,
                "current": px_now, "order_id": o.id, "state": o.state, "odno": o.odno}

    def _risk_block(self, code: str, why: str, source: dict | None) -> None:
        """위험 점검에 막힌 진입을 (날, 종류)마다 한 번 기록한다 — 손실 한도 · 진입 상한에 닿은 것을 알림이 받게."""
        kind = "stop" if "정지" in why else "cap" if "상한" in why else "loss" if "손실" in why else "other"
        key = (self.oms.day, kind)
        if key in self._risk_said:
            return
        self._risk_said.add(key)
        self.log.write("risk_block", code=code, kind=kind, reason=why, **({"source": source.get("source")} if source else {}))

    # ---------- 바꿔 끼우는 자리(백테스트·시뮬레이션이 덮어씀) ----------
    def clock_ms(self) -> int:
        return now_ms()

    def now_dt(self) -> datetime:
        return datetime.now()

    def _price_safe(self, code: str):
        try:
            return self.price_now(code)
        except Exception:
            return None

    # ---------- 실행 중 설정 바꾸기 ----------
    RELOADABLE = ("order_mode", "amount", "wait", "max_orders", "loss_limit", "codes", "strategy", "params", "poll",
                  "settle", "apply_to_open")

    def watch_settings(self, path, loader, current: dict) -> None:
        """설정 파일을 봉마다 확인해, 바뀌면 검사 뒤 적용한다. loader(path) → dict(오류면 예외)."""
        from pathlib import Path as _P
        self.settings_path, self.settings_loader = _P(path), loader
        self._settings_cur = dict(current)
        try:
            self._settings_mtime = self.settings_path.stat().st_mtime
        except OSError:
            self._settings_mtime = None

    def reload_settings(self, force: bool = False) -> dict | None:
        """바뀐 설정을 적용한다. 반환 = 적용한 바뀜(없으면 None). 잘못된 설정은 적용하지 않고 기존대로 계속 돈다."""
        if not self.settings_path or not self.settings_loader:
            return None
        try:
            mt = self.settings_path.stat().st_mtime
        except OSError:
            return None
        if not force and mt == self._settings_mtime:
            return None
        prev_mt, self._settings_mtime = self._settings_mtime, mt
        try:
            new = self.settings_loader(self.settings_path)
        except OSError:                               # 편집기가 바꿔 넣는 순간 등 — 기록하지 않고 다음 봉에 다시 읽는다
            self._settings_mtime = prev_mt
            return None
        except BaseException as e:                    # SystemExit 포함 — 실행기는 멈추지 않는다
            self.log.write("settings_rejected", error=_err(e))
            self.say(f"  [설정 적용 안 됨] {str(e)[:120]} — 기존 설정으로 계속")
            return None
        old = self._settings_cur
        changes = {k: [old.get(k), new.get(k)] for k in set(old) | set(new) if old.get(k) != new.get(k)}
        if not changes:
            return None
        bad = sorted(k for k in changes if k not in self.RELOADABLE)
        if bad:                                       # 봉 길이·시작·끝 시각 등은 다시 시작해야 반영 — 나머지만 적용
            self.log.write("settings_partial", not_applied=bad, note="실행 중에는 못 바꾸는 항목 — 다시 시작하면 반영")
            self.say(f"  [설정 일부만 적용] {', '.join(bad)} 는 다시 시작해야 반영됩니다")
            new = {**new, **{k: old.get(k) for k in bad}}
            changes = {k: v for k, v in changes.items() if k not in bad}
            if not changes:
                return None
        try:
            from execution.modes import check_kinds
            amount = float(new["amount"])
            if amount > self.max_amount:              # 깎아서 적용하지 않는다(기록과 실제가 어긋나지 않게)
                raise ValueError(f"1회 금액 {amount:,.0f}원이 상한 {self.max_amount:,.0f}원을 넘음")
            st = OrderSettings(new["order_mode"], amount, float(new["wait"]))
            check_kinds(st, self.broker.name)
            strat = self.strategy
            if "strategy" in changes or "params" in changes:
                from strategy.ported import load_strategy
                strat = load_strategy(new["strategy"], **(new.get("params") or {}))
            codes = [str(c).zfill(6) for c in new["codes"]]
        except Exception as e:
            self.log.write("settings_rejected", error=_err(e), changes=changes)
            self.say(f"  [설정 적용 안 됨] {str(e)[:120]} — 기존 설정으로 계속")
            return None
        method_changed = st.mode != self.settings.mode or st.wait_s != self.settings.wait_s
        self.settings, self.amount, self.strategy = st, amount, strat
        self.max_orders = self.risk.max_entries = int(new["max_orders"])
        self.risk.daily_loss_limit = float(new["loss_limit"])
        self.poll_s, self.settle = float(new["poll"]), float(new["settle"])
        if codes != self.codes:
            old_codes, self.codes = self.codes, codes
            self.on_codes_changed(old_codes, codes)
        self._settings_cur = dict(new)
        self.log.write("settings_changed", changes=changes)
        self.say(f"  [설정 바뀜] {', '.join(f'{k}: {v[0]} → {v[1]}' for k, v in changes.items())}")
        if method_changed and new.get("apply_to_open") and self.oms.has_active():
            n = self.oms.switch_open_orders("설정 바뀜 — 진행 중 주문을 새 방식으로")
            self.log.write("switch_requested", orders=n)
        return changes

    def on_codes_changed(self, old: list[str], new: list[str]) -> None:
        """종목이 바뀌었을 때(실시간 시뮬레이션은 웹소켓 등록을 바꾼다). 빠진 종목의 포지션·주문은 그대로 관리된다."""
        self.log.write("codes_changed", added=sorted(set(new) - set(old)), removed=sorted(set(old) - set(new)))

    def _switch_intent(self, o, remaining: int):
        """진행 중 주문을 새 방식으로 바꿀 때의 새 주문(남은 수량)."""
        from dataclasses import replace as _r
        px = self._price_safe(o.code) or self.oms.current.get(o.code) or o.intent.current
        kind = "long_entry" if o.side == "buy" else "long_exit"
        i, why = make_intent(kind, o.code, int(px), remaining if o.side == "sell" else 0, self.settings,
                             o.intent.t_signal_ms, self.oms._opp(o.code, o.side))
        if i is None:
            return None
        return _r(i, qty=int(remaining), reason="switched", parent=o.id)

    def price_now(self, code: str) -> int:
        """주문 직전 현재가 — 실시간은 한투 현재가 조회(실전 키, 조회만)."""
        return int(float(data.PACER.call(quote.current_price, data.cfg_of(self.data_mode), code)["stck_prpr"]))

    def _skip(self, code: str, why: str) -> None:
        self.log.write("order_skip", code=code, reason=why)
        self.say(f"  [건너뜀] {code}: {why}")
        return None

    # ---------- 반복 ----------
    def next_close(self, now: datetime | None = None) -> datetime:
        now = now or datetime.now()
        m = (now.hour * 60 + now.minute) // self.tf * self.tf + self.tf
        return now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=m)

    def beat(self, force: bool = False) -> None:
        """살아 있음 표시(state/heartbeat.json, dry-run 은 heartbeat_dry.json) — 텔레그램 /상태 가 읽는다. 10초에 한 번."""
        if not force and time.time() - self._hb_last < 10:
            return
        self._hb_last = time.time()
        try:
            import os
            self.heartbeat_path.write_text(json.dumps({"pid": os.getpid(), "at": datetime.now().isoformat(timespec="seconds"),
                                                       "broker": self.broker.name, "codes": self.codes,
                                                       "active_orders": len(self.oms.orders)}, ensure_ascii=False),
                                           encoding="utf-8")
        except OSError:
            pass

    def _safe_poll(self, where: str) -> None:
        """주문 조회 — 어떤 예외든 기록만 하고 다음 조회 때 다시(토큰 재발급 실패 등으로 실행기가 죽지 않게)."""
        try:
            self.oms.poll()
        except Exception as e:
            self.log.write("error", where=f"poll:{where}", error=f"{type(e).__name__}: {str(e)[:200]}")

    def _wait_until(self, target: datetime) -> bool:
        """target 까지 기다리며 진행 중 주문을 조회한다. 정지 파일이 생기면 False."""
        while True:
            self.beat()
            if self.risk.stop_requested():
                return False
            left = (target - datetime.now()).total_seconds()
            if left <= 0:
                return True
            if self.oms.has_active():
                self._safe_poll("wait")
                left = (target - datetime.now()).total_seconds()
            time.sleep(max(min(self.poll_s if self.oms.has_active() else 1.0, left), 0))

    def run(self, start: str = "09:00", until: str = "15:20", once: bool = False):
        if self.risk.stop_requested():
            self.say(f"  정지 파일이 있어 시작하지 않습니다: state/{self.risk.stop_path.name} 를 지우고 다시 시작하세요")
            self.log.write("start_refused", reason="stop file")
            return
        why = self.amount_refusal()
        if why:
            self.say(f"  {why} — 시작하지 않습니다(금액을 낮추거나 max_amount 를 확인하세요)")
            self.log.write("start_refused", reason="amount over cap", detail=why)
            return
        if self.ext is not None and self.lock_path.name == "run_dry.lock":
            # dry-run 과 모의는 같은 문 폴더(state/ext/)를 쓴다 — 모의가 도는 중에 dry-run 이 문을 열면 신호를 먼저 집어 가고 검토 요청을 지운다
            paper = paper_lock_pid(self.lock_path.with_name("run_paper.lock"))
            if paper:
                self.say(f"  모의 실행기(pid {paper})가 같은 문 폴더를 쓰고 있습니다 — --ext 없이 돌리거나 모의가 끝난 뒤에 시작하세요")
                self.log.write("start_refused", reason=f"door shared with paper runner pid {paper}")
                return
        other = acquire_lock(self.lock_path)
        if other:
            self.say(f"  이미 다른 실행기가 돌고 있습니다(pid {other}) — 시작하지 않습니다")
            self.log.write("start_refused", reason=f"lock held by pid {other}")
            return
        try:
            self._run(start, until, once)
        finally:
            release_lock(self.lock_path)

    def amount_refusal(self) -> str | None:
        """시작 전 금액 검사. None = 통과. 상한을 넘는 금액은 깎아서 돌지 않는다(기록한 금액과 실제가 어긋나지 않게)."""
        cap = float(order.MAX_ORDER_WON)
        if self.max_amount_asked is not None and self.max_amount_asked > cap:
            return f"금액 상한 max_amount {self.max_amount_asked:,.0f}원이 코드의 절대 상한 {cap:,.0f}원을 넘음"
        if self.amount_asked > self.max_amount:
            return f"1회 금액 {self.amount_asked:,.0f}원이 상한 {self.max_amount:,.0f}원을 넘음"
        return None

    def _run(self, start: str, until: str, once: bool):
        self.log.write("start", codes=self.codes, strategy=type(self.strategy).__name__, params=self.strategy.params,
                       tf=self.tf, amount=self.amount, order_mode=self.settings.mode, wait_s=self.settings.wait_s,
                       max_orders=self.max_orders, daily_loss_limit=self.risk.daily_loss_limit,
                       mode="paper-order" if self.paper_order else "dry-run", broker=self.broker.name,
                       positions=self.positions, entries_today=self.oms.entries_today, max_amount=self.max_amount,
                       ext=self.ext is not None)
        if self.ext is None:
            extdoor.warn_left_owned(STATE_DIR / "ext", self.log, self.say)
        if self.notice:
            self._start_notice()
        if self.oms.has_active():
            self._safe_poll("start")                         # 재시작 — 남아 있던 주문부터 정리
        if not self.broker.dry:
            rep = self.oms.reconcile()
            if rep.get("diffs"):
                self.say(f"  [잔고 대조] 차이 {rep['diffs']}")
        if once:
            self.step()
            self.save_bars()
            self.log.write("stop", reason="once", summary=self.oms.summary())
            return
        reason = "end"
        try:
            while True:
                now = datetime.now()
                if now.strftime("%H:%M") >= until:
                    self.say(f"  {until} 지남 — 끝")
                    break
                close = self.next_close(now)
                if close.strftime("%H:%M") <= start:
                    close = now.replace(hour=int(start[:2]), minute=int(start[3:]), second=0, microsecond=0) \
                        + timedelta(minutes=self.tf)
                if close.strftime("%H:%M") >= until:             # until 뒤 봉은 처리하지 않는다
                    if not self._wait_until(close.replace(hour=int(until[:2]), minute=int(until[3:]), second=0)):
                        reason = "stop file"
                    self.say(f"  {until} — 끝")
                    break
                if not self._wait_until(close + timedelta(seconds=self.settle)):
                    reason = "stop file"
                    self.say("  정지 파일 — 새 주문을 멈추고 정리합니다")
                    break
                self.step(bar_close_ms=int(close.timestamp() * 1000))
        except KeyboardInterrupt:
            reason = "ctrl+c"
            self.say("  Ctrl+C — 멈춤")
        finally:
            self.finish(reason)

    def finish(self, reason: str) -> None:
        """끝낼 때: 진행 중 주문 취소 → 정리될 때까지 조회(최대 60초) → 잔고 대조 → 하루 요약."""
        try:
            if self.oms.has_active():
                self.oms.cancel_all(f"실행기 종료({reason})")
                if not self.oms.wait_idle(60, self.poll_s):
                    self.say("  [주의] 60초 안에 정리되지 않은 주문이 있습니다 — state 파일에 남겨 다음 시작 때 이어서 확인")
            if not self.broker.dry:
                self.oms.reconcile()
        finally:
            self.save_bars()
            s = self.oms.summary()
            self.log.write("stop", reason=reason, summary=s)
            self.say(f"  요약: {s}")
            try:
                self.heartbeat_path.unlink(missing_ok=True)
            except OSError:
                pass
            if self.notifier:
                self.notifier.flush()

    def save_bars(self):
        """받은 1분봉을 data/bars/{code}_1m.csv 에 이어 저장(다음 날 지표 예열용). 진행 중인 봉은 빼고."""
        for code, df in self.bars.items():
            done = data.drop_unfinished_minute(df)
            if not done.empty:
                data.merge_save(code, "1m", done)

    # ---------- 체결통보(선택) ----------
    def _start_notice(self):
        cfg = self.order_cfg or config.load("paper")
        if not cfg.hts_id:
            self.log.write("notice_unavailable", reason="HTS ID 비어 있음(설정 my_htsid) — 체결통보 등록 불가")
            self.say("  [체결통보] HTS ID 가 비어 있어 연결하지 않음(나머지는 그대로 동작)")
            return
        threading.Thread(target=lambda: asyncio.run(self._notice_loop(cfg)), daemon=True).start()

    async def _notice_loop(self, cfg):
        import websockets
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from kis.realtime import _sub_msg, approval_key

        # 필드 순서는 공식 예제 ccnl_notice.py — 고객ID·계좌번호·계좌명은 기록하지 않는다
        cols = ["CUST_ID", "ACNT_NO", "ODER_NO", "OODER_NO", "SELN_BYOV_CLS", "RCTF_CLS", "ODER_KIND", "ODER_COND",
                "STCK_SHRN_ISCD", "CNTG_QTY", "CNTG_UNPR", "STCK_CNTG_HOUR", "RFUS_YN", "CNTG_YN", "ACPT_YN", "BRNC_NO",
                "ODER_QTY", "ACNT_NAME", "ORD_COND_PRC", "ORD_EXG_GB", "POPUP_YN", "FILLER", "CRDT_CLS",
                "CRDT_LOAN_DATE", "CNTG_ISNM40", "ODER_PRC"]
        hidden = {"CUST_ID", "ACNT_NO", "ACNT_NAME"}

        def decrypt(key: str, iv: str, text: str) -> str:
            dec = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode())).decryptor()
            raw = dec.update(base64.b64decode(text)) + dec.finalize()
            un = padding.PKCS7(128).unpadder()
            return (un.update(raw) + un.finalize()).decode("utf-8")

        try:
            async with websockets.connect(f"{cfg.ws_url}/tryitout", ping_interval=None, open_timeout=8) as ws:
                await ws.send(_sub_msg(approval_key(cfg), "H0STCNI9", cfg.hts_id))
                key = iv = None
                self.log.write("notice_connected")
                async for raw in ws:
                    t = now_ms()
                    if raw[0] in "01":
                        parts = raw.split("|")
                        if key and len(parts) >= 4:
                            vals = decrypt(key, iv, parts[3]).split("^")
                            f = {k: v for k, v in zip(cols, vals) if k not in hidden}
                            self.log.write("fill" if f.get("CNTG_YN") == "2" else "order_ack", t_recv_ms=t,
                                           odno=f.get("ODER_NO"), code=f.get("STCK_SHRN_ISCD"), qty=f.get("CNTG_QTY"),
                                           price=f.get("CNTG_UNPR"), exch_time=f.get("STCK_CNTG_HOUR"),
                                           refused=f.get("RFUS_YN"))
                        continue
                    d = json.loads(raw)
                    if d.get("header", {}).get("tr_id") == "PINGPONG":
                        await ws.send(raw)
                        continue
                    out = (d.get("body") or {}).get("output") or {}
                    if out.get("key"):
                        key, iv = out["key"], out["iv"]
        except Exception as e:
            self.log.write("notice_error", error=f"{type(e).__name__}: {str(e)[:200]}")
