"""공급자 — fake(키 없이 끝까지 시험) / anthropic(공식 파이썬 SDK, Messages API 만).

돌려주는 꼴(둘 다 같음): {"text", "model", "stop_reason", "stop_category", "usage": {input_tokens, output_tokens,
  cache_creation_input_tokens, cache_read_input_tokens}, "fake": bool}
오류: ProviderError(kind, message, retryable) — 문구에 키 · 주소를 넣지 않는다.

anthropic 공급자
  - 키: 설정 폴더(환경 변수 KIS_CONFIG_DIR, 없으면 홈 폴더 KIS/config)의 anthropic.yaml 칸 api_key 를 읽어 **명시로** 넘긴다.
    파일이 없거나 비었으면 KeyMissing — 부르지 않는다.
  - 환경 변수 ANTHROPIC_API_KEY 를 쓰지 않는다. SDK 는 키를 주지 않으면 환경 변수 · 로그인 프로필 · 워크로드 신원 연동 순으로
    스스로 자격 증명을 찾는다(claude-api 문서의 인증 순서). 그래서 키를 명시로 주고, 클라이언트를 만드는 순간에는 그 환경 변수들을
    잠깐 걷어 두었다가 그대로 되돌린다(값은 읽어 쓰지 않는다). 주소도 명시로(ANTHROPIC_BASE_URL 을 따르지 않게).
    윈도우 전역 환경 변수로 키를 두지 않는다 — 이 PC 의 다른 프로그램이 같은 변수를 보고 API 과금으로 바뀔 수 있다.
  - 호출 꼴(claude-api 문서에서 확인): client.messages.create(model, max_tokens, system, messages,
    output_config={"effort": …, "format": {"type": "json_schema", "schema": …}}). thinking 칸은 보내지 않는다
    (Opus 5.5 는 생각을 끌 수 없고 effort 로 줄인다). fallback: default 를 고른 정의만
    client.beta.messages.create(…, betas=["server-side-fallback-2026-07-01"], fallbacks="default").
  - SDK 의 자동 재시도는 끈다(max_retries=0) — 다시 묻는 횟수는 정의의 retries 만큼만(호출 수 · 비용을 정의가 정하게).
  - Batch · Files API 는 쓰지 않는다.
  - anthropic 패키지가 없어도 이 모듈의 import 는 죽지 않는다(가짜 공급자는 그대로 돈다). 부를 때 ProviderError(unavailable).
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path

import yaml

KEY_FILE_NAME = "anthropic.yaml"           # 칸 api_key 하나
BASE_URL = "https://api.anthropic.com"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# 클라이언트를 만드는 동안 걷어 두는 환경 변수(SDK 가 스스로 자격 증명 · 주소를 찾는 칸)
_CRED_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_BASE_URL",
             "ANTHROPIC_CONFIG_DIR", "ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID",
             "ANTHROPIC_IDENTITY_TOKEN_FILE", "ANTHROPIC_IDENTITY_TOKEN", "ANTHROPIC_SERVICE_ACCOUNT_ID",
             "ANTHROPIC_WORKSPACE_ID")
_env_lock = threading.Lock()


class KeyMissing(RuntimeError):
    """Anthropic API 키 파일이 없거나 비었다 — 부르지 않는다."""


class ProviderError(RuntimeError):
    def __init__(self, kind: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.kind, self.retryable = kind, retryable


def config_dir() -> Path:
    """설정 폴더 — KIS_CONFIG_DIR, 없으면 홈 폴더 KIS/config(kis.config · dart.opendart 와 같은 규칙, 부를 때마다 읽음)."""
    d = os.environ.get("KIS_CONFIG_DIR")
    return Path(d) if d else Path.home() / "KIS" / "config"


def load_key(cfg_dir: Path | None = None) -> str:
    """anthropic.yaml 의 api_key. 문구에는 파일 이름 · 칸 이름만(경로 · 내용을 넣지 않는다)."""
    p = Path(cfg_dir) if cfg_dir else config_dir()
    f = p / KEY_FILE_NAME
    if not f.exists():
        raise KeyMissing(f"{KEY_FILE_NAME} 없음 — Anthropic API 키를 넣기 전에는 부르지 않습니다")
    try:
        d = yaml.safe_load(f.read_text(encoding="utf-8-sig")) or {}
    except Exception:
        raise KeyMissing(f"{KEY_FILE_NAME} 을 읽지 못함(형식)") from None
    key = str((d.get("api_key") if isinstance(d, dict) else "") or "").strip()
    if not key or key.startswith("<") or " " in key:
        raise KeyMissing(f"{KEY_FILE_NAME} 의 api_key 가 비었음")
    return key


def _load_sdk():
    try:
        import anthropic                                    # 선택 패키지 — 없으면 None
        return anthropic
    except ImportError:
        return None


@contextlib.contextmanager
def _no_cred_env():
    """클라이언트를 만드는 동안만 SDK 가 스스로 읽는 자격 증명 환경 변수를 걷어 두고, 끝나면 그대로 되돌린다."""
    with _env_lock:
        saved = {k: os.environ.pop(k) for k in _CRED_ENV if k in os.environ}
        try:
            yield
        finally:
            os.environ.update(saved)


# ---------------- 가짜 ----------------
class FakeProvider:
    """정의의 fake.answers 를 차례로(끝나면 처음부터), 또는 fake.file(정의 폴더의 json 글 목록)을 돌려준다.
    한 답은 글 또는 {text, stop_reason}. 사용량은 실제와 같은 칸 이름(글자 수로 어림 — 비용 0)."""
    name = "fake"

    def __init__(self):
        self._i: dict[str, int] = {}

    def answers_of(self, d: dict) -> list:
        f = d.get("fake") or {}
        if f.get("file"):
            p = Path(d.get("_dir") or ".") / f["file"]
            try:
                seq = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(seq, list):
                    return seq
            except (OSError, ValueError):
                return []
        return list(f.get("answers") or [])

    def call(self, d: dict, system: str, user: str, schema: dict) -> dict:
        seq = self.answers_of(d)
        i = self._i.get(d["id"], 0)
        self._i[d["id"]] = i + 1
        a = seq[i % len(seq)] if seq else ""
        text, stop = (a.get("text", ""), a.get("stop_reason", "end_turn")) if isinstance(a, dict) else (str(a), "end_turn")
        return {"text": text, "model": "fake", "stop_reason": stop, "stop_category": None, "fake": True,
                "usage": {"input_tokens": len(system + user) // 2, "output_tokens": len(text) // 2,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


# ---------------- Anthropic ----------------
class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg_dir: Path | None = None, sdk=None, timeout_s: float = 60.0):
        key = load_key(cfg_dir)                             # 없으면 KeyMissing — SDK 를 건드리기 전에
        self.sdk = sdk if sdk is not None else _load_sdk()
        if self.sdk is None:
            raise ProviderError("unavailable", "anthropic 패키지가 설치되어 있지 않음(requirements.txt)")
        with _no_cred_env():
            self.client = self.sdk.Anthropic(api_key=key, base_url=BASE_URL, max_retries=0, timeout=float(timeout_s))
        if getattr(self.client, "auth_token", None):         # 키 말고 다른 자격 증명이 끼어들지 않게
            self.client.auth_token = None

    def _errors(self):
        s = self.sdk
        retry = tuple(c for c in (getattr(s, n, None) for n in ("RateLimitError", "InternalServerError",
                                                                "APIConnectionError")) if isinstance(c, type))
        status = getattr(s, "APIStatusError", None)
        return retry, status if isinstance(status, type) else None

    def call(self, d: dict, system: str, user: str, schema: dict) -> dict:
        m = d["model"]
        kw = dict(model=m["name"], max_tokens=int(m["max_tokens"]), system=system,
                  messages=[{"role": "user", "content": user}],
                  output_config={"effort": m["effort"], "format": {"type": "json_schema", "schema": schema}})
        retry, status = self._errors()
        try:
            if m.get("fallback") == "default":
                resp = self.client.with_options(timeout=float(m["timeout_s"])).beta.messages.create(
                    betas=[FALLBACK_BETA], fallbacks="default", **kw)
            else:
                resp = self.client.with_options(timeout=float(m["timeout_s"])).messages.create(**kw)
        except retry as e:
            raise ProviderError(type(e).__name__, f"{type(e).__name__}(다시 시도할 수 있음)", True) from None
        except Exception as e:
            if status is not None and isinstance(e, status):
                code = getattr(e, "status_code", None)
                raise ProviderError(type(e).__name__, f"{type(e).__name__}(HTTP {code})",
                                    bool(code and code >= 500)) from None
            raise ProviderError(type(e).__name__, f"{type(e).__name__}", False) from None
        texts = [getattr(b, "text", "") for b in (getattr(resp, "content", None) or []) if getattr(b, "type", "") == "text"]
        u = getattr(resp, "usage", None)
        usage = {k: int(getattr(u, k, 0) or 0) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                         "cache_read_input_tokens")}
        sd = getattr(resp, "stop_details", None)
        return {"text": "".join(texts), "model": str(getattr(resp, "model", m["name"])), "fake": False,
                "stop_reason": getattr(resp, "stop_reason", None),
                "stop_category": getattr(sd, "category", None) if sd is not None else None, "usage": usage}


class Providers:
    """판단마다 공급자를 고른다. anthropic 은 처음 부를 때 한 번 만든다(키 · 패키지가 없으면 그때의 예외를 기억)."""

    def __init__(self, cfg_dir: Path | None = None, sdk=None, force_fake: bool = False):
        self.cfg_dir, self.sdk, self.force_fake = cfg_dir, sdk, force_fake
        self.fake = FakeProvider()
        self._anthropic = None

    def name_for(self, d: dict) -> str:
        return "fake" if self.force_fake else d["model"]["provider"]

    def get(self, d: dict):
        if self.name_for(d) == "fake":
            return self.fake
        if self._anthropic is None:
            self._anthropic = AnthropicProvider(self.cfg_dir, self.sdk, d["model"]["timeout_s"])
        return self._anthropic
