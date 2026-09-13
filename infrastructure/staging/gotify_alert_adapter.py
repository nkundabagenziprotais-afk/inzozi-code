#!/usr/bin/env python3

import json
import os
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8091
ALERT_PATH = "/v1/alerts"

GOTIFY_BASE_URL = "http://127.0.0.1:8088"
GOTIFY_TOKEN_FILE = Path(
    "/srv/inzozi-code/secrets/gotify-app-token"
)

EXPECTED_SERVICE = "inzozi-code-staging"

EXPECTED_KEYS = {
    "service",
    "severity",
    "summary",
    "timestamp",
}

ALLOWED_SEVERITIES = {
    "warning",
    "critical",
}

MAX_BODY_BYTES = 16 * 1024
MAX_SUMMARY_LENGTH = 2048

SUMMARY_PATTERN = re.compile(
    r"^[A-Z0-9_.,;:-]+$"
)

NO_PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({})
)


class ValidationError(ValueError):
    pass


def emit(marker: str) -> None:
    print(
        marker,
        file=sys.stderr,
        flush=True,
    )


def secret_metadata_ok(
    owner_uid: int,
    mode: int,
) -> bool:
    return (
        owner_uid == 0
        and stat.S_ISREG(mode)
        and stat.S_IMODE(mode)
        in {0o400, 0o600}
    )


def read_private_token(
    path: Path = GOTIFY_TOKEN_FILE,
) -> str:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise RuntimeError(
            "gotify-token-file-unavailable"
        ) from exc

    if not stat.S_ISREG(
        metadata.st_mode
    ):
        raise RuntimeError(
            "gotify-token-not-regular-file"
        )

    if not secret_metadata_ok(
        metadata.st_uid,
        metadata.st_mode,
    ):
        raise RuntimeError(
            "gotify-token-permissions-unsafe"
        )

    try:
        token = path.read_text(
            encoding="utf-8"
        ).strip()
    except OSError as exc:
        raise RuntimeError(
            "gotify-token-read-failed"
        ) from exc

    if not token:
        raise RuntimeError(
            "gotify-token-empty"
        )

    if len(token) > 512:
        raise RuntimeError(
            "gotify-token-too-long"
        )

    if any(
        char.isspace()
        for char in token
    ):
        raise RuntimeError(
            "gotify-token-invalid-whitespace"
        )

    if any(
        ord(char) < 33
        or ord(char) > 126
        for char in token
    ):
        raise RuntimeError(
            "gotify-token-invalid-characters"
        )

    return token


def validate_local_gotify_url(
    value: str,
) -> str:
    parsed = urllib.parse.urlsplit(
        value
    )

    if parsed.scheme != "http":
        raise ValidationError(
            "gotify-url-scheme"
        )

    if parsed.hostname != "127.0.0.1":
        raise ValidationError(
            "gotify-url-host"
        )

    if parsed.port != 8088:
        raise ValidationError(
            "gotify-url-port"
        )

    if (
        parsed.username
        or parsed.password
    ):
        raise ValidationError(
            "gotify-url-credentials"
        )

    if parsed.path not in {"", "/"}:
        raise ValidationError(
            "gotify-url-path"
        )

    if (
        parsed.query
        or parsed.fragment
    ):
        raise ValidationError(
            "gotify-url-extra-components"
        )

    return value.rstrip("/")


def validate_timestamp(
    value: str,
) -> str:
    if not isinstance(value, str):
        raise ValidationError(
            "timestamp-type"
        )

    if not 10 <= len(value) <= 64:
        raise ValidationError(
            "timestamp-length"
        )

    candidate = value

    if candidate.endswith("Z"):
        candidate = (
            candidate[:-1]
            + "+00:00"
        )

    try:
        parsed = datetime.fromisoformat(
            candidate
        )
    except ValueError as exc:
        raise ValidationError(
            "timestamp-format"
        ) from exc

    if parsed.tzinfo is None:
        raise ValidationError(
            "timestamp-timezone"
        )

    return value


def validate_payload(
    payload: Any,
) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValidationError(
            "payload-not-object"
        )

    if set(payload.keys()) != EXPECTED_KEYS:
        raise ValidationError(
            "payload-fields"
        )

    service = payload["service"]
    severity = payload["severity"]
    summary = payload["summary"]
    timestamp = payload["timestamp"]

    if service != EXPECTED_SERVICE:
        raise ValidationError(
            "service"
        )

    if severity not in ALLOWED_SEVERITIES:
        raise ValidationError(
            "severity"
        )

    if not isinstance(summary, str):
        raise ValidationError(
            "summary-type"
        )

    if not (
        1
        <= len(summary)
        <= MAX_SUMMARY_LENGTH
    ):
        raise ValidationError(
            "summary-length"
        )

    if not SUMMARY_PATTERN.fullmatch(
        summary
    ):
        raise ValidationError(
            "summary-characters"
        )

    timestamp = validate_timestamp(
        timestamp
    )

    return {
        "service": service,
        "severity": severity,
        "summary": summary,
        "timestamp": timestamp,
    }


def to_gotify_message(
    payload: dict[str, str],
) -> dict[str, Any]:
    severity = payload["severity"]

    priority = (
        10
        if severity == "critical"
        else 5
    )

    return {
        "title": (
            "Inzozi Code staging "
            f"{severity.upper()}"
        ),
        "message": (
            f"{severity.upper()}: "
            f"{payload['summary']}\n"
            f"Timestamp: "
            f"{payload['timestamp']}"
        ),
        "priority": priority,
    }


