#
# BattGo is a battery communication protocol originally at BattGo.org . This protocol
# is used in Spektrum smart batteries as the communication protocol.
#
# This file is a Python implementation of the BattGo protocol based on the Go-BattGo project
# https://github.com/BertoldVdb/go-battgo
#
# This is NOT an official implementation supported by any manufacturer - any reference to
# trademarks does not indicate this is an authorized or approved project.
#
# This project has a BSD 2-clause license, which follows the Go-BattGo project license
#
#   Copyright (c) 2026, Colin O'Flynn (Python conversion)
#   Copyright (c) 2021, Bertold Van den Bergh (Go reference implementation)
#
#  Redistribution and use in source and binary forms, with or without
#  modification, are permitted provided that the following conditions are met:
#
#  1. Redistributions of source code must retain the above copyright notice, this
#     list of conditions and the following disclaimer.
#
#  2. Redistributions in binary form must reproduce the above copyright notice,
#     this list of conditions and the following disclaimer in the documentation
#     and/or other materials provided with the distribution.
#
#  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
#  AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
#  IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
#  DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
#  FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
#  DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
#  SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
#  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
#  OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
#  OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from enum import IntEnum
import struct
import time


def _scramble(seed: int, data: bytearray) -> None:
    """
    In-place scramble/unscramble of data bytes AFTER the seed.
    Go:
        xor := seed + 136
        out[i] = in[i] ^ xor
        xor += seed
        xor ^= seed
    """
    xor = (seed + 136) & 0xFF
    for i in range(len(data)):
        data[i] ^= xor
        xor = (xor + seed) & 0xFF
        xor ^= seed
        xor &= 0xFF


@dataclass
class DecodedPacket:
    addr_source: int
    addr_dest: int
    seed: int
    payload: bytes           # descrambled payload
    checksum_ok: bool
    raw_payload_field: bytes # includes seed + (scrambled) payload, as received pre-descramble


class BattGoPhyDecoder:
    """
    Streaming decoder matching the Go PHY.Run() state machine.
    Feed raw UART bytes (with 0xAA framing + stuffing), get decoded packets.
    """

    def __init__(self) -> None:
        self._rx_state = 0
        self._rx_len = 0
        self._addr_source = 0
        self._addr_dest = 0
        self._sum = 0  # uint16
        self._payload_field = bytearray()  # holds seed + payload + checksum (2 bytes) as received
        self._is_escaped = False

    def feed(self, chunk: bytes) -> List[DecodedPacket]:
        out: List[DecodedPacket] = []

        for m in chunk:
            if not self._is_escaped:
                if m == 0xAA:
                    self._is_escaped = True
                    continue
            else:
                # we previously saw 0xAA
                self._is_escaped = False
                if m != 0xAA:
                    # start-of-frame; m is first byte of new frame (addrSource)
                    self._rx_state = 1
                    self._sum = 0
                    # IMPORTANT: fall through with m as a normal byte in state machine

            if self._rx_state == 0:
                # "presence" bytes ignored here; caller can hook if desired
                continue

            elif self._rx_state == 1:
                self._addr_source = m
                self._sum = (self._sum + m) & 0xFFFF
                self._rx_state = 2

            elif self._rx_state == 2:
                self._addr_dest = m
                self._sum = (self._sum + m) & 0xFFFF
                self._rx_state = 3

            elif self._rx_state == 3:
                if m > 0:
                    self._payload_field.clear()
                    self._rx_len = int(m) + 2  # payload_field = (seed+payload) + checksum(2)
                    self._sum = (self._sum + m) & 0xFFFF
                    self._rx_state = 4
                else:
                    self._rx_state = 0

            elif self._rx_state == 4:
                self._payload_field.append(m)
                if len(self._payload_field) == self._rx_len:
                    pkt = self._finish_frame()
                    if pkt is not None:
                        out.append(pkt)
                    self._rx_state = 0

            else:
                self._rx_state = 0

        return out

    def _finish_frame(self) -> Optional[DecodedPacket]:
        pf = bytes(self._payload_field)
        if len(pf) < 3:  # must at least contain seed + 2-byte checksum
            return None

        csum_end = len(pf) - 2
        rx_csum = pf[csum_end] | (pf[csum_end + 1] << 8)

        # Add sum of seed+payload (but not the checksum bytes)
        s = self._sum
        for b in pf[:csum_end]:
            s = (s + b) & 0xFFFF

        checksum_ok = (rx_csum == s)

        seed = pf[0]
        scrambled_payload = bytearray(pf[1:csum_end])  # bytes after seed, before checksum

        # Go code always calls scramble(payload[0], payload[1:], payload[1:]) AFTER checksum check
        # We'll match that: only descramble if checksum passes.
        if checksum_ok:
            _scramble(seed, scrambled_payload)

        return DecodedPacket(
            addr_source=self._addr_source,
            addr_dest=self._addr_dest,
            seed=seed,
            payload=bytes(scrambled_payload) if checksum_ok else b"",
            checksum_ok=checksum_ok,
            raw_payload_field=pf[:csum_end],  # seed + scrambled payload
        )


