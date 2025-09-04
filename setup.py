from setuptools import setup, find_packages, Extension

extra_compile_args = " -Wno-unused-result -Wsign-compare -DNDEBUG -g -fwrapv -O2 -Wall -g -fstack-protector-strong -Wformat -Werror=format-security -g -fwrapv -O2 -g -fstack-protector-strong -Wformat -Werror=format-security -Wdate-time -D_FORTIFY_SOURCE=2 -fPIC -I/usr/include/python3.10".split()

# Extra hack. Need these flags to get the emultator to work correctly
extra_compile_args += ["-fomit-frame-pointer", "-DLSB_FIRST"]

setup(
    name="z80emu",
    version="0.0.1",
    packages=find_packages(),
    license="GPL-2",
    ext_modules=[
        Extension("z80emu", ["z80emu.c", "z80/Z80Dasm.c", "z80/Z80.c"],
                 extra_compile_args=extra_compile_args)
    ],
)
