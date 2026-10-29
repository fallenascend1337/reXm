"""Framing for Sony's headphone control protocol (message layout as documented by Gadgetbridge).

A message is  0x3E <type> <seq> <length:4> <payload> <checksum> 0x3C, with 0x3C/0x3D/0x3E inside
escaped as 0x3D followed by the byte & 0xEF.
"""

START, END, ESC = 0x3E, 0x3C, 0x3D
ACK, CMD1 = 0x01, 0x0C


def escape(data):
    out = bytearray()
    for b in data:
        if b in (START, END, ESC):
            out += bytes([ESC, b & 0xEF])
        else:
            out.append(b)
    return bytes(out)


def unescape(data):
    out, i = bytearray(), 0
    while i < len(data):
        if data[i] == ESC and i + 1 < len(data):
            out.append(data[i + 1] | 0x10)
            i += 2
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


def frame(msg_type, seq, payload):
    body = bytes([msg_type, seq]) + len(payload).to_bytes(4, "big") + bytes(payload)
    body += bytes([sum(body) & 0xFF])
    return bytes([START]) + escape(body) + bytes([END])


def parse(raw):
    """Split a byte stream into (type, seq, payload) messages."""
    msgs = []
    for chunk in raw.split(bytes([END])):
        if START not in chunk:
            continue
        body = unescape(chunk[chunk.index(START) + 1:])
        if len(body) < 7:
            continue
        n = int.from_bytes(body[2:6], "big")
        msgs.append((body[0], body[1], body[6:6 + n]))
    return msgs
