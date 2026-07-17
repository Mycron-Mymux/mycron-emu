#!/usr/bin/env python

import logging
import sys


ROOT_LOGGER = "mycron"
STATUS_LOGGER = "mycron.status"
TRACE_LOGGER = "mycron.trace"
IO_TRACE_LOGGER = "mycron.trace.io"
DISK_TRACE_LOGGER = "mycron.trace.disk"

# Need these filters to avoid having messages pop up in multiple changels, effectively
# duplicating messages stdout and stderr go to the same place.
class _LoggerPrefixFilter(logging.Filter):
    def __init__(self, prefix):
        super().__init__()
        self.prefix = prefix

    def filter(self, record):
        return (
            record.name == self.prefix
            or record.name.startswith(self.prefix + ".")
        )


class _ExcludePrefixFilter(logging.Filter):
    def __init__(self, prefix):
        super().__init__()
        self.prefix = prefix

    def filter(self, record):
        return not (
            record.name == self.prefix
            or record.name.startswith(self.prefix + ".")
        )


def configure_logging(
    *,
    trace_enabled=True,
    status_stream=None,
    trace_stream=None,
):
    """Configure the application's default text output.

    Future interfaces can replace these stream handlers with handlers
    that send records to separate TUI views.
    """

    status_stream = status_stream or sys.stderr
    trace_stream = trace_stream or sys.stdout

    root = logging.getLogger(ROOT_LOGGER)
    root.setLevel(logging.DEBUG)
    root.setLevel(logging.INFO)

    # Avoid duplicate handlers if configuration is called again.
    root.handlers.clear()
    root.propagate = False

    status_handler = logging.StreamHandler(status_stream)
    status_handler.setLevel(logging.INFO)
    status_handler.setFormatter(logging.Formatter("%(message)s"))
    status_handler.addFilter(_ExcludePrefixFilter(TRACE_LOGGER))

    trace_handler = logging.StreamHandler(trace_stream)
    trace_handler.setLevel(
        logging.DEBUG if trace_enabled else logging.CRITICAL + 1
    )
    trace_handler.setFormatter(logging.Formatter("%(message)s"))
    trace_handler.addFilter(_LoggerPrefixFilter(TRACE_LOGGER))

    root.addHandler(status_handler)
    root.addHandler(trace_handler)

    logging.Formatter("%(levelname)s: %(message)s")

    # log.setLevel(logging.INFO)
    # logging.basicConfig(level=logging.INFO)
