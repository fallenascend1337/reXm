"""Linux transport: BlueZ RFCOMM sockets. The channel comes from an SDP lookup, never from scanning
(scanning channels wedged an XM5 until it was power-cycled)."""
import asyncio
import re
import socket
import subprocess


def _bluetoothctl(*args):
    out = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=10)
    return out.stdout


def _sdp_channel(mac, service_uuid):
    """Ask the headset's SDP server which RFCOMM channel serves `service_uuid` (None if not offered)."""
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_SEQPACKET, socket.BTPROTO_L2CAP)
    s.settimeout(5)
    try:
        s.connect((mac, 1))  # PSM 1 = SDP
        params = (bytes([0x35, 0x11, 0x1C]) + service_uuid.bytes  # search pattern: one UUID128
                  + b"\xff\xff"                                  # max attribute bytes
                  + bytes([0x35, 0x03, 0x09, 0x00, 0x04])        # attribute 0x0004: protocol descriptors
                  + b"\x00")                                     # no continuation
        s.send(bytes([0x06, 0x00, 0x01]) + len(params).to_bytes(2, "big") + params)
        reply = s.recv(4096)
    finally:
        s.close()
    # RFCOMM (UUID16 0x0003) is followed by its channel as a uint8 (0x08)
    i = reply.find(bytes([0x19, 0x00, 0x03, 0x08]))
    return reply[i + 4] if i >= 0 else None


class Transport:
    def __init__(self, mac, name, proto, channel):
        self.mac, self.name, self.proto, self.channel = mac, name, proto, channel
        self.sock = None

    async def connect(self, service_uuid):
        loop = asyncio.get_running_loop()
        self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self.sock.setblocking(False)
        await asyncio.wait_for(loop.sock_connect(self.sock, (self.mac, self.channel)), 10)

    async def write(self, data):
        await asyncio.get_running_loop().sock_sendall(self.sock, data)

    async def read(self):
        return await asyncio.get_running_loop().sock_recv(self.sock, 1024)

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


async def find(services):
    """services: [(proto, uuid)] in preference order. Returns (connected, Transport) for each paired Sony headset."""
    loop = asyncio.get_running_loop()
    # every known device, not just "Paired": a connected-but-unbonded headset is still usable
    listing = await loop.run_in_executor(None, _bluetoothctl, "devices")
    found = []
    for mac, name in re.findall(r"Device ([0-9A-F:]{17}) (.+)", listing):
        info = await loop.run_in_executor(None, _bluetoothctl, "info", mac)
        uuids = info.lower()
        for proto, sid in services:
            if str(sid) not in uuids:
                continue
            connected = "connected: yes" in uuids
            channel = None
            if connected:
                channel = await loop.run_in_executor(None, _sdp_channel, mac, sid)
            found.append((connected and channel is not None, Transport(mac, name.strip(), proto, channel)))
            break
    return found
