import ipaddress
import socket
from urllib.parse import unquote, urlparse, urlsplit

from api.constants import DEPLOYMENT_MODE

_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def is_browser_safe_local_public_audio_path(path: object) -> bool:
    """Return whether an audio URL is safe to pass directly to ``<audio src>``.

    Browsers normalize backslashes as URL path separators. A value such as
    ``/\\\\evil.example/opening.wav`` can therefore become a scheme-relative
    request despite looking root-relative to a simple ``startswith('/')``
    check. Accept only a single-slash, same-origin path without browser- or
    server-side traversal escapes.
    """
    if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
        return False
    if "\\" in path:
        return False

    parsed = urlsplit(path)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        return False

    decoded_path = unquote(parsed.path)
    if (
        not decoded_path.startswith("/")
        or decoded_path.startswith("//")
        or "\\" in decoded_path
        or any(ord(character) < 32 for character in decoded_path)
    ):
        return False
    return all(segment not in {".", ".."} for segment in decoded_path.split("/"))


def validate_user_configured_service_url(
    url: str,
    *,
    field_name: str,
) -> None:
    """Restrict user-configured service URLs in hosted deployments.

    OSS deployments commonly point model services at localhost or private LAN
    hosts. SaaS deployments must not allow users to make Dograh infrastructure
    connect to private/internal network locations.
    """
    if DEPLOYMENT_MODE == "oss":
        return

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be an http, https, ws, or wss URL")

    hostname = parsed.hostname
    if hostname.lower() == "localhost":
        raise ValueError(f"{field_name} cannot point to localhost in SaaS mode")

    for ip in _resolve_hostname_ips(hostname, parsed.port):
        if _is_blocked_saas_service_ip(ip):
            raise ValueError(
                f"{field_name} must resolve to a public IP address in SaaS mode"
            )


def _resolve_hostname_ips(
    hostname: str, port: int | None
) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return [ipaddress.ip_address(hostname)]
    except ValueError:
        pass

    try:
        addr_infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise ValueError("Could not resolve service URL hostname") from e

    return [ipaddress.ip_address(addr_info[4][0]) for addr_info in addr_infos]


def _is_blocked_saas_service_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (ip.version == 4 and ip in _CGNAT_NETWORK)
    )
