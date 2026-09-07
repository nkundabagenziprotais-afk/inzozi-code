import pytest

from app.proxy import (
    ALLOWED_HOST,
    ALLOWED_PORT,
    HEADER_LIMIT,
    _parse_connect_target,
)


def request(target: str, method: str = "CONNECT") -> bytes:
    return (
        f"{method} {target} HTTP/1.1\r\n"
        f"Host: {target}\r\n"
        "\r\n"
    ).encode("ascii")


def test_exact_github_https_connect_is_allowed():
    assert _parse_connect_target(
        request("github.com:443")
    ) == (ALLOWED_HOST, ALLOWED_PORT)


def test_case_insensitive_github_hostname_is_allowed():
    assert _parse_connect_target(
        request("GitHub.com:443")
    ) == (ALLOWED_HOST, ALLOWED_PORT)


@pytest.mark.parametrize(
    "target",
    [
        "example.com:443",
        "api.github.com:443",
        "github.com:80",
        "github.com:22",
        "github.com.evil.example:443",
        "127.0.0.1:443",
        "169.254.169.254:443",
    ],
)
def test_non_allowlisted_destinations_are_denied(target):
    with pytest.raises(PermissionError):
        _parse_connect_target(request(target))


def test_plain_http_methods_are_denied():
    with pytest.raises(ValueError):
        _parse_connect_target(
            request("github.com:443", method="GET")
        )


def test_malformed_or_oversized_headers_are_denied():
    with pytest.raises(ValueError):
        _parse_connect_target(b"not-http\r\n\r\n")

    with pytest.raises(ValueError):
        _parse_connect_target(
            b"A" * (HEADER_LIMIT + 1)
        )
