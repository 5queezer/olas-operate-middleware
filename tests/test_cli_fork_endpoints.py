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

"""Tests for fork-specific endpoints in operate/cli.py.

Covers:
- The ``DISABLE_PARENT_WATCHDOG=1`` branch in lifespan startup.
- ``GET /api/v2/service/{id}/chatui_params``.
- ``PATCH /api/v2/service/{id}/chatui_params``.
- ``POST /api/v2/service/{id}/chat`` (incl. retry background thread).
- ``GET /api/v2/service/{id}/chat/status``.
"""

import json
import logging
from contextlib import ExitStack
from http import HTTPStatus
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.testclient import TestClient

from operate.cli import create_app

# ─── Helpers (mirroring tests/test_cli_unit.py) ───────────────────────────────


def _make_mock_operate() -> MagicMock:
    """Return a minimal mock OperateApp usable with create_app()."""
    m = MagicMock()
    m._path = MagicMock()
    kill_mock = MagicMock()
    kill_mock.write_text = MagicMock()
    m._path.__truediv__ = MagicMock(return_value=kill_mock)
    m.password = None
    m.user_account = None
    m.json = {
        "name": "Operate HTTP server",
        "version": "0.0.0",
        "home": "/tmp",
    }  # nosec B108
    m.settings = MagicMock()
    m.settings.json = {"version": "0.0.0"}
    m.wallet_manager = MagicMock()
    m.wallet_manager.__iter__ = MagicMock(side_effect=lambda: iter([]))
    m.bridge_manager = MagicMock()
    m.wallet_recovery_manager = MagicMock()
    m.funding_manager = MagicMock()
    m.funding_manager.funding_job = AsyncMock()
    svc_mgr = MagicMock()
    svc_mgr.validate_services.return_value = True
    svc_mgr.json = []
    svc_mgr.get_all_service_ids.return_value = []
    svc_mgr.get_all_services.return_value = ([], [])
    m.service_manager.return_value = svc_mgr
    return m


def _open_app(
    mock_operate: MagicMock,
    *,
    env: Optional[dict] = None,
) -> tuple:
    """Open a stack with create_app() patched for testing."""
    import os as _os

    stack = ExitStack()
    stack.enter_context(patch("operate.cli.OperateApp", return_value=mock_operate))
    mock_hc_cls = stack.enter_context(patch("operate.cli.HealthChecker"))
    mock_hc_cls.NUMBER_OF_FAILS_DEFAULT = 60
    stack.enter_context(patch("operate.cli.signal"))
    stack.enter_context(patch("operate.cli.atexit"))
    mock_wd = MagicMock()
    mock_wd.start = MagicMock()
    mock_wd.stop = AsyncMock()
    stack.enter_context(patch("operate.cli.ParentWatchdog", return_value=mock_wd))
    extra_env: dict = {"HEALTH_CHECKER_OFF": "0"}
    if env:
        extra_env.update(env)
    stack.enter_context(patch.dict(_os.environ, extra_env))

    app = create_app()
    return stack, app


# ─── 1. DISABLE_PARENT_WATCHDOG branch (cli.py 512-513) ───────────────────────


def test_lifespan_disable_parent_watchdog_logs_and_skips_watchdog(
    caplog: Any,
) -> None:
    """Cover cli.py 512-513: watchdog=None branch when env var is set."""
    m = _make_mock_operate()
    with caplog.at_level(logging.INFO, logger="operate"):
        stack, app = _open_app(m, env={"DISABLE_PARENT_WATCHDOG": "1"})
        with stack:
            app._server = MagicMock()
            with TestClient(app, raise_server_exceptions=False) as client:
                # Any valid request triggers lifespan startup
                resp = client.get("/api/account")
            assert resp.status_code == HTTPStatus.OK
    assert "ParentWatchdog disabled" in caplog.text


# ─── 2. GET /api/v2/service/{id}/chatui_params ────────────────────────────────


