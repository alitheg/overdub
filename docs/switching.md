# Switching root versions

## Getting the script

`dot_firmware.py` is one file. Download it from
<https://raw.githubusercontent.com/bboe/overdub/main/deploy/dot_firmware.py>
into the current directory:

```sh
curl -L -o dot_firmware.py https://raw.githubusercontent.com/bboe/overdub/main/deploy/dot_firmware.py
```

```sh
wget -O dot_firmware.py https://raw.githubusercontent.com/bboe/overdub/main/deploy/dot_firmware.py
```

In Windows PowerShell:

```powershell
Invoke-WebRequest -Uri https://raw.githubusercontent.com/bboe/overdub/main/deploy/dot_firmware.py -OutFile dot_firmware.py
```

Run it with `python3 dot_firmware.py` on macOS and Linux, and with
`py -3 dot_firmware.py` on Windows. It needs Python 3.9 or later, and `adb`
and `fastboot` from Android platform-tools; the README has the install steps
for each system.

## Targets

`dot_firmware.py` takes one optional target. Run it again with another
target to move a Dot to it. The run finds where the Dot is and does only the
steps that are left.

| target | what the Dot ends with |
|---|---|
| `v1-bboe` (the default) | rooted Fire OS 5.5.5.4 on amonet v1.1.0, TWRP 3.7.0_9-bboe2 in recovery |
| `v1` | the same, with amonet v1.1.0's own TWRP 3.2.3 in recovery |
| `v2` | rooted Fire OS 6 8146 on amonet v2.0.0, by amonet v2.0.0's procedure |
| `stock BUILD` | stock Fire OS 6 at that build |

- overdub runs only on Fire OS 5, so only `v1-bboe` and `v1` can host it.
- Keep the Dot on USB for the whole run. No step asks for a button, except
  from stock, which needs the fastboot gesture.
- The times below were measured on a Dot. Each line appears as its step
  ends.

## Between v1-bboe and v1

Only the TWRP in recovery changes, so this takes about 10 seconds.

```console
$ python3 dot_firmware.py v1
Keep the Dot plugged in until dot_firmware.py finishes.
[1/1] writing TWRP 3.2.3 to recovery         (~10 s)  ✅   7s      (7s total)
The Dot is rooted: Fire OS 5.5.5.4 (680767620), SELinux Permissive,
com.amazon.device.software.ota hidden.
Install overdub with deploy/install.py <name>.
```

```console
$ python3 dot_firmware.py
Keep the Dot plugged in until dot_firmware.py finishes.
[1/1] writing TWRP 3.7.0_9-bboe2 to recovery (~10 s)  ✅   7s      (7s total)
The Dot is rooted: Fire OS 5.5.5.4 (680767620), SELinux Permissive,
com.amazon.device.software.ota hidden.
Install overdub with deploy/install.py <name>.
```

## From v1-bboe or v1 to v2

The Dot reboots into its TWRP, takes amonet v2.0.0, and installs Fire OS 6
twice, once to each slot. This takes about 5 minutes. The run also needs
`boot-root.zip` from the XDA thread, and waits for it in `~/Downloads`.

```console
$ python3 dot_firmware.py v2
✅ OTA 8146 verified
Keep the Dot plugged in until dot_firmware.py finishes.
[1/8] waiting for recovery                   (~40 s)  ✅  30s     (30s total)
[2/8] installing amonet v2.0.0               (~40 s)  ✅  19s     (49s total)
[3/8] waiting for v2.0.0 recovery to start   (~40 s)  ✅  19s  (1m 08s total)
[4/8] wiping cache and data                  (~10 s)  ✅   3s  (1m 11s total)
[5/8] installing Fire OS 6 8146, first slot  (~2 min) ✅  96s  (2m 47s total)
[6/8] installing Fire OS 6 8146, second slot (~2 min) ✅ 117s  (4m 44s total)
[7/8] installing boot-root                   (~10 s)  ✅   8s  (4m 52s total)
[8/8] waiting for rooted Fire OS 6 to boot   (~1 min) ✅  24s  (5m 16s total)
The Dot is rooted: Fire OS 6574.1 (NS65741/8146), with root adb.
overdub cannot run on Fire OS 6. dot_firmware.py with no target roots it on
Fire OS 5.5.5.4, which overdub runs on.
```

## From v2 back to v1-bboe or v1

The Dot reboots into v2.0.0's TWRP, which writes amonet v1.1.0 back. Fire OS
5.5.5.4 then goes in as for a first root. This takes about 8 minutes, most of
it Fire OS 5's first boot. `v1` adds one step at the end, which writes TWRP
3.2.3 to recovery.

```console
$ python3 dot_firmware.py
This Dot runs rooted Fire OS 6 on amonet v2.0.0. Rebooting it into recovery.
Keep the Dot plugged in until dot_firmware.py finishes.
[1/8] waiting for v2.0.0 recovery to start   (~40 s)  ✅  19s     (19s total)
[2/8] writing amonet v1.1.0's bootloader     (~45 s)  ✅  43s  (1m 02s total)
[3/8] waiting for TWRP 3.7.0_9-bboe2         (~30 s)  ✅  27s  (1m 29s total)
[4/8] formatting userdata                    (~5 s)   ✅   3s  (1m 32s total)
[5/8] writing Fire OS 5.5.5.4's /system      (~100 s) ✅  79s  (2m 51s total)
[6/8] writing the boot image                 (~5 s)   ✅   3s  (2m 54s total)
[7/8] installing Magisk 17.3                 (~5 s)   ✅   1s  (2m 55s total)
[8/8] waiting for rooted Fire OS 5 to boot   (~4 min) ✅ 321s  (8m 16s total)
The Dot is rooted: Fire OS 5.5.5.4 (680767620), SELinux Permissive,
com.amazon.device.software.ota hidden.
Install overdub with deploy/install.py <name>.
```

## To and from stock

- `dot_firmware.py stock 8146` restores stock from any target, in 17 steps
  from v1-bboe. It erases the whole Dot.
- From stock, any target starts again with the fastboot gesture.
  [docs/rooting.md](rooting.md#back-to-stock-the-stock-target) has the
  details.

## A Dot that shows no light: `--short`

Use `--short` only for a Dot that shows no light and gives no fastboot after
the gesture. Its eMMC test point must be shorted to reach the bootrom.
`--short` goes with any target:

```sh
python3 dot_firmware.py --short          # to v1-bboe
python3 dot_firmware.py --short v1
```

1. Start the run with the Dot unplugged. It asks: "Short the Dot's test
   point and plug it in."
2. Hold the short and plug the Dot in. If the Dot starts normally, the run
   prints "Short 1 missed". Unplug, short, and plug it in again.
3. When the run prints "The bootrom answered. The short may come off now",
   take the short off. The run waits 5 seconds, then goes on.

The bootrom step writes amonet v1.1.0, and the run continues from its TWRP as
for any other Dot. `--short` to `v2` was never run on hardware.
[docs/rooting.md](rooting.md#a-dot-that-shows-no-light) has the details.