def decode_message(data: bytes) -> List[DecodedPacket]:
    """
    Decode one or more packets from raw line bytes (as captured from UART).
    """
    d = BattGoPhyDecoder()
    return d.feed(data)


def decode_message_hex(hex_string: str) -> List[DecodedPacket]:
    """
    Convenience: pass a hex dump like 'AA 01 02 ...' (spaces/newlines ok).
    """
    cleaned = "".join(ch for ch in hex_string if ch.strip() and ch not in ":,")
    # keep only hex chars
    cleaned = "".join(ch for ch in cleaned if ch in "0123456789abcdefABCDEF")
    return decode_message(bytes.fromhex(cleaned))

BatteryType = {0:"LiHv",
    1:"LiPo",
    2:"LiIon",
    3:"LiFe",
    5:"Pb",
    6:"NiMH",
    None:"Unknown"}

@dataclass
class BatteryData:
    connected: bool = True
    last_data_ts: float = 0.0  # unix time

    bus_address: int = 0
    serial_hex: str = ""
    manufacturer_name: str = ""

    battery_type = None
    cell_discharge_cutoff_v: float = 0.0
    cell_discharge_normal_v: float = 0.0
    cell_charge_max_v: float = 0.0
    cell_storage_default_v: float = 0.0
    cell_capacity_ah: float = 0.0
    battery_charge_max_current_a: float = 0.0
    battery_discharge_max_current_a: float = 0.0
    temp_use_low_c: int = 0
    temp_use_high_c: int = 0
    temp_storage_low_c: int = 0
    temp_storage_high_c: int = 0
    battery_has_auto_discharge: bool = False
    battery_number_of_cells: int = 0

    battery_preferred_charge_current_a: float = 0.0
    cell_preferred_storage_voltage_v: float = 0.0
    cell_preferred_max_voltage_v: float = 0.0
    battery_self_discharge_enabled: bool = False
    battery_self_discharge_hours: int = 0

    battery_charge_cycles: int = 0
    battery_error_over_charged: int = 0
    battery_error_over_discharged: int = 0
    battery_error_over_temperature: int = 0

    temp_current_c: int = 0
    cell_voltage_v: List[float] = field(default_factory=list)


def _u16le(b: bytes, off: int) -> int:
    return struct.unpack_from("<H", b, off)[0]


