import argparse

from mycron_emu.app import run_emulator
from mycron_emu.logging_config import configure_logging


def build_parser():
    parser = argparse.ArgumentParser(
        prog="mycron",
        description="Run the Mycron computer emulator",
    )

    parser.add_argument("-c", "--config-dir", default="run-tst",
                        help="Directory containing configuration, PROMs and disk images")

    parser.add_argument("-t",  help="Use one path for guest console input and output")
    parser.add_argument("-ti", help="Guest console input path")
    parser.add_argument("-to", help="Guest console output path")
    parser.add_argument("-ec", "--embedded-console",
                        help="Path for the embedded Python console pty")
    parser.add_argument("--script", metavar="FILE",
                        help="Override the configured startup script")
    parser.add_argument("--send", metavar="TEXT",
                        help="Send text to the guest after startup")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    configure_logging(trace_enabled=True)

    try:
        return run_emulator(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
