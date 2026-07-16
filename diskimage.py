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

import os
import argparse
from enum import Enum, auto

TRACKS=77
SECTORS=26
SECTOR_SIZE=128
IMG_LEN = TRACKS * SECTORS * SECTOR_SIZE

# prog at 0x3000
prog_simple = bytes([
    0x01,    # ld bc, nn
    0x00,    # 0x100 is the mycrop prompt
    0x01,
    0xcd,    # call ttcon
    0x4d,
    0x04,

    0x01,    # ld bc, nn
    0x1e,    # 0x11e has batch error text
    0x01,
    0xcd,    # call ttcon
    0x4d,
    0x04,

    0x01,    # ld bc, nn
    0x00,    # program text has batch error text
    0x40,
    0xcd,    # call ttclf... no still ttcon
    0x4d,
    0x04,

    0xcd,    # call monitor entry: 0x40. Just reset the entire thing: 0x00
    0x00,
    0x00,

    # 0xc9,    # ret - should return to monitor
])
prog_simple += bytes(128 - len(prog_simple))
# text at 0x4000
prog_simple_text = bytes("\n\rBEWARE OF THE LEOPARD.\n\r", encoding="ascii")
prog_simple_text += bytes(128 - len(prog_simple_text))

assert len(prog_simple) == 128
assert len(prog_simple_text) == 128


def hexdump_data(data):
    print("     0  1  2  3  4  5  6  7   8  9  a  b  c  d  e  f    012345678 9abcdef")
    while len(data) > 0:
        cur = data[:16]
        buf = "   "
        buf2 = "  |"
        for i, c in enumerate(cur):
            buf += f" {c:02x}"
            c = chr(c)
            buf2 += c if c.isprintable() else '.'
            if i == 7:
                buf += ' '
                buf2 += ' '
        print(buf, buf2)
        data = data[16:]


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
    def empty_image(cls, name="NA"):
        return cls(name, bytes(IMG_LEN))

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
                print("Flush write without any existing image. Flushing entire image from memory.")
                with open(self.fname, mode="wb") as f:
                    f.write(self.barr)


    def all_sectors(self):
        for trk in range(TRACKS):
            for sec in range(1, SECTORS + 1):
                yield (trk, sec, self.read_sector(trk, sec))


# ------------------------------------------------------

# bytes:
# 1-8 program name (padded with spaces at the end)
# 9 - first track #
# 10 - first sector #
# 11 - high order byte of load addr seg 1
# 12 - low order byte of load addr seg 1
# 13 - number of sectors seg 1
# 14 - high order byte of load addr seg 2
# 15 - low order byte of load addr seg 2
# 16 - number of sectors seg 2
def prog_make_dir_entry(fname, track, sector, addr1, nsec1, addr2, nsec2):
    """Returns one entry in a directory"""
    fname = f"{fname:8}"   # TODO: need to check if the padding is different in the prom's buffer.
    assert len(fname) == 8
    ret = bytes(fname, encoding="utf8") + bytes([
        track,
        sector,
        (addr1 >> 8) & 0xff,
        addr1 & 0xff,
        nsec1,
        (addr2 >> 8) & 0xff,
        addr2 & 0xff,
        nsec2
        ])
    assert len(ret) == 16
    return ret


def prog_make_8_dirents(*ents):
    """8 directory entries in a sector"""
    ents = list(ents) + [b' ' * 8 + bytes(8) for _ in range(len(ents), 8)]
    directory = b''.join(ents)
    assert len(directory) == 128
    return directory


# positions starting with 1 (not 0) in the docs
# see page 6-38 for more details
# - 1-4   is HDR1
# - 5     is space
# - 6-13  data set name (initialized with DATA, user defined)
# - 14-24 reserved or blank
# - 25-27 logical record length (max 128)  must be 080 on 3742 or > 000, less than 128 on 3741 or on the 3742 with 128 feature (init 080)
# - 28    reserved or blank
# - 29-33 beginning of extent (BOE) - first sector addr. 29+30 track number, 31 must be 0, 32+33 sector number.
# - 34    reserved or blank
# - 35-39 end of extent (last sector for data set)
# - 40    reserved or blank
# - 41    bypass data set - if B, 3747 will ignore, if blank - processed
# - 42    accessibility must be blank
# - 43    data set write protect P if protected, else blank
# - 44    reserved or blank
# - 45    multivolume inidicator.  blank=not multivol, C continued on another diskette, L this is the last diskette.
# - 46-72 reserved or blank
# - 73    verify mark: V = has been verified
# - 74    end of Data. indicates address of next unused sector of data set (init  was 01001 sect 8, 74001 on sect 9-26)
# - 80    reserved or blank
# TODO: I'm not verifying all of the above.
def data_make_dir_entry(fname, boe_t, boe_s, eoe_t, eoe_s, eod_t, eod_s):
    """
    NB: in practice, one filename + info is stored in one sector, so this returns a full sector.
    - beo is first track / sector of file
    - eoe is last sector in reserved space for file
    - eod is first free sector in the reserved space
    """
    fname = f"{fname:8}"
    entry = f"HDR1 {fname}           128 {boe_t:02}0{boe_s:02} {eoe_t:02}0{eoe_s:02}"
    entry += f"{' ':8}000000{' ':13}999999  "
    entry += f"{eod_t:02}0{eod_s:02} "

    # print(len(entry), repr(entry))
    # hexdump_data(bytes(entry, encoding="ascii"))
    assert len(entry) == 80
    sec = bytes(entry + (" " * 48), encoding="ascii") # pad to 128 bytes
    assert len(sec) == 128
    return sec

