"""UDP broadcast discovery of devices on the local network."""
from __future__ import annotations

import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

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


#: Largest range scanned at once (a /22): bigger networks are refused, scan them in parts.
MAX_SCAN_HOSTS = 1024


def _hosts(networks: Iterable[str]) -> List[str]:
    out: List[str] = []
    for net in networks:
        network = ipaddress.ip_network(net.strip(), strict=False)
        if network.version != 4:
            raise ValueError(f"{net}: only IPv4 is supported")
        if network.num_addresses > MAX_SCAN_HOSTS:
            raise ValueError(f"{net}: too large to scan (more than {MAX_SCAN_HOSTS} addresses)")
        hosts = [str(h) for h in network.hosts()] or [str(network.network_address)]
        out += [h for h in hosts if h not in out]
    return out


def _port_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def scan(networks: Iterable[str], port: int = 4370, password: str = "", timeout: float = 0.6,
         workers: int = 64) -> List[DiscoveredDevice]:
    """Find devices by trying TCP ``port`` on every address of ``networks``
    (e.g. ``["192.168.20.0/24"]``), then opening a session to read their identity.

    Unlike :func:`discover`, this crosses routers and VLANs (it is the same TCP port
    as the normal connection). A device that refuses ``password`` is still listed,
    with ``raw["auth"] == "required"`` and no serial number. Addresses where the port
    is open but no ZKTeco device answers are left out.
    """
    from .client import ZKAccess  # local import: client imports this package
    from .errors import DeviceError, ZKError

    hosts = _hosts(networks)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        open_hosts = [h for h, ok in zip(hosts, pool.map(lambda h: _port_open(h, port, timeout), hosts)) if ok]

    def identify(host: str) -> Optional[DiscoveredDevice]:
        try:
            with ZKAccess(host, port, password, timeout=3) as dev:
                info = dev.info()
        except DeviceError as ex:
            if ex.code == -14:
                return DiscoveredDevice(host, None, None, None, None, {"auth": "required", "port": str(port)})
            return None
        except (ZKError, OSError):
            return None
        return DiscoveredDevice(host, None, info.serial_number, info.device_name, info.firmware,
                                {"port": str(port), **info.raw})

    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [d for d in pool.map(identify, open_hosts) if d is not None]
    return found
