#!/usr/bin/env python

# diskcontroller.py

import logging

from mycron_emu import tracing
from mycron_emu.devices.base import IODevice
from mycron_emu.disks import image as diskimage

from mycron_emu import tracing
from mycron_emu.tracing import regs_stack_str, regs_str, pc_disasm_str, PC_OFFSET_STD_IO


log = logging.getLogger("mycron.status")
io_log = logging.getLogger("mycron.io")
disk_log = logging.getLogger("mycron.disk")

io_trace = logging.getLogger("mycron.trace.io")
disk_trace = logging.getLogger("mycron.trace.disk")


class DiskDrive:
    # Based on dim-1030 info
    SECTOR_D_SIZE = 128
    MARK_HDR = 0xfe
    MARK_DATA = 0xfb

    ST_INACTIVE = "inactive"
    ST_HDR = "hdr"
    ST_DATA = "data"

    def __init__(self, dno, image):
        self.disk_img = image
        self.dno = dno
        self.track = 0
        self.sector = 1
        self.reset()

    def reset(self):
        # when reading, positions in header or data
        self.hpos = -1
        self.dpos = -2
        self.mark_seen_by_status = False
        self.set_at_state(True, False)   # Before first read, there is an address mark
        self.state = self.ST_INACTIVE

    def set_at_state(self, at_mark, at_crc):
        self.at_mark = at_mark
        self.at_crc = at_crc

    def start(self):
        """Called before first read of sector head or sector data"""
        self.mark_seen_by_status = False
        match self.state:
            case self.ST_INACTIVE:
                self.state = self.ST_HDR
                self.set_at_state(True, False)

            case self.ST_HDR:
                self.state = self.ST_DATA
                # NB: Have to set the mark here as read_hdr doesn't try to consume bytes, it only waits for status.
                # This is probably a side effect of the way the controller is observing the spinning disk and that they
                # didn't use the same scan_start function for both parts. Old assembly coding....
                self.set_at_state(True, False)

            case self.ST_DATA:
                self.state = self.ST_INACTIVE
                self.set_at_state(False, False)


    def set_position(self, track, sector):
        if not 0 <= track < diskimage.TRACKS:
            raise ValueError(f"invalid track: {track}")
        if not 1 <= sector <= diskimage.SECTORS:
            raise ValueError(f"invalid sector: {sector}")

        self.track = track
        self.sector = sector
        self.reset()

    def step_track(self, direction):
        """Move the head one track.

        direction > 0 moves inward; direction < 0 moves outward.
        """

        self.track = min(
            max(self.track + direction, 0),
            diskimage.TRACKS - 1,
        )

        # This preserves the controller's current simplified behavior.
        self.sector = 1
        self.reset()

    def advance_sector(self):
        """Advance the simulated rotational position by one sector."""

        self.sector += 1
        if self.sector > diskimage.SECTORS:
            self.sector = 1

        self.reset()

    def begin_read_phase(self):
        """Start the next header/data scanning phase.

        The existing command sequence progresses as:

            inactive -> header -> data -> next sector/header
        """

        if self.state == self.ST_DATA:
            self.advance_sector()

        self.start()


    def status_read(self):
        # Called by the controller when STATUS is read.
        # If the CPU has actually seen the address-mark bit, the next data read
        # may return the mark byte.
        if self.at_mark:
            self.mark_seen_by_status = True

    def done(self):
        return self.dpos > self.SECTOR_D_SIZE + 2

    def _read_header(self):
        # DIM-1001 does:
        #   IN DATA     ; dummy/prime read
        #   IN STATUS   ; sees AM
        #   IN DATA     ; expects FE
        #
        # DIM-1003 does:
        #   IN STATUS   ; sees AM
        #   IN DATA     ; expects FE
        #
        # Therefore, a data read before the AM status has been observed must not
        # consume the FE header mark.
        if self.hpos == -1 and self.at_mark and not self.mark_seen_by_status:
            return 0

        self.hpos += 1
        # corresponds to the header part of budr
        match self.hpos:
            case 0:
                self.set_at_state(True, False)
                return self.MARK_HDR
            case 1:
                self.set_at_state(False, False)
                return self.track
            case 2:
                return 0
            case 3:
                return self.sector
            case 4:
                # next to read is crc, so set it here
                self.set_at_state(False, True)
                return 0
            # crc checksum - report it as ok
            case 5:
                return 0
            case 6:
                return 0
        # emulate the 17 0 bytes after the crc
        self.set_at_state(False, False)
        if self.hpos == 16:
            # prepare for the next to be the mark
            self.set_at_state(True, False)
        return 0

    # After reading a header, if it doesn't want to read that data packet, it just runs another 41 49
    # and scans until it finds the header address mark. This means that the above state machine should notice
    # the data done state and skip to the next...
    def _read_data(self):
        # - The prom only checks status for every second byte read until it finds address mark.
        #   read+check, read, read+check...
        # - After seeing the addressmark flag, it reads a single byte that is later used as return value
        #   with some bit fiddling  (basically, it looks like 0xfb address mark would give a return value of 0)
        if self.dpos < -1:
            # read-data always reads one byte before the mark is set
            self.dpos += 1
            self.set_at_state(True, False)
            return 0

        if self.dpos == 0:
            # Make sure there is a freshly read sector here (to enable swapping disks)
            # TODO: could perhaps just move this to "start"
            disk_trace.debug("RD_SECTOR dsk=%d sector %02d.%02d.. %s",
                          self.dno,
                          self.track,
                          self.sector,
                          pc_disasm_str(PC_OFFSET_STD_IO))
            self.data = self.disk_img.read_sector(self.track, self.sector)

        if self.dpos < self.SECTOR_D_SIZE:
            if self.dpos < 0:
                ret = self.MARK_DATA
            else:
                self.set_at_state(False, False)
                ret = self.data[self.dpos]
            self.dpos += 1
            if self.dpos == self.SECTOR_D_SIZE:
                self.set_at_state(False, True)
                # disk_trace.debug(f"DSK_READ_SECT_COMPLETE {self.track:02}.{self.sector:02} : {self.data} {pc_disasm_str(PC_OFFSET_STD_IO)}")
            return ret

        # TODO: potential for infinite loop, but prom seems to start a new read after crc
        self.dpos += 1
        return 0

    def read(self):
        """Reads one byte."""
        # disk_trace.debug("DSK_read", self)
        match self.state:
            case self.ST_HDR:
                return self._read_header()
            case self.ST_DATA:
                return self._read_data()
        return 0

    def prepare_write(self):
        """Can't write to a byte buffer directly, so make this temp buffer first and we can apply it later"""
        self.wbuf = []

    def commit_write(self):
        if len(self.wbuf) != self.SECTOR_D_SIZE:
            raise RuntimeError(
                f"expected {self.SECTOR_D_SIZE} bytes, got {len(self.wbuf)}"
            )
        self.data = bytes(self.wbuf)
        # disk_trace.debug(f"DSK_WRITE_SECT_COMPLETE {self.track:02}.{self.sector:02} : {self.data}")
        self.disk_img.write_sector(self.track, self.sector, self.data, flush=True)

    def write_add_byte(self, bval):
        if len(self.wbuf) >= self.SECTOR_D_SIZE:
            raise RuntimeError("sector write buffer overflow")
        self.wbuf.append(bval)

    def __repr__(self):
        return f"Disk({self.dno}, {self.track:02}.{self.sector:02}, m={self.at_mark}, crc={self.at_crc}, {self.state}, {self.hpos}, {self.dpos})"



