#!/usr/bin/env python


from mycron_emu import tracing


class IODevice:
    PORTS = ()
    def __init__(self, default_rval=0):
        self.default_rval = default_rval

    def write(self, port, val):
        pass

    def read(self, port):
        return self.default_rval

    def register_ports(self, port_registry, ports=None):
        """Register this io device to the port list in the class or the provided port list"""
        for p in (self.PORTS if ports is None else ports):
            port_registry[p] = self


class IOIgnore(IODevice):
    """Ignores read or write requests, always returning a 0 if
    a write is requested"""


class IOPrint(IODevice):
    """Ignores read or write requests, always returning a 0 if
    a write is requested.
    Additionally prints OUT and INP to the console for debugging."""
    def write(self, port, val):
        tracing.write(f"OUT [{port:02x}] = {val:02x}", pc_offset=tracing.PC_OFFSET_STD_IO)

    def read(self, port):
        tracing.write(f"INP [{port:02x}] : 0x{self.default_rval:x}", pc_offset=tracing.PC_OFFSET_STD_IO)
        return self.default_rval
