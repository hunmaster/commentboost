"""HTTP CONNECT 바인딩 프록시.

목적:
  - localhost에서 HTTP CONNECT 프록시 서버 구동
  - Chrome이 이 프록시를 사용하면 모든 HTTPS 트래픽이 프록시 통해 나감
  - 프록시는 외부 연결 시 IP_UNICAST_IF로 USB 테더링 인터페이스 강제 사용
  - 결과: Chrome 트래픽만 LTE로 나가고, 나머지 PC 트래픽은 이더넷 그대로 사용

작동 원리:
  Chrome → 127.0.0.1:18888 (프록시) → IP_UNICAST_IF → USB 테더링 → LTE → 인터넷

Chrome 통합 방법:
  playwright.chromium.launch(proxy={"server": "http://127.0.0.1:18888"})

스레드 모델:
  - 별도 스레드에서 asyncio 이벤트 루프 실행
  - main 스레드는 Playwright 동기 API 사용 가능
"""
from __future__ import annotations

import asyncio
import socket
import threading
from typing import Optional

from src.network_utils import make_bound_socket


class BindingProxy:
    """USB 테더링으로 강제 송출하는 HTTP CONNECT 프록시."""

    def __init__(self, if_index: int, source_ip: str, listen_port: int = 0):
        """
        Args:
            if_index: 외부 송출에 사용할 인터페이스 인덱스
            source_ip: 인터페이스의 IP (추가 안전장치)
            listen_port: 0이면 자동 할당, 그 외엔 지정 포트 사용
        """
        self.if_index = if_index
        self.source_ip = source_ip
        self.listen_port = listen_port
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server: Optional[asyncio.AbstractServer] = None
        self._actual_port: int = 0
        self._ready = threading.Event()

    @property
    def port(self) -> int:
        """실제 listen 중인 포트."""
        return self._actual_port

    @property
    def url(self) -> str:
        """Playwright proxy 옵션에 넘길 URL."""
        return f"http://127.0.0.1:{self._actual_port}"

    def start(self, wait_seconds: float = 5.0) -> bool:
        """프록시 서버를 백그라운드 스레드에서 시작.

        Returns:
            True: 시작 성공, False: 시작 실패
        """
        if self._thread and self._thread.is_alive():
            return True

        self._ready.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="BindingProxy")
        self._thread.start()
        if not self._ready.wait(timeout=wait_seconds):
            return False
        return self._actual_port > 0

    def stop(self):
        """프록시 서버 종료."""
        if self._loop and self._server:
            try:
                self._loop.call_soon_threadsafe(self._server.close)
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        self._loop = None
        self._server = None
        self._actual_port = 0

    def _run(self):
        """백그라운드 스레드 진입점."""
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._serve())
        except Exception:
            pass
        finally:
            try:
                if self._loop:
                    self._loop.close()
            except Exception:
                pass
            self._ready.set()  # 실패해도 unblock

    async def _serve(self):
        self._server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", self.listen_port,
        )
        # 실제 할당된 포트
        sockets = self._server.sockets
        if sockets:
            self._actual_port = sockets[0].getsockname()[1]
        self._ready.set()
        try:
            async with self._server:
                await self._server.serve_forever()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """각 클라이언트(Chrome 연결) 처리."""
        target_writer = None
        try:
            # HTTP 요청 첫 줄 + 헤더 끝까지 읽기
            try:
                header_data = await asyncio.wait_for(
                    reader.readuntil(b"\r\n\r\n"), timeout=15
                )
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                return

            first_line = header_data.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
            parts = first_line.split(" ")
            if len(parts) < 3:
                return
            method = parts[0].upper()

            if method == "CONNECT":
                # CONNECT host:port HTTP/1.1
                host_port = parts[1]
                if ":" not in host_port:
                    return
                host, port_s = host_port.rsplit(":", 1)
                port = int(port_s)
                # 외부 연결 (USB 테더링 강제)
                target_reader, target_writer = await self._connect_via_interface(host, port)
                if not target_reader:
                    writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                    await writer.drain()
                    return
                # 핸드셰이크 OK 응답
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                # 양방향 파이프 (TLS는 client/server가 직접 처리)
                await asyncio.gather(
                    self._pipe(reader, target_writer),
                    self._pipe(target_reader, writer),
                    return_exceptions=True,
                )
            else:
                # 일반 HTTP (GET/POST/...) — Chrome도 가끔 사용
                # URL이 절대 URL이어야 함: GET http://example.com/path HTTP/1.1
                url = parts[1]
                if url.startswith("http://"):
                    rest = url[len("http://"):]
                    if "/" in rest:
                        host_port, path = rest.split("/", 1)
                        path = "/" + path
                    else:
                        host_port = rest
                        path = "/"
                    if ":" in host_port:
                        host, port_s = host_port.rsplit(":", 1)
                        port = int(port_s)
                    else:
                        host = host_port
                        port = 80
                    target_reader, target_writer = await self._connect_via_interface(host, port)
                    if not target_reader:
                        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                        await writer.drain()
                        return
                    # 첫 줄을 절대 URL → 상대 경로로 변환해서 전송
                    new_first = f"{method} {path} {parts[2]}".encode("ascii")
                    rest_headers = header_data.split(b"\r\n", 1)[1]
                    target_writer.write(new_first + b"\r\n" + rest_headers)
                    await target_writer.drain()
                    await asyncio.gather(
                        self._pipe(reader, target_writer),
                        self._pipe(target_reader, writer),
                        return_exceptions=True,
                    )
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
            if target_writer:
                try:
                    target_writer.close()
                except Exception:
                    pass

    async def _connect_via_interface(self, host: str, port: int):
        """인터페이스 강제 송출로 외부에 연결."""
        try:
            # 호스트 이름 → IP 해석 (이건 OS 기본 DNS 사용 — 일반 인터넷)
            # IP 분리는 트래픽 자체에 적용되므로 DNS 조회는 어디서 해도 OK
            loop = asyncio.get_event_loop()
            addr_info = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM, family=socket.AF_INET)
            if not addr_info:
                return None, None
            target_addr = addr_info[0][4]

            # IP_UNICAST_IF 적용된 소켓 생성
            sock = make_bound_socket(self.if_index, self.source_ip)
            sock.setblocking(False)
            await loop.sock_connect(sock, target_addr)

            return await asyncio.open_connection(sock=sock)
        except Exception:
            return None, None

    @staticmethod
    async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter):
        try:
            while True:
                data = await src.read(16384)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except Exception:
            pass
        finally:
            try:
                dst.close()
            except Exception:
                pass
