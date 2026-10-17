# Alienware M15x: 16GB RAM display fix (BIOS A09)

With two 8GB DDR3/DDR3L SO-DIMMs installed (16GB), the Alienware M15x **works fine in the OS**, but
the BIOS Setup Main page shows nothing useful for **Memory Bank 0**, **Memory Bank 1** and
**Total Memory**, and **Memory Channel Mode** shows *Single* instead of *Dual*.

This project patches Dell's latest M15x BIOS (**A09**) so Setup shows `8192 MB` / `8192 MB` /
`16384 MB` and *Dual*. Nothing else changes.

> **Status:** the patch has been verified off-hardware: module checksums, exact recompression and CPU
> emulation of the new code. It has **not yet been flashed and tested on a real M15x**. Flashing a BIOS
> always carries risk. Proceed at your own risk.

## Root cause

The BIOS is Phoenix SecureCore (Calpella/W670 board) with a legacy, CSM-based Setup. During POST,
module `_B08` (the SMBIOS Type 17 builder) saves each DIMM's size into a 3-bit CMOS token: `0x204`
for bank 0 and `0x207` for bank 1 (CMOS byte `0x43`, bits 0-2 and 3-5). Setup module `_T00` reads
those tokens back. Both sides use the same fixed table:

| Size (MB) | 0 | 512 | 1024 | 2048 | 4096 | **8192** |
|---|---|---|---|---|---|---|
| Code | 0 | 1 | 2 | 3 | 4 | *not handled* |

An 8GB DIMM matches no entry, so a meaningless code is stored and Setup can't display the size. The OS
reads memory from the E820 map and SMBIOS, which were already correct, so it was never affected.

## The patch

The patch replaces both lookup tables with arithmetic. Codes are stored the same way as before, and 8GB
becomes code 5, which fits in the 3-bit token:

| Module | Offset (decompressed) | New code |
|---|---|---|
| `_B08` POST | `0x2B50`, `0x2B86` | `bsf ax,ax; sub al,8`: MB → code (8192 → 5) |
| `_T00` Setup | `0x3E26`, `0x3E6B` (Total Memory), `0x3EED` (Bank 0), `0x3F6A` (Bank 1) | `eax = 1 << (code+8)`: code → MB |

Every size the original handled (0 to 4096 MB) produces exactly the same result as before.

### Integrity

- Phoenix modules here are EFI-compressed. Recompressing **all 28 original modules** with the EDK
  `EfiCompress` reproduces Dell's bytes **bit-for-bit**, so the patched modules are packed exactly as
  Phoenix's build tool would pack them.
- Module sizes, offsets and headers are unchanged. The unused tail of each compressed stream is padded
  so each module's 8-bit checksum still matches its unchanged header.
- Only these two modules differ. The boot block (crisis recovery), ME, GbE, flash descriptor, NVRAM, EC
  firmware and the flasher are all byte-identical to Dell's.

## Getting the patched BIOS

The firmware is Dell/Phoenix copyrighted, so this repository contains no firmware. You have two
options:

**A. Build it yourself (recommended).** Download Dell's `M15xA09.iso`, then:

```sh
pip install uefi_firmware
python patch_m15x.py M15xA09.iso out/
```

The script refuses to run unless the input matches the original ISO's SHA-256. It writes the files
below into `out/`. Compare them against [`SHA256SUMS.txt`](SHA256SUMS.txt); they must match exactly.

**B. Download them from [Releases](../../releases).** Verify the hashes against `SHA256SUMS.txt`.

| File | Use |
|---|---|
| `M15xA09-16GB.iso` | Bootable. Booting it auto-runs Dell's `FA.bat`, exactly like the original ISO: full flash with `phlash16 /all`, then the EC flash. |
| `W6702M.A09` | phlash16 file. For a BIOS-only flash from your own DOS boot media: `phlash16 W6702M.A09 /mode=3 /bbl /c /s /exit` (Dell's `F.BAT`). |
| `W6702MB.A09` | Raw 2MB SPI image, for an external programmer. |

Flash on AC power with a charged battery and don't interrupt the flash. Keep the original Dell ISO.
The boot block is untouched, so Phoenix crisis recovery should remain available.

## Related

The M17x R1 has the same symptom. Its BIOS (A07) has not been looked at yet.
