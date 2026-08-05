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
from contextlib import ExitStack, contextmanager
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



def normalize_console_input(data):
    """Apply the guest console's input conventions to one byte."""
    ch = data[0] & 0x7f

    if ch == 0x0a:
        # The guest console expects CR rather than LF.
        ch = 0x0d

    return bytes([ch])


def register_input(poller, routes, stream, destination, transform=None):
    """Register a readable stream and its channel destination."""
    fd = stream.fileno()
    poller.register(fd, select.POLLIN)
    routes[fd] = (stream, destination, transform)


def poll_inputs(poller, routes):
    """Poll all registered host streams and route available input."""
    for fd, event in poller.poll(0):
        route = routes.get(fd)
        if route is None:
            continue

        stream, destination, transform = route

        if event & select.POLLIN:
            data = stream.read(1)

            if data:
                if transform is not None:
                    data = transform(data)
                destination(data)
                continue

        # POLLIN with no data means EOF. Error and hangup events also remove
        # the stream. A NUL byte is b"\x00" and is therefore not EOF.
        if event & (select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL):
            poller.unregister(fd)
            routes.pop(fd, None)


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


def run_sim(board, channels, input_poller, input_routes, scheduled_input, steps_per_chunk=1000):
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
        poll_inputs(input_poller, input_routes)
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
    """Reads config file and merges with CLI args, where args take precedence.

    CLI flags override config file values:
      -t / -ti/-to  >  console-tty
      -a            >  aux-tty
      -ec           >  debug-console
    """
    # Read from TOML config file.
    config = read_config(args.config_dir)
    print(config)
    print(args)

    # --- console-tty (maps to -t / -ti / -to) ---
    # If -t is given, use it for both input and output.
    if args.t:
        # Use the same pty for both input and output
        args.ti = args.t
        args.to = args.t

    # If -ti/-to are not given by the user, fall back to console-tty from config,
    # or require them to be specified.
    if not args.ti or not args.to:
        # Config key is "console-tty"; split into console_in / console_out.
        # Currently the config only supports a single TTY path (both in+out).
        console_tty = config.get("console-tty")
        if console_tty and not args.ti:
            args.ti = console_tty
        if console_tty and not args.to:
            args.to = console_tty

    # --- aux-tty (maps to -a/--aux) ---
    if not args.aux:
        args.aux = config.get("aux-tty")

    # --- debug-console (maps to -ec/--embedded-console) ---
    if args.embedded_console is None:
        args.embedded_console = config.get("debug-console")

    # Populate the config dict from args and config.
    config['run-dir']          = args.config_dir
    config['console_in']       = args.ti
    config['console_out']      = args.to
    config['script_arg']       = args.script
    config['send']             = args.send
    config['embedded_console'] = args.embedded_console
    config['aux']              = args.aux
    log.debug(config)

    if (not args.ti) or (not args.to):
        raise SystemExit(f"Please specify -t, or specify both -ti and -to. Alternatively, specify console-tty in {args.config_dir}/config.toml")


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
        aux_output = Channel("aux_output", suppress_listener_errors=True),
    )

    d_imgs = load_disk_images(config['run-dir'])


    # ExitStack handles the optional aux streams without trying to put None
    # into a multi-item with statement.
    with ExitStack() as stack:
        console_in = stack.enter_context(open(config["console_in"], "rb", buffering=0))
        console_out = stack.enter_context(open(config["console_out"], "wb", buffering=0))

        aux_in = None
        aux_out = None
        if config["aux"]:
            aux_in = stack.enter_context(open(config["aux"], "rb", buffering=0))
            aux_out = stack.enter_context(open(config["aux"], "wb", buffering=0))

        if (board_type := BOARD_TYPES.get(config['board'], None)) is None:
            supported = ", ".join(sorted(BOARD_TYPES))
            raise ValueError(f"unsupported board {config['board']!r}; expected one of: {supported}")

        board = board_type(config, d_imgs, channels)

        # Wire the board's serial devices to the application channels.
        channels.console_input.subscribe(board.sport.queue_bytes)
        channels.console_output.subscribe(raw_stream_sink(console_out))

        # DIM-1003 has an auxiliary input port. Boards without one simply
        # leave the aux input channel without a subscriber.
        if aux_queue := getattr(board.sport, "queue_aux_bytes", None):
            channels.aux_input.subscribe(aux_queue)

        # Preserve the previous stdout behavior unless -a/--aux was supplied.
        aux_output_stream = aux_out if aux_out is not None else sys.stdout.buffer
        channels.aux_output.subscribe(raw_stream_sink(aux_output_stream))

        # Scheduled input and scripts
        scheduled_input = ScheduledSender(channels.console_input.send)
        if config['send']:
            scheduled_input.schedule_after(0.3, config['send'])
        schedule_startup_script(scheduled_input, config)

        set_in_callback(board.io_in)
        set_out_callback(board.io_out)

        # Binary, unbuffered streams are required so PTY input is delivered
        # immediately rather than being held in a Python buffer.
        input_poller = select.poll()
        input_routes = {}

        register_input(input_poller, input_routes, console_in,
                       channels.console_input.send, normalize_console_input)

        if aux_in is not None:
            # Aux data remains 8-bit clean and is not subject to the console's
            # LF-to-CR translation.
            register_input(input_poller, input_routes, aux_in, channels.aux_input.send)

        # If embedded console:
        if (ec_fn := config['embedded_console']):
            # Give it everything
            console_server = start_pty_console(ec_fn, globals() | locals())

        # track cpm loading
        # z80.mem_set_track_mask(0xee00, 0xffff, z80.TRACK_EXEC)
        run_sim(board, channels, input_poller, input_routes, scheduled_input)

    return 0
