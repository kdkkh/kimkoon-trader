"""OpenDART 공시 목록 조회(dart/opendart.py) — 가짜 HTTP 와 임시 폴더의 가짜 키 파일만 쓴다.

바깥 API 를 부르지 않는다. 견본 행의 회사 이름 · 종목 코드 · 고유번호는 지어낸 값이다.
"""
import io
import zipfile

import pytest
import requests

from dart import opendart as od

FAKE = "fk-0123456"          # 가짜 인증키(시험용)


class FakePacer:
    def __init__(self):
        self.calls = 0

    def call(self, fn, *a, **k):
        self.calls += 1
        return fn(*a, **k)


class _Resp:
    def __init__(self, body=None, status=200, content=b""):
        self.status_code, self._b, self.content = status, body, content

    def json(self):
        if self._b is None:
            raise ValueError("not json")
        return self._b


class FakeHttp:
    """requests.get 자리 — 부른 주소 · 인자를 남기고 정해 둔 응답을 차례로 돌려준다."""

    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _item(n, stock="000001"):
    return {"corp_cls": "K", "corp_name": f"가짜회사{n}", "corp_code": f"{n:08d}", "stock_code": stock,
            "report_nm": "주요사항보고서  (유상증자결정)", "rcept_no": f"20261123{n:06d}", "flr_nm": "가짜회사",
            "rcept_dt": "20261123", "rm": "코"}


def _page(items, page=1, total_page=1):
    return {"status": "000", "message": "정상", "page_no": page, "page_count": 100,
            "total_count": len(items), "total_page": total_page, "list": items}


@pytest.fixture
def keydir(tmp_path, monkeypatch):
    d = tmp_path / "cfg"
    d.mkdir()
    (d / "opendart.yaml").write_text(f"api_key: {FAKE}\n", encoding="utf-8")
    monkeypatch.setenv("KIS_CONFIG_DIR", str(d))
    monkeypatch.setattr(od, "_today", lambda: "20261123")
    return d


def _http(monkeypatch, *responses):
    fake = FakeHttp(*responses)
    monkeypatch.setattr(od.requests, "get", fake)
    return fake


def test_parse_row_has_source_and_viewer():
    r = od.parse_row(_item(7))
    assert r["source"] == "금융감독원 전자공시(OpenDART)"
    assert r["rcept_no"] == "20261123000007" and r["viewer_url"].endswith("rcpNo=20261123000007")
    assert r["report_nm"] == "주요사항보고서 (유상증자결정)"
    assert {k for k in r} == {"rcept_no", "rcept_dt", "corp_code", "corp_name", "stock_code", "corp_cls",
                              "report_nm", "flr_nm", "rm", "viewer_url", "source"}


def test_pages_until_limit_and_counts(keydir, tmp_path, monkeypatch):
    fake = _http(monkeypatch, _Resp(_page([_item(i) for i in range(100)], 1, 3)),
                 _Resp(_page([_item(i) for i in range(100, 200)], 2, 3)))
    p = FakePacer()
    rows = od.list_disclosures(tmp_path / "st", start="20261120", end="20261123", limit=150, pacer=p)
    assert len(rows) == 150 and p.calls == 2
    url, q = fake.calls[1]
    assert url == od.LIST_URL
    assert q == {"crtfc_key": FAKE, "bgn_de": "20261120", "end_de": "20261123", "page_no": 2, "page_count": 100}
    assert od.CallCounter(tmp_path / "st").used() == 2
    assert (tmp_path / "st" / "opendart_calls_20261123.txt").read_text(encoding="utf-8") == "2"


def test_status_013_is_empty_list(keydir, tmp_path, monkeypatch):
    _http(monkeypatch, _Resp({"status": "013", "message": "조회된 데이타가 없습니다."}))
    assert od.list_disclosures(tmp_path, corp_code="00000001", pacer=FakePacer()) == []


def test_error_status_raises_with_code(keydir, tmp_path, monkeypatch):
    _http(monkeypatch, _Resp({"status": "011", "message": "사용할 수 없는 키입니다."}))
    with pytest.raises(od.OpenDartError) as e:
        od.list_disclosures(tmp_path, pacer=FakePacer())
    assert e.value.status == "011" and "011" in str(e.value)


