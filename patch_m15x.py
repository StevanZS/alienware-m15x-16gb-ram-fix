"""Patch Alienware M15x A09 BIOS so Setup shows 8GB DIMMs (Memory Bank 0/1, Total Memory, Channel Mode).

usage: python patch_m15x.py <Dell M15xA09.iso> <outdir>

Writes to <outdir>: M15xA09-16GB.iso (bootable, same flow as Dell's ISO), W6702M.A09 (phlash16 file),
W6702MB.A09 (raw 2MB SPI image). Requires: pip install uefi_firmware
"""
import sys, os, re, hashlib
import uefi_firmware.efi_compressor as efi

ORIG_ISO_SHA256 = 'dc1dfe7a4a24ae2e08deee99da32114d397d6d0d686f12f30cf3e09248894b1c'
# file extents inside Dell's M15xA09.iso (ISO 9660 tree, read by the boot disk's FA.bat)
ISO_CAP_OFF, CAP_LEN = 0x1de000, 2406102     # M15R1A09/W6702M.A09
ISO_IMG_OFFS, IMG_LEN = (0x429800, 0x629800), 0x200000  # W6702MB.A09, W6702MB.A09.bin
CAP_IMG_OFF = 0x40000          # 2MB SPI image starts here inside W6702M.A09

iso_path, outdir = sys.argv[1:3]
os.makedirs(outdir, exist_ok=True)
iso = bytearray(open(iso_path, 'rb').read())
if hashlib.sha256(iso).hexdigest() != ORIG_ISO_SHA256:
    raise SystemExit('input is not the original Dell M15xA09.iso (SHA-256 mismatch)')
cap = bytearray(iso[ISO_CAP_OFF:ISO_CAP_OFF + CAP_LEN])
img = bytearray(iso[ISO_IMG_OFFS[0]:ISO_IMG_OFFS[0] + IMG_LEN])
assert all(iso[o:o + IMG_LEN] == img for o in ISO_IMG_OFFS)
orig_img = bytes(img)

# ---- code patches (offsets are within the decompressed module, incl. its 4-byte size header) ----
NOP = 0x90
# AL = size code -> EAX = size in MB (0 -> 0, n -> 1 << (n+8))
DECODE = bytes.fromhex(
    '660fb6c0'      # movzx eax, al
    '0ac0'          # or al, al
    '740d'          # jz  +13
    '0408'          # add al, 8
    '8ac8'          # mov cl, al
    '66b801000000'  # mov eax, 1
    '66d3e0')       # shl eax, cl
ADD_EBX_EAX = bytes.fromhex('6601c3')
# AX = size in MB -> AL = size code (0 / <512MB -> 0, 512 -> 1 ... 4096 -> 4, 8192 -> 5)
ENCODE = bytes.fromhex(
    '0fbcc0'        # bsf ax, ax
    '7404'          # jz  +4 (-> xor)
    '2c08'          # sub al, 8
    '7702'          # ja  +2 (-> done)
    '32c0')         # xor al, al

def pad(code, n):
    assert len(code) <= n
    return code + bytes([NOP]) * (n - len(code))

PATCHES = {
    '_T00': [  # (start, end, expected-first-bytes, expected-bytes-at-end, new code)
        (0x3e26, 0x3e60, '0ac07508', '668bd8', DECODE),                 # Total Memory: bank 0 part
        (0x3e6b, 0x3ea7, '0ac07506', '668bc3', DECODE + ADD_EBX_EAX),   # Total Memory: bank 1 part
        (0x3eed, 0x3f27, '0bc07508', '8e4610', DECODE),                 # Memory Bank 0
        (0x3f6a, 0x3fa4, '0bc07508', '8e4610', DECODE),                 # Memory Bank 1
    ],
    '_B08': [
        (0x2b50, 0x2b7a, '0bc07504', '52ba0402', ENCODE),               # POST: bank 0 -> token 0x204
        (0x2b86, 0x2bb0, '0bc07504', '52ba0702', ENCODE),               # POST: bank 1 -> token 0x207
    ],
}

