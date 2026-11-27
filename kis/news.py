"""한국투자증권 뉴스 제목 조회 — '종합 시황/공시(제목)' FHKST01011800 [국내주식-141].

무엇을: 최근 뉴스 제목 N건(전체, 또는 종목 하나에 등록된 것)을 돌려준다. 본문은 이 API 에 없다.
조회만 하고 쌓지 않는다 — 이 모듈에는 파일로 저장하는 기능이 없다. 저장할지 · 어디에 둘지는 부르는 쪽이 정한다.
상시로 전부 받는 용도가 아니다. 부르는 쪽(판단 · 화면 단추)이 간격과 횟수를 정한다.

근거(우리 폴더의 공식 자료 사본):
  - 요청 · 응답 칸: 한국투자 오픈API 전체문서(엑셀) '종합 시황_공시(제목)' 시트, 공식 예제 저장소
    open-trading-api 의 examples_llm 아래 domestic_stock 의 news_title 폴더(news_title.py · chk_news_title.py)
  - 실전 전용: 같은 시트와 TR 목록에 '모의투자 미지원' → 실전 키로 조회만 한다(주문 없음).
  - 요청 인자 8개는 모두 필수 칸이다. 종목 칸만 쓰고 나머지는 빈칸(문서: '공백 필수 입력', 날짜 · 시각 공백 = 현재 기준).
  - 한 번에 40건쯤 온다. 그 앞쪽은 FID_INPUT_SRNO 에 받은 것 중 가장 작은 일련번호를 넣어 넘긴다
    (그 번호 포함, 내림차순). 연속 조회 헤더(tr_cont)는 문서상 '다음조회 불가 API' 라 쓰지 않는다.
    이 넘기기 방식은 전체 조회로 실측한 것이고, 종목을 지정했을 때 같은지는 아직 확인하지 않았다.
  - 종목 코드 칸: 문서 Layout 은 iscd1~5, 같은 문서의 응답 예시는 iscd1~10 → 있는 것만 읽는다.
  - 일련번호는 드물게 겹친다(같은 번호에 다른 기사) → 같은 기사인지는 (일련번호, 공급사 코드, 제목)으로 본다.

이용 조건: 받은 제목은 쓰는 사람 본인의 한투 키 · 약관에 따른다. 원공급사(연합뉴스 등)의 저장 · 배포 조건이
  따로 있으므로 제목 원문을 오래 쌓거나 남에게 보이는 일은 부르는 쪽이 판단한다.
비밀값: 헤더는 kis.client.get 이 만든다(이 모듈은 키 · 토큰을 다루지 않는다). 오류 문구에 키 · 토큰은 들어가지 않는다.
"""
from __future__ import annotations

import re

from . import client, config

# 경로는 앞부분 상수 + 뒷부분으로 나눠 적는다(공개 점검이 40자 넘는 이어진 문자열을 비밀값 후보로 잡는다).
QUOTATIONS = "/uapi/domestic-stock/v1/quotations"
NEWS_PATH = QUOTATIONS + "/news-title"
TR_ID = "FHKST01011800"

MAX_LIMIT = 400          # 한 번 부를 때 받을 수 있는 최대 건수(넘기기 열 번 안팎). 상시 수집을 막는 선이다.
GAP_S = 0.35             # 기본 호출 간격(앞 응답 뒤). 다른 간격이 필요하면 pacer 를 넘긴다.
_CODE = re.compile(r"[0-9A-Z]{6}")   # 숫자 6자리 말고도 영문 섞인 6자리 종목 코드가 있다

_pacer = None            # 모듈 자체 간격(처음 부를 때 만든다)


class KeyMissing(RuntimeError):
    """한투 실전 키(설정 파일 · 값)가 없다 — 화면은 '키 없음'만 보이면 된다."""


def _default_pacer():
    global _pacer
    if _pacer is None:
        from strategy.data import Pacer          # 실행기와 같은 줄 세우기(초당 한도 EGW00201 이면 한 번 더)
        _pacer = Pacer(GAP_S)
    return _pacer


def load_real_config() -> config.KisConfig:
    """실전 키 설정을 읽는다. 파일이 없거나 칸이 비어 있으면 KeyMissing(문구에는 파일 이름 · 칸 이름만)."""
    try:
        return config.load("real")
    except (FileNotFoundError, ValueError) as e:
        raise KeyMissing(f"한투 실전 키를 쓸 수 없습니다 — {e}") from None


