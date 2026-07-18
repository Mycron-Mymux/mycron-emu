#!/usr/bin/env python

"""The DIM-1003 card has a Z80 PIO chip that is used for some part of the PROM.
The assumption here is that we can use a simplified PIO to emulate what
the prom is trying to do with it.

One important point is redirec_print in the PROM that reads the PIO_B data port
to determine whether to
- val = 0xff : send character to SIO_A
- (val & 0x20) == 0 : send character using pio protocol at 1512
- (val & 0x10) == 0 : send character using pio protocol at 14cb
- otherwise fail

The rest of the PROM functions appear to test the PIO for ready status,
write some control values and then try to print using the redirect_print
function. It should be safe to just return 0xff and force everything to
use the SIO for simplicity. We can attach stream outputs to that serial port.
"""

from mycron_emu.devices.base import IODevice
from mycron_emu import tracing


class Z80PioPrinter(IODevice):
    """This only provides enough functionality to emulate the behaviour of the PIO
    as used in the PROMs we have now for the DIM 1003 cards. See module
    comments for more information.
    """
    A_DATA = 0x04   # Port A data
    B_DATA = 0x05   # Port B data
    A_CTRL = 0x06   # Port A control
    B_CTRL = 0x07   # Port B control
    PORTS = (A_DATA, B_DATA, A_CTRL, B_CTRL)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.a_data = 0xff
        self.a_ctrl = 0
        self.b_ctrl = 0

    def read(self, port):
        tracing.write(f"IOPAR INP [{port:02x}]", pc_offset=tracing.PC_OFFSET_STD_IO)
        if port == self.B_DATA:
            return 0xff          # Select SIO-A in redirect_print.
        if port == self.A_DATA:
            return self.a_data   # Optional harmless latch readback.
        return 0xff              # Control-port reads are not used by this PROM.

    def write(self, port, value):
        tracing.write(f"IOPAR OUT [{port:02x}] = {value:02x}", pc_offset=tracing.PC_OFFSET_STD_IO)
        value &= 0xff
        if port == self.A_DATA:
            self.a_data = value
        elif port == self.A_CTRL:
            self.a_ctrl = value
        elif port == self.B_CTRL:
            self.b_ctrl = value
