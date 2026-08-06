"""Web小説サイトへ負荷を掛けないための通信共通処理。"""

from __future__ import annotations

import os
import random
import threading
import time
from typing import Callable
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_HOST_LAST_REQUEST_AT: dict[str, float] = {}
_HOST_RATE_LOCK = threading.Lock()
_CONFIG_LOCK = threading.Lock()
_RUNTIME_CONFIG: dict[str, float | int] = {}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def configure_networking(
    minimum_interval: float,
    jitter: float,
    retries: int,
    backoff_factor: float,
) -> None:
    """画面で保存した通信設定を、このプロセスで作るSessionへ反映する。"""
    with _CONFIG_LOCK:
        _RUNTIME_CONFIG.update({
            "minimum_interval": max(1.5, float(minimum_interval)),
            "jitter": max(0.0, float(jitter)),
            "retries": min(max(1, int(retries)), 5),
            "backoff_factor": max(0.5, float(backoff_factor)),
        })


def get_networking_config() -> dict[str, float | int]:
    """現在有効な通信設定を返す。"""
    defaults = {
        "minimum_interval": max(
            1.5, _env_float("NOVEL_REQUEST_INTERVAL", 2.5)
        ),
        "jitter": max(0.0, _env_float("NOVEL_REQUEST_JITTER", 0.75)),
        "retries": min(max(1, _env_int("NOVEL_REQUEST_RETRIES", 3)), 5),
        "backoff_factor": max(
            0.5, _env_float("NOVEL_REQUEST_BACKOFF", 1.5)
        ),
    }
    with _CONFIG_LOCK:
        defaults.update(_RUNTIME_CONFIG)
    return defaults


class NovelNetworkError(RuntimeError):
    """作品取得を継続できない通信エラー。"""


class WorkNotFoundError(NovelNetworkError):
    """作品が削除済み、または公開されていない。"""


class AccessRestrictedError(NovelNetworkError):
    """サイト側からアクセスを制限された。"""


class PoliteSession(requests.Session):
    """同一ホストへのアクセス間隔と標準的な再試行を強制するSession。"""

    def __init__(
        self,
        minimum_interval: float | None = None,
        jitter: float | None = None,
        retries: int | None = None,
        backoff_factor: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__()
        config = get_networking_config()
        self.minimum_interval = max(
            0.0,
            minimum_interval
            if minimum_interval is not None
            else float(config["minimum_interval"]),
        )
        self.jitter = max(
            0.0,
            jitter
            if jitter is not None
            else float(config["jitter"]),
        )
        retry_count = min(
            max(1, retries if retries is not None else int(config["retries"])),
            5,
        )
        retry_backoff = max(
            0.5,
            backoff_factor
            if backoff_factor is not None
            else float(config["backoff_factor"]),
        )
        self._sleep = sleep
        retry_policy = Retry(
            total=retry_count,
            connect=retry_count,
            read=retry_count,
            status=retry_count,
            backoff_factor=retry_backoff,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD", "OPTIONS", "POST"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_policy, pool_connections=4, pool_maxsize=4)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def _wait_for_host(self, url: str) -> None:
        host = urlparse(url).netloc.lower()
        if not host:
            return

        with _HOST_RATE_LOCK:
            now = time.monotonic()
            last_request = _HOST_LAST_REQUEST_AT.get(host)
            target_interval = self.minimum_interval + random.uniform(0.0, self.jitter)
            if last_request is not None:
                remaining = target_interval - (now - last_request)
                if remaining > 0:
                    self._sleep(remaining)
            _HOST_LAST_REQUEST_AT[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs):
        self._wait_for_host(url)
        kwargs.setdefault("timeout", (10, 30))
        return super().request(method, url, **kwargs)


def validate_response(response: requests.Response, url: str) -> None:
    """HTTP失敗を0件として扱わず、意味のある例外へ変換する。"""
    if response.status_code == 404:
        raise WorkNotFoundError("作品が存在しないか、非公開になっています。")
    if response.status_code in (403, 429):
        retry_after = response.headers.get("Retry-After")
        suffix = f" Retry-After: {retry_after}秒" if retry_after else ""
        raise AccessRestrictedError(
            "サイトからアクセスを制限されました。しばらく時間を空けてください。"
            + suffix
        )
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise NovelNetworkError(f"ページを取得できませんでした: {url}") from exc
