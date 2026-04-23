# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------
#
#   Copyright 2026 Valory AG
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
#
# ------------------------------------------------------------------------------

"""Upstream release lookup for the /api/v2/version endpoint.

Provides a process-local cache for the latest upstream release on
valory-xyz/olas-operate-middleware. The fetch is triggered lazily on the
first request and refreshed when the TTL expires; failures fall back to
the last known good value.
"""

import logging
import threading
import time as _time
import typing as t
from datetime import datetime, timezone

import requests as http_requests
from packaging.version import InvalidVersion, Version

from operate import __version__

UPSTREAM_RELEASES_URL = "https://api.github.com/repos/valory-xyz/olas-operate-middleware/releases/latest"  # noqa: E501 pylint: disable=line-too-long
CACHE_TTL_SECONDS = 3600  # 1 hour
REQUEST_TIMEOUT_SECONDS = 10

_logger = logging.getLogger("operate")


def _iso_utc_now() -> str:
    """Return the current UTC time in ISO-8601 with a trailing Z."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_version(raw: str) -> t.Optional[Version]:
    """Parse a version string (with optional leading 'v'), or return None."""
    if not raw:
        return None
    candidate = raw[1:] if raw.startswith(("v", "V")) else raw
    try:
        return Version(candidate)
    except InvalidVersion:
        return None


def _trim_leading_v(raw: str) -> str:
    """Strip a leading 'v'/'V' from a version tag for display."""
    if raw and raw.startswith(("v", "V")):
        return raw[1:]
    return raw


def _compare(installed: str, latest: str) -> t.Optional[bool]:
    """Return True if installed < latest, False if >=, None if unparseable."""
    installed_v = _parse_version(installed)
    latest_v = _parse_version(latest)
    if installed_v is None or latest_v is None:
        return None
    return installed_v < latest_v


class UpstreamVersionCache:
    """Thread-safe in-process cache for the latest upstream release."""

    def __init__(
        self,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        url: str = UPSTREAM_RELEASES_URL,
        installed_version: str = __version__,
    ) -> None:
        """Initialise with the cache TTL, upstream URL, and installed version."""
        self._ttl = ttl_seconds
        self._url = url
        self._installed = installed_version
        self._lock = threading.Lock()
        self._latest: t.Optional[t.Dict[str, str]] = None
        self._checked_at_iso: t.Optional[str] = None
        self._checked_at_monotonic: t.Optional[float] = None

    def _is_fresh(self, now_monotonic: float) -> bool:
        if self._checked_at_monotonic is None:
            return False
        return (now_monotonic - self._checked_at_monotonic) < self._ttl

    def _fetch(self) -> t.Optional[t.Dict[str, str]]:
        """Fetch the latest release from GitHub. Returns None on failure."""
        try:
            resp = http_requests.get(
                self._url,
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers={"Accept": "application/vnd.github+json"},
            )
            if resp.status_code != 200:
                _logger.warning(
                    "Upstream version lookup failed: HTTP %s", resp.status_code
                )
                return None
            payload = resp.json()
            tag = payload.get("tag_name") or payload.get("name") or ""
            if not tag:
                return None
            return {
                "version": _trim_leading_v(str(tag)),
                "published_at": str(payload.get("published_at") or ""),
                "html_url": str(payload.get("html_url") or ""),
            }
        except (
            http_requests.RequestException,
            ValueError,
        ) as exc:  # pragma: no cover - defensive
            _logger.warning("Upstream version lookup error: %s", exc)
            return None

    def get(self, *, force_refresh: bool = False) -> t.Dict[str, t.Any]:
        """Return the current version snapshot, refreshing the cache if stale."""
        with self._lock:
            now = _time.monotonic()
            if force_refresh or not self._is_fresh(now):
                fresh = self._fetch()
                if fresh is not None:
                    self._latest = fresh
                    self._checked_at_iso = _iso_utc_now()
                    self._checked_at_monotonic = now
                # on failure we keep the last known good value (if any)

            latest = self._latest
            checked_at = self._checked_at_iso

        is_outdated: t.Optional[bool]
        if latest is None:
            is_outdated = None
        else:
            is_outdated = _compare(self._installed, latest["version"])

        return {
            "installed": self._installed,
            "latest": latest,
            "is_outdated": is_outdated,
            "checked_at": checked_at,
        }
