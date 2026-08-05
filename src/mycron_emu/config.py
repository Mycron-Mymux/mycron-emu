#!/usr/bin/env python3

import json
from pathlib import Path
import tomllib

def read_console_script(path):
    """Yield cumulative delay and ASCII bytes from a JSON-lines script."""
    path = Path(path)
    elapsed = 0.0

    with path.open(encoding="utf-8") as script_file:
        for line_number, raw_line in enumerate(script_file, start=1):
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            try:
                command = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {error.msg}") from error

            if not isinstance(command, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")

            unknown = command.keys() - {"delay", "send"}
            if unknown:
                names = ", ".join(sorted(unknown))
                raise ValueError(f"{path}:{line_number}: unknown fields: {names}")

            try:
                delay = float(command.get("delay", 0.0))
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}:{line_number}: delay must be a number") from error

            if delay < 0:
                raise ValueError(f"{path}:{line_number}: delay must be non-negative")

            text = command.get("send")
            if not isinstance(text, str):
                raise ValueError(f"{path}:{line_number}: send must be a string")

            try:
                data = text.encode("ascii")
            except UnicodeEncodeError as error:
                raise ValueError(f"{path}:{line_number}: send contains non-ASCII text") from error

            elapsed += delay
            yield elapsed, data


def read_config(dname):
    """Read the TOML config file and return a dict with defaults.

    The TOML file (config.toml) may contain any of the following keys:
        board          – board type (default: dim-1003)
        prom0          – path to first PROM binary
        prom1          – path to second PROM binary
        console-tty    – single TTY path for guest console I/O (replaces -t)
        aux-tty        – TTY path for guest auxiliary serial I/O (replaces -a)
        debug-console  – TTY path for the embedded Python console (replaces -ec)
        script         - A script.jsonl for scheduled input to the console.
    """
    dname = Path(dname)
    # Provide some defaults
    conf = {
        'board': 'dim-1003',
        'prom0': '',
        'prom1': '',
    }

    config_path = dname / "config.toml"
    print("confpath", config_path)
    if config_path.exists():
        with config_path.open("rb") as f:
            toml_data = tomllib.load(f)
        # Map TOML keys to our config dict keys.
        key_map = {
            "board": "board",
            "prom0": "prom0",
            "prom1": "prom1",
            "console-tty": "console-tty",
            "aux-tty": "aux-tty",
            "debug-console": "debug-console",
            "script" : "script",
        }
        for toml_key, conf_key in key_map.items():
            if toml_key in toml_data:
                conf[conf_key] = toml_data[toml_key]

    return conf


if __name__ == "__main__":
    import sys
    cdir = "run-tst"
    if len(sys.argv) > 1:
        cdir = sys.argv[1]
    conf = read_config(cdir)
    print(conf)
