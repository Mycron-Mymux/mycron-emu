# tests/conftest.py

import pytest

from mycron_emu.devices.disk import IODiskController
from mycron_emu.disks import image as diskimage


@pytest.fixture
def disk_images():
    return [
        diskimage.DiskImage.empty_image(
            f"drive-{number}",
            read_from_file=False,
        )
        for number in range(8)
    ]


@pytest.fixture
def disk_controller(disk_images):
    return IODiskController(disk_images)
