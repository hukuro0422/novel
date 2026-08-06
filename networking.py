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
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__()
        self.minimum_interval = max(
            0.0,
            minimum_interval
            if minimum_interval is not None
            else float(os.getenv("NOVEL_REQUEST_INTERVAL", "2.5")),
        )
        self.jitter = max(
            0.0,
            jitter
            if jitter is not None
            else float(os.getenv("NOVEL_REQUEST_JITTER", "0.75")),
        )
        self._sleep = sleep
        retry_policy = Retry(
            total=3,
            connect=3,
            read=2,
            status=3,
            backoff_factor=1.5,
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
