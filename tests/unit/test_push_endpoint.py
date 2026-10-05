"""Which URLs a web push may be sent to, and how the request is made.

The endpoint comes from the browser, and this service POSTs to it from inside its own
network. The rules are checked by `check_endpoint`; names resolve through `FakeDns`.
"""

from __future__ import annotations

import socket

import pytest
import requests

from celine.nudging.config.settings import Settings
from celine.nudging.publishers.web import endpoint as ep
from celine.nudging.publishers.web.endpoint import EndpointRefused, check_endpoint

FCM = "https://fcm.googleapis.com/fcm/send/abc:def"


def _settings(**update) -> Settings:
    return Settings(_env_file=None).model_copy(update=update)


@pytest.fixture
def shipped() -> Settings:
    """The shipped allow-list, without the suite's `push.test`."""
    return Settings(
        _env_file=None, WEBPUSH_ALLOWED_HOSTS=Settings.model_fields["WEBPUSH_ALLOWED_HOSTS"].default
    )


@pytest.mark.parametrize(
    "url",
    [
        FCM,
        "https://updates.push.services.mozilla.com/wpush/v2/gAAAAA",
        "https://web.push.apple.com/QGx2",
        "https://wns2-par02p.notify.windows.com/w/?token=BQYAAA",
        "https://fcm.googleapis.com:443/fcm/send/x",
    ],
)
# @verifies REQ-0083
def test_the_browser_push_services_are_accepted_by_default(shipped, url):
    check_endpoint(url, shipped, env="staging")


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://fcm.googleapis.com/fcm/send/x", "https"),
        ("https://user:pw@fcm.googleapis.com/x", "credentials"),
        ("https://fcm.googleapis.com:8443/x", "port 443"),
        ("https://", "no host"),
        ("not a url", "https"),
        ("https://example.org/push", "not an allowed push service"),
        ("https://fcm.googleapis.com.example.org/x", "not an allowed push service"),
        ("https://notfcm.googleapis.com/x", "not an allowed push service"),
        ("https://127.0.0.1/x", "not an allowed push service"),
        ("https://[::1]/x", "not an allowed push service"),
    ],
)
# @verifies REQ-0083
def test_a_url_breaking_a_rule_is_refused_for_good(shipped, url, reason):
    with pytest.raises(EndpointRefused, match=reason) as refused:
        check_endpoint(url, shipped, env="staging")

    assert refused.value.permanent is True


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "100.100.100.200",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fd00:ec2::254",
        "fe80::1",
        "::ffff:10.0.0.5",
    ],
)
# @verifies REQ-0083
def test_an_allowed_host_resolving_to_a_non_public_address_is_refused(shipped, dns, address):
    dns.set("fcm.googleapis.com", address)

    with pytest.raises(EndpointRefused, match="non-public address") as refused:
        check_endpoint(FCM, shipped, env="staging")

    # The name may resolve properly later, so a stored subscription is kept.
    assert refused.value.permanent is False


# @verifies REQ-0083
def test_one_non_public_address_among_public_ones_is_enough_to_refuse(shipped, dns):
    dns.set("fcm.googleapis.com", "34.120.0.10", "10.0.0.5")

    with pytest.raises(EndpointRefused):
        check_endpoint(FCM, shipped, env="staging")


# @verifies REQ-0083
def test_a_name_that_does_not_resolve_is_refused_but_not_for_good():
    with pytest.raises(EndpointRefused, match="does not resolve") as refused:
        check_endpoint(
            "https://push.nowhere.test/x",
            _settings(WEBPUSH_ALLOWED_HOSTS="nowhere.test"),
            env="staging",
        )
    assert refused.value.permanent is False


# @verifies REQ-0083
def test_a_wildcard_lifts_the_list_but_not_the_address_rules(dns):
    settings = _settings(WEBPUSH_ALLOWED_HOSTS="*")
    dns.set("push.example.org", "34.120.0.10")
    dns.set("internal.example.org", "10.1.2.3")

    check_endpoint("https://push.example.org/x", settings, env="staging")
    with pytest.raises(EndpointRefused, match="non-public"):
        check_endpoint("https://internal.example.org/x", settings, env="staging")
    with pytest.raises(EndpointRefused, match="not public"):
        check_endpoint("https://169.254.169.254/latest", settings, env="staging")
    with pytest.raises(EndpointRefused, match="https"):
        check_endpoint("http://push.example.org/x", settings, env="staging")


# @verifies REQ-0083
def test_the_relaxation_counts_only_in_dev(dns):
    settings = _settings(WEBPUSH_ENDPOINT_RELAXED=True)

    check_endpoint("http://localhost:8099/push", settings, env="dev")
    for env in ("", "staging", "prod"):
        with pytest.raises(EndpointRefused):
            check_endpoint("http://localhost:8099/push", settings, env=env)


# @verifies REQ-0083
def test_without_the_relaxation_dev_applies_every_rule(shipped):
    with pytest.raises(EndpointRefused):
        check_endpoint("http://localhost:8099/push", shipped, env="dev")


# ---------------------------------------------------------------------------
# The session pywebpush sends through
# ---------------------------------------------------------------------------


# @verifies REQ-0084
def test_the_session_follows_no_redirect_and_ignores_proxy_settings(shipped):
    session = ep.push_session(shipped, env="staging")

    assert session.trust_env is False
    assert session.max_redirects == 0
    assert isinstance(session.get_adapter("https://fcm.googleapis.com/"), ep._PublicPeerAdapter)

    seen = {}

    def fake_request(self, method, url, *args, **kwargs):
        seen.update(kwargs)
        return "response"

    original = requests.Session.request
    requests.Session.request = fake_request
    try:
        session.post("https://fcm.googleapis.com/x", data=b"", timeout=3)
    finally:
        requests.Session.request = original
    assert seen["allow_redirects"] is False


# @verifies REQ-0084
def test_a_connection_that_reaches_a_non_public_peer_is_dropped(shipped):
    """
    The name is resolved again when connecting, so the socket's peer is what counts.
    A listener on loopback stands in for a name that now resolves somewhere private.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        session = ep.push_session(shipped, env="staging")
        with pytest.raises(requests.ConnectionError, match="non-public address"):
            session.post(f"https://127.0.0.1:{port}/x", data=b"", timeout=2)
    finally:
        listener.close()
