"""Vast.ai UDP 端口映射补丁（用于 aiortc / aioice 的纯 WebRTC P2P）。

vast.ai 容器只把预分配的一小组 UDP 端口映射到公网，容器内看到的
本地端口和公网端口不同。此补丁把 ICE socket 绑定到这些预分配端口，
并对外通告 ``PUBLIC_IP:public_port`` 的 host candidate，让浏览器能
通过 vast 的端口 NAT 直接连上。

仅对 ``--transport webrtc`` 的纯 P2P 路径有意义；rtcpush（本机 WHIP
推流到 SRS）不需要。若相关环境变量缺失，``apply()`` 会自动跳过，
因此在非 vast.ai 环境是无副作用的 no-op。

需要的环境变量：
- ``VAST_UDP_PORT_<container_port>=<public_port>``（可多个）
- ``PUBLIC_IPADDR`` 或 ``VAST_PUBLIC_IP``
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Optional

try:
    from aioice import turn
    from aioice.candidate import Candidate, candidate_foundation, candidate_priority
    from aioice.ice import (
        StunProtocol,
        TransportPolicy,
        relayed_candidate,
        server_reflexive_candidate,
    )
    _AIOICE_AVAILABLE = True
except ImportError:  # aioice 未安装（非 webrtc 环境），补丁自动禁用
    _AIOICE_AVAILABLE = False

_PORT_PAIRS: list[tuple[int, int]] = []  # [(container_port, public_port), ...]
_PUBLIC_IP: Optional[str] = None
_next_idx = 0
_applied = False


def _load_port_map() -> list[tuple[int, int]]:
    """从 VAST_UDP_PORT_* 环境变量读取 (容器端口, 公网端口) 列表。"""
    pairs = []
    for key, value in os.environ.items():
        if not key.startswith("VAST_UDP_PORT_"):
            continue
        try:
            pairs.append((int(key[len("VAST_UDP_PORT_"):]), int(value)))
        except ValueError:
            continue
    pairs.sort(key=lambda x: x[0])
    return pairs


def _next_ports() -> Optional[tuple[int, int]]:
    """轮询取下一组端口对；无映射时返回 None（退回随机端口）。"""
    global _next_idx
    if not _PORT_PAIRS:
        return None
    pair = _PORT_PAIRS[_next_idx % len(_PORT_PAIRS)]
    _next_idx += 1
    return pair


async def _patched_get_component_candidates(self, component, addresses, timeout=5):
    candidates = []
    loop = asyncio.get_event_loop()
    host_protocols = []

    for address in addresses:
        ports = _next_ports()
        local_port = 0 if ports is None else ports[0]
        advertise_port = None if ports is None else ports[1]

        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: StunProtocol(self), local_addr=(address, local_port)
            )
            sock = transport.get_extra_info("socket")
            if sock is not None:
                sock.setsockopt(
                    socket.SOL_SOCKET, socket.SO_RCVBUF, turn.UDP_SOCKET_BUFFER_SIZE
                )
        except OSError as exc:
            print(f"[vast_ice] bind failed {address}:{local_port} {exc}")
            continue

        host_protocols.append(protocol)
        sockname = protocol.transport.get_extra_info("sockname")
        host_ip = _PUBLIC_IP or sockname[0]
        host_port = advertise_port if advertise_port is not None else sockname[1]

        protocol.local_candidate = Candidate(
            foundation=candidate_foundation("host", "udp", host_ip),
            component=component,
            transport="udp",
            priority=candidate_priority(component, "host"),
            host=host_ip,
            port=host_port,
            type="host",
        )
        if self._transport_policy == TransportPolicy.ALL:
            candidates.append(protocol.local_candidate)

    self._protocols += host_protocols

    tasks = []
    if self.stun_server:
        for protocol in host_protocols:
            try:
                if ipaddress.ip_address(protocol.local_candidate.host).version == 4:
                    tasks.append(
                        asyncio.create_task(
                            server_reflexive_candidate(protocol, self.stun_server)
                        )
                    )
            except ValueError:
                continue

    if self.turn_server:
        tasks.append(
            asyncio.create_task(
                relayed_candidate(
                    component=component,
                    protocol_factory=lambda: StunProtocol(self),
                    turn_server=self.turn_server,
                    turn_username=self.turn_username,
                    turn_password=self.turn_password,
                    turn_ssl=self.turn_ssl,
                    turn_transport=self.turn_transport,
                )
            )
        )

    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in done:
            if task.exception() is None:
                candidate, protocol = task.result()
                candidates.append(candidate)
                if protocol is not None:
                    self._protocols.append(protocol)
        for task in pending:
            task.cancel()

    return candidates


def apply() -> bool:
    """安装 ICE 补丁。缺少 aioice / vast 端口映射 / 公网 IP 时跳过并返回 False。"""
    global _PORT_PAIRS, _PUBLIC_IP, _applied, _next_idx

    if not _AIOICE_AVAILABLE:
        print("[vast_ice] skip patch (aioice not installed)")
        return False

    from aioice.ice import Connection

    _PORT_PAIRS = _load_port_map()
    _PUBLIC_IP = os.environ.get("PUBLIC_IPADDR") or os.environ.get("VAST_PUBLIC_IP")
    _next_idx = 0

    if not _PORT_PAIRS or not _PUBLIC_IP:
        print(f"[vast_ice] skip patch (ports={len(_PORT_PAIRS)} public={_PUBLIC_IP})")
        return False

    if not _applied:
        Connection.get_component_candidates = _patched_get_component_candidates
        _applied = True

    print(f"[vast_ice] patched ICE public_ip={_PUBLIC_IP} udp_map={_PORT_PAIRS}")
    return True
