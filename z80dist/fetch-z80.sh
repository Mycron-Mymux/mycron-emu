#!/bin/bash

TARG_ZIP=z80dist/z80em.zip

FSIZE=0

if test -f $TARG_ZIP
then 
    FSIZE=$(stat -c%s $TARG_ZIP)
fi

if (( FSIZE < 2048 ))
then
    echo "Downloading from source"
    curl https://www.komkon.org/~dekogel/files/misc/z80em.zip -o $TARG_ZIP
fi    

if ! test -d z80
then
    unzip -d z80 $TARG_ZIP
    echo "Patching z80 library" 
    patch -d z80 -p1 < z80dist/z80.patch
fi
