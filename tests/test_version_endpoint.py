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

"""Tests for the /api/v2/version endpoint and UpstreamVersionCache."""

import typing as t
from unittest import mock

import pytest

from operate.version_check import UpstreamVersionCache


class _FakeResponse:
    def __init__(self, status_code: int, payload: t.Dict[str, t.Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> t.Dict[str, t.Any]:
        return self._payload


def _release(tag: str, published: str = "2026-04-20T12:00:00Z") -> t.Dict[str, t.Any]:
    return {
        "tag_name": tag,
        "published_at": published,
        "html_url": (
            "https://github.com/valory-xyz/olas-operate-middleware/releases/tag/" + tag
        ),
    }


def test_is_outdated_true_when_installed_older() -> None:
    """Older installed version flagged as outdated."""
    cache = UpstreamVersionCache(installed_version="0.15.7")
    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(200, _release("v0.16.3")),
    ):
        snap = cache.get()
    assert snap["installed"] == "0.15.7"
    assert snap["latest"] == {
        "version": "0.16.3",
        "published_at": "2026-04-20T12:00:00Z",
        "html_url": (
            "https://github.com/valory-xyz/olas-operate-middleware/"
            "releases/tag/v0.16.3"
        ),
    }
    assert snap["is_outdated"] is True
    assert snap["checked_at"] is not None


def test_is_outdated_false_when_installed_equal() -> None:
    """Equal versions are not outdated."""
    cache = UpstreamVersionCache(installed_version="0.16.3")
    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(200, _release("0.16.3")),
    ):
        snap = cache.get()
    assert snap["is_outdated"] is False
    assert snap["latest"]["version"] == "0.16.3"


def test_is_outdated_true_for_prerelease_vs_newer_stable() -> None:
    """A pre-release installed is outdated vs a newer stable release."""
    cache = UpstreamVersionCache(installed_version="0.16.0rc1")
    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(200, _release("v0.16.0")),
    ):
        snap = cache.get()
    assert snap["is_outdated"] is True


def test_lookup_failure_returns_nulls_on_contract() -> None:
    """On GitHub failure with no prior cache, latest/is_outdated/checked_at are null."""
    cache = UpstreamVersionCache(installed_version="0.15.7")
    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(500, {}),
    ):
        snap = cache.get()
    assert snap["installed"] == "0.15.7"
    assert snap["latest"] is None
    assert snap["is_outdated"] is None
    assert snap["checked_at"] is None


def test_lookup_failure_falls_back_to_last_cached() -> None:
    """After a successful fetch, subsequent failures keep the cached value."""
    cache = UpstreamVersionCache(installed_version="0.15.7", ttl_seconds=0)
    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(200, _release("v0.16.3")),
    ):
        first = cache.get()
    assert first["latest"] is not None

    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(500, {}),
    ):
        second = cache.get(force_refresh=True)

    assert second["latest"] == first["latest"]
    assert second["is_outdated"] is True
    assert second["checked_at"] == first["checked_at"]


def test_ttl_prevents_repeat_http_calls() -> None:
    """Within TTL, the fetcher is not called a second time."""
    cache = UpstreamVersionCache(installed_version="0.15.7", ttl_seconds=3600)
    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(200, _release("v0.16.3")),
    ) as mocked:
        cache.get()
        cache.get()
        cache.get()
    assert mocked.call_count == 1


def test_response_contract_shape_via_create_app(tmp_path: t.Any) -> None:
    """The /api/v2/version endpoint returns the documented JSON shape."""
    from fastapi.testclient import TestClient

    from operate.cli import create_app
    from operate.constants import OPERATE

    app = create_app(home=tmp_path / OPERATE)
    client = TestClient(app)

    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(200, _release("v0.16.3")),
    ):
        resp = client.get("/api/v2/version")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"installed", "latest", "is_outdated", "checked_at"}
    assert isinstance(body["installed"], str)
    assert body["latest"] is not None
    assert set(body["latest"].keys()) == {"version", "published_at", "html_url"}
    assert body["latest"]["version"] == "0.16.3"
    assert body["is_outdated"] in (True, False)
    assert body["checked_at"] is not None


@pytest.mark.parametrize(
    "bad_tag",
    ["", "not-a-version"],
)
def test_unparseable_versions_produce_null_is_outdated(bad_tag: str) -> None:
    """Unparseable tag yields is_outdated=None but keeps latest block."""
    cache = UpstreamVersionCache(installed_version="0.15.7")
    with mock.patch(
        "operate.version_check.http_requests.get",
        return_value=_FakeResponse(200, _release(bad_tag)),
    ):
        snap = cache.get()
    if bad_tag == "":
        # Empty tag_name => no latest record at all
        assert snap["latest"] is None
        assert snap["is_outdated"] is None
    else:
        assert snap["latest"] is not None
        assert snap["is_outdated"] is None
