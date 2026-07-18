#!/usr/bin/env python3

"""
This uses Python to:
- control the emulation
- emulate some I/O devices (serial port and disk)
- scripted input (for easy debugging, testing and tracing)

TODO:
- Cleanups (this is a figure-it-out-hack)
- copy to pty for read/write (not just terminal and scripted)
- move debug output to logging, which can be to a (optionally specified) file.
- add serial ports for the serial card mymux is using.
- Check timer interrupt. Mymux etc may need it.
- better org and separation between cpu board and computer (with multiple board types)

Ideas:
- maybe move some of the emulation to C later to make this portable to smaller devices?
"""

import time
import sys
import select
from contextlib import contextmanager
from pathlib import Path
import logging

from mycron_emu import z80
from mycron_emu.z80 import set_in_callback, set_out_callback
from mycron_emu.config import read_config, read_console_script
from mycron_emu.embedded_console import start_pty_console
from mycron_emu.disks import image as diskimage
from mycron_emu import tracing
from mycron_emu.boards import BOARD_TYPES

log = logging.getLogger("mycron.status")
io_log = logging.getLogger("mycron.io")
disk_log = logging.getLogger("mycron.disk")

io_trace = logging.getLogger("mycron.trace.io")
disk_trace = logging.getLogger("mycron.trace.disk")


def check_console(board, ch_in, poller):
    # Keyboard / Console input
    if not poller.poll(0):
        return

    data = ch_in.read(1)
    if not data:
        poller.unregister(ch_in.fileno())
        return

    # The console doesn't like 8-bit ascii, so limit it to 7-bit.
    ch = data[0] & 0x7f

    if ch == 0xa:
        # doesn't understand newline (\n), so translate to CR (\r)
        ch = 0xd

    board.sport.queue_bytes(bytes([ch]))


# Used to pause and continue the simulator.
sim_paused = False

def sim_pause():
    global sim_paused
    sim_paused = True
    log.info("Sim paused")

def sim_cont():
    global sim_paused
    sim_paused = False
    log.info("Sim continued")


@contextmanager
def sim_paused_context():
    """Safer handling of sim state"""
    old_state = sim_paused
    try:
        sim_pause()
        yield
    finally:
        if not old_state:
            # should be un-paused
            sim_cont()


def run_sim(board, ch_in, ch_in_p, steps_per_chunk=1000):
    """Starts the simulator
    steps_per_chunk is how many steps the z80 C library should run before returning to Python
    for another iteration. Too many steps means the console and some other functions
    (like the console) may become less responsive. Too few steps add overhead.
    """
    N = 3_550_000     # report on performance once after >= N iterations
    tstart = time.time()
    iters = 0
    while True:
        if sim_paused:
            time.sleep(0.1)
            continue
        check_console(board, ch_in, ch_in_p)
        z80.run_steps(steps_per_chunk)

        # Performance monitoring at startup
        iters += steps_per_chunk
        if iters > N and tstart > 0:
            tstop = time.time()
            log.info(f"Ran {iters:_} steps in {tstop-tstart:.3f} seconds ({iters/(tstop-tstart):_} steps/s)")
            tstart = 0  # disable


def dump_mem(fname):
    """Write a memory dump to a file"""
    print(f"Dumping memory to {fname}. Pausing simulator.")
    with sim_paused_context():
        Path(fname).write_bytes(z80.memory_snapshot())
        print(f" - done dumping memory to {fname}. Restoring pause state of simulator.")


def make_config(args):
    """Reads config file and adds information from args to the config"""
    if args.t:
        # Use the same pty for both input and output
        args.ti = args.t
        args.to = args.t

    if (not args.ti) or (not args.to):
        raise SystemExit(f"Please specify -t, or specify both -ti and -to")

    config = read_config(args.config_dir)

    # Maybe not the cleanest yet, but this is a step towards a single
    # config environment without having to deal with arg parsing.
    config['run-dir']          = args.config_dir
    config['console_in']       = args.ti
    config['console_out']      = args.to
    config['script_arg']       = args.script
    config['send']             = args.send
    config['embedded_console'] = args.embedded_console
    log.debug(config)
    return config


def schedule_startup_script(board, config):
    if config["script_arg"]:
        script_path = Path(config["script_arg"])
    elif script_name := config.get("script"):
        script_path = Path(config["run-dir"]) / script_name
    else:
        return

    log.info("Loading console script %s", script_path)

    for delay, data in read_console_script(script_path):
        board.sport.schedule_bytes(delay, data)


def run_emulator(args):
    z80.reset()
    config = make_config(args)

    # TODO: Hack since there is currently no clean way of failing if a
    # non-existing disk is requested in the controller. The emulator currently
    # just crashes.
    # This adds some default images, with write-through if any image is modified.
    # Non-existing images will be created on the disk on the first write.
    log.info(f"Using disk image(s) from {config['disk-images']}")
    d_imgs = [diskimage.DiskImage.from_file(dname) for dname in config['disk-images']]

    if len(d_imgs) > 8:
        raise ValueError(f"disk controller supports at most 8 images, got {len(d_imgs)}")

    for drivenum in range(len(d_imgs), 8):
        path = Path(config['run-dir']) / f"disk-{drivenum:02}.img"
        d_imgs.append(diskimage.DiskImage.empty_image(path))

    with open(config["console_in"], "rb", buffering=0) as console_in, \
         open(config["console_out"], "wb", buffering=0) as console_out:

        if (board_type := BOARD_TYPES.get(config['board'], None)) is None:
            supported = ", ".join(sorted(BOARD_TYPES))
            raise ValueError(f"unsupported board {config['board']!r}; expected one of: {supported}")

        board = board_type(config, d_imgs, console_out)

        set_in_callback(board.io_in)
        set_out_callback(board.io_out)

        if config['send']:
            board.sport.schedule_bytes(0.3, config['send'])

        schedule_startup_script(board, config)

        # Slightly hacky, but this lets us read from the serial port and put it in the
        # queue of the emulator's serial port.
        # NB: need to set it to binary + unbuffered, otherwise terminal input will be buffered and not work properly
        console_in_p = select.poll()
        console_in_p.register(console_in.fileno(), select.POLLIN)

        # If embedded console:
        if (ec_fn := config['embedded_console']):
            # Give it everything
            console_server = start_pty_console(ec_fn, globals() | locals())

        # track cpm loading
        # z80.mem_set_track_mask(0xee00, 0xffff, z80.TRACK_EXEC)
        run_sim(board, console_in, console_in_p)

    return 0
