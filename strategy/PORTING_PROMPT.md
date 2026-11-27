# Pine → 파이썬 이식 프롬프트 (v1, 2026-11-03)

유튜브 '김쿤 | AI 자동매매' 6편(트레이딩뷰 전략 옮기기)에서 쓰는 프롬프트. 클로드·GPT·제미나이 등 **어떤 AI 에든** 아래 "붙여 넣을 프롬프트" 블록 전체를 붙여 넣고, 그 아래에 옮길 Pine 소스 전체를 붙인다.

## 사용법

1. 트레이딩뷰 Pine 편집기에서 전략 소스를 **처음부터 끝까지 전부** 복사한다(일부만 주면 AI 가 나머지를 지어낸다).
2. 각 AI 의 **가장 강한 모델**을 고른다(추론·코드가 강한 모델일수록 대응표를 잘 지킨다).
3. 아래 "붙여 넣을 프롬프트"(한국어 또는 영어 판) 전체 + Pine 소스를 붙여 넣는다.
4. AI 가 준 파이썬 파일 하나를 `strategy/ported/<전략이름>.py` 로 저장한다.
5. 검증기를 돌린다: `python -X utf8 scripts/strategy_validate.py strategy/ported/<전략이름>.py`
   → 트레이딩뷰 거래 목록 CSV 가 있으면 `--code <종목코드> --tf 1d --days 365 --tv-trades 거래목록.csv` 까지(종목 코드는 쓰는 사람이 넣는다).
6. **판정이 "통과"인 파일만** 쓴다. AI 마다 결과가 다를 수 있다 — 같은 Pine 이라도 검증기를 통과해야 한다.
   "경고"면 표시된 첫 갈라진 거래와 앞뒤 봉 값을 AI 에게 그대로 보여 주고 고치게 한다.

정답 예시: `strategy/ported/supertrend_strategy.py` (아래 템플릿과 완전히 같은 형식).

---

## 붙여 넣을 프롬프트 (한국어)

````text
[역할과 목표]
당신은 트레이딩뷰 Pine Script 전략을 파이썬으로 옮기는 이식 담당입니다. 아래에 붙인 Pine 전략의 '매매 신호 논리'만
정해진 파이썬 틀(Strategy 클래스)에 그대로 옮깁니다. 주문·체결·수량·수수료는 우리 프로그램(러너)이 처리하므로 전략은
"이 봉이 닫힌 순간 진입/청산 조건이 맞았나"만 bool 로 냅니다. 모르는 것은 지어내지 말고 '미지원' 표에 적습니다.

[우리 전략 틀 규격 — strategy/base.py]
입력 df: pandas DataFrame, 열 = time, open, high, low, close, volume (오름차순, index 0..n-1, 값은 float)
출력: 같은 index 의 DataFrame, 열 = long_entry, long_exit, short_entry, short_exit (모두 bool)
      - empty_signals(df) 로 모두 False 인 틀을 만든 뒤 채운다
      - 대조용 지표 열(예: "supertrend")을 더 붙여도 된다(러너는 위 4개 열만 본다)
쓸 수 있는 import 는 이것뿐:
    from __future__ import annotations
    import math
    import numpy as np
    import pandas as pd
    from strategy import indicators as ta
    from strategy.base import Strategy, empty_signals
클래스 형식:
    class XxxStrategy(Strategy):
        name = "xxx"                      # 소문자 짧은 이름
        params = {"length": 14}           # Pine input 기본값 그대로, 이름도 Pine 변수 이름 그대로
        def signals(self, df: pd.DataFrame) -> pd.DataFrame:
            p = self.params
            sig = empty_signals(df)
            ...
            return sig
    STRATEGY = XxxStrategy

