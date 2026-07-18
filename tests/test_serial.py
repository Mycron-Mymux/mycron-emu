#!/usr/bin/env python

import io

from mycron_emu.devices.serial import IOSerial


def test_empty_serial_read_returns_zero():
    serial = IOSerial(io.BytesIO())

    assert serial.read(serial.BD) == 0



def test_serial_queued_bytes_are_returned_in_order():
    serial = IOSerial(io.BytesIO())
    serial.queue_bytes(b"\x00A\r")

    assert serial.read(serial.BC) & 1
    assert serial.read(serial.BD) == 0
    assert serial.read(serial.BD) == ord("A")
    assert serial.read(serial.BD) == ord("\r")
    assert not serial.read(serial.BC) & 1
