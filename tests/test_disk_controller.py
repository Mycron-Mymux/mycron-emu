#!/usr/bin/env python

# tests/test_disk.py
from mycron_emu.devices.disk import DiskDrive, IODiskController
from mycron_emu.devices.serial import SerialPort
from mycron_emu.disks.image import DiskImage, TRACKS, SECTORS

def make_images(count=8):
    return [
        DiskImage.empty_image(f"drive-{number}", read_from_file=False)
        for number in range(count)
    ]


def make_controller():
    return IODiskController(make_images())


def test_drives_keep_independent_track_positions():
    controller = make_controller()

    controller.select_drive(0)
    controller.drive.step_track(+1)
    controller.drive.step_track(+1)

    controller.select_drive(1)
    assert controller.drive.track == 0

    controller.drive.step_track(+1)
    assert controller.drive.track == 1

    controller.select_drive(0)
    assert controller.drive.track == 2


def test_track_zero_status_uses_selected_drive():
    controller = make_controller()

    controller.select_drive(0)
    assert controller._read_status() & controller.STATUS_T0

    controller.drive.step_track(+1)
    assert not (controller._read_status() & controller.STATUS_T0)

    controller.select_drive(1)
    assert controller._read_status() & controller.STATUS_T0

def test_drives_keep_independent_sector_positions():
    controller = make_controller()

    controller.select_drive(0)
    controller.drive.advance_sector()
    assert controller.drive.sector == 2

    controller.select_drive(1)
    assert controller.drive.sector == 1

    controller.select_drive(0)
    assert controller.drive.sector == 2

def test_drive_track_is_clamped():
    controller = make_controller()
    drive = controller.drive

    drive.step_track(-1)
    assert drive.track == 0

    for _ in range(TRACKS + 10):
        drive.step_track(+1)

    assert drive.track == TRACKS - 1

def test_sector_wraps_to_one():
    controller = make_controller()
    drive = controller.drive
    drive.sector = SECTORS

    drive.advance_sector()

    assert drive.sector == 1

def test_read_phase_advances_to_next_sector_after_data_phase():
    controller = make_controller()
    drive = controller.drive

    assert drive.state == drive.ST_INACTIVE
    assert drive.sector == 1

    drive.begin_read_phase()
    assert drive.state == drive.ST_HDR
    assert drive.sector == 1

    drive.begin_read_phase()
    assert drive.state == drive.ST_DATA
    assert drive.sector == 1

    drive.begin_read_phase()
    assert drive.state == drive.ST_HDR
    assert drive.sector == 2

def test_serial_output_is_sent_to_sink():
    output = []
    serial = SerialPort(
        data_port=0x01,
        control_port=0x02,
        output=output.append,
    )

    serial.write(serial.data_port, 0x41)

    assert output == [b"A"]