def _u32le(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def _i8(b: int) -> int:
    return struct.unpack("<b", bytes([b]))[0]

class BattGoDecoder(object):

    def __init__(self):
        self.data = BatteryData()

    def process_packet(self, packet: bytes) -> dict:
        if packet[0] == 0x02:
            #Ping can be used to set device address as well, we ignore that here
            return "REQ: Ping"
        elif packet[0] == 0x03:
            #Pong includes the battery SN in bytes [1:11] as well but ignored ehre
            return "RESP: Pong"
        elif packet[0] == 0x42:
            return "REQ: User settings"
        elif packet[0] == 0x43:
            return self.decode_usersettings(packet)
        elif packet[0] == 0x44:
            return "REQ: State"
        elif packet[0] == 0x45:
            return self.decode_state(packet)
        elif packet[0] == 0x4A:
            return "REQ: Cycle info"
        elif packet[0] == 0x4B:
            return self.decode_cycleinfo(packet)
        elif packet[0] == 0x84:
            return "REQ: Serial info"
        elif packet[0] == 0x85:
            return self.decode_serialinfo(packet)
        elif packet[0] == 0x88:
            return "REQ: Factory info"
        elif packet[0] == 0x89:
            return self.decode_factoryinfo(packet)
        else:
            return "Unknown packet"

    def decode_cycleinfo(self, payload: bytes) -> dict:
        """
        Expected reply 0x4B, min len 12
        Fields:
        cycles: uint16 at [1:3]
        overtemp: uint16 [6:8]
        overcharged: uint16 [8:10]
        overdischarged: uint16 [10:12]
        """
        if len(payload) < 12 or payload[0] != 0x4B:
            return None

        self.data.battery_charge_cycles = _u16le(payload, 1)
        self.data.battery_error_over_temperature = _u16le(payload, 6)
        self.data.battery_error_over_charged = _u16le(payload, 8)
        self.data.battery_error_over_discharged = _u16le(payload, 10)
        return {"battery charge cycles":self.data.battery_charge_cycles,
                "stored over temps":self.data.battery_error_over_temperature,
                "stored over charged":self.data.battery_error_over_charged,
                "stored over discharged":self.data.battery_error_over_discharged}


    def decode_usersettings(self, payload: bytes) -> dict:
        """
        Expected reply 0x43, min len 9
        chargeCurrentA = (uint32le[1:5] & 0xFFFFFF)/1000.0
        storageV = u16le[4:6]/1000.0
        maxV = u16le[6:8]/1000.0
        selfDischargeEnabled = payload[8] != 0xFF
        selfDischargeHours = payload[8]
        """
        if len(payload) < 9 or payload[0] != 0x43:
            return None

        raw = _u32le(payload, 1) & 0xFFFFFF
        self.data.battery_preferred_charge_current_a = raw / 1000.0
        self.data.cell_preferred_storage_voltage_v = _u16le(payload, 4) / 1000.0
        self.data.cell_preferred_max_voltage_v = _u16le(payload, 6) / 1000.0
        self.data.battery_self_discharge_enabled = (payload[8] != 0xFF)
        self.data.battery_self_discharge_hours = int(payload[8])
        return {"user battery charge current (a)":self.data.battery_preferred_charge_current_a,
                "user cell storage voltage":self.data.cell_preferred_storage_voltage_v,
                "user cell max voltage":self.data.cell_preferred_max_voltage_v,
                "user self discharge enabled": bool(self.data.battery_self_discharge_enabled),
                "user self discharge time (h)": self.data.battery_self_discharge_hours}


    def decode_serialinfo(self, payload: bytes) -> dict:
        """
        Eexpected reply 0x85.
        Requires: len >= 11
        ManufacturerName is bytes starting at [11:], up to first 0x00 (or full len).
        """
        if len(payload) < 11 or payload[0] != 0x85:
            return None

        #if payload[1:11] != device_serial_10:
        #    return False

        self.data.serial_hex = payload[1:11].hex()

        rest = payload[11:]
        zero = rest.find(b"\x00")
        if zero == -1:
            zero = len(rest)
        name = rest[:zero].decode(errors="replace")
        self.data.manufacturer_name = name

        return {
            "serial":self.data.serial_hex,
            "manufacturer":self.data.manufacturer_name
        }


    def decode_state(self, payload: bytes) -> dict:
        """
        Expected reply 0x45.
        Conditions: len >= 6 and payload[1] == 0.
        numCell = payload[2] + 1
        Layout:
        [0]=0x45, [1]=status?, [2]=numCell-1
        then 2*numCell bytes of u16le cell mV
        then 1 byte temp (int8)
        """

        if len(payload) < 6 or payload[0] != 0x45 or payload[1] != 0:
            return None

        num_cell = int(payload[2]) + 1
        need = 3 + 1 + 2 * num_cell
        if len(payload) < need:
            return None

        voltages: List[float] = []
        idx = 3
        for _ in range(num_cell):
            mv = _u16le(payload, idx)
            voltages.append(mv / 1000.0)
            idx += 2

        self.data.cell_voltage_v = voltages
        self.data.temp_current_c = _i8(payload[idx])
        return {
            "cell voltages (V)":self.data.cell_voltage_v,
            "battery temp (c)":self.data.temp_current_c
        }


    def decode_factoryinfo(self, payload: bytes) -> dict:
        """
        Expected reply 0x89, min len 24
        Mapping:
        [1] battery type
        [2:4] cutoff mV
        [4:6] normal mV
        [6:8] charge max mV
        [8:10] storage default mV
        [10:14] capacity mAh (actually /1000 => Ah)
        [14:16] max charge C-rate*10? => /10 * Ah
        [16:18] max discharge /10 * Ah
        [18..21] temps int8
        [22] auto discharge flag
        [23] number of cells
        """
        if len(payload) < 24 or payload[0] != 0x89:
            return None

        self.data.battery_type = BatteryType[int(payload[1])]
        self.data.cell_discharge_cutoff_v = _u16le(payload, 2) / 1000.0
        self.data.cell_discharge_normal_v = _u16le(payload, 4) / 1000.0
        self.data.cell_charge_max_v = _u16le(payload, 6) / 1000.0
        self.data.cell_storage_default_v = _u16le(payload, 8) / 1000.0
        self.data.cell_capacity_ah = _u32le(payload, 10) / 1000.0

        # these depend on capacity
        self.data.battery_charge_max_current_a = (_u16le(payload, 14) / 10.0) * self.data.cell_capacity_ah
        self.data.battery_discharge_max_current_a = (_u16le(payload, 16) / 10.0) * self.data.cell_capacity_ah

        self.data.temp_use_low_c = _i8(payload[18])
        self.data.temp_use_high_c = _i8(payload[19])
        self.data.temp_storage_low_c = _i8(payload[20])
        self.data.temp_storage_high_c = _i8(payload[21])

        self.data.battery_has_auto_discharge = payload[22] > 0
        self.data.battery_number_of_cells = int(payload[23])
        return {
            "battery type":self.data.battery_type,
            "battery number of cells":self.data.battery_number_of_cells,
            "battery supports auto discharge":bool(self.data.battery_has_auto_discharge),
            "cell discharge cutoff voltage":self.data.cell_discharge_cutoff_v,
            "cell discharge normal voltage":self.data.cell_discharge_normal_v,
            "cell charge max voltage":self.data.cell_charge_max_v,
            "cell storage default voltage":self.data.cell_storage_default_v,
            "cell capacity (mAh)":self.data.cell_capacity_ah*1000,
            "battery max charge current (A)":self.data.battery_charge_max_current_a,
            "battery max discharge current (A)":self.data.battery_discharge_max_current_a,
            "low temp use (C)":self.data.temp_use_low_c,
            "high temp use (C)": self.data.temp_use_high_c,
            "low temp storage (C)":self.data.temp_storage_low_c,
            "high temp storage (C)":self.data.temp_storage_high_c,
        }
