#!/usr/bin/env python

import io

from mycron_emu.config import read_console_script



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
