#!/usr/bin/env python3

import json
from pathlib import Path


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


# TODO: maybe just use yaml or similar?
def read_config(dname):
    dname = Path(dname)
    # Provide some defaults
    conf = {
        'board': 'dim-1003',
        'prom0': '',
        'prom1': '',
    }

    # TODO : consider using existing config parsers
    with (dname / "config.txt").open() as cf:
        for line in cf:
            line = line.partition("#")[0].strip()
            if not line:
                continue
            key, separator, value = line.partition(":")
            if not separator:
                continue
            conf[key.strip()] = value.strip()

    conf['disk-images'] = [
        str(path)
        for path in sorted(dname.glob("disk-??.img"))]

    return conf





if __name__ == "__main__":
    import sys
    cdir = "run-tst"
    if len(sys.argv) > 1:
        cdir = sys.argv[1]
    conf = read_config(cdir)
    print(conf)
