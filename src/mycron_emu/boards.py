#!/usr/bin/env python

import logging
from pathlib import Path

from mycron_emu.devices.base import IOPrint
from mycron_emu.devices.serial import IOSerial, IOSerialDim1001
from mycron_emu.devices.disk import IODiskController
from mycron_emu.devices.simple import IOPar, IOCTC, IOP14
from mycron_emu.prom import PromRegion


log = logging.getLogger("mycron.status")

class Board:
    def __init__(self, config, d_imgs, console_output):
        self.config = config
        self.d_imgs = d_imgs
        self.console_output = console_output
        # Set up I/O address space
        self.io_ports = {}
        self.unknown_io = IOPrint()
        self.proms = []

    def set_proms_enabled(self, enabled):
        log.info("-- NB: Board setting proms to %s", "enabled" if enabled else "disabled")
        for prom in self.proms:
            prom.set_enabled(enabled)

    def io_in(self, port):
        return self.io_ports.get(port, self.unknown_io).read(port)

    def io_out(self, port, val):
        self.io_ports.get(port, self.unknown_io).write(port, val)


class Board1001(Board):
    def __init__(self, config, d_imgs, console_output):
        super().__init__(config, d_imgs, console_output)
        self.sport = IOSerialDim1001(console_output)
        self.sport.register_ports(self.io_ports)

        # Dim 1001 uses only one prom chip.
        self.proms.append(PromRegion(Path(config['run-dir'], config['prom0']), 0x0))

        self.dsk = IODiskController(d_imgs)
        self.dsk.register_ports(self.io_ports)


class Board1003(Board):
    def __init__(self, config, d_imgs, console_output):
        super().__init__(config, d_imgs, console_output)
        self.sport = IOSerial(console_output)

        self.sport.register_ports(self.io_ports)
        self.pport = IOPar()
        self.pport.register_ports(self.io_ports)

        self.ctcdev = IOCTC()
        self.ctcdev.register_ports(self.io_ports)

        self.iop14 = IOP14(self)
        self.iop14.register_ports(self.io_ports)

        # Dim 1003 uses two proms. The second one is mapped at 0x1000, leaving a region of RAM between the chips.
        self.proms.append(PromRegion(Path(config['run-dir'], config['prom0']), 0x0))
        self.proms.append(PromRegion(Path(config['run-dir'], config['prom1']), 0x1000))

        self.dsk = IODiskController(d_imgs)
        self.dsk.register_ports(self.io_ports)


BOARD_TYPES = {
    'dim-1001' : Board1001,
    'dim-1003' : Board1003
}
