"""위험 점검 — 새 진입(매수) 앞에서만 막는다. 청산(매도)은 막지 않는다(들고 있는 것은 언제든 닫혀야 하므로).

  정지 파일   state/stop 이 있으면 실행기가 새 주문을 멈추고, 진행 중 주문을 취소한 뒤 기록을 마치고 끝낸다.
              (텔레그램 '끄기'도 이 파일을 만든다 — 프로세스를 강제로 죽이지 않는다.)
  진입 상한   하루 새 매수 횟수(보낸 것 기준, 기본 5).
  일 손실 한도 오늘 실현 손익 + 들고 있는 것의 평가 손익 ≤ −한도(기본 3만 원)이면 그날 새 진입 중단(한 번 걸리면 그날은 계속).
수수료·세금은 넣지 않은 값이다(모의).
"""
from __future__ import annotations

from pathlib import Path


class RiskGate:
    def __init__(self, stop_path: Path, max_entries: int = 5, daily_loss_limit: float = 30_000):
        self.stop_path = Path(stop_path)
        self.max_entries = int(max_entries)
        self.daily_loss_limit = float(daily_loss_limit)
        self.loss_tripped_day = ""

    def stop_requested(self) -> bool:
        return self.stop_path.exists()

    def pnl_today(self, oms) -> float:
        return oms.realized + oms.unrealized()

    def check_entry(self, oms) -> str | None:
        """None = 통과, 문자열 = 막은 이유."""
        if self.stop_requested():
            return "정지 파일이 있음"
        if oms.entries_today >= self.max_entries:
            return f"오늘 진입 상한 {self.max_entries}회에 닿음(청산은 상한 밖)"
        if self.loss_tripped_day == oms.day:
            return f"일 손실 한도 {self.daily_loss_limit:,.0f}원에 닿아 오늘 새 진입 중단"
        pnl = self.pnl_today(oms)
        if pnl <= -self.daily_loss_limit:
            self.loss_tripped_day = oms.day
            return f"일 손실 한도: 오늘 손익 {pnl:,.0f}원 ≤ −{self.daily_loss_limit:,.0f}원 → 오늘 새 진입 중단"
        return None
