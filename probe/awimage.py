#!/usr/bin/env python3
"""Unpack an Allwinner IMAGEWTY firmware image.  Usage: awimage.py IMAGE OUTDIR

Waveshare's MK10_V1.0.img is one of these. The header is 0x60 bytes; the file index starts at 0x400 in
1024-byte entries; the data follows it. Entry layout, worked out from the image itself:

    0x000 u32   filename length (0x100)
    0x004 u32   entry size (0x400)
    0x008 char  maintype[8]        e.g. "RFSFAT16"
    0x010 char  subtype[16]        e.g. "ROOTFS_FEX000000"
    0x020 u32   unused
    0x024 char  filename[256]      e.g. "rootfs.fex"
    0x124 u64   stored length
    0x12c u64   original length
    0x134 u64   offset in the image

rootfs.fex is plain ext4: `debugfs -R "ls -l /data" rootfs.fex` reads it without mounting anything.
"""
import os, struct, sys
img, out = sys.argv[1], sys.argv[2]
f = open(img, "rb")
hdr = f.read(0x60)
assert hdr[:8] == b"IMAGEWTY"
num = struct.unpack_from("<I", hdr, 0x3C)[0]
f.seek(0x400)
index = f.read(0x400 * num)
print("%-28s %-9s %-18s %12s  %s" % ("name", "maintype", "subtype", "bytes", "offset"))
for i in range(num):
    e = index[i * 0x400:(i + 1) * 0x400]
    main = e[8:16].decode("latin-1").strip()
    sub = e[16:32].split(b"\0")[0].decode("latin-1").strip()
    name = e[0x24:0x124].split(b"\0")[0].decode("latin-1")
    stored, orig, off = (struct.unpack_from("<Q", e, o)[0] for o in (0x124, 0x12c, 0x134))
    print("%-28s %-9s %-18s %12d  0x%x" % (name, main, sub, orig, off))
    f.seek(off)
    data = f.read(orig)
    with open(os.path.join(out, name or ("file%02d" % i)), "wb") as g:
        g.write(data)