class TestGetChatuiParams:
    """Cover cli.py 1295-1316."""

    def test_service_not_found_returns_404(self) -> None:
        """GET chatui_params returns 404 when the service does not exist."""
        m = _make_mock_operate()
        m.service_manager.return_value.exists.return_value = False
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/v2/service/nope/chatui_params")
            assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_store_missing_returns_empty_dict(self, tmp_path: Path) -> None:
        """GET returns an empty dict when the backing store file is absent."""
        m = _make_mock_operate()
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        svc.env_variables = {"STORE_PATH": {"value": str(tmp_path)}}
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/v2/service/svc1/chatui_params")
            assert resp.status_code == HTTPStatus.OK
            assert resp.json() == {}

    def test_store_present_returns_contents(self, tmp_path: Path) -> None:
        """GET returns the JSON contents of an existing store file."""
        m = _make_mock_operate()
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        svc.env_variables = {"STORE_PATH": {"value": str(tmp_path)}}
        m.service_manager.return_value.load.return_value = svc
        store = tmp_path / "chatui_param_store.json"
        store.write_text(json.dumps({"foo": "bar"}))
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/v2/service/svc1/chatui_params")
            assert resp.status_code == HTTPStatus.OK
            assert resp.json() == {"foo": "bar"}

    def test_store_unreadable_returns_500(self, tmp_path: Path) -> None:
        """Invalid-JSON store file yields a 500 with an explanatory message."""
        m = _make_mock_operate()
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        svc.env_variables = {"STORE_PATH": {"value": str(tmp_path)}}
        m.service_manager.return_value.load.return_value = svc
        store = tmp_path / "chatui_param_store.json"
        store.write_text("{not-json")
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/v2/service/svc1/chatui_params")
            assert resp.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
            assert "Cannot read" in resp.json()["error"]

    def test_store_path_default_when_env_missing(self, tmp_path: Path) -> None:
        """Cover the default-value fallback when STORE_PATH is absent."""
        m = _make_mock_operate()
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        # env_variables lacks STORE_PATH → default "." is used. Chdir so
        # relative-path reads resolve to a controlled empty location.
        svc.env_variables = {}
        m.service_manager.return_value.load.return_value = svc
        import os as _os

        cwd = _os.getcwd()
        _os.chdir(tmp_path)
        try:
            stack, app = _open_app(m)
            with stack:
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.get("/api/v2/service/svc1/chatui_params")
                assert resp.status_code == HTTPStatus.OK
                assert resp.json() == {}
        finally:
            _os.chdir(cwd)


# ─── 3. PATCH /api/v2/service/{id}/chatui_params ──────────────────────────────


