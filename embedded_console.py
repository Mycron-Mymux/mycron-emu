#!/usr/bin/env python

"""embedded_console.py
Created using ChatGPT 5.5 thinking. 

WARNING: Note that this console is not thread safe.
I'm not fixing this now as it's only used for debug/testing at the moment,
where thread safety is less of an issue as state manipulation from
the console is usually done with the simulator paused.

A cleaner solution requires more thinking about how I want to interact with
the simulator.
"""

import builtins
import code
import io
import os
import sys
import threading
import time

class PtyConsole(code.InteractiveConsole):
    def __init__(self, locals_, rfile, wfile):
        super().__init__(locals=locals_)
        self.rfile = rfile
        self.wfile = wfile

    def raw_input(self, prompt=""):
        self.wfile.write(prompt)
        self.wfile.flush()

        line = self.rfile.readline()

        if line == "":
            raise EOFError
        # print("Console read line", line)
        return line.rstrip("\r\n")

    def write(self, data):
        self.wfile.write(data)
        self.wfile.flush()

    def runcode(self, code_obj):
        """
        Redirect stdout/stderr/displayhook while executing console commands,
        so things like:

            >>> print("hello")
            hello
            >>> 1 + 2
            3

        go back to the PTY console.
        """

        old_stdout = sys.stdout
        old_stderr = sys.stderr
        old_displayhook = sys.displayhook

        sys.stdout = self.wfile
        sys.stderr = self.wfile

        def displayhook(value):
            if value is not None:
                builtins._ = value
                print(repr(value), file=self.wfile)

        sys.displayhook = displayhook

        try:
            super().runcode(code_obj)
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            sys.displayhook = old_displayhook


def start_pty_console(path, namespace, banner=None):
    """
    Start an embedded Python console connected to an existing PTY path.

    Example:

        start_pty_console("/tmp/sim-repl-a", {"sim": sim})
    """

    def thread_main():
        # Wait for socat to create the PTY symlink.
        while not os.path.exists(path):
            time.sleep(0.1)

        fd = os.open(path, os.O_RDWR | os.O_NOCTTY)

        rfile = io.TextIOWrapper(
            os.fdopen(os.dup(fd), "rb", buffering=0),
            encoding="utf-8",
            newline="\n",
        )

        wfile = io.TextIOWrapper(
            os.fdopen(fd, "wb", buffering=0),
            encoding="utf-8",
            newline="\n",
            write_through=True,
        )

        console = PtyConsole(namespace, rfile, wfile)

        if banner is None:
            banner_text = (
                "Embedded simulator Python console\n"
                "Available names: "
                + ", ".join(sorted(namespace.keys()))
                + "\n"
            )
        else:
            banner_text = banner

        console.interact(banner=banner_text, exitmsg="Console disconnected.")

    t = threading.Thread(target=thread_main, daemon=True)
    t.start()
    return t
