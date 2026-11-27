"""'전략 신호가 났을 때만 묻기'(검토) 시험 — 문 폴더에 파일을 직접 써서(판단 프로세스 없이).

규칙: review.json 이 있으면 전략 진입을 바로 내지 않고 entry_review 를 남긴다. 답(inbox 의 review_of 신호)이 다음 봉 처리
첫머리까지 오면 그때 진입, 안 오면 마감(on_timeout: skip 기본 = 진입 안 함 · pass = 전략대로 진입). 종목마다 검토 하나.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strategy import live  # noqa: E402
from tests.step_fakes import events, kinds, make_runner  # noqa: E402
from tests.test_ext import put, sig  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    r, st = make_runner(tmp_path, monkeypatch, ext=True)
    return r, st, tmp_path / "state" / "ext"


def review_on(root: Path, ms: int = 60_000, **kw) -> None:
    (root / "review.json").write_text(json.dumps({"expires_ms": live.now_ms() + ms, **kw}), encoding="utf-8")


def reviews(r) -> list[dict]:
    return events(r, "entry_review")


def next_bar(r, st, nxt=None) -> None:
    st.next = nxt
    r.last_bar.clear()
    r.step()


def test_no_review_file_means_review_off(env):
    r, st, root = env
    st.next = "long_entry"
    r.step()
    assert not reviews(r) and r.oms.held("005930") > 0


def test_review_pass_enters_next_bar(env):
    r, st, root = env
    review_on(root)
    st.next = "long_entry"
    r.step()
    rv = reviews(r)
    assert len(rv) == 2 and r.oms.held("005930") == 0 and r.entries_today() == 0     # 진입 대신 검토 요청만
    req = json.loads((root / "review_requests" / f"{rv[0]['id']}.json").read_text(encoding="utf-8"))
    assert req["code"] == "005930" and req["deadline_ms"] > req["t_signal_ms"] and req["bar_time"].startswith("2026-11-11T10:04")
    put(root, "p", sig(1, review_of=rv[0]["id"]))                                    # 통과 답(exit_by 는 strategy 로 채워짐)
    next_bar(r, st)
    assert r.oms.held("005930") > 0 and not r.ext.owns("005930")
    assert events(r, "order_intent")[0]["ext_id"] == "s1"
    assert events(r, "review_expired")[0]["code"] == "000660"                        # 답이 없던 종목은 한 봉 뒤 마감
    assert r.oms.held("000660") == 0                                                  # on_timeout 기본 = 진입 안 함
    assert not list((root / "review_requests").glob("*.json"))                       # 결론 난 요청은 지움
    next_bar(r, st, "long_exit")
    assert r.oms.held("005930") == 0                                                  # 전략 청산이 판다


def test_late_answer_is_rejected(env):
    r, st, root = env
    review_on(root)
    st.next = "long_entry"
    r.step()
    rid = reviews(r)[0]["id"]
    next_bar(r, st)                                                                   # 답 없이 한 봉 → 마감
    put(root, "p", sig(1, review_of=rid))
    next_bar(r, st)
    assert r.oms.held("005930") == 0
    assert any("맞는 검토 대기 없음" in e["reason"] for e in events(r, "ext_rejected"))


def test_timeout_pass_enters_one_bar_late(env):
    r, st, root = env
    review_on(root, on_timeout="pass")
    st.next = "long_entry"
    r.step()
    assert r.oms.held("005930") == 0
    next_bar(r, st)
    ex = events(r, "review_expired")
    assert len(ex) == 2 and all(e["on_timeout"] == "pass" for e in ex)
    assert r.oms.held("005930") > 0 and r.oms.held("000660") > 0
    assert {e["source"] for e in events(r, "order_intent")} == {"review:on_timeout_pass"}


def test_judge_dead_skips_and_says_once(env):
    r, st, root = env
    review_on(root, ms=-1)                                                            # 판단이 늘려 주지 않아 만료된 표시
    for _ in range(3):
        next_bar(r, st, "long_entry")
    assert r.oms.held("005930") == 0 and not reviews(r)
    assert kinds(r).count("review_unavailable") == 1
    assert sum("검토 판단이 응답하지 않음" in e["reason"] for e in events(r, "order_skip")) == 6   # 종목 둘 × 봉 셋


def test_judge_dead_pass_acts_like_before(env):
    r, st, root = env
    review_on(root, ms=-1, on_timeout="pass")
    st.next = "long_entry"
    r.step()
    assert r.oms.held("005930") > 0 and kinds(r).count("review_unavailable") == 1 and not reviews(r)


def test_deny_answer_no_entry(env):
    r, st, root = env
    review_on(root)
    st.next = "long_entry"
    r.step()
    rid = reviews(r)[0]["id"]
    put(root, "d", {"id": "d1", "source": "ai:x", "code": "005930", "action": "deny", "review_of": rid,
                    "expires_ms": live.now_ms() + 60_000})
    next_bar(r, st)
    assert events(r, "review_denied")[0]["id"] == rid and r.oms.held("005930") == 0


def test_one_review_per_code_exit_cancels(env):
    r, st, root = env
    review_on(root)
    st.next = "long_entry"
    r.step()
    rid = reviews(r)[0]["id"]
    r.ext.pending["005930"]["step"] = r.ext.step_no + 5                                # 마감 전(같은 봉 안에서 다시 신호)
    r.ext.pending["000660"]["step"] = r.ext.step_no + 5
    r.last_bar.clear()
    r.step()
    assert len(reviews(r)) == 2                                                       # 대기 중이라 새 검토 없음
    assert any("검토 대기 중" in e["reason"] for e in events(r, "order_skip"))
    next_bar(r, st, "long_exit")                                                      # 대기 중 전략 청산 → 검토 거둠
    assert kinds(r).count("review_cancelled") == 2
    put(root, "p", sig(1, review_of=rid))
    next_bar(r, st)
    assert r.oms.held("005930") == 0                                                  # 늦은 통과는 진입하지 않음


def test_review_listed_codes_after_gate(env):
    r, st, root = env
    review_on(root, codes=["000660"])
    (root / "gates" / "g.json").write_text(json.dumps({"block_entries": True, "codes": ["000660"],
                                                       "expires_ms": live.now_ms() + 60_000}), encoding="utf-8")
    st.next = "long_entry"
    r.step()
    assert r.oms.held("005930") > 0                                                   # 검토 대상 아님 → 바로 진입
    assert not reviews(r) and events(r, "entry_filtered")[0]["code"] == "000660"      # 거르기가 먼저(막힐 진입은 안 물음)


def test_review_answer_on_exit_rejected(env):
    r, st, root = env
    put(root, "p", sig(1, action="exit", review_of="x"))
    put(root, "q", {"id": "q", "source": "ai:x", "code": "005930", "action": "deny", "expires_ms": live.now_ms() + 9999})
    r.step()
    reasons = [e["reason"] for e in events(r, "ext_rejected")]
    assert any("review_of" in x for x in reasons) and any("deny" in x for x in reasons)