def test_server_message_key_is_masked(keydir, tmp_path, monkeypatch):
    _http(monkeypatch, _Resp({"status": "100", "message": f"crtfc_key={FAKE} 값이 부적절"}))
    with pytest.raises(od.OpenDartError) as e:
        od.list_disclosures(tmp_path, pacer=FakePacer())
    assert FAKE not in str(e.value) and "***" in str(e.value)


def test_network_error_hides_url_and_key(keydir, tmp_path, monkeypatch):
    boom = requests.ConnectionError(f"Max retries exceeded with url: {od.LIST_URL}?crtfc_key={FAKE}")
    _http(monkeypatch, boom)
    with pytest.raises(od.OpenDartError) as e:
        od.list_disclosures(tmp_path, pacer=FakePacer())
    msg = str(e.value) + repr(e.value)
    assert FAKE not in msg and "crtfc_key" not in msg and "opendart.fss" not in msg
    assert e.value.__cause__ is None and e.value.__suppress_context__


def test_not_json_raises(keydir, tmp_path, monkeypatch):
    _http(monkeypatch, _Resp(None, status=502))
    with pytest.raises(od.OpenDartError, match="502"):
        od.list_disclosures(tmp_path, pacer=FakePacer())


def test_daily_cap_blocks_before_sending(keydir, tmp_path, monkeypatch):
    fake = _http(monkeypatch, *[_Resp({"status": "013"}) for _ in range(3)])
    for _ in range(2):
        od.list_disclosures(tmp_path, daily_cap=2, pacer=FakePacer())
    with pytest.raises(od.DailyCapReached):
        od.list_disclosures(tmp_path, daily_cap=2, pacer=FakePacer())
    assert len(fake.calls) == 2                       # 세 번째는 보내지 않음


def test_daily_cap_resets_next_day(keydir, tmp_path, monkeypatch):
    _http(monkeypatch, _Resp({"status": "013"}), _Resp({"status": "013"}))
    od.list_disclosures(tmp_path, daily_cap=1, pacer=FakePacer())
    monkeypatch.setattr(od, "_today", lambda: "20261124")
    od.list_disclosures(tmp_path, daily_cap=1, pacer=FakePacer())
    c = od.CallCounter(tmp_path, 1)
    assert c.used("20261123") == 1 and c.used("20261124") == 1


def test_server_020_is_daily_cap(keydir, tmp_path, monkeypatch):
    _http(monkeypatch, _Resp({"status": "020", "message": "요청 제한을 초과하였습니다."}))
    with pytest.raises(od.DailyCapReached):
        od.list_disclosures(tmp_path, pacer=FakePacer())


def test_bad_daily_cap_rejected(tmp_path):
    for cap in (0, od.HARD_DAILY_CAP + 1):
        with pytest.raises(ValueError):
            od.CallCounter(tmp_path, cap)


def test_key_missing_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("KIS_CONFIG_DIR", str(tmp_path / "empty"))
    fake = _http(monkeypatch)
    with pytest.raises(od.KeyMissing) as e:
        od.list_disclosures(tmp_path, pacer=FakePacer())
    assert str(tmp_path) not in str(e.value) and fake.calls == []
    assert od.CallCounter(tmp_path).used() == 0       # 키가 없으면 세지도 않는다


