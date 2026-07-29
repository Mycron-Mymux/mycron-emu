#!/usr/bin/env python

from mycron_emu.cli.mycron import build_parser


def test_aux_argument():
    args = build_parser().parse_args([
        "-t", "/dev/pts/10",
        "-a", "/dev/pts/11",
    ])

    assert args.aux == "/dev/pts/11"