def find_module(name):
    m = re.search(rb'\xf8\x00..(...)\x02' + re.escape(name.encode()), img, re.S)
    assert m, name
    h = m.start()
    size = int.from_bytes(img[h+4:h+7], 'little')
    # compressed stream header follows the "...0001" section marker
    for o in range(h + 12, h + 0x40):
        cs = int.from_bytes(img[o:o+4], 'little'); us = int.from_bytes(img[o+4:o+8], 'little')
        if img[o-2:o] == b'\x00\x01' and 0 < cs < size and o + 8 + cs <= h + size:
            try:
                r = efi.EfiDecompress(bytes(img[o:o+8+cs]), 8 + cs)
            except Exception:
                continue
            if len(r) == us:
                return h, size, o, cs, us, bytearray(r)
    raise SystemExit('module not found ' + name)

def lane_sums(b, base):
    s = [0, 0, 0, 0]
    for i, v in enumerate(b):
        s[(base + i) & 3] += v
    return s

report = []
for name, plist in PATCHES.items():
    h, size, o, cs, us, mod = find_module(name)
    orig_mod = bytes(mod)
    for start, end, exp_head, exp_tail, code in plist:
        assert mod[start:start+len(bytes.fromhex(exp_head))].hex() == exp_head, (name, hex(start))
        assert mod[end:end+len(bytes.fromhex(exp_tail))].hex() == exp_tail, (name, hex(end))
        mod[start:end] = pad(code, end - start)
    # recompress with standard EFI compression; must fit the original stream slot
    comp = efi.EfiCompress(bytes(mod), len(mod))
    ncs = int.from_bytes(comp[0:4], 'little'); nus = int.from_bytes(comp[4:8], 'little')
    assert nus == us and len(comp) == 8 + ncs, (name, hex(ncs), hex(nus))
    assert efi.EfiDecompress(comp, len(comp)) == bytes(mod)
    slot = cs                                   # bytes available for the stream body
    assert ncs <= slot, f'{name}: recompressed {ncs:#x} > slot {slot:#x}'
    # keep header CompSize/module size = original (modules are packed back to back); zero-fill
    # the unused tail and use its last byte to keep the module's 8-bit sum unchanged, so the
    # header checksum bytes (h+2, h+3) stay valid without modification
    assert slot > ncs, f'{name}: no spare byte for checksum balance'
    body_off = o + 8
    old_body = bytes(img[body_off:body_off + slot])
    new_body = bytearray(comp[8:]) + bytearray(slot - ncs)
    new_body[-1] = (sum(old_body) - sum(new_body)) & 0xff
    assert sum(new_body) & 0xff == sum(old_body) & 0xff
    img[body_off:body_off + slot] = new_body
    # verify decode from the image with the ORIGINAL CompSize field (tail must be ignored)
    chk = efi.EfiDecompress(bytes(img[o:o+8+cs]), 8 + cs)
    assert chk == bytes(mod), name
    # module 8-bit checksum (byte h+3) must still make the module sum to 0xF8
    assert sum(img[h:h+size]) & 0xff == 0xf8, name
    assert sum(img[h:h+0x18]) - img[h+3] & 0xff == 0xf8, name
    report.append(f'{name}: module@{h:#x} stream@{o:#x} slot {cs:#x}, recompressed {ncs:#x} ({cs-ncs} spare)')

diff = [i for i in range(len(img)) if img[i] != orig_img[i]]
assert all(0x131122 <= i < 0x1354d9 or 0x1437ac <= i < 0x145e03 for i in diff), 'change outside target modules'
assert sum(img) & 0xff == sum(orig_img) & 0xff

# apply identical bytes to the flasher file (its embedded image differs only in 21 descriptor bytes)
for i in diff:
    assert cap[CAP_IMG_OFF + i] == orig_img[i]
    cap[CAP_IMG_OFF + i] = img[i]

# same-size in-place replacement inside the ISO 9660 tree (no ISO checksums exist)
iso[ISO_CAP_OFF:ISO_CAP_OFF + CAP_LEN] = cap
for o in ISO_IMG_OFFS:
    iso[o:o + IMG_LEN] = img

open(os.path.join(outdir, 'W6702MB.A09'), 'wb').write(img)
open(os.path.join(outdir, 'W6702M.A09'), 'wb').write(cap)
open(os.path.join(outdir, 'M15xA09-16GB.iso'), 'wb').write(iso)
print('\n'.join(report))
print('changed bytes:', len(diff), 'range', hex(diff[0]), '-', hex(diff[-1]))
for f in ('M15xA09-16GB.iso', 'W6702M.A09', 'W6702MB.A09'):
    print(f, hashlib.sha256(open(os.path.join(outdir, f), 'rb').read()).hexdigest())
