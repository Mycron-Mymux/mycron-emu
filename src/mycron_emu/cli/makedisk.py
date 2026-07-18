import argparse

from mycron_emu.disks.builders import (
    data_make_test_img,
    prog_make_test_img,
)
from mycron_emu.disks.image import DiskImage


def build_parser():
    parser = argparse.ArgumentParser(
        prog="makedisk",
        description="Create Mycron disk images",
    )

    group = parser.add_mutually_exclusive_group(required=True)

    group.add_argument("--program", metavar="FILE",
                       help="Create a program disk image")
    group.add_argument("--data", metavar="FILE",
                       help="Create a data disk image")
    group.add_argument("--empty", metavar="FILE",
                       help="Create an empty disk image")

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.program:
        prog_make_test_img(args.program).save()
    elif args.data:
        data_make_test_img(args.data).save()
    elif args.empty:
        DiskImage.empty_image(args.empty).save()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
