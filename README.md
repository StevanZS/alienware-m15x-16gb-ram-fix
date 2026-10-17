# Alienware M15x: 16GB RAM display fix (BIOS A09)

With two 8GB DDR3/DDR3L SO-DIMMs installed (16GB), the Alienware M15x **works fine in the OS**, but
the BIOS Setup Main page shows **Total Memory 0 MB, Memory Bank 0 0 MB, Memory Bank 1 0 MB**, and
*Advanced → Performance Options* shows **Memory Channel Mode: Single** instead of *Dual*.

This project patches Dell's latest M15x BIOS (**A09**) so Setup shows `8192 MB` / `8192 MB` /
`16384 MB` and *Dual*. Nothing else changes.

> **Status:** ✅ **tested on real hardware.** An M15x (i7-720QM, 2×8GB Samsung DDR3L-1600 running at
> DDR3-1333) flashed with the patched A09 shows Total Memory 16384 MB, Memory Bank 0/1 8192 MB, Memory
> Channel Mode *Dual* and Memory Frequency 1333 MHz. Flashing a BIOS always carries some risk. Proceed
> at your own risk.

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
| `W6702M.A09` | phlash16 file. For a BIOS-only flash from your own DOS boot media (see below). |
| `W6702MB.A09` | Raw 2MB SPI image, for an external programmer. |

### Flashing (tested procedure)

**Your laptop must already run stock A09 / EC 1.18**; Setup's Main page shows both. If it's on an
older BIOS, update it first; see the next section. Boot a plain DOS USB stick (for example FreeDOS
made with Rufus) holding Dell's `PHLASH16.EXE`, Dell's original `W6702M.A09` and the patched
`W6702M.A09` in separate folders.

```
REM 1) back up the BIOS region (reads only; phlash16 needs an image argument, so give it Dell's original)
cd \STOCK
phlash16 W6702M.A09 /mode=3 /RO=A09BAK.ROM /s /exit
REM 2) flash the patched BIOS region: no /all, no /bbl
cd \PATCHED
phlash16 W6702M.A09 /mode=3 /s /v /exit
```

Copy `A09BAK.ROM` to another computer before step 2. The boot block in the patched image is
identical to Dell's, so leaving out `/bbl` loses nothing and keeps Phoenix crisis recovery untouched.
Flash on AC power with a charged battery, and don't interrupt the flash.

## Updating an early M15x (A00/A01) to A09

Dell's A09 can't be flashed directly onto A01. Its `phlash16 /all` fails with **"Undefined error
number -8953"**, before anything is erased. A09 also updates the Intel ME firmware (V1126 → V1185),
and per Dell's notes, ME/GbE update support only arrived in **A02**. The working path is
**A01 → A02 → A08 → A09**:

1. **A02** (`M15xA02.iso`, MD5 `78e5b8562c9edea768eb9c6b4a2d6adb`): its `FA.BAT` has a bug; it calls
   `W6702.A02`, but the file is `W6702M.A02`, so the BIOS step fails and the EC step still runs.
   Run the commands by hand instead:
   `phlash16 W6702M.A02 /mode=3 /bbl /c /s /exit`, then `dflash EC_W670.096`. That gives BIOS A02 and
   EC 0.96.
2. **A08** (`M15xA08.exe`, run in Windows): it builds a bootable USB stick, whose `AUTOEXEC.BAT`
   flashes A02 first if needed, else A08. Its script also flashes the EC even if the BIOS flash
   failed, so it's safer to run it by hand:
   `cd \A08`, `phlash16 W6702M.A08 /all /mode=3 /bbl /c /s /exit /mfg`, and only if that succeeds,
   `rpg` and `dflash EC_W670.116`. That gives BIOS A08, EC 1.16 and ME V1185.
3. **A09** from Dell's ISO files, by hand:
   `phlash16 W6702M.A09 /all /mode=3 /bbl /c /s /exit`, then `rpg` and `dflash EC_W670.118`. That
   gives BIOS A09 and EC 1.18.

The EC flasher switches the laptop off when it finishes, which is normal. `/c` clears CMOS, so set the
date and time again afterwards.

## License

The patch script and documentation are [MIT](LICENSE) licensed. Dell/Phoenix firmware is not covered and remains its owners' property.
