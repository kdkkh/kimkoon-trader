# sample_data/ — 키 · 네트워크 없이 돌려 보는 가짜 견본

**여기 있는 값은 모두 가짜다.** 종목 코드 `999901` · `999902` 는 실제 종목이 아니고, 날짜는 2097 · 2099 년, 가격은 고정 시드 랜덤워크다.
실제 시세 · 실제 트레이딩뷰 결과 · 실제 AI 답이 아니다. 다시 만들기: `python -X utf8 sample_data/make_samples.py`.

| 파일 | 무엇 | 쓰는 곳 |
|---|---|---|
| `fake_daily_bars.csv` | 가짜 일봉 400개(`time, open, high, low, close, volume`) = `strategy/validate.py` 의 `sample_bars(400, seed=7, start="2097-01-02")` | 봉 백테스트 · 대조 검증기 |
| `fake_tv_trades.csv` | 트레이딩뷰 거래 목록(List of trades) **형식** 견본 4거래 — 위 일봉에 이 프로그램의 봉 백테스트를 돌린 마지막 4거래를 그 열 이름 · 순서로 적은 것(트레이딩뷰에서 내보낸 것이 아니다) | 대조 검증기 `--tv-trades` |
| `fake_minute_bars.csv` | 가짜 1분봉, 두 종목 × 이틀(2099-01-02 금 · 2099-01-05 월, 09:00~15:19). `code` 열로 종목을 가름 | 봉 재생 `scripts/bar_replay.py` · 견본 한 바퀴 |
| `fake_judge_log.jsonl` | 가짜 판단 기록 두 줄(`logs/judge_*.jsonl` 꼴): 시황 판단 risk_off → 거르기 30분 · 특정 상황 판단 80점 → 999902 진입 신호(보유 15분) | `python -m judge.replay` 로 바꾸는 견본 |
| `fake_judge_replay.jsonl` | 위 기록을 `judge.replay` 로 바꾼 재생 입력(문에 닿은 시각 `at_ms` + 신호 · 거르기) | 봉 재생 `--ext` |
| `fake_run_log.jsonl` | 위 1분봉 · 재생 입력으로 2099-01-05 을 봉 재생한 실행기 기록(봉 사건은 뺌, 시각 칸도 가상 시계) | 기록 형식을 보는 견본 |

```
python -X utf8 scripts/strategy_backtest.py --csv sample_data/fake_daily_bars.csv --strategy supertrend
python -X utf8 scripts/strategy_validate.py supertrend --csv sample_data/fake_daily_bars.csv --tv-trades sample_data/fake_tv_trades.csv
python -X utf8 scripts/bar_replay.py --csv sample_data/fake_minute_bars.csv --day 20990105
python -X utf8 scripts/bar_replay.py --csv sample_data/fake_minute_bars.csv --day 20990105 --ext sample_data/fake_judge_replay.jsonl
python -X utf8 -m judge.replay sample_data/fake_judge_log.jsonl --out data/judge_replay.jsonl
```

## 진짜 자료는 어디서
- **한투 일봉 · 분봉**: 설정 폴더에 `kis_devlp.yaml` 을 넣은 뒤 `scripts/strategy_backtest.py --code <종목코드>` 로 받으면 `data/bars/` 에 캐시된다(저장소에 싣지 않음). 실시간 실행기도 끝날 때 받은 1분봉을 같은 곳에 이어 저장한다 → `bar_replay.py --csv data/bars/<종목코드>_1m.csv --code <종목코드>`.
- **다른 곳에서 받은 봉**: 같은 열(`time, open, high, low, close, volume`, 여러 종목이면 `code` 열)의 CSV 로 만든다. `time` 에 시간대가 없으면 한국 시간으로 본다. 그 자료의 이용 조건은 받은 곳의 약관을 따른다.
- **트레이딩뷰 거래 목록**: 트레이딩뷰 Strategy Tester → 거래 목록 → 내보내기(CSV). 차트 시간대를 Asia/Seoul 로.
