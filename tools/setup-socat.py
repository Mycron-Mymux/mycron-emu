#!/usr/bin/env python3
# NB: AI-coded.
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


PORT_NAMES = ("console", "aux", "debug")


def remove_stale_path(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def start_pair(directory, name):
    emulator_path = directory / f"{name}-emu"
    user_path = directory / f"{name}-user"

    remove_stale_path(emulator_path)
    remove_stale_path(user_path)

    command = [
        "socat",
        f"pty,rawer,echo=0,link={emulator_path}",
        f"pty,rawer,echo=0,link={user_path}",
    ]

    process = subprocess.Popen(command)
    return process, emulator_path, user_path


def wait_for_paths(paths, processes, timeout=5.0):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        failed = [
            process.returncode
            for process in processes
            if process.poll() is not None
        ]
        if failed:
            raise RuntimeError(
                f"socat exited while creating PTYs: {failed}"
            )

        if all(path.exists() for path in paths):
            return

        time.sleep(0.05)

    missing = [str(path) for path in paths if not path.exists()]
    raise TimeoutError(f"PTY paths were not created: {missing}")


def main(argv=None):
    default_dir = (
        Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
        / f"mycron-{os.getuid()}"
    )

    parser = argparse.ArgumentParser(
        description="Create stable PTY names for the Mycron emulator",
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=default_dir,
        help="Directory for stable PTY symlinks",
    )
    parser.add_argument(
        "--ports",
        nargs="+",
        choices=PORT_NAMES,
        default=list(PORT_NAMES),
    )
    args = parser.parse_args(argv)

    args.directory.mkdir(mode=0o700, parents=True, exist_ok=True)

    processes = []
    paths = []

    try:
        for name in args.ports:
            process, emulator_path, user_path = start_pair(
                args.directory,
                name,
            )
            processes.append(process)
            paths.extend((emulator_path, user_path))

        wait_for_paths(paths, processes)

        print(f"PTY directory: {args.directory}")
        print()
        print("Emulator:")
        print(
            "  mycron"
            f" -t {args.directory / 'console-emu'}"
            f" -a {args.directory / 'aux-emu'}"
            f" -ec {args.directory / 'debug-emu'}"
        )
        print()
        print("Clients:")
        print(f"  tio {args.directory / 'console-user'}")
        print(f"  tio {args.directory / 'aux-user'}")
        print(
            "  rlwrap -a socat - "
            f"{args.directory / 'debug-user'},raw,echo=0"
        )
        print()
        print("Press Ctrl-C to remove the PTYs.")

        while True:
            for process in processes:
                returncode = process.poll()
                if returncode is not None:
                    raise RuntimeError(
                        f"socat exited unexpectedly with status {returncode}"
                    )
            time.sleep(1)

    except KeyboardInterrupt:
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()

        for process in processes:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        for path in paths:
            remove_stale_path(path)


if __name__ == "__main__":
    raise SystemExit(main())
