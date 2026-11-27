"""OpenDART 공시검색(list.json) 조회 — 종목 · 기간, 오늘 것.

무엇을: 공시 목록(접수번호 · 회사 · 보고서 이름 · 제출인 · 비고)을 돌려준다. 시각 칸은 없다(접수일자만).
조회만 하고 쌓지 않는다 — 이 모듈에는 받은 공시를 파일로 저장하는 기능이 없다(호출 수 기록 파일 하나만 쓴다).
상시로 전부 받는 용도가 아니다. 부르는 쪽(판단 · 화면 단추)이 간격과 횟수를 정한다.

근거(OpenDART 개발가이드 '공시검색' · '고유번호' 문서, opendart.fss.or.kr):
  - 요청 인자: crtfc_key(필수) · corp_code(고유번호 8자리) · bgn_de · end_de(YYYYMMDD) · corp_cls(Y/K/N/E)
    · pblntf_ty(A~J) · page_no · page_count(1~100). 날짜를 주지 않으면 당일 기준.
    고유번호 없이 찾으면 검색 기간이 3개월로 제한된다(문서). 이 모듈은 기간을 미리 자르지 않는다.
  - 응답: status · message · page_no · page_count · total_count · total_page · list[corp_cls · corp_name · corp_code
    · stock_code · report_nm · rcept_no · flr_nm · rcept_dt · rm]
  - status 000 정상 · 013 조회된 데이터 없음(정상으로 빈 목록) · 020 요청 제한 초과 · 그 밖은 오류.
  - 한도: 개인 키는 하루 20,000건(모든 오픈API 합산, 자정 초기화 여부는 문서에 없음), 분당 1,000회 넘으면 IP 차단.
    → 이 모듈은 이 PC 에서 하루에 쓸 몫(daily_cap)을 따로 두고, 호출 전에 날짜별 기록 파일로 센다.
      같은 키를 다른 프로그램 · 다른 PC 와 나눠 쓰면 그쪽 호출은 여기 숫자에 들어가지 않는다.
  - list.json 에는 종목 코드 인자가 없다. 종목으로 찾는 길은 둘:
      ① 고유번호 표(corpCode.xml, zip 한 번에 전체)로 종목 코드 → 고유번호를 바꿔 corp_code 로 찾는다(종목당 1회).
         표는 쓰는 사람이 단추로 한 번 받아 두고(fetch_corp_code_zip → 저장은 부르는 쪽), 그 파일을 load_corp_map 으로 읽는다.
         표의 변경 반영은 '익영업일 오전'(FAQ) — 오늘 상장한 회사는 아직 없을 수 있다.
      ② 표 없이: 오늘 전체 목록을 쪽마다 받아 종목 코드로 거른다(today_for_codes 의 corp_map=None).

이용 조건: 공시 목록은 공공누리 제1유형(출처 표시) — 돌려주는 행마다 source 칸에 출처 문구를 붙인다.
  공시 내용의 정확성은 금융감독원이 보장하지 않는다(이용약관 제23조).
비밀값: 인증키는 요청 주소(쿼리)에 붙는다 → 이 모듈은 주소 · 요청 예외 원문을 문구에 싣지 않고,
  서버 문구에 키가 섞여 오면 가린다. 키 파일 경로도 문구에 내지 않는다(사용자 폴더 이름이 들어 있으므로).
"""
from __future__ import annotations

import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

import requests
import yaml

SOURCE = "금융감독원 전자공시(OpenDART)"
HOST = "https://opendart.fss.or.kr"
LIST_URL = HOST + "/api/list.json"
CORPCODE_URL = HOST + "/api/corpCode.xml"
VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="   # 개발가이드의 공시뷰어 연결 예시
KEY_FILE_NAME = "opendart.yaml"                                  # 칸 api_key 하나
CALLS_PREFIX = "opendart_calls_"                                 # 호출 수 기록: <뿌리>/opendart_calls_YYYYMMDD.txt

