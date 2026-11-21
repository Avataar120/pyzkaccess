"""UDP broadcast discovery of devices on the local network."""
from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from . import codec, protocol
from .errors import ProtocolError


@dataclass
class DiscoveredDevice:
    ip: Optional[str]
    mac: Optional[str]
    serial_number: Optional[str]
    device_name: Optional[str]
    firmware: Optional[str]
    raw: Dict[str, str]


def discover(timeout: float = 2.0, interface: str = "0.0.0.0",
             broadcast: str = "255.255.255.255") -> List[DiscoveredDevice]:
    """Broadcast a discovery request and collect the answers.

    Broadcasts do not cross routers or VLANs: devices on another subnet are
    not found; connect to them by IP address instead.
    """
    frame = protocol.build_frame(protocol.Command.DISCOVER, protocol.DISCOVERY_PAYLOAD)
    found: Dict[str, DiscoveredDevice] = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.bind((interface, 0))
        sock.sendto(frame, (broadcast, protocol.DISCOVERY_PORT))
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            sock.settimeout(left)
            try:
                payload, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            try:
                frame_in = protocol.parse_frame(payload)
            except ProtocolError:
                continue
            if frame_in.command != protocol.Reply.OK:
                continue
            kv = codec.parse_params(frame_in.data)
            ip = kv.get("IP") or addr[0]
            found[ip] = DiscoveredDevice(ip, kv.get("MAC"), kv.get("SN"), kv.get("Device"),
                                         kv.get("Ver"), kv)
    return list(found.values())