[Pine → 우리 함수 대응표] (ta = strategy.indicators. 모두 pandas Series 를 받고 같은 길이 Series 를 돌려준다)
| # | Pine | 파이썬 |
|---|---|---|
| 1 | ta.sma(src, n) | ta.sma(src, n) |
| 2 | ta.ema(src, n) | ta.ema(src, n) |
| 3 | ta.rma(src, n) | ta.rma(src, n) |
| 4 | ta.atr(n) | ta.atr(df["high"], df["low"], df["close"], n) |
| 5 | ta.tr / ta.tr(true) | ta.tr(df["high"], df["low"], df["close"], handle_na=False) / handle_na=True |
| 6 | ta.rsi(src, n) | ta.rsi(src, n) |
| 7 | [st, dir] = ta.supertrend(factor, atrPeriod) | st, dir = ta.supertrend(df["high"], df["low"], df["close"], factor, atrPeriod)  (dir: -1 상승, 1 하락 — Pine 과 같음) |
| 8 | ta.crossover(a, b) | ta.crossover(a, b)  (b 는 숫자도 됨) |
| 9 | ta.crossunder(a, b) | ta.crossunder(a, b) |
| 10 | ta.highest(src, n) / ta.highest(n) | ta.highest(src, n) / ta.highest(df["high"], n) |
| 11 | ta.lowest(src, n) / ta.lowest(n) | ta.lowest(src, n) / ta.lowest(df["low"], n) |
| 12 | ta.change(src) / ta.change(src, n) | ta.change(src) / ta.change(src, n) |
| 13 | open, high, low, close, volume | df["open"], df["high"], df["low"], df["close"], df["volume"] |
| 14 | hl2, hlc3, ohlc4 | (df["high"]+df["low"])/2, (df["high"]+df["low"]+df["close"])/3, (df["open"]+df["high"]+df["low"]+df["close"])/4 |
| 15 | x[1], x[n] | x.shift(1), x.shift(n) |
| 16 | nz(x), nz(x, y) | x.fillna(0), x.fillna(y) |
| 17 | na(x) | x.isna() |
| 18 | a and b, a or b, not a | (a) & (b), (a) \| (b), ~(a)  — 괄호 필수 |
| 19 | cond ? a : b | pd.Series(np.where(cond, a, b), index=df.index) |
| 20 | math.max(a, b), math.min(a, b), math.abs(x) | np.maximum(a, b), np.minimum(a, b), x.abs() |
| 21 | input(defval, ...), input.int / input.float / input.bool(defval, ...) | params["Pine변수이름"] = defval |
| 22 | strategy.entry(id, strategy.long) | sig["long_entry"] = 조건 |
| 23 | strategy.close(id), strategy.close_all() | sig["long_exit"] = 조건 |
| 24 | strategy.entry(id, strategy.short) | 공매도 없음 → 같은 조건을 sig["long_exit"] 로(롱 청산만). short_* 는 False 그대로 |
| 25 | strategy(...) 의 default_qty_*, initial_capital, commission_*, slippage, pyramiding | 전략 파일에 쓰지 않음(러너 인자) — 머리말 '바꾼 점'에 적기 |
| 26 | var 변수, := 로 봉마다 이어지는 상태 | numpy 배열 + for 루프로 봉 순서대로(앞 봉 값 참조) |
| 27 | plot, plotshape, bgcolor, fill, label, line, table, alertcondition | 옮기지 않음(그림·알림). 대조에 필요하면 sig 에 값 열만 추가 |
| 28 | barstate.isconfirmed, barstate.isfirst | 러너가 닫힌 봉만 넘김 → 보통 필요 없음 / isfirst 는 index == 0 |
위 표에 없는 함수(예: strategy.exit 의 stop·limit·trail, request.security, ta.vwap, ta.macd, ta.bb, 시간·세션 함수 등)는
절대 지어내지 말고, 직접 구현하지도 말고, 파일 끝 '자기 점검 표'의 [미지원 항목]에 "Pine 함수 — 이유"로 적은 뒤
그 부분이 빠지면 결과가 어떻게 달라지는지 한 줄로 적습니다.

[규칙]
1. 국내 주식은 공매도를 하지 않는다: short_entry, short_exit 는 항상 False.
2. 체결은 다음 봉 시가(러너가 처리). 전략은 신호만 낸다 — 주문·수량·가격 계산 코드를 넣지 않는다.
3. 네트워크·파일·주문 코드, 위 import 목록 밖 라이브러리(pandas-ta, ta-lib 등 지표 라이브러리 포함) 금지.
4. params 의 기본값과 이름은 Pine input 의 기본값·변수 이름 그대로.
5. Pine 한 줄마다 바로 위에 원래 Pine 줄을 주석으로 붙인다(대조용).
6. 파일 머리말(docstring)에 [Pine 원본](출처·버전·원문 그대로) · [이식](이식 날짜·이식한 AI·바꾼 점)을 적는다.
7. 파일 끝에 '자기 점검 표' 주석: [사용한 Pine 함수 → 대응 함수] · [미지원 항목] · [의도적으로 바꾼 점].