def _params(code: str, srno: str) -> dict:
    return {
        "FID_NEWS_OFER_ENTP_CODE": "",
        "FID_COND_MRKT_CLS_CODE": "",
        "FID_INPUT_ISCD": code,          # 빈칸 = 전체, 종목 코드 = 그 코드가 등록된 뉴스
        "FID_TITL_CNTT": "",
        "FID_INPUT_DATE_1": "",          # 빈칸 = 현재 기준
        "FID_INPUT_HOUR_1": "",
        "FID_RANK_SORT_CLS_CODE": "",
        "FID_INPUT_SRNO": srno,          # 빈칸 = 최신부터, 일련번호 = 그 번호부터 앞쪽
    }


def parse_row(x: dict) -> dict:
    """응답 한 줄 → 돌려주는 행. 문서에서 확인한 칸만 쓴다."""
    codes = [c for c in ((x.get(f"iscd{i}") or "").strip() for i in range(1, 11)) if c]
    return {
        "srno": (x.get("cntt_usiq_srno") or "").strip(),            # 내용 조회용 일련번호(앞 14자리 = 등록 시각)
        "date": (x.get("data_dt") or "").strip(),                   # 작성일자 YYYYMMDD
        "time": (x.get("data_tm") or "").strip(),                   # 작성시간 HHMMSS
        "provider_code": (x.get("news_ofer_entp_code") or "").strip(),   # 뉴스 제공 업체 코드
        "provider": (x.get("dorg") or "").strip(),                  # 자료원(공급사 이름)
        "codes": codes,                                             # 관련 종목 코드(없으면 빈 목록)
        "title": " ".join((x.get("hts_pbnt_titl_cntt") or "").split()),
    }


def recent_titles(code: str = "", limit: int = 40, *, cfg: config.KisConfig | None = None, pacer=None) -> list[dict]:
    """최근 뉴스 제목 limit 건(최신 것부터). code 를 주면 그 종목에 등록된 뉴스만.

    부르는 쪽이 간격과 횟수를 정한다 — 상시로 전부 받는 용도가 아니다. 결과를 저장하지 않는다.
    인자
      code   빈칸 = 전체, 종목 코드 6자리(영문 섞인 코드 포함)
      limit  1 ~ MAX_LIMIT
      cfg    실전 키 설정(없으면 load_real_config()). 모의 설정이면 ValueError — 이 API 는 모의 미지원.
      pacer  호출을 감쌀 줄 세우기(.call(fn, *args)). 없으면 모듈 자체 간격(GAP_S).
    돌려주는 행: srno · date · time · provider_code · provider · codes(list) · title
    예외: KeyMissing(키 없음) · kis.client.KisError(한투 오류 코드 · 문구) · requests 의 연결 오류
    """
    code = (code or "").strip().upper()
    if code and not _CODE.fullmatch(code):
        raise ValueError("종목 코드는 6자리(숫자 · 영문 대문자)")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit 은 1 ~ {MAX_LIMIT}")
    if cfg is None:
        cfg = load_real_config()
    if getattr(cfg, "is_paper", False):
        raise ValueError("뉴스 제목 조회는 모의투자 미지원 — 실전 키 설정으로 부른다(조회만)")
    pacer = pacer or _default_pacer()

    out: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    srno = ""
    for _ in range(MAX_LIMIT // 20 + 2):         # 넘기기 횟수의 안전 상한(한 쪽이 적게 와도 끝나게)
        d = pacer.call(client.get, cfg, NEWS_PATH, TR_ID, _params(code, srno))
        raw = d.get("output") or []
        if isinstance(raw, dict):                # 한 건일 때 객체로 올 수 있다(공식 예제가 같은 처리)
            raw = [raw]
        fresh = 0
        for x in raw:
            row = parse_row(x)
            key = (row["srno"], row["provider_code"], row["title"])
            if not row["srno"] or key in seen:   # 넘길 때 경계 한 건은 겹쳐 온다
                continue
            seen.add(key)
            out.append(row)
            fresh += 1
            if len(out) >= limit:
                return out
        if not fresh:                            # 더 앞쪽이 없다(보관 범위의 끝 또는 빈 응답)
            break
        srno = min(r["srno"] for r in out)
    return out