def empty_phdr_sec():
    return bytes('\x40' * 80 + "\x00" * 48, encoding="ascii")

def empty_dhdr_sec():
    return bytes(" " * 80 + "\x00" * 48, encoding="ascii")

def data_make_empty_dir_entry():
    return bytes([0x44] + [0xff] * 127)


def prog_make_test_img(name="NA"):
    VOLID="PROGDUMMY"
    vol_sec = bytearray(bytes(VOLID + " " * (SECTOR_SIZE - len(VOLID)), encoding="ascii"))
    img = DiskImage.empty_image(name)
    for sno in [1, 2, 3, 4, 6]:
        img.write_sector(0, sno, empty_phdr_sec())
    ehdr = bytearray(empty_phdr_sec())
    ehdr[:5] = b"ERMAP"    # TODO:  c5 d9 d4 c1 d7  instead of ERMAP?
    img.write_sector(0, 5, ehdr)
    img.write_sector(0, 7, vol_sec)
    img.write_sector(0, 8, prog_make_8_dirents(prog_make_dir_entry('FOO', 2, 1, 0x3000, 1, 0x4000, 1)))
    for hno in range(9, SECTORS+1):
        img.write_sector(0, hno, prog_make_8_dirents())
    img.write_sector(2, 1, prog_simple)
    img.write_sector(2, 2, prog_simple_text)
    return img


def data_make_test_img(name="NA"):
    """Creates a data volum1 with two files on it. The files should have enough room to
    use TMP1 as a target listing device and TMP2 as output file for compiling mymux.
    NB: the files have to have enough room preallocated - PLZ does not grow the files
    beyound the preallocated extents.
    """
    VOLID = "VOL1IBMASC"
    vol_sec = bytearray(bytes(VOLID + " " * (SECTOR_SIZE - len(VOLID)), encoding="ascii"))
    vol_sec[79] = ord('W')
    img = DiskImage.empty_image(name)
    for sno in [1, 2, 3, 4, 6]:
        img.write_sector(0, sno, empty_dhdr_sec())
    ehdr = bytearray(empty_dhdr_sec())
    ehdr[:5] = b"ERMAP"
    img.write_sector(0, 5, ehdr)
    img.write_sector(0, 7, vol_sec)
    # Making enough room to compile mymux with listing at tmp1 and output at tmp2
    img.write_sector(0, 8, data_make_dir_entry('TMP1',  1, 1, 40, 26,  1, 2))
    img.write_sector(0, 9, data_make_dir_entry('TMP2', 41, 1, 60, 26, 41, 2))
    for hno in range(10, SECTORS+1):
        img.write_sector(0, hno, data_make_empty_dir_entry())
    img.write_sector(1, 1, bytes(b'EMPTY' + b'\x00' + b' ' * 122))
    return img


def test():
    def test_read(img, sectors):
        for tno, sno in sectors:
            s = img.read_sector(tno, sno)
            print(f"---{img.fname} sector {tno}, {sno}----")
            hexdump_data(s)

    print("tst conv", (7, 8), ts_to_secno(7, 8), secno_to_ts(ts_to_secno(7, 8)))
    if 0:
        img = DiskImage.from_file("diskimg/mm-03.img")
        test_read(img, [(0, 1), (0, 26), (0, 7), (0, 8), (1, 1)])

    if 1:
        img2 = prog_make_test_img('/tmp/tst-prog.img')
        test_read(img2, [(0, 1), (0, 7), (0, 8), (2, 1), (2, 2)])
        img2.save()

    if 1:
        img3 = data_make_test_img('/tmp/tst-data.img')
        test_read(img3, [(0, 1), (0, 7), (0, 8), (0, 9), (1, 1), (1, 2)])
        img3.save()

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-mp", nargs=1, default=[],
                        help="Create a Mycron prog disk image and store it to he given file name")
    parser.add_argument("-md", nargs=1, default=[],
                        help="Create a Mycron data disk image and store it to he given file name")
    parser.add_argument("-me", nargs=1, default=[],
                        help="Create an emptydisk image and store it to he given file name")
    parser.add_argument("-t", action="store_true",
                        help="Run some simple tests")
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    for fn in args.mp:
        print("Making prog test image", fn)
        prog_make_test_img(fn).save()
    for fn in args.md:
        print("Making data test image", fn)
        data_make_test_img(fn).save()
    for fn in args.me:
        print("Making empty image", fn)
        DiskImage.empty_image(fn).save()
    if args.t:
        test()