[출력 규격] 설명 없이 파이썬 파일 하나만, 아래 템플릿과 같은 형식으로:
```python
"""<전략 이름> — 트레이딩뷰 Pine 전략 이식

[Pine 원본]
- 출처: <내장 스크립트 이름 또는 공개 스크립트 주소>
- 원본 버전: <버전/날짜 — 모르면 '미확인'>
- 원문(그대로):
    <Pine 소스 전체를 4칸 들여 써서 그대로>

[이식]
- 이식 날짜: <YYYY-MM-DD>
- 이식한 AI: <AI 이름과 모델>
- 바꾼 점:
  1. <예: 공매도 없음 — short 진입을 long 청산으로>
  2. <예: 수량은 러너가 금액 기준으로>
"""
from __future__ import annotations

import pandas as pd

from strategy import indicators as ta
from strategy.base import Strategy, empty_signals


class XxxStrategy(Strategy):
    name = "xxx"
    params = {
        "length": 14,        # length = input.int(14, "Length")
    }

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = empty_signals(df)

        # <원래 Pine 줄>
        ...

        # <원래 Pine 줄> → strategy.entry(..., strategy.long)
        sig["long_entry"] = (<조건>).astype(bool)
        # <원래 Pine 줄> → strategy.close(...) 또는 short 진입(공매도 없음 → long 청산)
        sig["long_exit"] = (<조건>).astype(bool)
        return sig


STRATEGY = XxxStrategy

# ── 자기 점검 표 ────────────────────────────────────────────────────────────
# [사용한 Pine 함수 → 대응 함수]
#   <Pine>  → <파이썬>
# [미지원 항목]
#   <없음 또는 "Pine 함수 — 이유 — 빠지면 달라지는 점">
# [의도적으로 바꾼 점]
#   1. <...>
```

[옮길 Pine 소스]
(여기에 Pine 전략 소스 전체를 붙여 넣으세요)
````

---

## Paste-in prompt (English)

````text
[Role and goal]
You port a TradingView Pine Script strategy to Python. Port ONLY the trade-signal logic of the Pine strategy pasted below
into our fixed Python framework (a Strategy class). Orders, fills, position size and fees are handled by our runner, so the
strategy only outputs booleans: "did the entry/exit condition hold when this bar closed?". Never invent anything you are
unsure about — list it in the 'unsupported' table instead.

[Our framework spec — strategy/base.py]
Input df: pandas DataFrame with columns time, open, high, low, close, volume (ascending, index 0..n-1, float values)
Output: DataFrame with the same index and columns long_entry, long_exit, short_entry, short_exit (all bool)
      - start from empty_signals(df) (all False) and fill it
      - you may add extra indicator columns for comparison (e.g. "supertrend"); the runner reads only the 4 columns above
Allowed imports — these only:
    from __future__ import annotations
    import math
    import numpy as np
    import pandas as pd
    from strategy import indicators as ta
    from strategy.base import Strategy, empty_signals
Class format:
    class XxxStrategy(Strategy):
        name = "xxx"                      # short lowercase name
        params = {"length": 14}           # Pine input defaults, same variable names as in Pine
        def signals(self, df: pd.DataFrame) -> pd.DataFrame:
            p = self.params
            sig = empty_signals(df)
            ...
            return sig
    STRATEGY = XxxStrategy

[Pine → our function mapping] (ta = strategy.indicators; every function takes pandas Series and returns a Series of the same length)
| # | Pine | Python |
|---|---|---|
| 1 | ta.sma(src, n) | ta.sma(src, n) |
| 2 | ta.ema(src, n) | ta.ema(src, n) |
| 3 | ta.rma(src, n) | ta.rma(src, n) |
| 4 | ta.atr(n) | ta.atr(df["high"], df["low"], df["close"], n) |
| 5 | ta.tr / ta.tr(true) | ta.tr(df["high"], df["low"], df["close"], handle_na=False) / handle_na=True |
| 6 | ta.rsi(src, n) | ta.rsi(src, n) |
| 7 | [st, dir] = ta.supertrend(factor, atrPeriod) | st, dir = ta.supertrend(df["high"], df["low"], df["close"], factor, atrPeriod)  (dir: -1 up, 1 down — same as Pine) |
| 8 | ta.crossover(a, b) | ta.crossover(a, b)  (b may be a number) |
| 9 | ta.crossunder(a, b) | ta.crossunder(a, b) |
| 10 | ta.highest(src, n) / ta.highest(n) | ta.highest(src, n) / ta.highest(df["high"], n) |
| 11 | ta.lowest(src, n) / ta.lowest(n) | ta.lowest(src, n) / ta.lowest(df["low"], n) |
| 12 | ta.change(src) / ta.change(src, n) | ta.change(src) / ta.change(src, n) |
| 13 | open, high, low, close, volume | df["open"], df["high"], df["low"], df["close"], df["volume"] |
| 14 | hl2, hlc3, ohlc4 | (df["high"]+df["low"])/2, (df["high"]+df["low"]+df["close"])/3, (df["open"]+df["high"]+df["low"]+df["close"])/4 |
| 15 | x[1], x[n] | x.shift(1), x.shift(n) |
| 16 | nz(x), nz(x, y) | x.fillna(0), x.fillna(y) |
| 17 | na(x) | x.isna() |
| 18 | a and b, a or b, not a | (a) & (b), (a) \| (b), ~(a)  — parentheses required |
| 19 | cond ? a : b | pd.Series(np.where(cond, a, b), index=df.index) |
| 20 | math.max(a, b), math.min(a, b), math.abs(x) | np.maximum(a, b), np.minimum(a, b), x.abs() |
| 21 | input(defval, ...), input.int / input.float / input.bool(defval, ...) | params["PineVariableName"] = defval |
| 22 | strategy.entry(id, strategy.long) | sig["long_entry"] = condition |
| 23 | strategy.close(id), strategy.close_all() | sig["long_exit"] = condition |
| 24 | strategy.entry(id, strategy.short) | no short selling → use the same condition as sig["long_exit"] (close the long only); keep short_* False |
| 25 | default_qty_*, initial_capital, commission_*, slippage, pyramiding in strategy(...) | do not put in the strategy file (runner arguments) — note it under 'changes' in the header |
| 26 | var variables, state carried bar to bar with := | numpy arrays + a for loop in bar order (referencing previous bar values) |
| 27 | plot, plotshape, bgcolor, fill, label, line, table, alertcondition | do not port (drawing/alerts). Add value columns to sig only if needed for comparison |
| 28 | barstate.isconfirmed, barstate.isfirst | the runner passes closed bars only → usually unnecessary / isfirst is index == 0 |
Anything not in this table (e.g. stop/limit/trail of strategy.exit, request.security, ta.vwap, ta.macd, ta.bb, time/session
functions) must NOT be invented or hand-implemented. List it under [Unsupported] in the self-check table at the end of the file
as "Pine function — reason", plus one line on how the result changes without it.

