#!/usr/bin/env python

from mycron_emu.disks.image import DiskImage

def test_empty_file_backed_image_detects_later_inserted_disk(tmp_path):
    path = tmp_path / "disk-03.img"

    image = DiskImage.empty_image(
        path,
        read_from_file=True,
    )

    inserted = DiskImage.empty_image(path)
    inserted.write_sector(0, 1, bytes([0x5a]) * 128)
    inserted.save()

    assert image.read_sector(0, 1) == bytes([0x5a]) * 128