DEFAULT_DAILY_CAP = 2000     # 이 PC 의 하루 몫 기본값(개인 키 전체 한도 20,000건 중 일부 — 다른 쓰임과 나눠 쓰게)
HARD_DAILY_CAP = 20000       # 설정으로 올릴 수 있는 끝(개인 키 문서 한도)
PAGE_MAX = 100               # page_count 최대(문서)
MAX_LIMIT = 3000             # 한 번 부를 때 받을 최대 건수(서른 쪽). 하루 공시는 영업일 평균 천 건 안팎(1년 24만 5천여 건 기록으로 어림), 마감일엔 더 많다
GAP_S = 0.3                  # 쪽 사이 기본 간격
_YMD = re.compile(r"\d{8}")
_CORP = re.compile(r"\d{8}")
_STOCK = re.compile(r"[0-9A-Z]{6}")

_pacer = None


class KeyMissing(RuntimeError):
    """OpenDART 인증키 파일 · 값이 없다 — 화면은 '키 없음'만 보이면 된다."""


class DailyCapReached(RuntimeError):
    """오늘 몫(daily_cap)을 다 썼거나 서버가 요청 제한 초과(020)를 알렸다 — 오늘은 더 부르지 않는다."""


class OpenDartError(RuntimeError):
    """OpenDART 가 오류 상태를 돌려줬거나 요청이 실패했다. 문구에 주소 · 키를 싣지 않는다."""

    def __init__(self, msg: str, status: str = ""):
        super().__init__(msg)
        self.status = status


# ---------- 키 ----------
def config_dir() -> Path:
    """설정 폴더 — 환경 변수 KIS_CONFIG_DIR, 없으면 홈 폴더 KIS/config (kis.config 와 같은 규칙, 부를 때마다 읽음)."""
    d = os.environ.get("KIS_CONFIG_DIR")
    return Path(d) if d else Path.home() / "KIS" / "config"


def load_key(cfg_dir: Path | None = None) -> str:
    """opendart.yaml 의 api_key 를 읽는다. 값은 돌려주기만 하고 출력하지 않는다."""
    p = Path(cfg_dir) if cfg_dir else config_dir()
    p = p / KEY_FILE_NAME
    if not p.is_file():
        raise KeyMissing(f"{KEY_FILE_NAME} 이 없습니다 (위치: KIS_CONFIG_DIR 또는 ~/KIS/config/)")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        # yaml 오류 문구에는 파일 내용 한 줄(키일 수 있음)이 들어가므로 원문을 싣지 않는다
        raise KeyMissing(f"{KEY_FILE_NAME} 을 읽을 수 없습니다(형식 확인)") from None
    key = str(raw.get("api_key") or "").strip() if isinstance(raw, dict) else ""
    if not key or re.search("[가-힣]", key):     # 비었거나 안내 문구(한글) 그대로
        raise KeyMissing(f"{KEY_FILE_NAME} 의 api_key 가 비어 있습니다")
    return key


# ---------- 하루 호출 수 ----------
def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


class CallCounter:
    """날짜별 호출 수 — <state_root>/opendart_calls_YYYYMMDD.txt 에 숫자 하나. 보내기 전에 하나를 센다
    (실패한 요청도 서버 한도에서 빠질 수 있으므로). 여러 프로세스가 동시에 세는 경우는 고려하지 않는다."""

    def __init__(self, state_root: Path, daily_cap: int = DEFAULT_DAILY_CAP):
        if not isinstance(daily_cap, int) or not 1 <= daily_cap <= HARD_DAILY_CAP:
            raise ValueError(f"daily_cap 은 1 ~ {HARD_DAILY_CAP}")
        self.root = Path(state_root)
        self.cap = daily_cap

    def _file(self, day: str) -> Path:
        return self.root / f"{CALLS_PREFIX}{day}.txt"

    def used(self, day: str | None = None) -> int:
        f = self._file(day or _today())
        try:
            return int(f.read_text(encoding="utf-8").strip() or 0)
        except (FileNotFoundError, ValueError):
            return 0

    def take(self) -> int:
        """하나를 쓴다. 오늘 몫을 넘으면 DailyCapReached(보내지 않음). 쓴 뒤의 오늘 호출 수를 돌려준다."""
        day = _today()
        n = self.used(day)
        if n >= self.cap:
            raise DailyCapReached(f"OpenDART 오늘 몫 {self.cap}건을 다 썼습니다(설정 daily_cap)")
        self.root.mkdir(parents=True, exist_ok=True)
        f = self._file(day)
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        tmp.write_text(str(n + 1), encoding="utf-8")
        os.replace(tmp, f)
        return n + 1


