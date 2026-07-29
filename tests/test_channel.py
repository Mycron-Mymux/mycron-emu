#!/usr/bin/env python

import pytest
from mycron_emu.channels import Channel
from mycron_emu.devices.serial import SerialPort


def test_channel_sends_to_all_listeners():
    first = []
    second = []
    channel = Channel[bytes]("test")

    channel.subscribe(first.append)
    channel.subscribe(second.append)

    channel.send(b"\x00A")

    assert first == [b"\x00A"]
    assert second == [b"\x00A"]

def test_channel_unsubscribe():
    output = []
    channel = Channel[bytes]("test")

    unsubscribe = channel.subscribe(output.append)
    channel.send(b"a")
    unsubscribe()
    channel.send(b"b")

    assert output == [b"a"]


def test_listener_can_unsubscribe_during_delivery():
    output = []
    channel = Channel[str]("test")

    unsubscribe = None

    def listener(message):
        output.append(message)
        unsubscribe()

    unsubscribe = channel.subscribe(listener)

    channel.send("first")
    channel.send("second")

    assert output == ["first"]

def test_console_input_channel_feeds_serial_port():
    channel = Channel[bytes]("console.input")
    serial = SerialPort(
        data_port=1,
        control_port=2,
    )

    channel.subscribe(serial.queue_bytes)
    channel.send(b"\x00A")

    assert serial.read(1) == 0
    assert serial.read(1) == ord("A")

def test_serial_output_reaches_all_channel_listeners():
    channel = Channel[bytes]("console.output")
    first = []
    second = []

    channel.subscribe(first.append)
    channel.subscribe(second.append)

    serial = SerialPort(
        data_port=1,
        control_port=2,
        output=channel.send,
    )

    serial.write(1, ord("A"))

    assert first == [b"A"]
    assert second == [b"A"]
