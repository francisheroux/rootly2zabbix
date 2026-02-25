from unittest.mock import MagicMock, patch

import pytest
import requests

from zabbix import (
    ACTION_ACKNOWLEDGE,
    ACTION_CLOSE,
    ACTION_MESSAGE,
    ACTION_SEVERITY,
    ACTION_UNACKNOWLEDGE,
    ZabbixAPIError,
    ZabbixClient,
    ZabbixConnectionError,
)


def _mock_ok(result=None):
    """Return a mock response with a successful Zabbix JSON-RPC body."""
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {"jsonrpc": "2.0", "id": "1", "result": result or {}}
    return mock


def _mock_api_error(code=-32602, message="Invalid params", data="No permissions"):
    mock = MagicMock()
    mock.raise_for_status = MagicMock()
    mock.json.return_value = {
        "jsonrpc": "2.0",
        "id": "1",
        "error": {"code": code, "message": message, "data": data},
    }
    return mock


def _mock_http_error(status_code=500):
    mock = MagicMock()
    mock.status_code = status_code
    mock.raise_for_status.side_effect = requests.HTTPError(response=mock)
    return mock


class TestZabbixClient:
    def setup_method(self):
        self.client = ZabbixClient(
            urls=["http://zabbix.example.com/api_jsonrpc.php"],
            token="test_token",
            retries=2,
            backoff=0.01,  # fast backoff for tests
        )

    # ------------------------------------------------------------------
    # Successful calls
    # ------------------------------------------------------------------

    def test_acknowledge_success(self):
        with patch.object(self.client.session, "post") as mock_post:
            mock_post.return_value = _mock_ok({"eventids": ["12345"]})
            result = self.client.acknowledge("12345", "Test message", ACTION_ACKNOWLEDGE | ACTION_MESSAGE)

        assert result == {"eventids": ["12345"]}
        payload = mock_post.call_args[1]["json"]
        assert payload["method"] == "event.acknowledge"
        assert payload["params"]["eventids"] == ["12345"]
        assert payload["params"]["action"] == ACTION_ACKNOWLEDGE | ACTION_MESSAGE
        assert payload["params"]["message"] == "Test message"

    def test_acknowledge_close(self):
        with patch.object(self.client.session, "post") as mock_post:
            mock_post.return_value = _mock_ok()
            self.client.acknowledge("12345", "Resolved", ACTION_CLOSE | ACTION_MESSAGE)

        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["action"] == ACTION_CLOSE | ACTION_MESSAGE

    def test_acknowledge_with_severity(self):
        with patch.object(self.client.session, "post") as mock_post:
            mock_post.return_value = _mock_ok()
            self.client.acknowledge("12345", "Severity changed", ACTION_SEVERITY | ACTION_MESSAGE, severity=4)

        payload = mock_post.call_args[1]["json"]
        assert payload["params"]["severity"] == 4

    def test_severity_not_sent_without_action_bit(self):
        """severity param should be omitted if ACTION_SEVERITY bit is not set."""
        with patch.object(self.client.session, "post") as mock_post:
            mock_post.return_value = _mock_ok()
            self.client.acknowledge("12345", "msg", ACTION_MESSAGE, severity=4)

        payload = mock_post.call_args[1]["json"]
        assert "severity" not in payload["params"]

    def test_no_message_param_when_empty(self):
        """Empty message string should not be included in params."""
        with patch.object(self.client.session, "post") as mock_post:
            mock_post.return_value = _mock_ok()
            self.client.acknowledge("12345", "", ACTION_ACKNOWLEDGE)

        payload = mock_post.call_args[1]["json"]
        assert "message" not in payload["params"]

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def test_api_error_raises_immediately(self):
        with patch.object(self.client.session, "post") as mock_post:
            mock_post.return_value = _mock_api_error()
            with pytest.raises(ZabbixAPIError):
                self.client.acknowledge("12345", "msg", ACTION_MESSAGE)
        # API errors are not retried
        assert mock_post.call_count == 1

    def test_connection_error_retries_then_raises(self):
        with patch.object(self.client.session, "post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("Connection refused")
            with pytest.raises(ZabbixConnectionError):
                self.client.acknowledge("12345", "msg", ACTION_MESSAGE)
        assert mock_post.call_count == 2  # retries=2

    def test_http_error_retries_then_raises(self):
        with patch.object(self.client.session, "post") as mock_post:
            mock_post.return_value = _mock_http_error(500)
            with pytest.raises(ZabbixConnectionError):
                self.client.acknowledge("12345", "msg", ACTION_MESSAGE)
        assert mock_post.call_count == 2

    # ------------------------------------------------------------------
    # Failover
    # ------------------------------------------------------------------

    def test_failover_to_second_url(self):
        client = ZabbixClient(
            urls=[
                "http://primary.example.com/api",
                "http://secondary.example.com/api",
            ],
            token="test_token",
            retries=1,
            backoff=0.01,
        )

        def side_effect(url, **kwargs):
            if "primary" in url:
                raise requests.ConnectionError("Primary down")
            return _mock_ok({"eventids": ["12345"]})

        with patch.object(client.session, "post", side_effect=side_effect) as mock_post:
            result = client.acknowledge("12345", "msg", ACTION_MESSAGE)

        assert result == {"eventids": ["12345"]}
        assert mock_post.call_count == 2  # 1 fail on primary + 1 success on secondary

    def test_all_urls_fail_raises(self):
        client = ZabbixClient(
            urls=["http://url1.example.com/api", "http://url2.example.com/api"],
            token="test_token",
            retries=1,
            backoff=0.01,
        )
        with patch.object(client.session, "post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("All down")
            with pytest.raises(ZabbixConnectionError):
                client.acknowledge("12345", "msg", ACTION_MESSAGE)

    # ------------------------------------------------------------------
    # Action bitmask constants
    # ------------------------------------------------------------------

    def test_action_constants_are_correct_bitmasks(self):
        from zabbix import ACTION_ACKNOWLEDGE, ACTION_CLOSE, ACTION_MESSAGE, ACTION_SEVERITY, ACTION_UNACKNOWLEDGE
        assert ACTION_CLOSE == 1
        assert ACTION_ACKNOWLEDGE == 2
        assert ACTION_MESSAGE == 4
        assert ACTION_SEVERITY == 8
        assert ACTION_UNACKNOWLEDGE == 16