# ---------- 요청 ----------
def _default_pacer():
    global _pacer
    if _pacer is None:
        from strategy.data import Pacer
        _pacer = Pacer(GAP_S)
    return _pacer


def _scrub(text: str, key: str) -> str:
    return str(text).replace(key, "***") if key else str(text)


def _get(url: str, params: dict, key: str, counter: CallCounter, timeout: float):
    counter.take()
    try:
        return requests.get(url, params={"crtfc_key": key, **params}, timeout=timeout)
    except requests.RequestException as e:
        # 예외 원문에는 키가 붙은 주소가 들어 있다 → 종류 이름만
        raise OpenDartError(f"OpenDART 요청 실패: {type(e).__name__}") from None


def _get_json(params: dict, key: str, counter: CallCounter) -> dict:
    r = _get(LIST_URL, params, key, counter, timeout=10)
    try:
        j = r.json()
    except ValueError:
        raise OpenDartError(f"OpenDART 응답이 JSON 이 아닙니다(HTTP {r.status_code})") from None
    if not isinstance(j, dict):
        raise OpenDartError(f"OpenDART 응답 형식이 다릅니다(HTTP {r.status_code})")
    st = str(j.get("status") or "")
    if st == "020":
        raise DailyCapReached("OpenDART 요청 제한 초과(status 020) — 오늘은 더 부르지 않는다")
    if st not in ("000", "013"):
        msg = _scrub(j.get("message") or "", key)[:120]
        raise OpenDartError(f"OpenDART status {st or '?'} {msg}".strip(), status=st)
    return j


def parse_row(x: dict) -> dict:
    """list 한 줄 → 돌려주는 행. 개발가이드에서 확인한 칸만 쓰고 출처 문구를 붙인다."""
    no = str(x.get("rcept_no") or "").strip()
    return {
        "rcept_no": no,                                          # 접수번호 14자리(앞 8자리 = 날짜)
        "rcept_dt": str(x.get("rcept_dt") or "").strip(),        # 접수일자 YYYYMMDD(시각 없음)
        "corp_code": str(x.get("corp_code") or "").strip(),      # 고유번호 8자리
        "corp_name": str(x.get("corp_name") or "").strip(),
        "stock_code": str(x.get("stock_code") or "").strip(),    # 상장회사만 6자리, 아니면 빈칸
        "corp_cls": str(x.get("corp_cls") or "").strip(),        # Y 유가 · K 코스닥 · N 코넥스 · E 기타
        "report_nm": " ".join(str(x.get("report_nm") or "").split()),
        "flr_nm": str(x.get("flr_nm") or "").strip(),            # 공시 제출인
        "rm": str(x.get("rm") or "").strip(),                    # 비고(유 · 코 · 정 · 연 · 철 등)
        "viewer_url": VIEWER_URL + no if no else "",
        "source": SOURCE,
    }


def _check_day(name: str, v: str) -> str:
    v = (v or "").strip()
    if v and not _YMD.fullmatch(v):
        raise ValueError(f"{name} 은 YYYYMMDD")
    return v


