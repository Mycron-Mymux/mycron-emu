#!/usr/bin/env python3

from mycron_emu import z80


class PromRegion:
    """Emulates PROM chips as well as the memory covering the same address space when PROM chips are turned off.
    Some Z80 programs (like the CPM loaders) turn off the PROM region, leaving RAM to cover the same region.
    The tricky thing is that some functions can flip the PROM back on and off again to temporarily run
    support functions from the PROM.
    This class handles the logic of emulating the PROM flipping by keeping track of both RAM and PROM data
    and updating the memory view depending on the current PROM configuration.
    """
    def __init__(self, fname, start_addr):
        self.fname = fname
        self.start_addr = start_addr
        self.raw_data = open(fname, 'rb').read()
        self.ram_vals = bytes(len(self.raw_data))
        self.is_on = False
        self.turn_on()

    def __len__(self):
        return len(self.raw_data)

    def _write_region(self, data, backup=False, protect=0):
        """Writes data to the region, optionally storing the old values to self.ram_vals"""
        mem = z80.raw_memory()
        rng = slice(self.start_addr, self.start_addr + len(self))
        if backup:
            self.ram_vals = bytes(mem[rng])
        if data is not None:
            # Only update memory and set protection if data is there. Used by save_ram
            mem[rng] = data
            z80.mem_set_prot(self.start_addr, self.start_addr + len(self), protect)

    def set_enabled(self, enabled):
        enabled = bool(enabled)

        if enabled == self.is_on:
            # No change
            return

        if enabled:
            # RAM is visible. Save it before replacing it with PROM.
            self._write_region(self.raw_data, backup=True, protect=1)
        else:
            # Restore the saved RAM.
            self._write_region(self.ram_vals, backup=False, protect=0)

        self.is_on = enabled

    def turn_on(self):
        self.set_enabled(True)

    def turn_off(self):
        self.set_enabled(False)

