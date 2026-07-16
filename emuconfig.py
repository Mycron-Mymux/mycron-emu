#!/usr/bin/env python3

import pathlib

# TODO: maybe just use yaml or similar?
def read_config(dname):
    dname = pathlib.Path(dname)
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
    conf = read_config("run-tst")
    print(conf)
