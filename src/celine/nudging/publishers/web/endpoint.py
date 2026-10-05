"""Where a web push may be sent (REQ-0083, REQ-0084).

A subscription's `endpoint` is a URL chosen by the browser, so by whoever holds the
session. The service POSTs to it from inside its own network, which makes the URL a
destination this service is asked to reach. It is therefore checked twice: when it is
stored, and again just before each send, since a name can resolve somewhere else later.

The rules:

- `https`, port 443, no credentials in the URL;
- a host on the allow-list (`WEBPUSH_ALLOWED_HOSTS`, the browser push services by
  default; `*` lifts the list but not the rules below);
- every address the host resolves to is public — not private, loopback, link-local
  (which includes the cloud metadata address), shared, reserved or multicast;
- at send time the connected peer is checked again, redirects are not followed, the
  environment's proxy settings are ignored and the request has a timeout.

`WEBPUSH_ENDPOINT_RELAXED=true` lifts all of this for a local push-service stand-in. It is
honoured only with `CELINE_ENV=dev`, and the posture guard refuses it anywhere else.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Callable, Iterable
from urllib.parse import urlsplit

import requests
from celine.sdk.posture import is_dev
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
from urllib3.exceptions import NewConnectionError

Resolver = Callable[[str, int], Iterable[str]]


class EndpointRefused(ValueError):
    """The endpoint is not a destination this service sends to.

    `permanent` is true when the URL itself breaks a rule, so it will never pass; it is
    false when only its current addresses do (resolution can change), so a stored
    subscription is kept for a later attempt.
    """

    def __init__(self, reason: str, *, permanent: bool) -> None:
        super().__init__(reason)
        self.reason = reason
        self.permanent = permanent


def relaxed(settings, env: str | None = None) -> bool:
    """`WEBPUSH_ENDPOINT_RELAXED` counts only under `CELINE_ENV=dev`."""
    if not settings.WEBPUSH_ENDPOINT_RELAXED:
        return False
    return env == "dev" if env is not None else is_dev()


def allowed_hosts(settings) -> list[str]:
    return [
        h.strip().lower().rstrip(".")
        for h in settings.WEBPUSH_ALLOWED_HOSTS.split(",")
        if h.strip()
    ]


def is_public_address(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast and not ip.is_reserved


def _resolve(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


def _host_allowed(host: str, allowed: list[str]) -> bool:
    if "*" in allowed:
        return True
    return any(host == entry or host.endswith("." + entry) for entry in allowed)


def check_endpoint(
    url: str,
    settings,
    *,
    env: str | None = None,
    resolve: Resolver | None = None,
) -> None:
    """Raise `EndpointRefused` unless `url` is a destination a push may be sent to."""
    if relaxed(settings, env):
        return

    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise EndpointRefused("endpoint is not a valid URL", permanent=True)

    if parts.scheme != "https":
        raise EndpointRefused("endpoint must use https", permanent=True)
    if parts.username is not None or parts.password is not None:
        raise EndpointRefused("endpoint must not carry credentials", permanent=True)
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise EndpointRefused("endpoint has no host", permanent=True)
    if port not in (None, 443):
        raise EndpointRefused("endpoint must use port 443", permanent=True)
    if not _host_allowed(host, allowed_hosts(settings)):
        raise EndpointRefused("endpoint host is not an allowed push service", permanent=True)

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_public_address(host):
            raise EndpointRefused("endpoint address is not public", permanent=True)

    try:
        addresses = list((resolve or _resolve)(host, 443))
    except (OSError, UnicodeError):
        raise EndpointRefused("endpoint host does not resolve", permanent=False)
    if not addresses:
        raise EndpointRefused("endpoint host does not resolve", permanent=False)
    if not all(is_public_address(a) for a in addresses):
        raise EndpointRefused("endpoint resolves to a non-public address", permanent=False)


# ---------------------------------------------------------------------------
# The HTTP session handed to pywebpush
# ---------------------------------------------------------------------------


class _PublicPeerHTTPSConnection(HTTPSConnection):
    """Refuses the connection when the peer it reached is not a public address.

    The name was checked before the send, but it is resolved again here; checking the
    socket's peer closes the gap between the two lookups.
    """

    def _new_conn(self) -> socket.socket:
        sock = super()._new_conn()
        try:
            peer = sock.getpeername()[0]
        except OSError:
            peer = ""
        if not is_public_address(peer):
            sock.close()
            raise NewConnectionError(self, "push endpoint connected to a non-public address")
        return sock


class _PublicPeerHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PublicPeerHTTPSConnection


class _PublicPeerAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs) -> None:
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            "http": HTTPConnectionPool,
            "https": _PublicPeerHTTPSPool,
        }


class _NoRedirectSession(requests.Session):
    """A redirect comes back as the response; pywebpush reports it as a failed push."""

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        kwargs["allow_redirects"] = False
        return super().request(method, url, *args, **kwargs)


def push_session(settings, env: str | None = None) -> requests.Session:
    """The session every web push is sent through."""
    session = _NoRedirectSession()
    session.max_redirects = 0
    # Proxy variables would make the proxy the peer, defeating the address check.
    session.trust_env = False
    if not relaxed(settings, env):
        session.mount("https://", _PublicPeerAdapter())
    return session