[Rules]
1. Korean stocks: no short selling. short_entry and short_exit are always False.
2. Fills happen at the next bar's open (the runner does this). The strategy only emits signals — no order, size or price code.
3. No network, file or order code; no libraries outside the import list (including indicator libraries such as pandas-ta, ta-lib).
4. params defaults and names are exactly the Pine input defaults and variable names.
5. Put the original Pine line as a comment right above each ported line (for comparison).
6. The file docstring has [Pine 원본] (source, version, verbatim source) and [이식] (date, AI used, changes) — keep these Korean headings.
7. End the file with the '자기 점검 표' (self-check table) comment: [사용한 Pine 함수 → 대응 함수] · [미지원 항목] · [의도적으로 바꾼 점].

[Output spec] Output exactly one Python file, no explanations, in the same format as this template:
```python
"""<strategy name> — 트레이딩뷰 Pine 전략 이식

[Pine 원본]
- 출처: <built-in script name or public script URL>
- 원본 버전: <version/date — '미확인' if unknown>
- 원문(그대로):
    <entire Pine source verbatim, indented 4 spaces>

[이식]
- 이식 날짜: <YYYY-MM-DD>
- 이식한 AI: <AI name and model>
- 바꾼 점:
  1. <e.g. no short selling — short entry becomes long exit>
  2. <e.g. size is set by the runner (fixed KRW amount)>
"""
from __future__ import annotations

import pandas as pd

from strategy import indicators as ta
from strategy.base import Strategy, empty_signals


class XxxStrategy(Strategy):
    name = "xxx"
    params = {
        "length": 14,        # length = input.int(14, "Length")
    }

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        sig = empty_signals(df)

        # <original Pine line>
        ...

        # <original Pine line> → strategy.entry(..., strategy.long)
        sig["long_entry"] = (<condition>).astype(bool)
        # <original Pine line> → strategy.close(...) or short entry (no shorting → long exit)
        sig["long_exit"] = (<condition>).astype(bool)
        return sig


STRATEGY = XxxStrategy

# ── 자기 점검 표 ────────────────────────────────────────────────────────────
# [사용한 Pine 함수 → 대응 함수]
#   <Pine>  → <Python>
# [미지원 항목]
#   <none, or "Pine function — reason — what changes without it">
# [의도적으로 바꾼 점]
#   1. <...>
```

[Pine source to port]
(paste the entire Pine strategy source here)
````

---

### 대응표 메모(사람용)
- 대응표 28항목. 우리 `indicators.py` 의 함수 12개(sma·ema·rma·tr·atr·rsi·supertrend·crossover·crossunder·highest·lowest·change)가 모두 들어 있고, 검증기는 이 12개 밖의 `ta.xxx` 를 경고한다.
- `ta.ema` 첫 값: v6 공식 문서 예시는 첫 source 값으로 시작(우리 기본). 내장 함수가 단순 평균으로 시작하는지는 미확인 → 앞부분이 다르면 `ta.ema(src, n, seed="sma")` 로 대조.
- 새 지표가 필요하면 먼저 `indicators.py` 에 Pine 문서 정의대로 넣고 테스트를 단 뒤 대응표·검증기 허용 목록에 더한다(프롬프트 버전 올리기).
