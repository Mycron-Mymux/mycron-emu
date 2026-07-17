#!/usr/bin/env python

# tests/test_disk.py

import diskimage
from diskcontroller import DiskDrive, IODiskController


def make_images(count=8):
    return [
        diskimage.DiskImage.empty_image("drive-{number}", read_from_file=False)
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

    for _ in range(diskimage.TRACKS + 10):
        drive.step_track(+1)

    assert drive.track == diskimage.TRACKS - 1
    
def test_sector_wraps_to_one(disk_controller):
    drive = disk_controller.drive
    drive.sector = diskimage.SECTORS

    drive.advance_sector()

    assert drive.sector == 1
