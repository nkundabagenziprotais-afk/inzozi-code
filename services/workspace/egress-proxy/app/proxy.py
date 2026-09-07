from __future__ import annotations

import asyncio
from contextlib import suppress

ALLOWED_HOST = "github.com"
ALLOWED_PORT = 443

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 3128

HEADER_LIMIT = 8192
HEADER_TIMEOUT_SECONDS = 5
CONNECT_TIMEOUT_SECONDS = 8


def _parse_connect_target(header: bytes) -> tuple[str, int]:
    if not header or len(header) > HEADER_LIMIT:
        raise ValueError("Invalid proxy request")

    try:
        text = header.decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid proxy request") from exc

    first_line = text.split("\r\n", 1)[0]
    parts = first_line.split(" ")

    if len(parts) != 3:
        raise ValueError("Invalid proxy request")

    method, target, version = parts

    if method != "CONNECT":
        raise ValueError("Only HTTPS CONNECT is supported")

    if version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise ValueError("Unsupported proxy protocol")

    host, separator, port_text = target.rpartition(":")

    if not separator or not host or not port_text.isdigit():
        raise ValueError("Invalid CONNECT target")

    port = int(port_text)

    if host.casefold() != ALLOWED_HOST or port != ALLOWED_PORT:
        raise PermissionError("CONNECT destination is not allowed")

    return ALLOWED_HOST, ALLOWED_PORT


async def _send_status(
    writer: asyncio.StreamWriter,
    status: bytes,
) -> None:
    writer.write(
        b"HTTP/1.1 "
        + status
        + b"\r\n"
        + b"Connection: close\r\n"
        + b"Content-Length: 0\r\n"
        + b"\r\n"
    )
    await writer.drain()


async def _relay(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    while True:
        data = await reader.read(65536)

        if not data:
            return

        writer.write(data)
        await writer.drain()


async def _handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    remote_writer: asyncio.StreamWriter | None = None

    try:
        try:
            header = await asyncio.wait_for(
                client_reader.readuntil(b"\r\n\r\n"),
                timeout=HEADER_TIMEOUT_SECONDS,
            )
        except (
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
            TimeoutError,
        ):
            await _send_status(client_writer, b"400 Bad Request")
            return

        try:
            host, port = _parse_connect_target(header)
        except PermissionError:
            await _send_status(client_writer, b"403 Forbidden")
            return
        except ValueError:
            await _send_status(client_writer, b"400 Bad Request")
            return

        try:
            remote_reader, remote_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=CONNECT_TIMEOUT_SECONDS,
            )
        except (OSError, TimeoutError):
            await _send_status(client_writer, b"502 Bad Gateway")
            return

        client_writer.write(
            b"HTTP/1.1 200 Connection Established\r\n\r\n"
        )
        await client_writer.drain()

        client_to_remote = asyncio.create_task(
            _relay(client_reader, remote_writer)
        )
        remote_to_client = asyncio.create_task(
            _relay(remote_reader, client_writer)
        )

        done, pending = await asyncio.wait(
            {client_to_remote, remote_to_client},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()

        await asyncio.gather(
            *done,
            *pending,
            return_exceptions=True,
        )

    finally:
        if remote_writer is not None:
            remote_writer.close()
            with suppress(Exception):
                await remote_writer.wait_closed()

        client_writer.close()
        with suppress(Exception):
            await client_writer.wait_closed()


async def main() -> None:
    server = await asyncio.start_server(
        _handle_client,
        LISTEN_HOST,
        LISTEN_PORT,
        limit=HEADER_LIMIT + 1,
    )

    print(
        "GitHub-only CONNECT proxy ready on "
        f"{LISTEN_HOST}:{LISTEN_PORT}",
        flush=True,
    )

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
