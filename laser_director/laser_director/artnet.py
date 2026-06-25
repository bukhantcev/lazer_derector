from __future__ import annotations

import socket
import struct


ARTNET_PORT = 6454
ARTDMX_OPCODE = 0x5000
ARTNET_PROTOCOL_VERSION = 14


class ArtNetSender:
    def __init__(self, ip: str = "2.255.255.255", port: int = ARTNET_PORT, universe: int = 0) -> None:
        self.ip = ip
        self.port = port
        self.universe = universe
        self.sequence = 1
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def configure(self, ip: str, port: int, universe: int) -> None:
        self.ip = ip
        self.port = int(port)
        self.universe = int(universe)

    def send_dmx(self, dmx: bytes | bytearray) -> None:
        payload = bytes(dmx[:512]).ljust(512, b"\x00")
        packet = self.build_artdmx(payload, self.universe, self.sequence)
        self._socket.sendto(packet, (self.ip, self.port))
        self.sequence = 1 if self.sequence >= 255 else self.sequence + 1

    @staticmethod
    def build_artdmx(dmx: bytes, universe: int = 0, sequence: int = 1) -> bytes:
        length = len(dmx)
        if length % 2:
            dmx += b"\x00"
            length += 1
        header = b"Art-Net\x00"
        header += struct.pack("<H", ARTDMX_OPCODE)
        header += struct.pack(">H", ARTNET_PROTOCOL_VERSION)
        header += bytes([sequence & 0xFF, 0, universe & 0xFF, (universe >> 8) & 0x7F])
        header += struct.pack(">H", length)
        return header + dmx

    def close(self) -> None:
        self._socket.close()
