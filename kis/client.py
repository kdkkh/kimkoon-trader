"""REST 호출 공통 — 헤더 구성, 응답 오류 확인. 비밀값은 오류 메시지에 넣지 않는다."""
from __future__ import annotations

import requests

from .auth import get_token
from .config import KisConfig


class KisError(RuntimeError):
    pass


def get(cfg: KisConfig, path: str, tr_id: str, params: dict) -> dict:
    return get_page(cfg, path, tr_id, params)[0]


def get_page(cfg: KisConfig, path: str, tr_id: str, params: dict, cont: bool = False) -> tuple[dict, bool]:
    """한 쪽 조회. 반환 (응답, 다음 쪽이 더 있는가). cont=True 면 이어 받기 요청(요청 헤더 tr_cont: N).
    다음 쪽 여부는 응답 헤더 tr_cont 가 M · F 일 때 — 이어 받는 쪽이 응답의 CTX_AREA_* 값을 다음 요청에 넣는다."""
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_token(cfg)}",
        "appkey": cfg.app_key,
        "appsecret": cfg.app_secret,
        "tr_id": tr_id,
        "custtype": "P",
        "User-Agent": cfg.user_agent,
    }
    if cont:
        headers["tr_cont"] = "N"
    r = requests.get(f"{cfg.rest_url}{path}", headers=headers, params=params, timeout=10)
    try:
        d = r.json()
    except ValueError:
        raise KisError(f"[{tr_id}] HTTP {r.status_code} (JSON 아님)") from None
    if r.status_code != 200 or d.get("rt_cd") != "0":
        raise KisError(f"[{tr_id}] HTTP {r.status_code} {d.get('msg_cd', '')} {d.get('msg1', '')}".strip())
    more = str((getattr(r, "headers", None) or {}).get("tr_cont", "") or "").strip().upper() in ("M", "F")
    return d, more


def post(cfg: KisConfig, path: str, tr_id: str, body: dict) -> dict:
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {get_token(cfg)}",
        "appkey": cfg.app_key,
        "appsecret": cfg.app_secret,
        "tr_id": tr_id,
        "custtype": "P",
        "User-Agent": cfg.user_agent,
    }
    r = requests.post(f"{cfg.rest_url}{path}", headers=headers, json=body, timeout=10)
    try:
        d = r.json()
    except ValueError:
        raise KisError(f"[{tr_id}] HTTP {r.status_code} (JSON 아님)") from None
    if r.status_code != 200 or d.get("rt_cd") != "0":
        raise KisError(f"[{tr_id}] HTTP {r.status_code} {d.get('msg_cd', '')} {d.get('msg1', '')}".strip())
    return d
