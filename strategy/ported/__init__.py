"""ported/ — 이식한 전략 모음. 파일 하나 = 전략 하나, 파일 안에 STRATEGY = <클래스>.

load_strategy("supertrend")                       # 이름(클래스의 name 또는 파일 이름)
load_strategy("strategy/ported/my_rsi.py")        # 파일 경로(검증기·다른 AI 결과 시험용)
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

from strategy.base import Strategy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _class_of(mod) -> type[Strategy]:
    cls = getattr(mod, "STRATEGY", None)
    if cls is None:     # STRATEGY 가 없으면 Strategy 자식 클래스 하나를 찾는다
        found = [v for v in vars(mod).values()
                 if isinstance(v, type) and issubclass(v, Strategy) and v is not Strategy]
        if len(found) != 1:
            raise ImportError(f"{mod.__name__}: STRATEGY = <클래스> 가 없습니다")
        cls = found[0]
    if not (isinstance(cls, type) and issubclass(cls, Strategy)):
        raise TypeError(f"{mod.__name__}: STRATEGY 는 Strategy 를 상속한 클래스여야 합니다")
    return cls


def import_file(path: str | Path):
    """파일 경로로 모듈을 불러온다(strategy 패키지를 찾을 수 있게 이 프로그램 폴더를 경로에 넣음)."""
    p = Path(path).resolve()
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location(f"ported_file_{p.stem}", p)
    if spec is None or spec.loader is None:
        raise ImportError(f"불러올 수 없습니다: {p}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def list_strategies() -> dict[str, Path]:
    return {p.stem: p for p in sorted(HERE.glob("*.py")) if not p.name.startswith("_")}


def strategy_class(name_or_path: str) -> type[Strategy]:
    if name_or_path.endswith(".py") or "/" in name_or_path or "\\" in name_or_path:
        return _class_of(import_file(name_or_path))
    files = list_strategies()
    if name_or_path in files:
        return _class_of(importlib.import_module(f"strategy.ported.{name_or_path}"))
    for stem in files:                       # 파일 이름이 아니면 클래스의 name 으로 찾는다
        cls = _class_of(importlib.import_module(f"strategy.ported.{stem}"))
        if cls.name == name_or_path:
            return cls
    raise KeyError(f"전략을 찾을 수 없습니다: {name_or_path} (있는 것: {', '.join(files) or '없음'})")


def load_strategy(name_or_path: str, **params) -> Strategy:
    return strategy_class(name_or_path)(**params)
