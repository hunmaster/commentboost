"""네트워크 유틸리티 — IP 분리를 위한 인터페이스 감지 및 강제 송출 헬퍼.

핵심 기능:
  - Windows 활성 인터페이스 나열 (GetAdaptersAddresses)
  - USB 테더링 인터페이스 자동 감지
  - IP_UNICAST_IF 소켓 옵션 적용 (특정 인터페이스로 강제 송출)
  - 외부 IP 확인 (일반 + 인터페이스별)
"""
from __future__ import annotations

import ctypes
import socket
import struct
import sys
from ctypes import wintypes
from typing import List, NamedTuple, Optional


IP_UNICAST_IF = 31  # Windows-specific IPv4 socket option


class NetworkInterface(NamedTuple):
    if_index: int
    name: str
    ip: str
    gateway: Optional[str]


# ─── Windows API 구조체 정의 ──────────────────────────────────────────

if sys.platform == "win32":
    _iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)

    class _SOCKADDR(ctypes.Structure):
        _fields_ = [
            ("sa_family", ctypes.c_ushort),
            ("sa_data", ctypes.c_byte * 14),
        ]

    class _SOCKADDR_IN(ctypes.Structure):
        _fields_ = [
            ("sin_family", ctypes.c_short),
            ("sin_port", ctypes.c_ushort),
            ("sin_addr", ctypes.c_ubyte * 4),
            ("sin_zero", ctypes.c_byte * 8),
        ]

    class _SOCKET_ADDRESS(ctypes.Structure):
        _fields_ = [
            ("lpSockaddr", ctypes.POINTER(_SOCKADDR)),
            ("iSockaddrLength", ctypes.c_int),
        ]

    class _IP_ADAPTER_UNICAST_ADDRESS(ctypes.Structure):
        pass

    _IP_ADAPTER_UNICAST_ADDRESS._fields_ = [
        ("Length", ctypes.c_ulong),
        ("Flags", ctypes.c_ulong),
        ("Next", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
        ("Address", _SOCKET_ADDRESS),
        ("PrefixOrigin", ctypes.c_int),
        ("SuffixOrigin", ctypes.c_int),
        ("DadState", ctypes.c_int),
        ("ValidLifetime", ctypes.c_ulong),
        ("PreferredLifetime", ctypes.c_ulong),
        ("LeaseLifetime", ctypes.c_ulong),
        ("OnLinkPrefixLength", ctypes.c_ubyte),
    ]

    class _IP_ADAPTER_GATEWAY_ADDRESS(ctypes.Structure):
        pass

    _IP_ADAPTER_GATEWAY_ADDRESS._fields_ = [
        ("Length", ctypes.c_ulong),
        ("Reserved", ctypes.c_ulong),
        ("Next", ctypes.POINTER(_IP_ADAPTER_GATEWAY_ADDRESS)),
        ("Address", _SOCKET_ADDRESS),
    ]

    class _IP_ADAPTER_ADDRESSES(ctypes.Structure):
        pass

    _IP_ADAPTER_ADDRESSES._fields_ = [
        ("Length", ctypes.c_ulong),
        ("IfIndex", ctypes.c_ulong),
        ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
        ("AdapterName", ctypes.c_char_p),
        ("FirstUnicastAddress", ctypes.POINTER(_IP_ADAPTER_UNICAST_ADDRESS)),
        ("FirstAnycastAddress", ctypes.c_void_p),
        ("FirstMulticastAddress", ctypes.c_void_p),
        ("FirstDnsServerAddress", ctypes.c_void_p),
        ("DnsSuffix", ctypes.c_wchar_p),
        ("Description", ctypes.c_wchar_p),
        ("FriendlyName", ctypes.c_wchar_p),
        ("PhysicalAddress", ctypes.c_ubyte * 8),
        ("PhysicalAddressLength", ctypes.c_ulong),
        ("Flags", ctypes.c_ulong),
        ("Mtu", ctypes.c_ulong),
        ("IfType", ctypes.c_ulong),
        ("OperStatus", ctypes.c_int),
        ("Ipv6IfIndex", ctypes.c_ulong),
        ("ZoneIndices", ctypes.c_ulong * 16),
        ("FirstPrefix", ctypes.c_void_p),
        ("TransmitLinkSpeed", ctypes.c_uint64),
        ("ReceiveLinkSpeed", ctypes.c_uint64),
        ("FirstWinsServerAddress", ctypes.c_void_p),
        ("FirstGatewayAddress", ctypes.POINTER(_IP_ADAPTER_GATEWAY_ADDRESS)),
    ]


def _sockaddr_to_ip(sockaddr_ptr) -> Optional[str]:
    if not sockaddr_ptr:
        return None
    sa = sockaddr_ptr.contents
    if sa.sa_family != socket.AF_INET:
        return None
    sa_in = ctypes.cast(sockaddr_ptr, ctypes.POINTER(_SOCKADDR_IN)).contents
    return f"{sa_in.sin_addr[0]}.{sa_in.sin_addr[1]}.{sa_in.sin_addr[2]}.{sa_in.sin_addr[3]}"


def list_active_interfaces() -> List[NetworkInterface]:
    """모든 활성 IPv4 네트워크 인터페이스를 반환."""
    if sys.platform != "win32":
        return []

    GetAdaptersAddresses = _iphlpapi.GetAdaptersAddresses
    GetAdaptersAddresses.argtypes = [
        ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
        ctypes.POINTER(_IP_ADAPTER_ADDRESSES), ctypes.POINTER(ctypes.c_ulong),
    ]
    GetAdaptersAddresses.restype = ctypes.c_ulong

    GAA_FLAG_INCLUDE_PREFIX = 0x0010
    size = ctypes.c_ulong(15000)
    buf = ctypes.create_string_buffer(size.value)
    ret = GetAdaptersAddresses(
        socket.AF_INET, GAA_FLAG_INCLUDE_PREFIX, None,
        ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_ADDRESSES)), ctypes.byref(size),
    )
    if ret == 111:  # ERROR_BUFFER_OVERFLOW
        buf = ctypes.create_string_buffer(size.value)
        ret = GetAdaptersAddresses(
            socket.AF_INET, GAA_FLAG_INCLUDE_PREFIX, None,
            ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_ADDRESSES)), ctypes.byref(size),
        )
    if ret != 0:
        return []

    results: List[NetworkInterface] = []
    adapter = ctypes.cast(buf, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
    while adapter:
        a = adapter.contents
        if a.OperStatus == 1:  # IfOperStatusUp
            ip = None
            uni = a.FirstUnicastAddress
            if uni:
                ip = _sockaddr_to_ip(uni.contents.Address.lpSockaddr)
            gw = None
            gwp = a.FirstGatewayAddress
            if gwp:
                gw = _sockaddr_to_ip(gwp.contents.Address.lpSockaddr)
            if ip and not ip.startswith("127."):
                results.append(NetworkInterface(
                    if_index=a.IfIndex,
                    name=a.FriendlyName,
                    ip=ip,
                    gateway=gw,
                ))
        adapter = a.Next if a.Next else None
    return results


def find_usb_tethering_interface() -> Optional[NetworkInterface]:
    """USB 테더링 인터페이스를 자동 감지.

    감지 로직:
      1. 이름에 '테더', 'tether', 'NDIS', 'RNDIS' 포함
      2. 그 외 사설 대역(192.168/10/172.16-31)이지만 이름이 '이더넷'이 아닌 것
      3. 마지막으로 사설 대역 인터페이스 중 인터페이스 인덱스가 가장 큰 것
         (보통 USB 장치는 최근 추가되어 인덱스가 큼)
    """
    interfaces = list_active_interfaces()
    if not interfaces:
        return None

    private_ifaces = [i for i in interfaces if _is_private(i.ip)]
    if not private_ifaces:
        return None

    # 1) 이름 기반 감지 (가장 신뢰)
    for i in private_ifaces:
        n = (i.name or "").lower()
        if any(k in n for k in ["tether", "테더", "ndis", "remote ndis", "usb"]):
            return i

    # 2) "이더넷" 단순 이름 제외 (집 LAN일 가능성)
    non_ethernet = [i for i in private_ifaces if not _looks_like_lan(i.name)]
    if len(non_ethernet) == 1:
        return non_ethernet[0]
    if non_ethernet:
        # 인터페이스 인덱스가 가장 작은 것 (보통 USB는 작거나 큼, 일관성 중요)
        return min(non_ethernet, key=lambda x: x.if_index)

    # 3) 폴백: 사설 인터페이스 중 인덱스가 가장 작은 것 (단일이면 그것)
    if len(private_ifaces) == 1:
        return private_ifaces[0]
    return None


def _is_private(ip: str) -> bool:
    if ip.startswith("192.168.") or ip.startswith("10."):
        return True
    parts = ip.split(".")
    if len(parts) >= 2 and parts[0] == "172":
        try:
            return 16 <= int(parts[1]) <= 31
        except ValueError:
            return False
    return False


def _looks_like_lan(name: str) -> bool:
    """이름이 집 LAN(이더넷)으로 보이는지."""
    if not name:
        return False
    n = name.lower().strip()
    # "이더넷" 그 자체 (숫자 안 붙은) → 보통 집 LAN
    if n in ("이더넷", "ethernet", "local area connection"):
        return True
    return False


def make_bound_socket(if_index: int, source_ip: Optional[str] = None) -> socket.socket:
    """IP_UNICAST_IF로 특정 인터페이스에 강제 바인딩된 TCP 소켓 생성.

    Args:
        if_index: 송출에 사용할 인터페이스 인덱스
        source_ip: 추가 안전장치로 source IP도 바인딩 (옵션)

    Returns:
        설정된 비연결 상태의 socket
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if_index_be = struct.pack("!I", if_index)
    sock.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, if_index_be)
    if source_ip:
        try:
            sock.bind((source_ip, 0))
        except OSError:
            pass
    return sock


def get_external_ip_normal(timeout: int = 5) -> Optional[str]:
    """현재 PC의 일반 외부 IP (라우팅 우선순위에 따른 기본 인터페이스 사용)."""
    return _http_get_ip("api.ipify.org", None, None, timeout)


def get_external_ip_via_interface(if_index: int, source_ip: str, timeout: int = 8) -> Optional[str]:
    """특정 인터페이스로 강제 송출하여 외부 IP 확인."""
    return _http_get_ip("api.ipify.org", if_index, source_ip, timeout)


def _http_get_ip(host: str, if_index: Optional[int], source_ip: Optional[str], timeout: int) -> Optional[str]:
    try:
        if if_index is not None:
            sock = make_bound_socket(if_index, source_ip)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, 80))
        sock.sendall(f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        body = data.split(b"\r\n\r\n", 1)[-1].decode().strip()
        # 첫 IPv4-like 토큰만 추출
        for token in body.split():
            parts = token.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                return token
        return body or None
    except Exception:
        return None
