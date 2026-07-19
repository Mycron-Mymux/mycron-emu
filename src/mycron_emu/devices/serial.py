#!/usr/bin/env python

"""The two main board types I have available use two different UARTS.

The DIM-1001 (i8080 based) card uses a 40-pin TMS6011NC (extra marking: AP
7714 on card 3191). It is wired to io port 0 (data) and 1 (control). Baud
rates etc are probably wired to control pins instead of using control
registers.

The DIM-1003 (Z80 based) card uses Z80 SIO chips. These have two serial
ports: port A and port B. The monitor PROMs use port B for the console.
On the DIO-1003:
- port A is mapped to io addr 0xd (control) and 0xc (data)
- port B is mapped to io addr 0xf (control) and 0xe (data)

With the exception of WR0, each register write requires two bytes:
- one byte with D2-D0 selecting a register
- the next byte with the value for the register
z80 peripherals page 293 (of 330)

For the purpose of this emulator, the same logic can be used to emulate
both UART types as they use the same polling logic to inspect state (RX/TX
ready) and read/transmit data.

Future updates/TODO:
- Mymux needs a 4-port serial card for the multicomputer code (maybe two SIOs?).
"""


from collections import deque
import logging

from mycron_emu.devices.base import IODevice
from mycron_emu import tracing

io_log = logging.getLogger("mycron.io")


class SerialPort(IODevice):
    """Generic serial port providing the necessary functionality for the
    DIM-1001 and DIM-1003 ports. It only emulates the actual ports and
    is intended to be used for compositioning by the other classes in this file.
    It is based on the observed use from the DIM-1003 proms for the Z80 SIO.
    """
    def __init__(self, *, data_port, control_port, output=None, name="serial", **kwargs):
        super().__init__(**kwargs)
        self.data_port = data_port
        self.control_port = control_port
        self.PORTS = (data_port, control_port)

        self.name = name
        self.output = output
        self.input_bytes = deque()
        self.selected_wr = 0
        self.write_registers = [0] * 8

    def queue_bytes(self, data):
        """Queue bytes delivered"""
        if isinstance(data, str):
            data = data.encode("ascii")
        self.input_bytes.extend(data)

    def rx_ready(self):
        return bool(self.input_bytes)

    def tx_ready(self):
        return True

    def _read_control(self):
        # For polling mode:
        # bit 2 : just indicate that it's always ready to transmit more (4)
        # bit 0 : whether there is data queued (1 or 0).
        return 4 | bool(self.input_bytes)

    def _read_data(self):
        if self.input_bytes:
            return self.input_bytes.popleft()
        io_log.warning(f"read from empty serial port {self.data_port}. Returning 0 {tracing.pc_disasm_str()}")
        return 0

    def read(self, port):
        if port == self.control_port:
            return self._read_control()
        if port == self.data_port:
            return self._read_data()
        io_log.warning("%s: read from unknown port %#04x; returning 0", self.name, port)
        return 0

    def _write_control(self, value):
        """Emulate write to console serial port's ctrl register"""
        # A bit clumsy as a first take on the register write sequences
        # This is incomplete and is just there to detect if something interesting
        # is set up on the serial channel.
        if self.selected_wr:
            self.write_registers[self.selected_wr] = value
            self.selected_wr = 0
            return
        register = value & 0x07
        if register:
            self.selected_wr = register
        else:
            # WR0 command. The PROM commonly writes zero before
            # polling status; retaining it is sufficient initially.
            self.write_registers[0] = value

    def _write_data(self, val):
        if self.output is not None:
            self.output(bytes([val]))

    def write(self, port, val):
        """Emulate a write to a serial port"""
        match port:
            case self.data_port:
                self._write_data(val)
            case self.control_port:
                self._write_control(val)


class SerialDIM1001:
    """The serial port for the console on DIM 1001 uses a different UART (TMS6011NC)
    and different ports (0 and 1).
    The i8080 monitor prom doesn't try to write to the control registers through the
    serial port's control io port, so it appears safe re-use the same port
    for the 1001 as well.
    See module comments for more information.
    """
    def __init__(self, *, output=None):
        self.console = SerialPort(
            data_port = 0x00,
            control_port = 0x01,
            output = output,
            name="TMS60011 console for DIM1001")

    def register_ports(self, port_registry):
        self.console.register_ports(port_registry)

    def queue_bytes(self, data):
        self.console.queue_bytes(data)


class SerialDIM1003:
    """Serial ports on the DIM-1003, using a Z80 SIO.
    Port B is used as the main console.
    Port A appears to be used as an aux output, selected depending on
    the data input from what is connected to the PIO.
    See module comments for more information.
    """
    def __init__(self, *, output=None, aux_output=None):
        # Console uses port B
        self.console = SerialPort(
            data_port = 0x0e,
            control_port = 0x0f,
            output = output,
            name="Z80 SIO console")
        # aux (used by redirect_print and MSYSTEM boot) uses port A
        self.aux = SerialPort(
            data_port = 0x0c,
            control_port = 0x0d,
            output = aux_output,
            name="Z80 SIO aux")

    def register_ports(self, port_registry):
        self.console.register_ports(port_registry)
        self.aux.register_ports(port_registry)

    def queue_bytes(self, data):
        self.console.queue_bytes(data)