@pytest.mark.parametrize("text", ["api_key: ''\n", "other: 1\n", "api_key: 인증키를 넣으세요\n", ""])
def test_key_missing_empty_or_placeholder(tmp_path, text):
    (tmp_path / "opendart.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(od.KeyMissing):
        od.load_key(tmp_path)


def test_broken_yaml_hides_content(tmp_path):
    (tmp_path / "opendart.yaml").write_text(f"api_key: [{FAKE}\n  : :", encoding="utf-8")
    with pytest.raises(od.KeyMissing) as e:
        od.load_key(tmp_path)
    assert FAKE not in str(e.value) and e.value.__cause__ is None


def test_key_dir_from_env(keydir):
    assert od.config_dir() == keydir and od.load_key() == FAKE


def test_today_filters_codes_without_map(keydir, tmp_path, monkeypatch):
    items = [_item(1, "000001"), _item(2, "000002"), _item(3, ""), _item(4, "00000K")]
    fake = _http(monkeypatch, _Resp(_page(items)))
    rows = od.today_for_codes(tmp_path, ["000001", "00000k"], pacer=FakePacer())
    assert [r["stock_code"] for r in rows] == ["000001", "00000K"]
    assert fake.calls[0][1]["bgn_de"] == "20261123" == fake.calls[0][1]["end_de"]


def test_today_with_map_queries_corp_code(keydir, tmp_path, monkeypatch):
    fake = _http(monkeypatch, _Resp(_page([_item(1)])))
    rows = od.today_for_codes(tmp_path, ["000001", "000009"], corp_map={"000001": "00000001"}, pacer=FakePacer())
    assert len(rows) == 1 and len(fake.calls) == 1            # 표에 없는 종목은 부르지 않음
    assert fake.calls[0][1]["corp_code"] == "00000001"


def test_today_stops_at_max_pages(keydir, tmp_path, monkeypatch):
    pages = [_Resp(_page([_item(n, "000001")] * 100, n, 9)) for n in range(1, 4)]
    fake = _http(monkeypatch, *pages)
    rows = od.today_for_codes(tmp_path, ["000001"], max_pages=2, pacer=FakePacer())
    assert len(fake.calls) == 2 and len(rows) == 200


def test_today_bad_code_rejected(keydir, tmp_path):
    with pytest.raises(ValueError):
        od.today_for_codes(tmp_path, ["12345"])


def _corp_zip():
    xml = ("<?xml version='1.0' encoding='UTF-8'?><result>"
           "<list><corp_code>00000001</corp_code><corp_name>가짜회사1</corp_name><stock_code>000001</stock_code></list>"
           "<list><corp_code>00000002</corp_code><corp_name>비상장</corp_name><stock_code> </stock_code></list>"
           "</result>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", xml.encode("utf-8"))
    return buf.getvalue()


def test_corp_zip_fetch_and_map(keydir, tmp_path, monkeypatch):
    fake = _http(monkeypatch, _Resp(content=_corp_zip()))
    data = od.fetch_corp_code_zip(tmp_path)
    assert fake.calls[0] == (od.CORPCODE_URL, {"crtfc_key": FAKE})
    assert od.load_corp_map(data) == {"000001": "00000001"}
    assert od.CallCounter(tmp_path).used() == 1
    assert list(tmp_path.glob("*.zip")) == []                  # 저장은 부르는 쪽 몫


def test_corp_zip_error_reply(keydir, tmp_path, monkeypatch):
    body = f'{{"status":"010","message":"등록되지 않은 키 {FAKE}"}}'.encode()
    _http(monkeypatch, _Resp(content=body))
    with pytest.raises(od.OpenDartError) as e:
        od.fetch_corp_code_zip(tmp_path)
    assert e.value.status == "010" and FAKE not in str(e.value)


def test_corp_map_from_file(tmp_path):
    p = tmp_path / "corp.zip"
    p.write_bytes(_corp_zip())
    assert od.load_corp_map(p) == {"000001": "00000001"}


@pytest.mark.parametrize("kw", [{"corp_code": "123"}, {"start": "2026-11-23"}, {"corp_cls": "X"},
                                {"pblntf_ty": "Z"}, {"limit": 0}, {"limit": od.MAX_LIMIT + 1}])
def test_bad_arguments_rejected(keydir, tmp_path, kw):
    with pytest.raises(ValueError):
        od.list_disclosures(tmp_path, pacer=FakePacer(), **kw)


def test_sources_one_fetch_per_round(tmp_path, monkeypatch):
    """종목마다 묻는 판단이 한 바퀴에 전체 목록을 한 번만 받는다(2026-11-26: 종목 수만큼 받아 하루 몫이 바닥남)."""
    from judge import inputs
    n = []

    def fake_all(state_root, **kw):
        n.append(kw.get("daily_cap"))
        return [{"stock_code": "000001", "rcept_no": "1"}, {"stock_code": "000002", "rcept_no": "2"}]
    monkeypatch.setattr(od, "today_all", fake_all)
    t = [1000.0]
    s = inputs.Sources(tmp_path, opendart_cap=7, clock=lambda: t[0])
    assert [r["rcept_no"] for r in s.disclosures(["000001"])] == ["1"]
    assert [r["rcept_no"] for r in s.disclosures(["000002"])] == ["2"]
    assert s.disclosures(["000009"]) == [] and s.disclosures([]) == []
    assert n == [7] and s.calls["disclosures"] == 1
    t[0] += 11                                                            # 다음 바퀴에는 새로 받는다
    s.disclosures(["000001"])
    assert len(n) == 2