def list_disclosures(state_root: Path, *, corp_code: str = "", start: str = "", end: str = "",
                     corp_cls: str = "", pblntf_ty: str = "", limit: int = 100,
                     daily_cap: int = DEFAULT_DAILY_CAP, cfg_dir: Path | None = None, pacer=None) -> list[dict]:
    """공시 목록 limit 건(서버 기본 정렬 = 접수일자 내림차순). 날짜를 비우면 당일.

    부르는 쪽이 간격과 횟수를 정한다 — 상시로 전부 받는 용도가 아니다. 결과를 저장하지 않는다.
    인자
      state_root  호출 수 기록 파일을 둘 폴더(부르는 쪽의 state 폴더 등)
      corp_code   고유번호 8자리(빈칸 = 모든 회사, 이때 기간은 3개월까지 — 문서)
      start, end  YYYYMMDD. corp_cls 'Y'/'K'/'N'/'E'. pblntf_ty 'A'~'J'
      limit       1 ~ MAX_LIMIT (100건마다 1회 호출)
      daily_cap   이 PC 의 하루 몫(기본 DEFAULT_DAILY_CAP, 1 ~ 20,000)
      cfg_dir     키 파일 폴더(없으면 KIS_CONFIG_DIR 또는 ~/KIS/config)
      pacer       쪽 사이 줄 세우기(.call(fn, *args)). 없으면 모듈 자체 간격(GAP_S)
    돌려주는 행: rcept_no · rcept_dt · corp_code · corp_name · stock_code · corp_cls · report_nm · flr_nm · rm
                 · viewer_url · source(출처 문구)
    예외: KeyMissing · DailyCapReached · OpenDartError
    """
    corp_code = (corp_code or "").strip()
    if corp_code and not _CORP.fullmatch(corp_code):
        raise ValueError("corp_code 는 고유번호 8자리")
    start, end = _check_day("start", start), _check_day("end", end)
    if corp_cls and corp_cls not in ("Y", "K", "N", "E"):
        raise ValueError("corp_cls 는 Y · K · N · E")
    if pblntf_ty and pblntf_ty not in tuple("ABCDEFGHIJ"):
        raise ValueError("pblntf_ty 는 A ~ J")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit 은 1 ~ {MAX_LIMIT}")
    counter = CallCounter(state_root, daily_cap)
    key = load_key(cfg_dir)
    pacer = pacer or _default_pacer()

    base = {k: v for k, v in (("corp_code", corp_code), ("bgn_de", start), ("end_de", end),
                              ("corp_cls", corp_cls), ("pblntf_ty", pblntf_ty)) if v}
    size = min(PAGE_MAX, limit)
    out: list[dict] = []
    page = 1
    while True:
        j = pacer.call(_get_json, {**base, "page_no": page, "page_count": size}, key, counter)
        if j.get("status") == "013":               # 조회된 데이터 없음 = 정상, 빈 목록
            break
        rows = j.get("list") or []
        out.extend(parse_row(x) for x in rows)
        try:
            total_page = int(j.get("total_page") or 0)
        except (TypeError, ValueError):
            total_page = 0
        if len(out) >= limit or not rows or page >= total_page:
            break
        page += 1
    return out[:limit]


def today_for_codes(state_root: Path, codes, *, corp_map: dict[str, str] | None = None, max_pages: int = 20,
                    daily_cap: int = DEFAULT_DAILY_CAP, cfg_dir: Path | None = None, pacer=None) -> list[dict]:
    """종목 코드들의 오늘 공시. 부르는 쪽이 간격과 횟수를 정한다 — 상시로 전부 받는 용도가 아니다.

    corp_map(종목 코드 → 고유번호, load_corp_map 결과)이 있으면 종목마다 1회(표에 없는 종목은 건너뜀).
    없으면 오늘 전체 목록을 쪽마다(100건씩, 최대 max_pages 쪽) 받아 종목 코드로 거른다 — 종목이 많을 때는 이쪽이 적게 부른다.
    이 길은 최신 것부터 max_pages 쪽까지만 본다. 그보다 앞서 접수된 오늘 공시는 빠진다(정기보고서 마감일처럼
    하루 공시가 많은 날) — 빠짐없이 보려면 corp_map 길을 쓴다.
    돌려주는 행은 list_disclosures 와 같다.
    """
    want = []
    for c in codes:
        c = str(c).strip().upper()
        if not _STOCK.fullmatch(c):
            raise ValueError("종목 코드는 6자리(숫자 · 영문 대문자)")
        if c not in want:
            want.append(c)
    if not want:
        return []
    day = _today()
    if corp_map is not None:
        out: list[dict] = []
        for c in want:
            cc = corp_map.get(c)
            if cc:
                out.extend(list_disclosures(state_root, corp_code=cc, start=day, end=day, limit=PAGE_MAX,
                                            daily_cap=daily_cap, cfg_dir=cfg_dir, pacer=pacer))
        return out
    rows = today_all(state_root, max_pages=max_pages, daily_cap=daily_cap, cfg_dir=cfg_dir, pacer=pacer)
    keep = set(want)
    return [r for r in rows if r["stock_code"] in keep]


