"""한투 뉴스 제목 조회(kis/news.py) — 가짜 클라이언트 · 가짜 HTTP 만 쓴다(바깥 API 를 부르지 않는다).

견본 행의 회사 · 공급사 이름과 종목 코드는 지어낸 값이다.
"""
from types import SimpleNamespace

import pytest

from kis import client, config, news

CFG = SimpleNamespace(mode="real", is_paper=False, rest_url="https://example.invalid",
                      app_key="AK-fake-01", app_secret="AS-fake-02", user_agent="test")


class FakePacer:
    def __init__(self):
        self.calls = 0

    def call(self, fn, *a, **k):
        self.calls += 1
        return fn(*a, **k)


def _row(srno, title="시험 제목", code="", prov="9"):
    r = {"cntt_usiq_srno": srno, "news_ofer_entp_code": prov, "data_dt": srno[:8], "data_tm": srno[8:14],
         "hts_pbnt_titl_cntt": title, "news_lrdv_code": "01", "dorg": "가짜통신"}
    for i in range(1, 11):
        r[f"iscd{i}"] = ""
    r["iscd1"] = code
    return r


def _srno(n):
    return f"20261123{90000 + n:06d}{n:05d}"     # 앞 14자리 = 날짜 + 시각, 뒤 5자리 = 번호


class FakeGet:
    """client.get 자리 — 일련번호 인자에 따라 쪽을 돌려준다."""

    def __init__(self, pages):
        self.pages, self.params = pages, []

    def __call__(self, cfg, path, tr_id, params):
        assert path == news.NEWS_PATH and tr_id == news.TR_ID
        self.params.append(dict(params))
        return {"rt_cd": "0", "output": self.pages.get(params["FID_INPUT_SRNO"], [])}


def test_parse_row_fields():
    x = _row(_srno(1), "  제목   가운데  띄움 ", code="000001")
    x["iscd7"] = "00000K"
    r = news.parse_row(x)
    assert r == {"srno": _srno(1), "date": "20261123", "time": "090001", "provider_code": "9",
                 "provider": "가짜통신", "codes": ["000001", "00000K"], "title": "제목 가운데 띄움"}


def test_path_split_and_full_value():
    assert news.NEWS_PATH == news.QUOTATIONS + "/news-title"
    assert news.NEWS_PATH.endswith("/quotations/news-title")


def test_pages_by_srno_and_dedupes(monkeypatch):
    first = [_row(_srno(n)) for n in range(80, 40, -1)]            # 40건, 내림차순
    low = first[-1]["cntt_usiq_srno"]
    second = [first[-1]] + [_row(_srno(n)) for n in range(40, 20, -1)]   # 경계 한 건 겹침 + 20건
    fake = FakeGet({"": first, low: second})
    monkeypatch.setattr(news.client, "get", fake)
    p = FakePacer()
    rows = news.recent_titles(limit=50, cfg=CFG, pacer=p)
    assert len(rows) == 50 and len({r["srno"] for r in rows}) == 50
    assert [q["FID_INPUT_SRNO"] for q in fake.params] == ["", low]
    assert p.calls == 2
    assert rows[0]["srno"] == _srno(80) and rows[-1]["srno"] == _srno(31)


def test_stops_when_no_new_rows(monkeypatch):
    first = [_row(_srno(n)) for n in range(5, 0, -1)]
    low = first[-1]["cntt_usiq_srno"]
    fake = FakeGet({"": first, low: [first[-1]]})                # 앞쪽이 더 없음
    monkeypatch.setattr(news.client, "get", fake)
    rows = news.recent_titles(limit=40, cfg=CFG, pacer=FakePacer())
    assert len(rows) == 5 and len(fake.params) == 2


def test_empty_output_is_empty_list(monkeypatch):
    monkeypatch.setattr(news.client, "get", FakeGet({}))
    assert news.recent_titles(cfg=CFG, pacer=FakePacer()) == []


def test_code_param_and_all_fields_sent(monkeypatch):
    fake = FakeGet({"": [_row(_srno(1), code="000001")]})
    monkeypatch.setattr(news.client, "get", fake)
    news.recent_titles(" 000001 ", limit=1, cfg=CFG, pacer=FakePacer())
    q = fake.params[0]
    assert q["FID_INPUT_ISCD"] == "000001"
    assert set(q) == {"FID_NEWS_OFER_ENTP_CODE", "FID_COND_MRKT_CLS_CODE", "FID_INPUT_ISCD", "FID_TITL_CNTT",
                      "FID_INPUT_DATE_1", "FID_INPUT_HOUR_1", "FID_RANK_SORT_CLS_CODE", "FID_INPUT_SRNO"}


def test_default_module_pacer_is_used(monkeypatch):
    p = FakePacer()
    monkeypatch.setattr(news, "_pacer", p)
    monkeypatch.setattr(news.client, "get", FakeGet({"": [_row(_srno(1))]}))
    news.recent_titles(limit=1, cfg=CFG)
    assert p.calls == 1


def test_default_pacer_is_strategy_pacer(monkeypatch):
    from strategy.data import Pacer
    monkeypatch.setattr(news, "_pacer", None)
    p = news._default_pacer()
    assert isinstance(p, Pacer) and p.gap == news.GAP_S


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._b = status, body

    def json(self):
        return self._b


def test_kis_error_has_no_secrets(monkeypatch):
    # 실제 client.get 을 지나되 HTTP 와 토큰은 가짜
    monkeypatch.setattr(client, "get_token", lambda cfg: "TOKEN-fake-03")
    monkeypatch.setattr(client.requests, "get", lambda *a, **k: _Resp(
        500, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}))
    with pytest.raises(client.KisError) as e:
        news.recent_titles(cfg=CFG, pacer=FakePacer())
    msg = str(e.value)
    assert "EGW00123" in msg and news.TR_ID in msg
    for secret in ("AK-fake-01", "AS-fake-02", "TOKEN-fake-03"):
        assert secret not in msg


def test_key_missing_without_config(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "none" / "kis_devlp.yaml")
    with pytest.raises(news.KeyMissing) as e:
        news.recent_titles(pacer=FakePacer())
    assert str(tmp_path) not in str(e.value)


def test_key_missing_when_fields_empty(tmp_path, monkeypatch):
    f = tmp_path / "kis_devlp.yaml"
    f.write_text("my_app: ''\nmy_sec: ''\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    with pytest.raises(news.KeyMissing) as e:
        news.load_real_config()
    assert "my_app" in str(e.value) and str(tmp_path) not in str(e.value)


def test_paper_config_rejected():
    paper = SimpleNamespace(**{**vars(CFG), "mode": "paper", "is_paper": True})
    with pytest.raises(ValueError):
        news.recent_titles(cfg=paper, pacer=FakePacer())


@pytest.mark.parametrize("kw", [{"code": "12345"}, {"code": "00593O!"}, {"limit": 0},
                                {"limit": news.MAX_LIMIT + 1}])
def test_bad_arguments_rejected(kw):
    with pytest.raises(ValueError):
        news.recent_titles(cfg=CFG, pacer=FakePacer(), **kw)
