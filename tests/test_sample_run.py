# 모의투자로만 검증한 것이며, 실전 주문 기능은 들어 있지 않습니다. 고쳐 쓰는 일과 그 결과는 쓰는 사람의 책임입니다.
"""견본 한 바퀴(scripts/sample_run.py) 시험 — 봉 백테스트 → 검증기 → 봉 재생 → 간단 시뮬레이션 + 문 + 가짜 판단 →
판단 다시 틀기 두 번 → 봇 가짜 HTTP(켜기 · 설정 · 사고팔기 · 끄기). 임시 폴더에 복사해 돌리므로 이 폴더에는 쓰지 않는다."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_sample_run_all_steps(tmp_path):
    work = tmp_path / "w"
    r = subprocess.run([sys.executable, "-X", "utf8", "-B", str(ROOT / "scripts" / "sample_run.py"), "--work", str(work)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=600)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    for n in range(1, 7):
        assert f"  {n} 통과" in out, out[-3000:]
    assert not work.exists()                                                  # --keep 이 없으면 작업 폴더를 지운다