class TestPatchChatuiParams:
    """Cover cli.py 1324-1364."""

    def test_not_logged_in_returns_401(self) -> None:
        """PATCH requires the user to be logged in."""
        m = _make_mock_operate()
        m.password = None
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.patch("/api/v2/service/svc1/chatui_params", json={"a": 1})
            assert resp.status_code == HTTPStatus.UNAUTHORIZED

    def test_service_not_found_returns_404(self) -> None:
        """PATCH returns 404 when the service does not exist."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = False
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.patch("/api/v2/service/nope/chatui_params", json={"a": 1})
            assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_creates_new_store_when_missing(self, tmp_path: Path) -> None:
        """PATCH creates and persists the store file when it does not exist."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        svc.env_variables = {"STORE_PATH": {"value": str(tmp_path)}}
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.patch("/api/v2/service/svc1/chatui_params", json={"a": 1})
            assert resp.status_code == HTTPStatus.OK
            assert resp.json() == {"a": 1}
            # persisted to disk
            stored = json.loads((tmp_path / "chatui_param_store.json").read_text())
            assert stored == {"a": 1}

    def test_merges_existing_params(self, tmp_path: Path) -> None:
        """PATCH merges updates on top of existing store contents."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        svc.env_variables = {"STORE_PATH": {"value": str(tmp_path)}}
        m.service_manager.return_value.load.return_value = svc
        (tmp_path / "chatui_param_store.json").write_text(
            json.dumps({"existing": True, "a": 0})
        )
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.patch(
                    "/api/v2/service/svc1/chatui_params", json={"a": 1, "b": 2}
                )
            assert resp.status_code == HTTPStatus.OK
            assert resp.json() == {"existing": True, "a": 1, "b": 2}

    def test_corrupt_existing_store_is_ignored(self, tmp_path: Path) -> None:
        """When the existing store is unreadable JSON, updates overwrite it."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        svc.env_variables = {"STORE_PATH": {"value": str(tmp_path)}}
        m.service_manager.return_value.load.return_value = svc
        (tmp_path / "chatui_param_store.json").write_text("{not-json")
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.patch("/api/v2/service/svc1/chatui_params", json={"a": 1})
            assert resp.status_code == HTTPStatus.OK
            assert resp.json() == {"a": 1}

    def test_write_failure_returns_500(self, tmp_path: Path) -> None:
        """An OSError during persistence surfaces as HTTP 500."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        svc.env_variables = {"STORE_PATH": {"value": str(tmp_path)}}
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        with stack:
            with patch("operate.cli.open", side_effect=OSError("disk full")):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.patch("/api/v2/service/svc1/chatui_params", json={"a": 1})
            assert resp.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
            assert "Cannot write" in resp.json()["error"]


# ─── 4. POST /api/v2/service/{id}/chat + GET chat/status ──────────────────────


def _prepare_service_with_agent(tmp_path: Path, *, port: int = 8716) -> MagicMock:
    """Create a mock service whose deployment/agent.json is readable."""
    service_path = tmp_path / "service"
    deployment_dir = service_path / "deployment"
    deployment_dir.mkdir(parents=True)
    (deployment_dir / "agent.json").write_text(
        json.dumps({"CONNECTION_HTTP_SERVER_CONFIG_PORT": port})
    )
    svc = MagicMock()
    svc.path = service_path
    return svc


class TestChatWithAgent:
    """Cover cli.py 1371-1376, 1382-1414, 1425-1505, 1513-1517."""

    def test_not_logged_in_returns_401(self) -> None:
        """POST chat requires the user to be logged in."""
        m = _make_mock_operate()
        m.password = None
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.UNAUTHORIZED

    def test_service_not_found_returns_404(self) -> None:
        """POST chat returns 404 when the service does not exist."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = False
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/api/v2/service/nope/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.NOT_FOUND

    def test_agent_not_deployed_returns_400(self, tmp_path: Path) -> None:
        """POST chat returns 400 when the deployment/agent.json is missing."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        svc.path = tmp_path / "svc"
        svc.path.mkdir()
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.BAD_REQUEST
            assert "not deployed" in resp.json()["error"].lower()

    def test_missing_prompt_returns_400(self, tmp_path: Path) -> None:
        """POST chat rejects an empty prompt."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/api/v2/service/svc1/chat", json={"prompt": ""})
            assert resp.status_code == HTTPStatus.BAD_REQUEST
            assert "Prompt is required" in resp.json()["error"]

    def test_corrupt_agent_json_uses_default_port(self, tmp_path: Path) -> None:
        """Cover the JSONDecodeError fallback-port branch."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = MagicMock()
        service_path = tmp_path / "svc"
        (service_path / "deployment").mkdir(parents=True)
        (service_path / "deployment" / "agent.json").write_text("not-json")
        svc.path = service_path
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"ok": True}
        with stack:
            with patch("operate.cli.http_requests.post", return_value=fake_resp):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.OK
            assert resp.json() == {"ok": True}

    def test_successful_chat_returns_agent_response(self, tmp_path: Path) -> None:
        """A reachable agent's JSON reply is passed through unchanged."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"answer": "42"}
        with stack:
            with patch(
                "operate.cli.http_requests.post", return_value=fake_resp
            ) as mocked:
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.OK
            assert resp.json() == {"answer": "42"}
            assert mocked.call_count == 1

    def test_agent_not_started_queues_background_retry(self, tmp_path: Path) -> None:
        """Agent responds with 'not started' → prompt queued, thread spawned."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"error": "agent not started yet"}
        fake_thread = MagicMock()
        with stack:
            with (
                patch("operate.cli.http_requests.post", return_value=fake_resp),
                patch(
                    "operate.cli.threading.Thread", return_value=fake_thread
                ) as mock_thread_cls,
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.OK
            body = resp.json()
            assert body["status"] == "queued"
            mock_thread_cls.assert_called_once()
            fake_thread.start.assert_called_once()

    def test_connection_error_queues_retry(self, tmp_path: Path) -> None:
        """Unreachable agent → prompt queued, retry thread started."""
        import requests

        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        fake_thread = MagicMock()
        with stack:
            with (
                patch(
                    "operate.cli.http_requests.post",
                    side_effect=requests.exceptions.ConnectionError(),
                ),
                patch(
                    "operate.cli.threading.Thread", return_value=fake_thread
                ) as mock_thread_cls,
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.OK
            assert resp.json()["status"] == "queued"
            mock_thread_cls.assert_called_once()
            fake_thread.start.assert_called_once()

    def test_timeout_returns_504(self, tmp_path: Path) -> None:
        """A slow agent causes a gateway-timeout response."""
        import requests

        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        with stack:
            with patch(
                "operate.cli.http_requests.post",
                side_effect=requests.exceptions.Timeout(),
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.GATEWAY_TIMEOUT
            assert "too long" in resp.json()["error"].lower()

    def test_generic_exception_returns_500(self, tmp_path: Path) -> None:
        """Unexpected errors surface as HTTP 500 with the exception text."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        with stack:
            with patch(
                "operate.cli.http_requests.post",
                side_effect=RuntimeError("boom"),
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
            assert resp.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
            assert "boom" in resp.json()["error"]


# ─── 5. GET /api/v2/service/{id}/chat/status ──────────────────────────────────


class TestChatStatus:
    """Cover cli.py 1513-1517."""

    def test_no_pending_chat_returns_none(self) -> None:
        """GET chat/status returns {'status': 'none'} when no chat is pending."""
        m = _make_mock_operate()
        stack, app = _open_app(m)
        with stack:
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/api/v2/service/svc1/chat/status")
            assert resp.status_code == HTTPStatus.OK
            assert resp.json() == {"status": "none"}

    def test_returns_pending_entry_after_queueing(self, tmp_path: Path) -> None:
        """Queue a prompt then look up its status in the same app instance."""
        import requests

        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)
        fake_thread = MagicMock()
        with stack:
            with (
                patch(
                    "operate.cli.http_requests.post",
                    side_effect=requests.exceptions.ConnectionError(),
                ),
                patch("operate.cli.threading.Thread", return_value=fake_thread),
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
                    resp = c.get("/api/v2/service/svc1/chat/status")
            assert resp.status_code == HTTPStatus.OK
            assert resp.json()["status"] == "queued"
            assert resp.json()["prompt"] == "hi"


# ─── 6. _retry_pending_chat background logic (1382-1414) ──────────────────────


class TestRetryPendingChat:
    """Directly exercise the retry thread body.

    The ``/chat`` endpoint wires this up via ``threading.Thread``. To keep the
    test fast and deterministic we reach into ``create_app`` and call the
    nested helper synchronously, since the function is not exposed as a
    module-level symbol. We do so by triggering the queue path and then
    replacing the spawned thread's target.
    """

    @staticmethod
    def _invoke_via_queue(
        tmp_path: Path,
        agent_response: Any,
        side_effects: Optional[list] = None,
    ) -> MagicMock:
        """Queue a prompt and run the retry target synchronously.

        Returns the patched post mock so callers can assert call counts.
        """
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)

        captured: dict = {}

        class _ImmediateThread:
            def __init__(self, target: Any, args: Any, daemon: bool) -> None:
                captured["target"] = target
                captured["args"] = args

            def start(self) -> None:
                captured["started"] = True

        initial_post = MagicMock()
        initial_post.json.return_value = {"error": "agent not started yet"}

        with stack:
            retry_post = MagicMock()
            if side_effects is not None:
                retry_post.side_effect = side_effects
            else:
                retry_post.return_value.json.return_value = agent_response

            with (
                patch(
                    "operate.cli.http_requests.post",
                    return_value=initial_post,
                ),
                patch("operate.cli.threading.Thread", _ImmediateThread),
                patch("operate.cli._time.sleep", return_value=None),
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})
                    assert resp.status_code == HTTPStatus.OK

                # Now run the retry target with the retry-post patched in.
                with patch("operate.cli.http_requests.post", retry_post):
                    captured["target"](*captured["args"])

                status = c.get("/api/v2/service/svc1/chat/status").json()

        return retry_post, status  # type: ignore[return-value]

    def test_retry_delivers_on_first_retry_success(self, tmp_path: Path) -> None:
        """Retry thread records the agent reply on the first successful attempt."""
        _, status = self._invoke_via_queue(tmp_path, {"ok": True})
        assert status["status"] == "delivered"
        assert status["result"] == {"ok": True}

    def test_retry_keeps_retrying_when_agent_still_not_ready(
        self, tmp_path: Path
    ) -> None:
        """Agent still not ready on every attempt → eventually marked failed."""
        not_ready = MagicMock()
        not_ready.json.return_value = {"error": "agent not started yet"}
        _, status = self._invoke_via_queue(
            tmp_path,
            agent_response=None,
            side_effects=[not_ready] * 40,
        )
        assert status["status"] == "failed"
        assert "20 minutes" in status["result"]["error"]

    def test_retry_swallows_exceptions_and_continues(self, tmp_path: Path) -> None:
        """Retries survive per-attempt exceptions and eventually succeed."""
        success = MagicMock()
        success.json.return_value = {"ok": "finally"}
        _, status = self._invoke_via_queue(
            tmp_path,
            agent_response=None,
            side_effects=[RuntimeError("temporary glitch"), success],
        )
        assert status["status"] == "delivered"
        assert status["result"] == {"ok": "finally"}

    def test_retry_aborts_when_entry_cancelled(self, tmp_path: Path) -> None:
        """If the pending entry is marked 'cancelled', the retry loop exits."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)

        captured: dict = {}

        class _ImmediateThread:
            def __init__(self, target: Any, args: Any, daemon: bool) -> None:
                captured["target"] = target
                captured["args"] = args

            def start(self) -> None:
                pass

        initial = MagicMock()
        initial.json.return_value = {"error": "agent not started yet"}
        with stack:
            with (
                patch("operate.cli.http_requests.post", return_value=initial),
                patch("operate.cli.threading.Thread", _ImmediateThread),
                patch("operate.cli._time.sleep", return_value=None),
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})

                # Flip the closure-local pending dict entry to cancelled.
                target = captured["target"]
                idx = target.__code__.co_freevars.index("_pending_chat")
                pending_dict = target.__closure__[idx].cell_contents
                pending_dict["svc1"] = {
                    "prompt": "hi",
                    "status": "cancelled",
                    "result": None,
                }

                def _should_not_post(*a: Any, **k: Any) -> Any:
                    raise AssertionError("retry should have exited")

                with patch(
                    "operate.cli.http_requests.post", side_effect=_should_not_post
                ):
                    target(*captured["args"])

    def test_retry_exits_when_entry_missing(self, tmp_path: Path) -> None:
        """If the pending entry is removed entirely, the retry loop exits."""
        m = _make_mock_operate()
        m.password = "pw"  # nosec B105
        m.service_manager.return_value.exists.return_value = True
        svc = _prepare_service_with_agent(tmp_path)
        m.service_manager.return_value.load.return_value = svc
        stack, app = _open_app(m)

        captured: dict = {}

        class _ImmediateThread:
            def __init__(self, target: Any, args: Any, daemon: bool) -> None:
                captured["target"] = target
                captured["args"] = args

            def start(self) -> None:
                pass

        initial = MagicMock()
        initial.json.return_value = {"error": "agent not started yet"}
        with stack:
            with (
                patch("operate.cli.http_requests.post", return_value=initial),
                patch("operate.cli.threading.Thread", _ImmediateThread),
                patch("operate.cli._time.sleep", return_value=None),
            ):
                with TestClient(app, raise_server_exceptions=False) as c:
                    c.post("/api/v2/service/svc1/chat", json={"prompt": "hi"})

                # Clear the entry to trigger the 'entry is None' branch.
                target = captured["target"]
                idx = target.__code__.co_freevars.index("_pending_chat")
                pending_dict = target.__closure__[idx].cell_contents
                pending_dict.pop("svc1", None)

                def _should_not_post(*a: Any, **k: Any) -> Any:
                    raise AssertionError("retry should have exited")

                with patch(
                    "operate.cli.http_requests.post", side_effect=_should_not_post
                ):
                    target(*captured["args"])


# ─── 7. version_check line 59 (_parse_version returns None on empty) ──────────


def test_parse_version_empty_string_returns_none() -> None:
    """Cover version_check.py line 59: empty input short-circuits to None."""
    from operate.version_check import _parse_version

    assert _parse_version("") is None
