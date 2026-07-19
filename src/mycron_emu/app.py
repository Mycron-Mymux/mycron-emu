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
from mycron_emu.channels import Channel, EmulatorChannels
from mycron_emu.scheduler import ScheduledSender

log = logging.getLogger("mycron.status")
io_log = logging.getLogger("mycron.io")
disk_log = logging.getLogger("mycron.disk")

io_trace = logging.getLogger("mycron.trace.io")
disk_trace = logging.getLogger("mycron.trace.disk")


def check_console(chan_send, ch_in, poller):
    # Keyboard / Console input
    if not poller.poll(0):
        return

    data = ch_in.read(1)
    if not data:
        # EOF. A NUL byte is b"\x00" and is not treated as EOF.
        poller.unregister(ch_in.fileno())
        return

    # The console doesn't like 8-bit ascii, so limit it to 7-bit.
    ch = data[0] & 0x7f

    if ch == 0xa:
        # doesn't understand newline (\n), so translate to CR (\r)
        ch = 0xd

    chan_send(bytes([ch]))


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


def run_sim(board, channels, ch_in, ch_in_p, scheduled_input, steps_per_chunk=1000):
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

        scheduled_input.poll()
        check_console(channels.console_input.send, ch_in, ch_in_p)
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


def schedule_startup_script(scheduler, config):
    if config["script_arg"]:
        script_path = Path(config["script_arg"])
    elif script_name := config.get("script"):
        script_path = Path(config["run-dir"]) / script_name
    else:
        return

    log.info("Loading console script %s", script_path)

    start = time.monotonic()
    for offset, data in read_console_script(script_path):
        scheduler.schedule_at(start + offset, data)


def raw_stream_sink(stream):
    """Used to create channel sinks that write to files or streams with
    a write method and (optionally) a flush method."""
    def send(data: bytes):
        stream.write(data)

        flush = getattr(stream, "flush", None)
        if flush is not None:
            flush()

    return send

def text_stream_sink(stream, *, encoding="ascii", errors="replace"):
    def send(data: bytes):
        stream.write(data.decode(encoding, errors=errors))
        stream.flush()
    return send


def load_disk_images(run_dir):
    """Returns 8 disk images by either reading the file disk-XX.img from
    the run directory, or, if any file is missing, injecting empty images.
    NB: The empty images are there as the current emulator will crash if
    an image is missing. To deal with "non-inserted" disks, there needs
    to be a further examination of how the proms deal with disks
    that are not present before, during or after read or write operations.
    """
    log.info(f"Using disk image(s) from {run_dir}")
    run_dir = Path(run_dir)
    images = []

    for drive_number in range(8):
        path = run_dir / f"disk-{drive_number:02}.img"
        if path.exists():
            image = diskimage.DiskImage.from_file(path)
        else:
            image = diskimage.DiskImage.empty_image(path, read_from_file=True)
        images.append(image)

    return images


def run_emulator(args):
    z80.reset()
    config = make_config(args)

    channels = EmulatorChannels(
        console_input = Channel("console_input", suppress_listener_errors=False),
        console_output = Channel("console_output", suppress_listener_errors=True),
        aux_input = Channel("aux_input"),
        aux_output = Channel("aux_output"),
    )

    d_imgs = load_disk_images(config['run-dir'])

    with open(config["console_in"], "rb", buffering=0) as console_in, \
         open(config["console_out"], "wb", buffering=0) as console_out:

        if (board_type := BOARD_TYPES.get(config['board'], None)) is None:
            supported = ", ".join(sorted(BOARD_TYPES))
            raise ValueError(f"unsupported board {config['board']!r}; expected one of: {supported}")

        board = board_type(config, d_imgs, channels)

        # Wire up channels for input/output which opens up for more flexible console and other io.
        # NB: Aux is just wired up to stdout for now. This will have to do for now until we
        # can figure out more about what it is supposed to be doing. It will probably need some
        # config or parameter in the future. Also: aux input is not wired up to anything.
        channels.console_input.subscribe(board.sport.queue_bytes)
        stdout_sink = raw_stream_sink(console_out)
        channels.console_output.subscribe(stdout_sink)
        aux_sink = raw_stream_sink(sys.stdout.buffer)
        channels.aux_output.subscribe(aux_sink)

        # Scheduled input and scripts
        scheduled_input = ScheduledSender(channels.console_input.send)
        if config['send']:
            scheduled_input.schedule_after(0.3, config['send'])
        schedule_startup_script(scheduled_input, config)

        set_in_callback(board.io_in)
        set_out_callback(board.io_out)

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
        run_sim(board, channels, console_in, console_in_p, scheduled_input)

    return 0
