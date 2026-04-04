"""
CONNECT-over-WebSocket relay for CCR upstreamproxy.

Listens on localhost TCP, accepts HTTP CONNECT from curl/gh/kubectl/etc,
and tunnels bytes over WebSocket to the CCR upstreamproxy endpoint.
The CCR server-side terminates the tunnel, MITMs TLS, injects org-configured
credentials (e.g. DD-API-KEY), and forwards to the real upstream.

WHY WebSocket and not raw CONNECT: CCR ingress is GKE L7 with path-prefix
routing; there's no connect_matcher in cdk-constructs.

Protocol: bytes are wrapped in UpstreamProxyChunk protobuf messages
(message UpstreamProxyChunk { bytes data = 1; }) for compatibility with
gateway.NewWebSocketStreamAdapter on the server side.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import struct
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Envoy per-request buffer cap.
MAX_CHUNK_BYTES = 512 * 1024

# Sidecar idle timeout is 50s; ping well inside that.
PING_INTERVAL_S = 30.0


def encode_chunk(data: bytes) -> bytes:
    """
    Encode an UpstreamProxyChunk protobuf message by hand.

    For `message UpstreamProxyChunk { bytes data = 1; }` the wire format is:
      tag = (field_number << 3) | wire_type = (1 << 3) | 2 = 0x0a
      followed by varint length, followed by the bytes.
    """
    length = len(data)
    varint_bytes = bytearray()
    n = length
    while n > 0x7F:
        varint_bytes.append((n & 0x7F) | 0x80)
        n >>= 7
    varint_bytes.append(n)
    return b"\x0a" + bytes(varint_bytes) + data


def decode_chunk(buf: bytes) -> Optional[bytes]:
    """
    Decode an UpstreamProxyChunk. Returns the data field, or None if malformed.
    Tolerates the server sending a zero-length chunk (keepalive semantics).
    """
    if len(buf) == 0:
        return b""
    if buf[0] != 0x0A:
        return None

    length = 0
    shift = 0
    i = 1
    while i < len(buf):
        b = buf[i]
        length |= (b & 0x7F) << shift
        i += 1
        if (b & 0x80) == 0:
            break
        shift += 7
        if shift > 28:
            return None

    if i + length > len(buf):
        return None
    return buf[i : i + length]


@dataclass
class UpstreamProxyRelay:
    port: int
    stop: Callable[[], None]


@dataclass
class ConnState:
    ws: Optional[object] = None
    connect_buf: bytearray = field(default_factory=bytearray)
    pinger_task: Optional[asyncio.Task] = None
    pending: list[bytes] = field(default_factory=list)
    ws_open: bool = False
    established: bool = False
    closed: bool = False


async def start_upstream_proxy_relay(
    ws_url: str,
    session_id: str,
    token: str,
) -> UpstreamProxyRelay:
    """
    Start the relay. Returns the ephemeral port it bound and a stop function.
    Uses asyncio TCP server.
    """
    try:
        import websockets
    except ImportError:
        raise ImportError(
            "websockets package required for upstream proxy relay. "
            "Install with: pip install websockets"
        )

    auth_header = "Basic " + base64.b64encode(
        f"{session_id}:{token}".encode()
    ).decode()
    ws_auth_header = f"Bearer {token}"

    async def _handle_client(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        st = ConnState()

        async def _send_keepalive(ws: object) -> None:
            """Send periodic keepalive chunks."""
            try:
                while True:
                    await asyncio.sleep(PING_INTERVAL_S)
                    try:
                        await ws.send(encode_chunk(b""))  # type: ignore
                    except Exception:
                        break
            except asyncio.CancelledError:
                pass

        async def _open_tunnel(
            connect_line: str,
        ) -> None:
            """Open a WebSocket tunnel to the upstream proxy."""
            headers = {
                "Content-Type": "application/proto",
                "Authorization": ws_auth_header,
            }

            try:
                ws = await websockets.connect(  # type: ignore
                    ws_url,
                    additional_headers=headers,
                )
            except Exception as e:
                logger.debug(f"[upstreamproxy] ws connect error: {e}")
                if not st.established:
                    writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    await writer.drain()
                writer.close()
                return

            st.ws = ws
            st.ws_open = True

            # Send CONNECT line with auth
            head = (
                f"{connect_line}\r\n"
                f"Proxy-Authorization: {auth_header}\r\n"
                f"\r\n"
            )
            await ws.send(encode_chunk(head.encode("utf-8")))  # type: ignore

            # Flush pending data
            for buf in st.pending:
                await _forward_to_ws(ws, buf)
            st.pending.clear()

            # Start keepalive
            st.pinger_task = asyncio.create_task(_send_keepalive(ws))

            # Read from WebSocket and forward to client
            try:
                async for message in ws:  # type: ignore
                    if isinstance(message, bytes):
                        payload = decode_chunk(message)
                        if payload and len(payload) > 0:
                            st.established = True
                            writer.write(payload)
                            await writer.drain()
            except Exception as e:
                logger.debug(f"[upstreamproxy] ws error: {e}")
                if not st.closed:
                    st.closed = True
                    if not st.established:
                        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                        try:
                            await writer.drain()
                        except Exception:
                            pass
            finally:
                st.closed = True
                _cleanup_conn(st)
                try:
                    writer.close()
                except Exception:
                    pass

        async def _forward_to_ws(ws: object, data: bytes) -> None:
            """Forward client data to WebSocket in chunks."""
            for off in range(0, len(data), MAX_CHUNK_BYTES):
                chunk = data[off : off + MAX_CHUNK_BYTES]
                await ws.send(encode_chunk(chunk))  # type: ignore

        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break

                # Phase 1: accumulate CONNECT request
                if st.ws is None:
                    st.connect_buf.extend(data)
                    header_end = st.connect_buf.find(b"\r\n\r\n")
                    if header_end == -1:
                        if len(st.connect_buf) > 8192:
                            writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                            await writer.drain()
                            break
                        continue

                    req_head = st.connect_buf[:header_end].decode("utf-8")
                    first_line = req_head.split("\r\n")[0] if req_head else ""

                    import re

                    m = re.match(
                        r"^CONNECT\s+(\S+)\s+HTTP/1\.[01]$",
                        first_line,
                        re.IGNORECASE,
                    )
                    if not m:
                        writer.write(
                            b"HTTP/1.1 405 Method Not Allowed\r\n\r\n"
                        )
                        await writer.drain()
                        break

                    # Stash trailing bytes
                    trailing = st.connect_buf[header_end + 4 :]
                    if trailing:
                        st.pending.append(bytes(trailing))
                    st.connect_buf = bytearray()

                    # Open tunnel in background, continue reading
                    tunnel_task = asyncio.create_task(
                        _open_tunnel(first_line)
                    )
                    continue

                # Phase 2: forward to WebSocket
                if not st.ws_open:
                    st.pending.append(data)
                else:
                    await _forward_to_ws(st.ws, data)

        except Exception as e:
            logger.debug(f"[upstreamproxy] client error: {e}")
        finally:
            _cleanup_conn(st)
            try:
                writer.close()
            except Exception:
                pass

    def _cleanup_conn(st: ConnState) -> None:
        if st.pinger_task:
            st.pinger_task.cancel()
            st.pinger_task = None
        if st.ws:
            try:
                asyncio.ensure_future(st.ws.close())  # type: ignore
            except Exception:
                pass
            st.ws = None

    server = await asyncio.start_server(
        _handle_client, "127.0.0.1", 0
    )
    addr = server.sockets[0].getsockname()
    port = addr[1]

    def stop() -> None:
        server.close()

    logger.debug(f"[upstreamproxy] relay listening on 127.0.0.1:{port}")
    return UpstreamProxyRelay(port=port, stop=stop)
