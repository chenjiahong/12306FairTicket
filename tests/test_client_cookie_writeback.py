"""A login-page redirect must never clobber the shared session cookie file.

Regression: submitOrderRequest redirected to the login page (HTML + anonymous
Set-Cookie). maybe_save_cookies() ran before the response was validated and
persisted the anonymous cookies, overwriting the real login session.
"""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from ticket_app.client import RailwayClient
from ticket_app.configuration import ResponseFormatError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.setattr(
        requests.Session, "request",
        lambda *_args, **_kwargs: pytest.fail("Unexpected network request"),
    )


class FakeResponse:
    def __init__(self, content, url, *, status_code=200):
        self.content = content
        self.url = url
        self.status_code = status_code
        self.headers = {}
        self.history = []

    def json(self):
        import json as _json
        return _json.loads(self.content.decode("utf-8"))


HTML_LOGIN = b"<!DOCTYPE html>\n<html><head><title>\xe7\x99\xbb\xe5\xbd\x95</title></head></html>"
JSON_OK = b'{"status": true, "data": {"flag": true}}'


def make_client(tmp_path):
    client = object.__new__(RailwayClient)
    client.cfg = SimpleNamespace(
        persist_session=True,
        session_file=tmp_path / "session.cookies",
        request_timeout_seconds=5,
        purpose_codes="ADULT",
    )
    client.session = requests.Session()
    client.session.cookies.set("JSESSIONID", "REAL-SESSION", domain="kyfw.12306.cn")
    client._saved_cookie_snapshot = {}  # force a change so a save *would* happen
    return client


def test_login_redirect_html_body_skips_writeback(tmp_path):
    client = make_client(tmp_path)
    resp = FakeResponse(HTML_LOGIN, "https://kyfw.12306.cn/otn/login/login.html")
    client.maybe_save_cookies(resp)
    assert not client.cfg.session_file.exists(), "login-page cookies must not be persisted"


def test_login_redirect_login_url_skips_writeback(tmp_path):
    client = make_client(tmp_path)
    # JSON body but final URL is a login page -> still a redirect, skip save.
    resp = FakeResponse(JSON_OK, "https://kyfw.12306.cn/otn/login/checkUser")
    client.maybe_save_cookies(resp)
    assert not client.cfg.session_file.exists()


def test_normal_json_response_still_writes_back(tmp_path):
    client = make_client(tmp_path)
    resp = FakeResponse(JSON_OK, "https://kyfw.12306.cn/otn/leftTicket/query")
    client.maybe_save_cookies(resp)
    assert client.cfg.session_file.exists(), "rotated cookies must still be persisted"


def test_no_response_arg_still_writes_back(tmp_path):
    client = make_client(tmp_path)
    client.maybe_save_cookies()
    assert client.cfg.session_file.exists()


def test_submit_order_request_html_does_not_clobber_file(tmp_path):
    client = make_client(tmp_path)
    client.session.post = Mock(return_value=FakeResponse(
        HTML_LOGIN, "https://kyfw.12306.cn/otn/login/login.html",
    ))
    ticket = {
        "query_date": "2026-11-11",
        "secret_str": "mock-secret",
        "from_station": "A",
        "to_station": "B",
    }
    with pytest.raises(ResponseFormatError):
        client.submit_order_request(ticket)
    # The real session cookie must survive: no anonymous cookies written back.
    assert not client.cfg.session_file.exists()
    assert client.session.cookies.get("JSESSIONID") == "REAL-SESSION"


def test_submit_order_request_json_success_writes_back(tmp_path):
    client = make_client(tmp_path)
    client.session.post = Mock(return_value=FakeResponse(
        b'{"status": true}', "https://kyfw.12306.cn/otn/leftTicket/submitOrderRequest",
    ))
    ticket = {
        "query_date": "2026-11-11",
        "secret_str": "mock-secret",
        "from_station": "A",
        "to_station": "B",
    }
    ok, _ = client.submit_order_request(ticket)
    assert ok is True
    assert client.cfg.session_file.exists()
