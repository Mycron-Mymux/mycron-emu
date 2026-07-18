#!/usr/bin/env python

import io

from mycron_emu.devices.serial import SerialPort


def test_empty_serial_read_returns_zero():
    serial = SerialPort(data_port=0x1, control_port=0x2, output=io.BytesIO())

    assert serial.read(serial.data_port) == 0


def test_serial_queued_bytes_are_returned_in_order():
    serial = SerialPort(data_port=0x1, control_port=0x2, output=io.BytesIO())
    serial.queue_bytes(b"\x00A\r")

    assert serial.read(serial.control_port) & 1
    assert serial.read(serial.data_port) == 0
    assert serial.read(serial.data_port) == ord("A")
    assert serial.read(serial.data_port) == ord("\r")
    assert not serial.read(serial.control_port) & 1