def today_all(state_root: Path, *, max_pages: int = 20, daily_cap: int = DEFAULT_DAILY_CAP,
              cfg_dir: Path | None = None, pacer=None) -> list[dict]:
    """오늘 전체 공시 목록(최신 것부터 max_pages 쪽까지, 100건마다 1회 호출). 여러 종목을 차례로 볼 때는
    이것을 한 번 받아 두고 종목 코드로 거른다 — 종목마다 today_for_codes 를 부르면 같은 목록을 종목 수만큼 받는다."""
    if not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_LIMIT // PAGE_MAX:
        raise ValueError(f"max_pages 는 1 ~ {MAX_LIMIT // PAGE_MAX}")
    day = _today()
    return list_disclosures(state_root, start=day, end=day, limit=max_pages * PAGE_MAX,
                            daily_cap=daily_cap, cfg_dir=cfg_dir, pacer=pacer)


# ---------- 고유번호 표(단추식: 쓰는 사람이 한 번 받아 둔다) ----------
def fetch_corp_code_zip(state_root: Path, *, daily_cap: int = DEFAULT_DAILY_CAP,
                        cfg_dir: Path | None = None) -> bytes:
    """고유번호 표(corpCode.xml, zip) 를 한 번 받아 바이트로 돌려준다. 저장은 부르는 쪽 몫(화면의 '받아 두기' 단추 등).

    자동으로 부르지 않는다 — 전체 회사 목록이라 한 번에 크고, 변경 반영은 하루 한 번(익영업일 오전)이다.
    OpenDART 점검 때 자주 멈추는 API 다(공지의 중지 대상). 호출 1회를 오늘 몫에서 센다.
    """
    counter = CallCounter(state_root, daily_cap)
    key = load_key(cfg_dir)
    r = _get(CORPCODE_URL, {}, key, counter, timeout=60)
    body = r.content or b""
    if zipfile.is_zipfile(io.BytesIO(body)):
        return body
    # zip 이 아니면 오류 응답(XML 또는 JSON) — 상태 코드와 문구만 꺼낸다
    head = body[:400].decode("utf-8", "replace")
    st = re.search(r"<status>(\d+)</status>|\"status\"\s*:\s*\"(\d+)\"", head)
    msg = re.search(r"<message>(.*?)</message>|\"message\"\s*:\s*\"(.*?)\"", head)
    st = (st.group(1) or st.group(2)) if st else ""
    msg = _scrub((msg.group(1) or msg.group(2)) if msg else "", key)[:120]
    if st == "020":
        raise DailyCapReached("OpenDART 요청 제한 초과(status 020) — 오늘은 더 부르지 않는다")
    raise OpenDartError(f"고유번호 표가 zip 이 아닙니다(HTTP {r.status_code}, status {st or '?'}) {msg}".strip(), status=st)


def load_corp_map(zip_source) -> dict[str, str]:
    """고유번호 zip(파일 경로 또는 바이트) → {종목 코드: 고유번호}. 종목 코드가 없는 회사(비상장 등)는 뺀다.
    zip 은 디스크에 풀지 않고 메모리에서 읽는다. 표는 받은 날 기준 상장 상태다."""
    data = zip_source if isinstance(zip_source, (bytes, bytearray)) else Path(zip_source).read_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".xml")), None)
        if name is None:
            raise OpenDartError("고유번호 zip 안에 XML 파일이 없습니다")
        xml = z.read(name)
    out: dict[str, str] = {}
    for e in ET.fromstring(xml).iter("list"):
        cc = (e.findtext("corp_code") or "").strip()
        sc = (e.findtext("stock_code") or "").strip()
        if cc and sc:
            out[sc] = cc
    return out
