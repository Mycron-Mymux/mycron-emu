#!/usr/bin/env python

import io
import select
from mycron_emu.app import normalize_console_input, poll_inputs, read_console_script

class FakePoller:
    def __init__(self, events):
        self.events = events
        self.unregistered = []

    def poll(self, timeout):
        assert timeout == 0
        events, self.events = self.events, []
        return events

    def unregister(self, fd):
        self.unregistered.append(fd)



def test_console_script_supports_escapes_and_cumulative_delays(tmp_path):
    script = tmp_path / "startup.jsonl"
    script.write_text(
        '{"delay": 0.1, "send": "ABC\\r"}\n'
        '{"delay": 0.4, "send": "\\u0000"}\n',
        encoding="utf-8",
    )

    commands = list(read_console_script(script))

    assert commands == [
        (0.1, b"ABC\r"),
        (0.5, b"\x00"),
    ]


def test_console_input_is_normalized():
    assert normalize_console_input(b"\n") == b"\r"
    assert normalize_console_input(b"\xff") == b"\x7f"
    assert normalize_console_input(b"\x00") == b"\x00"


def test_poll_inputs_routes_multiple_streams():
    console_output = []
    aux_output = []

    poller = FakePoller([
        (10, select.POLLIN),
        (11, select.POLLIN),
    ])
    routes = {
        10: (
            io.BytesIO(b"\n"),
            console_output.append,
            normalize_console_input,
        ),
        11: (
            io.BytesIO(b"\xff"),
            aux_output.append,
            None,
        ),
    }

    poll_inputs(poller, routes)

    assert console_output == [b"\r"]
    assert aux_output == [b"\xff"]


def test_poll_inputs_unregisters_eof():
    poller = FakePoller([(10, select.POLLIN)])
    routes = {
        10: (io.BytesIO(b""), lambda data: None, None),
    }

    poll_inputs(poller, routes)

    assert poller.unregistered == [10]
    assert 10 not in routes
