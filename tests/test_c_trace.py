#!/usr/bin/env python

import logging
import pytest
from mycron_emu import z80


def test_c_trace_reaches_python_logging(caplog):
    from mycron_emu import z80

    with caplog.at_level(logging.DEBUG, logger="mycron.trace.core"):
        z80.mem_set_prot(0, 0, 0)

    assert any("Setting memory protection" in record.message
               for record in caplog.records)


def test_keyboard_interrupt_from_c_log_callback_propagates(monkeypatch):
    from mycron_emu import z80

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(z80.core_trace_log, "log", interrupt)

    with pytest.raises(KeyboardInterrupt):
        z80.mem_set_prot(0, 0, 0)


def test_ordinary_c_logging_failure_does_not_propagate(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("logger failed")

    monkeypatch.setattr(z80.core_trace_log, "log", fail)

    z80.mem_set_prot(0, 0, 0)

    captured = capsys.readouterr()
    assert "C logging callback failed" in captured.err
