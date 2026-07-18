#!/usr/bin/env python3

"""
This deals with raw Mycron disk images that have the following layouts:
- single sided disks
- 77 tracks   (numbered 0 to 76)
- 26 sectors  (numbered 1 to 26)
- 128 bytes per sector
Which is a total of 77 * 26 * 128 = 256256 bytes


- bytearray(bytes) - > bytearrays are not read only
"""

import argparse
import logging


TRACKS=77
SECTORS=26
SECTOR_SIZE=128
IMG_LEN = TRACKS * SECTORS * SECTOR_SIZE

log_dimg = logging.getLogger("mycron.diskimg")

def ts_to_secno(track, sector):
    return track * SECTORS + sector - 1


def secno_to_ts(secno):
    track = secno // SECTORS
    sector = (secno % SECTORS) + 1
    return track, sector


class DiskImage:
    def __init__(self, fname, data, read_from_file=True):
        self.fname = fname
        self.barr = bytearray(data)
        self._read_from_file = read_from_file

    @classmethod
    def empty_image(cls, name="NA", read_from_file=True):
        return cls(name, bytes(IMG_LEN), read_from_file=read_from_file)

    @classmethod
    def from_file(cls, fname):
        data = open(fname, 'rb').read()
        if len(data) != IMG_LEN:
            raise ValueError(f"{fname}: expected {IMG_LEN} bytes, got {len(data)}")
        return cls(fname, data)

    def save(self, fname=None):
        if fname is None:
            fname = self.fname
        with open(fname, 'wb') as f:
            f.write(self.barr)

    def _get_offset(self, track, sector):
        if not 0 <= track < TRACKS:
            raise ValueError(f"invalid track: {track}")
        if not 1 <= sector <= SECTORS:
            raise ValueError(f"invalid sector: {sector}")
        return ((track * SECTORS) + (sector - 1)) * SECTOR_SIZE

    def read_sector(self, track, sector):
        """NB: sectors are numbered 1..26.
        Uses "read-through" semantics if the disk image exists
        (ignores buffer and reads directly from the file) to support
        floopy change by simply overwriting or replacing the
        file.
        """
        offset = self._get_offset(track, sector)
        if self._read_from_file:
            try:
                with open(self.fname, 'rb') as f:
                    f.seek(offset)
                    data = f.read(SECTOR_SIZE)
            except FileNotFoundError:
                pass
            else:
                # pad zero
                data = data.ljust(SECTOR_SIZE, b"\0")
                # update buffer copy of the image
                self.barr[offset:offset + SECTOR_SIZE] = data
        return bytes(self.barr[offset:offset + SECTOR_SIZE])

    def write_sector(self, track, sector, data, flush=False):
        """NB: sectors are numbered 1..26
        Uses "write-through" semantics.
        If the file does not exist, creates the file and dumps the entire
        disk image to the file.
        """
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("sector data must be bytes or bytearray")
        if len(data) != SECTOR_SIZE:
            raise ValueError(f"sector data must contain {SECTOR_SIZE} bytes, got {len(data)}")

        offset = self._get_offset(track, sector)
        self.barr[offset:offset + SECTOR_SIZE] = data
        if flush:
            try:
                # print(f"DSK_WRITE_FLUSH {track:02}.{sector:02} {data}")
                with open(self.fname, 'rb+') as f:
                    f.seek(offset)
                    f.write(data)
            except FileNotFoundError:
                log_dimg.warning("Flush write without any existing image. Flushing entire image from memory.")
                with open(self.fname, mode="wb") as f:
                    f.write(self.barr)


    def all_sectors(self):
        for trk in range(TRACKS):
            for sec in range(1, SECTORS + 1):
                yield (trk, sec, self.read_sector(trk, sec))