class IODiskController(IODevice):
    # Based on information from the DIM-1030 disc controller.
    O_CW1 = 0x88
    O_CW2 = 0x89
    O_DATA = 0x8a
    I_STATUS = 0x98
    I_DATA = 0x9a
    PORTS = (O_CW1, O_CW2, O_DATA, I_STATUS, I_DATA)
    pnames = {
        O_CW1 : "O_CW1",
        O_CW2 : "O_CW2",
        O_DATA : "O_DATA",
        I_STATUS : "I_STATUS",
        I_DATA : "I_DATA",
    }

    # from 1030 manual
    STATUS_FI  = 0x80    # 1 when FILE INOPERABLE is sent from drive
    STATUS_IXM = 0x40    # 1 when hard index mark is detected
    STATUS_CRC = 0x20    # 1 when CRC is ok
    STATUS_T0  = 0x10    # 1 when drive is at track Zero
    STATUS_A1  = 0x08    # Displays which addressmark has been
    STATUS_A0  = 0x04    # detected.
    STATUS_AM  = 0x02    # 1 when addressmark is dtected
    STATUS_DRY = 0x01    # 1 when drive is ready

    # not including the NC signals.
    # n : signal on low
    CW1_WR     = 0x80
    CW1_nLD    = 0x40
    CW1_WG     = 0x20
    CW1_nRAM   = 0x08
    CW1_CRC_ON = 0x04
    CW1_CRC_1  = 0x02
    CW1_CRC_0  = 0x01

    CW2_FI_RES  = 0x80
    CW2_LH      = 0x40
    CW2_S       = 0x20
    CW2_DIR_SEL = 0x10
    CW2_DRIVE_2 = 0x04
    CW2_DRIVE_1 = 0x02
    CW2_DRIVE_0 = 0x01

    RD_ST_OFF = "off"
    RD_ST_1   = "s1"   # prepare for run
    RD_ST_RUN = "run"  # running - can read data from sector

    WR_ST_OFF = "off"
    WR_ST_1   = "w1"
    WR_ST_2   = "w2"
    WR_ST_3   = "w3"
    WR_ST_4   = "w4"
    WR_ST_DATA = "wdata"  # next up is a stream of data on the input

    N_TRACKS = diskimage.TRACKS   # track 0..76
    N_SECTS = diskimage.SECTORS   # sector 1..26

    verbose = False

    def __init__(self, disk_imgs, **kvals):
        super().__init__(**kvals)
        self.drives = {i : DiskDrive(i, img) for i, img in enumerate(disk_imgs)}
        # a particular sequence of writes to a specific port will move the head out or in.
        # state can
        # - always be ok + whether track is 0
        # - mark if at the mark positions on each sector
        # - crc ok when passing the crc bytes.
        # data reads:
        # - fake head and data mark ids as well as sector and track numbers
        self.drive_no = 0
        # 0 if not spitting out a sector, 1 if warming up and 2 if last init command issued
        self.rd_state = self.RD_ST_OFF
        self.wr_state = self.WR_ST_OFF

    @property
    def drive(self):
        return self.drives[self.drive_no]

    def select_drive(self, drive_no):
        if drive_no not in self.drives:
            raise ValueError(f"drive {drive_no} is not available")

        self.drive_no = drive_no

    def pname(self, port):
        return self.pnames.get(port, "?? unk ??")

    def write_cw1(self, val):
        # disk_trace.debug(f"DSK_WRITE_CW1 f{val:#02x}")
        drive = self.drive
        match val:
            # NB: both read_hdr and read__data run the 41 49 sequence!
            case 0x41:
                self.rd_state = self.RD_ST_1
            case 0x49:
                self.rd_state = self.RD_ST_RUN
                drive.begin_read_phase()
            case 0xc9:
                # C9 is WR=1, /LD=1, WG=0,
                # see dsk_write_sector_data. A write sector starts with
                # C9 to CW1, then C0 to CW2
                # disk_trace.debug("TODO: Trying disk write (C9 to CW1)")
                self.wr_state = self.WR_ST_1
            case 0xa1:
                self.wr_state = self.WR_ST_3
                # disk_trace.debug("TODO: wr_state now", self.wr_state)
            case 0xa8:
                self.wr_state = self.WR_ST_4
                # disk_trace.debug("TODO: wr_state now", self.wr_state)
            case 0xa9:
                # Fetch current sector and prepare it for writing
                self.wr_state = self.WR_ST_DATA
                # disk_trace.debug("TODO: wr_state now", self.wr_state)
                drive.prepare_write()
            case 0xad:
                drive.commit_write()
                self.wr_state = self.WR_ST_OFF
                # disk_trace.debug("TODO: wr_state now", self.wr_state, 'Write done, so commiting and ignoring the rest')

    # When trying to set down head, it might try hex: 41, 51, 71, 51, then 01 when it gives up.
    # The last nibble 1 is for drive 1.
    # This is where the controller seelcts which disk to work with for the next commands.
    # CW2 writes. 50, 70, 50 -> step d1,  60, 40 ->  step d0
    def write_cw2(self, val):
        self.select_drive(val & 7)
        drive = self.drive
        # Simplified. Should really look for the sequence
        if (val & 0x70) == 0x70:
            # Next track
            drive.step_track(+1)
        elif (val & 0x60) == 0x60:
            # Prev track
            drive.step_track(-1)
        elif (val & 0xf0) == 0xc0:
            # TODO: strictly speaking, we could mask with 0xf0 since 0x8 is an NC bit
            self.wr_state = self.WR_ST_2

    def write(self, port, val):
        pre = self.pname(port)
        _org_rd_state = self.rd_state
        match port:
            # need two commands to start a read: 0x41 and then 0x49
            case self.O_CW1:
                self.write_cw1(val)
            case self.O_CW2:
                self.write_cw2(val)
            case self.O_DATA:
                if self.wr_state == self.WR_ST_DATA:
                    self.drive.write_add_byte(val)
            case _:
                disk_log.warning(f"DSK_WRITE_unknown: {port=:#x} {val=:#x} {pc_disasm_str(PC_OFFSET_STD_IO)}")
        if self.verbose:
            drive = self.drive
            st = f"DSK_OUT {pre:10} [{port:02x}] = {val:02x}.  rdstate {_org_rd_state}->"
            st += f"{self.rd_state} wstate {self.wr_state} T={drive.track} S={drive.sector}  {self.drive_no}"
            tracing.write(st, include_stack=True, pc_offset=PC_OFFSET_STD_IO)

    # A few simplifications compared to a real drive:
    # - instant track move and time to next pos
    # - always ready
    def _read_status(self):
        drive = self.drive
        val = self.STATUS_DRY

        if drive.track == 0:
            val |= self.STATUS_T0

        if drive.at_mark:
            val |= self.STATUS_AM
            drive.status_read()

        if drive.at_crc:
            val |= self.STATUS_CRC

        return val

    def _read_data(self):
        if self.rd_state != self.RD_ST_RUN:
            return 0

        if self.drive.done():
            return 0

        return self.drive.read()

    def read(self, port):
        pre = self.pname(port)
        val = 0
        match port:
            case self.I_STATUS:
                val = self._read_status()
            case self.I_DATA:
                val = self._read_data()
        if self.verbose:
            tracing.write(f"DSK_INP {pre:10} [{port:02x}] : {hex(val):6}",
                            include_stack=True, pc_offset=PC_OFFSET_STD_IO)
        return val