def gotify_health(
    base_url: str = GOTIFY_BASE_URL,
) -> bool:
    base = validate_local_gotify_url(
        base_url
    )

    request = urllib.request.Request(
        f"{base}/health",
        method="GET",
        headers={
            "Accept": "application/json",
        },
    )

    try:
        with NO_PROXY_OPENER.open(
            request,
            timeout=3,
        ) as response:
            return (
                200
                <= response.status
                < 300
            )
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        OSError,
    ):
        return False


def forward_to_gotify(
    payload: dict[str, str],
    base_url: str = GOTIFY_BASE_URL,
) -> tuple[bool, int | None]:
    base = validate_local_gotify_url(
        base_url
    )

    token = read_private_token()

    body = json.dumps(
        to_gotify_message(payload),
        separators=(",", ":"),
    ).encode("utf-8")

    request = urllib.request.Request(
        f"{base}/message",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type":
                "application/json",
            "X-Gotify-Key": token,
        },
    )

    try:
        with NO_PROXY_OPENER.open(
            request,
            timeout=5,
        ) as response:
            status = response.status

    except urllib.error.HTTPError as exc:
        return False, exc.code

    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ):
        return False, None

    return (
        200 <= status < 300,
        status,
    )


class AlertHandler(
    BaseHTTPRequestHandler
):
    server_version = (
        "InzoziCodeGotifyAdapter/1"
    )

    def log_message(
        self,
        _format: str,
        *_args: Any,
    ) -> None:
        # Never log request body,
        # headers, token, or arbitrary
        # client-provided values.
        return

    def send_fixed(
        self,
        status_code: int,
        body: bytes = b"",
    ) -> None:
        self.send_response(
            status_code
        )

        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.end_headers()

        if body:
            self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_fixed(
                404,
                b"not-found\n",
            )
            return

        try:
            read_private_token()
        except RuntimeError:
            emit(
                "ADAPTER_HEALTH_TOKEN=FAIL"
            )
            self.send_fixed(
                503,
                b"unavailable\n",
            )
            return

        if not gotify_health():
            emit(
                "ADAPTER_HEALTH_GOTIFY=FAIL"
            )
            self.send_fixed(
                503,
                b"unavailable\n",
            )
            return

        self.send_fixed(
            200,
            b"ok\n",
        )

    def do_POST(self) -> None:
        if self.path != ALERT_PATH:
            self.send_fixed(
                404,
                b"not-found\n",
            )
            return

        content_type = (
            self.headers.get(
                "Content-Type",
                "",
            )
            .split(";", 1)[0]
            .strip()
            .lower()
        )

        if content_type != "application/json":
            self.send_fixed(
                415,
                b"unsupported-media-type\n",
            )
            return

        raw_length = self.headers.get(
            "Content-Length"
        )

        try:
            content_length = int(
                raw_length or ""
            )
        except ValueError:
            self.send_fixed(
                400,
                b"invalid-content-length\n",
            )
            return

        if (
            content_length < 1
            or
            content_length > MAX_BODY_BYTES
        ):
            self.send_fixed(
                413,
                b"payload-too-large\n",
            )
            return

        raw_body = self.rfile.read(
            content_length
        )

        if (
            len(raw_body)
            != content_length
        ):
            self.send_fixed(
                400,
                b"incomplete-body\n",
            )
            return

        try:
            payload = json.loads(
                raw_body.decode("utf-8")
            )

            payload = validate_payload(
                payload
            )

        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
        ):
            emit(
                "ADAPTER_PAYLOAD_VALIDATION=FAIL"
            )

            self.send_fixed(
                400,
                b"invalid-alert\n",
            )
            return

        try:
            succeeded, status = (
                forward_to_gotify(
                    payload
                )
            )
        except (
            RuntimeError,
            ValidationError,
        ):
            emit(
                "ADAPTER_FORWARD_CONFIGURATION=FAIL"
            )

            self.send_fixed(
                503,
                b"unavailable\n",
            )
            return

        if not succeeded:
            if status is None:
                emit(
                    "GOTIFY_FORWARD_HTTP_STATUS="
                    "UNAVAILABLE"
                )
            else:
                emit(
                    "GOTIFY_FORWARD_HTTP_STATUS="
                    f"{status}"
                )

            self.send_fixed(
                502,
                b"delivery-failed\n",
            )
            return

        emit(
            "GOTIFY_ALERT_DELIVERY=PASS"
        )

        self.send_fixed(204)


def main() -> int:
    if os.geteuid() != 0:
        emit(
            "ADAPTER_ERROR=must-run-as-root"
        )
        return 1

    try:
        validate_local_gotify_url(
            GOTIFY_BASE_URL
        )

        read_private_token()

    except (
        RuntimeError,
        ValidationError,
    ):
        emit(
            "ADAPTER_STARTUP_CONFIGURATION=FAIL"
        )
        return 1

    server = ThreadingHTTPServer(
        (
            LISTEN_HOST,
            LISTEN_PORT,
        ),
        AlertHandler,
    )

    server.daemon_threads = True

    emit(
        "ISSUE39_GOTIFY_ALERT_ADAPTER=STARTED"
    )
    emit(
        "ADAPTER_LISTEN_ADDRESS="
        "127.0.0.1:8091"
    )
    emit(
        "ADAPTER_SECRET_VALUES_PRINTED=NO"
    )

    try:
        server.serve_forever(
            poll_interval=0.5
        )
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
