#!/usr/bin/env python3

import pathlib

# TODO: maybe just use yaml or similar? 
def read_config(dname):
    cfname = pathlib.Path(dname, "config.txt")
    conf = dict()
    # TODO : consider using existing config parsers
    with open(cfname, 'r') as cf:
        for line in cf.readlines():
            line = line.strip().split('#')[0].strip()
            if line.startswith("#") or ":" not in line:
                continue
            key, val = [v.strip() for v in line.split(":")]
            conf[key] = val

    image_fnames = [str(p) for p in sorted(pathlib.Path(dname).glob("disk-??.img"))]
    conf['disk-images'] = image_fnames
    return conf


if __name__ == "__main__":
    conf = read_config("run-tst")
    print(conf)
    
