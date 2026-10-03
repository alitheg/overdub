# Rooting

`deploy/dot_root.py` takes a Dot from stock Fire OS 6 to rooted Fire OS 5.5.5.4.
It polls the Dot every 2 seconds, does the stage for the state it finds, and
stops when the Dot is rooted.

| state | how it is recognised | what the run does |
|---|---|---|
| stock-booted | `adb get-state` says `unauthorized`, or Fire OS 6 without root | prints the fastboot gesture |
| stock-fastboot | `unlock_status` is `false` | amonet v2.0.0 fastbrick |
| amonet-v2-twrp, v2-fastboot | unlocked, `lk_build_desc` is not v1's | downgrade to amonet v1.1.0 |
| v1-fastboot | `lk_build_desc` is `f379dba-20170906_000423` | v1 TEE and TWRP 3.7.0_9-bboe1, then recovery |
| amonet-v1-twrp | recovery, v1's `lk_build_desc`, another `ro.twrp.version`, `mtp` in `sys.usb.config` | writes TWRP 3.7.0_9-bboe1 to `recovery`, reboots into it |
| bboe-v1-twrp | recovery, v1's `lk_build_desc`, `ro.twrp.version` 3.7.0_9-bboe1, `mtp` in `sys.usb.config` | Fire OS 5.5.5.4, boot and /system patches, Magisk 17.3 |
| rooted | `sys.boot_completed` is 1 and `su -c id` answers uid 0 | hides the updater and checks it |

- A probe can land part way through a boot. TWRP answers adb before it sets
  `ro.twrp.version`, and while USB drops for MTP, `adb shell` answers with
  adb's error text. Either would read as v2's and start a downgrade. So an
  `lk_build_desc` not shaped like `f379dba-20170906_000423`, or a version not
  starting with a digit, reads as `starting`. The downgrade checks the shape
  too.
- An empty `lk_build_desc` would read as v2's and repeat the downgrade, `boot0`
  erase included. So an empty or timed-out fastboot read, and any poll that
  times out, reads as `starting` too.
- `su` answers about 27 seconds into Fire OS 5's first boot, while
  `dumpsys package` still answers `Can't find service: package`. So rooted
  also needs `sys.boot_completed` to be 1.
- Each state is acted on at most once per run, so a stale read cannot repeat a
  stage. The downgrade ends with v1-fastboot's stage, and counts as both.
- The script waits without limit for a Dot and for the fastboot gesture. Any
  other state unchanged for 10 minutes stops it, and the next run redoes that
  stage.
- Rooting formats userdata. A registered Dot loses its Wi-Fi and its Alexa
  registration, and finishes in setup mode.
- After boot0 is erased the Dot shows up only as the bootrom's serial
  port, which no probe sees. So the script creates `boot0-erased` in its cache
  before the erase. It deletes the file once amonet logs `Reboot to unlocked
  fastboot`, when boot0 is written, and whenever it sees the Dot in any state.
- A run that finds the file and no Dot runs the bootrom step again, without
  the erase. A run stopped inside amonet's payload leaves the Dot there, and
  the payload never restarts. So the resume first sends every MediaTek port
  the payload's reboot command, `0xf00dd00d` then `0x3000`; the Dot came back
  as its bootrom within 5 seconds. Sent to the bootrom itself, it is not
  tested.
- The command goes out before amonet starts. amonet records the ports at
  start and takes any port that appears later as the Dot, so a command sent
  after it starts could reach a Dot it is already talking to. A port reset
  before amonet starts is either still gone or back as the bootrom; in the
  second case amonet takes it at the bootrom's next reset.
- On macOS, a run stopped 12 seconds into amonet's `tz` write resumed this
  way: amonet found the bootrom 4 seconds after the run started, and the root
  finished.
- With boot0 erased the bootrom resets every 30 to 40 seconds: from Linux,
  the resume went on after 30 seconds, twice, with no one touching the Dot.

## A Dot that shows no light

- A Dot whose preloader runs but never starts LK shows no light and offers
  no fastboot: only `0e8d:2000`, the preloader, for about a second every
  30 seconds. Its boot0 is intact, so only the eMMC test-point short reaches
  the bootrom. `--short` runs the bootrom step for it, with no erase.
- Neither the gesture nor `FACTFACT` sent to the preloader reached fastboot.
  mtkclient's four ways to crash a preloader into the bootrom all failed on
  Amazon's: each came back as the preloader. Its register writes echoed but
  took no effect.
- The bootrom step takes only a port it can identify as the bootrom,
  `0e8d:0003`, vendor and product both, and skips a stock preloader's, which
  `--short` reports as a missed short. amonet would otherwise take whichever
  port appears first.
- It reads ports from pyserial's USB list and opens only the bootrom's, where
  amonet opened every port to see it. On Linux the preloader's
  `/dev/ttyACM*` is `dialout`'s, and the udev rule covers only `0e8d:0003`,
  so amonet's way would never see a missed short.
- It remembers each port by name and USB ID, and checks every port on each
  poll. The bootrom usually reuses the preloader's name: on Linux both are
  `/dev/ttyACM0`, and macOS names a port by its USB location. A port whose ID
  cannot be read yet, or that will not open yet, is looked at again on the
  next poll.
- `--short` applies only to a Dot first seen with nothing on USB. Any other
  state turns it off, so a later reboot cannot start the short flow. That
  includes `starting`, which covers a probe that timed out as well as a Dot
  half started; after a timeout the run asks for fastboot, and a rerun with
  `--short` goes on.
- amonet takes any port that appears after it lists the ports at start. So
  `--short` asks for the short only once amonet logs that it is waiting, and
  sends no reset first: a Dot plugged in earlier would be in that list and
  never count as new.
- The short must be off before the payload starts the eMMC, its first act.
  Nothing before that touches the eMMC, and the handshake disables the
  watchdog. So the run holds amonet at its "Remove the short" prompt, counts
  down 5 seconds, then lets it go on.
- amonet's first write clears boot0's header. Under `--short` its bootrom
  step creates `boot0-erased` just before each write, after amonet's first
  boot0 check, as the erase path creates it before the erase. A run that
  stops before that leaves boot0 intact and no file. A run that fails or is
  stopped after it is resumed by a plain rerun.
- The bootrom step logs the error for a port it still cannot open after 1
  second, once, and the run shows it at once. udev can set a new port's mode
  a moment after it appears, so a first failure alone means nothing. A
  missing udev rule or ModemManager would otherwise hold a `--short` run
  silent for 10 minutes.
- The payload starts the eMMC once. With the short still on, either it never
  comes up and amonet times out waiting for it, or the bootrom step's first
  read, the partition table's `55 AA`, fails. Both stop the run before amonet
  writes anything, and a new short starts over.

## Recording a run

- `--verbose` prints each `adb` and `fastboot` command and its exit status
  with the time, and each state `dot_root.py` sees. The 2-second polls are not
  printed.
- `--delay [SECONDS]` implies `--verbose` and counts down before each stage:
  10 seconds by default in `dot_root.py`, 5 in `dot_restore_stock.py`. A
  recording then shows each state settled, and the times match frames to
  commands.
- The delay goes only between stages, because timing inside one matters. The
  fastbrick relies on an 8-second timeout, and amonet v1.1.0's bootrom step
  starts before boot0 is erased and has 60 seconds to find the port.
- In verbose mode, or when the output is not a terminal, a stage prints a line
  when it starts and when it ends, with no running count. Output that is not
  a terminal also gets no download meter. Either would fill a log with `\r`
  frames.
- A running stage shows a Braille spinner where its mark will go, and a
  finished stage prints ✅. Where the output's encoding cannot represent them,
  the spinner is `|/-\` and the mark is `done`. On Windows that is output
  redirected to a file: since Python 3.6 a console reports UTF-8 whatever its
  code page.
- A finished line is at most 79 columns, so it does not wrap on an 80-column
  terminal. That caps a stage label at 36 characters.
- Each stage is numbered by its place in a root from stock, `[1/9]` to
  `[9/9]`. A run that starts part way starts part way through the count. The
  resume after a stopped bootrom step shows both of its stages as `[3/9]`,
  the downgrade they finish.
- Each stage's estimate is an upper bound: the slowest time measured for it,
  rounded up to the next 5 seconds, over roots on macOS and Windows 11. One
  case is left out: a first run on a computer that finds the Dot already in
  TWRP waits up to 35 s more for the system image.
- The estimates and the rings are from TWRP 3.2.3. One root on macOS with
  TWRP 3.7.0_9-bboe1 came in under every estimate. Its replace step took 37 s
  on bryce.

The ring during `dot_restore_stock.py 6302`, from a rooted Dot not set up:

| ring | when |
|---|---|
| purple | rooted Fire OS 5, setup timed out (`anim_OOBE_start_error`) |
| off, about 12 s | `adb reboot recovery` |
| deep blue, about 10 s, a brighter segment turning in its last 3 | v1.1.0's TWRP starting, before adb answers |
| a blink off, then a cyan arc turning, about 3 s | TWRP up: `adb wait-for-recovery` returns |
| cyan, whole ring, steady | through all 15 steps |
| off, about 8 s | the reboot into stock |
| deep blue, about 50 s | stock Fire OS 6 booting |
| cyan and blue, turning | Fire OS 6 starting Alexa |
| orange | setup mode, about 90 s after the reboot |

The ring during `dot_root.py` from stock 8138, in daylight, 14 min 49 s in all:

| ring | when |
|---|---|
| green | stock fastboot, from the button held at power-on |
| off 1 s, then yellow, shrinking to an arc over about 10 s | the fastbrick flashed |
| red-orange, about 7 s, then green, about 5 s, then off, about 5 s | the exploit, before TWRP |
| blue, about 9 s | v2.0.0's TWRP starting |
| a white flash, a blink off, a cyan arc | v2.0.0's TWRP up; the script reboots it at once |
| off, about 7 s | `adb reboot bootloader` |
| a white flash, then every color, turning, about 5 s | the exploit's unlocked fastboot |
| off, about 5 min | the bootrom step: `boot0` erased, then amonet v1.1.0 |
| blue, about 10 s, then every color, turning, about 3 s | v1.1.0's fastboot, while `tee2` and `recovery` are flashed |
| off, about 5 s | `fastboot oem reboot-recovery` |
| deep blue, about 13 s | v1.1.0's TWRP starting |
| blinks off, a cyan arc turning, about 5 s | adb answers, MTP not yet on |
| cyan, whole ring, steady | from MTP on, through the Fire OS push and install |
| off, green, off, green, off, about 5 s | the Fire OS install finishing |
| cyan, whole ring, steady | the boot image, /system and Magisk |
| off, about 6 s | the first reboot into Fire OS 5 |
| deep blue, about 35 s | Fire OS 5 booting |
| cyan and blue, turning, about 3 min | the first boot, until `sys.boot_completed` |
| orange | setup mode, as the script finishes |

A run from stock 5041, at night, showed the same, except that the fastbrick
ring read white.

## The host

- Python 3.9 or later, because stock macOS's `/usr/bin/python3` is 3.9.6. CI
  byte-compiles both scripts under 3.9's parser and runs their `--help`.
- The standard library only, so the scripts run on stock macOS, Linux and
  Windows. adb and fastboot are the only external tools.
- On a terminal, `ERROR:` lines are red and warnings yellow. `NO_COLOR`,
  `TERM=dumb`, a pipe or a file turns that off. On Windows only Windows
  Terminal (`WT_SESSION`) gets color; the old console prints the escape codes
  as text. A blank line separates one kind of message from another.
- adb must be 1.0.36 (platform-tools r24) or newer: 1.0.32 answers
  `wait-for-recovery` with `unknown host service` and passes `shell -n` to the
  device. Ubuntu 22.04 and 24.04 ship 1.0.41. fastboot must accept `-S`, as
  every release back to r19 does. Both scripts check `adb version`, and
  `dot_root.py` checks `fastboot --help` for `-S`. Old releases, run with no
  device, set these floors.
- Downloads go to `$XDG_CACHE_HOME/overdub-root` (default
  `~/.cache/overdub-root`), or `%LOCALAPPDATA%\overdub-root` on Windows. Each
  is checked against a pinned SHA-256 before use and on every run. The amonet
  trees unpacked from the two zips are not, and neither is the system image
  built from Fire OS's.
- The system image's folder is named after the zip's hash, so a new zip builds
  a new image. Its `md5` file is written last and synced with the image before
  the folder is renamed into place, so the script trusts a folder only when
  `md5` is in it, and rebuilds otherwise. If the md5 read back still fails
  after the last try, the script deletes `md5` alone, which works even when
  another process holds the image open.
- `dot_root.py` starts the downloads in a background thread at the first probe
  that does not find the Dot booted, rooted or starting. So they overlap the
  wait for a Dot and for the fastboot gesture. A Dot found rooted needs no
  download.
- The main thread waits for that thread's locks half a second at a time.
  On Windows, Ctrl-C does not interrupt a blocking lock wait, so one long wait
  would ignore it until a 397 MB download ended.
- The downloads are about 476 MB. With the unpacked trees and the system
  image, the cache holds about 1.3 GB.
- Before it changes the Dot, the script waits for every download and checks
  it. A bad download then stops the run with the Dot unchanged.
- The downloads run one at a time, amonet v2.0.0's zip first, because the
  fastbrick needs it first. Fire OS is 397 MB of the 476, and one download
  filled the line at about 40 MB/s, so parallel downloads would only delay
  that zip.
- Nothing deletes a download, so a later run downloads nothing. A rooted run
  prints the cache's path and size and says it is safe to delete.
- amonet v1.1.0's bootrom step needs pyserial. The script puts the pinned pure
  wheel from PyPI on the child's `PYTHONPATH`, and Python imports it straight
  from the zip. Nothing is installed.
- `adb get-state` reports `unauthorized` on stderr, so the script reads both
  streams.
- Without `ANDROID_SERIAL`, both scripts use the one Dot on USB in
  `adb devices -l`, and ignore network adb: a rooted Dot with tcp/5555 open
  would make `adb get-state` answer `more than one device/emulator`.
  `dot_root.py` picks again on every poll, because each reboot drops the Dot
  off USB. With two Dots on USB the scripts stop and ask for `ANDROID_SERIAL`,
  which fastboot follows too.
- An `ANDROID_SERIAL` of the form `host:port` is refused: TWRP starts no
  Wi-Fi, and fastboot and the bootrom are USB-only.
- Windows 11's adb prints no `usb:` field, so there a line counts as USB unless
  its serial is `host:port` or `emulator-*`.

### Linux permissions

- adb, fastboot and the bootrom's serial port need the Dot's USB devices open
  to the user. Ubuntu's `android-sdk-platform-tools-common` and
  android-udev-rules, which LineageOS points to, list other Lab126 products
  but not a booted Dot, `1949:0112`. Other guides for this Dot use `sudo`.
  These rules, in `/etc/udev/rules.d/51-echo-dot.rules`, cover every state,
  the serial port included:

  ```
  SUBSYSTEM=="usb", ATTR{idVendor}=="1949", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", ATTR{idProduct}=="4ee2", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", ATTR{idProduct}=="d001", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  SUBSYSTEM=="usb", ATTR{idVendor}=="0bb4", ATTR{idProduct}=="0c01", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", ATTR{idProduct}=="0003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  SUBSYSTEM=="tty", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  ```

  Then `sudo udevadm trigger`. Fire OS 5 is `1949:0112` (`mtp,adb`, set by its
  own init scripts, the same on every Dot), TWRP `18d1:d001` and
  `18d1:4ee2`, fastboot `0bb4:0c01`, and the bootrom `0e8d:0003`.
- `uaccess` covers a user at the machine; over SSH the user must be in
  `plugdev`. A new group reaches only new processes, and a running adb server
  keeps its old permissions: with the group added and the old server up,
  `adb devices` listed nothing. So after `adb kill-server`, `sg plugdev -c`
  runs a script with the group, without a new login.
- Both scripts refuse to run as root: the downloads would belong to root, and
  the rules make `sudo` needless.
- Both check at start that the process has `plugdev`, even for a desktop user
  covered by `uaccess`, so the check is one question. A user not in the group
  gets the rules and the commands to add them, starting with
  `groupadd -f plugdev` because Fedora and Arch have no such group. A user in
  `/etc/group` whose shell predates that gets `adb kill-server` and the `sg`
  line. The `sg` line repeats the command as run, interpreter, path and flags
  included, because `./dot_root.py` is wrong from another directory.
- Without the rules adb lists the Dot as `no permissions`. When that lasts 5
  seconds and nothing else listed is usable, both scripts stop and print the
  same rules and commands. A shorter spell is udev still setting permissions.
  A phone the user cannot open, beside a Dot that answers, does not stop them.

## Unlock: amonet v2.0.0

- The fastbrick image is `fastbrick-20221007.img` when `lk_build_desc` is
  `63cb91b-20221007_072309`, else `fastbrick.img`.
- A flash that returns means the exploit did not start, so it is retried, up to
  10 times. A flash still running after 8 seconds means the exploit is running:
  the script stops fastboot and leaves the Dot to reach TWRP.
- `eMMC-RO` or `Device mismatch` means the payload refused, and the Dot is
  unchanged.

## Downgrade: amonet v2.0.0 to v1.1.0

- v1.1.0's `modules/main.py` must start **before** the Dot reboots: it records
  the serial ports that exist, then waits for a new one. The erase runs only
  if main.py is still running 3 seconds after it starts.
- The script stops if `lk_build_desc` is already v1.1.0's: a second erase
  gains nothing. In v2's fastboot it reads `getvar`, and in v2's TWRP
  `ro.boot.lk_build_desc`.
- From v2's TWRP it clears boot0's 4 KiB header there, the way
  `dot_restore_stock.py` does, reads it back as zeros, and runs `adb reboot`.
  This saves the 11 s reboot into fastboot. From v2's fastboot it runs
  `fastboot erase boot0` and `fastboot reboot`. Either way the Dot drops into
  its bootrom.
- The script feeds main.py the newlines its prompts read, and is done when
  main.py logs `Reboot to unlocked fastboot`.
- The bootrom is `0e8d:0003`. On macOS it is `/dev/cu.usbmodem*`. On Linux it
  is `/dev/ttyACM*`, owned by `dialout` unless the udev `tty` line gives it to
  `plugdev`, and ModemManager can grab it first. On Windows it is a COM port
  that needs MediaTek's VCOM driver (`cdc-acm.inf`, class Ports), installed by
  hand; without it `0e8d:0003` is an unknown device.
- No other mode needs a hand-installed driver. On a Windows machine that rooted
  a Dot, the driver store held only MediaTek's and Amazon's
  `FireDevicesUsbDeviceClass`, which serves amonet fastboot (`0bb4:0c01`) and
  can come through Windows Update. Windows bound adb (`1949:0112`) and
  v1.1.0's TWRP (`18d1:4ee2`) itself. TWRP 3.7.0_9-bboe1 is untried on
  Windows. macOS and Linux need no driver.
- main.py's `serial_ports()` silently skips a port it cannot open, and waits
  forever. So the script gives it 60 seconds after the reboot to log
  `Found port`, then stops it and says why a port may be missing.
- The script runs main.py through `BOOTROM_PY`, with v2.0.0's payload from the
  zip the fastbrick already uses. It writes 64 blocks per command, `0x1003`.
  On macOS and Windows the step takes 18 s from `Found port` to the reboot.
- v2.0.0's payload ends a 512-byte block read and a 256-byte RPMB read on a
  full packet, and Windows' VCOM driver waits for a short packet. So
  `BOOTROM_PY` follows each read with a 4-byte read, `0x5000`, and drops those
  4 bytes.
- v1.1.0's `fastboot-step.sh` ships a Linux-only fastboot. The script runs its
  three commands with the host's fastboot instead: `bin/tz.img` to `tee2`,
  TWRP 3.7.0_9-bboe1 to `recovery` in place of v1.1.0's `bin/twrp.img`, then
  `fastboot oem reboot-recovery`.

## Install: TWRP 3.7.0_9-bboe1

- The image is the `biscuit` build of
  [bboe/twrp_device_amazon_echo-mt8163](https://github.com/bboe/twrp_device_amazon_echo-mt8163),
  on [bboe/android_kernel_amazon_biscuit](https://github.com/bboe/android_kernel_amazon_biscuit).
  GitHub Actions builds and attests both. The script pins the SHA-256.
- v1.1.0's TWRP 3.2.3 moved 5.1 MB/s over adb, and 7.0 MB/s with cpu0 at
  `performance`. This one moves 21.5 to 22.2 MB/s with either governor.
- v2.0.0's TWRP cannot replace 3.2.3. Its kernel is 32-bit, and v1.1.0's LK
  starts only a 64-bit one.
- The kernel is Amazon's 5.5.5.4 one with two back-ports for Android 9's
  `init`. Without SELinux ioctl permissions, `init` fails with `avtab: invalid
  type or class`. Without `mmap_rnd_bits`, it fails with `Unable to set
  adequate mmap entropy value!`.
- amonet v1.1.0 keeps its image in `boot_a` and `boot_b`. Fire OS boots from
  `boot_a_x` and `boot_b_x`. This build points `/boot` at `_x`. The script
  does not rely on that. It writes `boot<slot>_x` and `system<slot>` by name.
- `dd` to a by-name path that does not exist writes a file in TWRP's RAM
  `/dev`, and the read-back of that file matches. So each target must be a
  block device.
- A Dot in TWRP 3.2.3 gets this TWRP written to `recovery` and checked by
  md5. Then the script runs `adb reboot recovery`. The script drops 3.2.3's
  `__bionic_open_tzdata...` lines from `adb shell` output.
- adb answers before MTP is on, as `18d1:d001` in this TWRP. The switch to
  `mtp,adb` puts the Dot back on USB under a new ID. In 3.2.3 the switch
  failed a push with `failed to read copy response: EOF`. So both scripts
  wait for `mtp` in `sys.usb.config`, and each push gets 3 tries.
- This TWRP's `adb shell` returns the exit status. So each device-side step
  is a pushed script, judged by its status. The scripts have `\n` line
  endings.
- The userdata script unmounts `/sdcard` and `/data`. It formats userdata
  with `mke2fs -t ext4 -b 4096 <userdata> <blocks - 256>`, which leaves 1 MiB
  for the crypto footer. Then it mounts it with `mount -t ext4`. The script
  sets the size, block size and type itself, so the result does not depend on
  the TWRP's fstab.
- `/data` is then checked as a mountpoint. Otherwise Magisk's database lands
  in TWRP's RAM and is gone after the reboot.
- This TWRP mounts system at `/system_root`. So the script patches Fire OS's
  system at `/tmp/fireos-system`.
- A failed patch leaves that mount. So the write first unmounts both.
  `/proc/mounts` keeps the name `mount` was given. So the check looks for the
  by-name link and the `mmcblk0p` node.
- Never `umount -a` in TWRP: it unmounts `/proc`.
- Every push is read back by md5 on the device.

## Writing Fire OS

- The OTA zip's installer does 2 writes that matter: `system.new.dat` to
  `other-system` and `boot.img` to `other-boot`. In TWRP 3.2.3 those name
  the current slot's `system` and `boot_x`. Its LK write goes nowhere:
  amonet's TWRP links `other-lk` to `/dev/null`. Its TEE and preloader are byte
  for byte the ones v1.1.0 already wrote to `tee1` and boot0. Its last write,
  `target.blocklist` to `/cache/recovery/last_blocklist`, is skipped: it is a
  file in `/cache`, not a partition the Dot boots from.
- So the zip never goes to the Dot. The host builds the partition image from
  `system.transfer.list` once, into the cache: 805 MB, 386 MB gzipped. It
  takes 22 s on macOS and 31 s on Windows 11, in a background thread, while
  the Dot unlocks and downgrades.
- The built image matched `system_a` after a `twrp install` in every MiB that
  the root's own edits and ext4's mount metadata leave alone.
- `adb shell` streams the gzip into `gunzip | dd` on the Dot, so transfer,
  unpacking and writes overlap. `adb push` cannot feed a pipe.
- TWRP 3.2.3 used `adb exec-in`: 65 s on macOS, 76 s on Windows 11. It
  returned up to 10 s before `dd` ended.
- This TWRP's `exec-in` drops unread input: 1 MiB arrived as 890,197 bytes.
  Its `adb shell` carried 100 MB intact from macOS. This TWRP's `adb shell`
  returns when `dd` ends, with `dd`'s status. Windows is untried.
- On bryce the gzip reaches `cat > /dev/null` in 17.5 s. Through `gunzip`,
  with the output discarded, it takes 36.4 to 39.3 s. So `gunzip` on the Dot
  is the limit. The whole stage, with the eMMC and the md5 read-back, took
  80 s. With TWRP 3.2.3 it took about 77 s.
- The partition is then read back by md5: 12 s with 3.2.3.

## The boot image

- The host builds it from the zip's `boot.img` and Magisk 17.3's zip, in
  about 2 s. Nothing runs magiskboot or Magisk's installer.
- 17.3 is the last 17.x. Up to v25.2 support Android 5.1, but from v18 the
  boot patch and `service.d` path change, and overdub needs only `su` and
  `service.d`.
- The ramdisk loses `verify` from every fstab, and `default.prop` gets
  `ro.secure=0`, `ro.debuggable=1` and `persist.sys.usb.config=mtp,adb`.
- The cmdline is the 512-byte header field at offset 64. Stock is
  `bootopt=64S3,32N2,64N2`; the script appends
  ` androidboot.selinux=permissive`.
- Then Magisk's patch, as its `boot_patch.sh` does it with `KEEPVERITY` and
  `KEEPFORCEENCRYPT` false. `init` becomes `magiskinit` and `verity_key`
  goes; both originals go into `.backup`, with `.magisk`. In the kernel,
  `skip_initramfs` becomes `want_initramfs`.
- Magisk's installer also kept a gzipped copy of the image as it found it,
  `/data/stock_boot_<sha1>.img.gz`, and named it in `.backup/.sha1`. Only
  Magisk Manager's image restore reads them, and `dot_restore_stock.py` is
  the way back to stock here, so neither is written.
- The kernel is a 512-byte MTK header, a gzip stream and the dtb. Only the
  stream and the header's size field change. The ramdisk's cpio is written as
  magiskboot writes one: sorted, inodes from 300000, every mtime 0.
- The stock image ends in a 2,048-byte signature. It is dropped, as magiskboot
  dropped it: the unlocked LK does not check it.
- Compared with the image that `twrp install` of Magisk wrote on bryce, every
  header field, the kernel, the dtb and every ramdisk file are the same, except
  in 3 places. There is no `.backup/.sha1`. 3 symlinks keep their stock modes,
  where busybox's `cpio` had made them 0777. The MTK header keeps the stock 0xff padding, where
  magiskboot wrote zeros.
- The image is padded to a 4096-byte multiple and written with `dd`.
  After `sync` the page cache is dropped, and the partition is read back by
  md5 over the image's length.
- The host's copies go in a folder under the cache, not in `%TEMP%`. On
  Windows 11, reading a newly written boot image from `%TEMP%` stalled for
  10.3 s in 10 of 15 trials, and in 0 of 23 trials elsewhere.

## /system

- `/system/etc/init.fosflags.sh` strips adb from the USB configuration on
  every boot after the first. The script forces its `FOS_FLAGS_ADB_ON` branch
  true and neutralizes its `unset_adb_persistent_property` calls, then checks
  both edits.
- The 4 Amazon update hosts go into `/system/etc/hosts` as `127.0.0.1`.

## Magisk

- The zip's installer is not run. Its writes go to the Dot as one cpio
  archive, which TWRP's `cpio` unpacks into `/data`. The files are then read
  back as one md5, over all of them in name order.
- `/data/adb/magisk` gets `arm/`, `common/` and `chromeos/` from the zip, mode
  755, and the installer's busybox: base64 of xz, in `update-binary` as
  `BB_ARM`.
- It also gets `magisk`, which `boot_patch.sh` wrote with
  `magiskinit -x magisk`, and which module installs and `--unlock-blocks` run.
  It is the xz stream in `magiskinit` that unpacks to an ELF. Unpacked on the
  host and by `magiskinit -x` on bryce, its md5 is the same. `/sbin/magisk.bin`
  differs from it in 62 bytes, which magiskinit randomizes at boot.
- On a new userdata the installer wrote to `/data/magisk`, because
  `/data/adb` did not exist yet. Magisk's daemon moved it to `/data/adb/magisk`
  at the first boot. The script writes there directly.
- The rest of the installer changes nothing on this Dot: there is no
  `/system/addon.d`, no `su` in `/system`, and nothing in `/data` to migrate.
- Its database is seeded so the adb shell gets root without a prompt the Dot
  cannot show: `/data/adb/magisk.db`, mode 600, with
  `policies(uid INT, package_name TEXT, policy INT, until INT, logging INT,
  notification INT)` holding `(2000, 'com.android.shell', 2, 0, 1, 0)`.

## The updater

- An update would replace the boot image and remove root. On the first rooted
  run the script runs `pm hide com.amazon.device.software.ota`, then reads
  `hidden=true` back from `dumpsys package`.

## Back to stock: dot_restore_stock.py

`deploy/dot_restore_stock.py <build>` returns a Dot on amonet v1.1.0 or v2.0.0
to stock Fire OS 6, to test `dot_root.py` from a clean start. It follows
`dot_root.py`'s host rules.

- v2.0.0's TWRP mounts by name, under `/dev/block/platform/.../by-name/`, so an
  unmount pattern anchored on `/dev/block/mmcblk0` matched nothing there and
  the writes went to mounted filesystems. What is left mounted is named
  back, because a refused `umount` is otherwise silent and the Dot has no
  screen to look at.
- That TWRP carries two `dd` implementations which do not take the same
  operands: the one on the path answers `conv option disabled`. So the script
  uses `toybox dd` where it exists. TWRP 3.7.0_9-bboe1's `toybox dd`
  truncates at its `seek` offset, and the block device answers `ftruncate:
  Invalid argument`. So the one write with `seek` passes `conv=notrunc` to
  `toybox dd`. `sgdisk`, `mke2fs`, `blockdev` and `md5sum` are looked for before
  the countdown, because `sgdisk` and `mke2fs` are not reached until after
  1.6 GB has gone in.
- 6.5.5.5 (4310M) and Fire OS 5 ship a block image (`system.new.dat`) rather
  than a `payload.bin`, which is why they are not among the builds offered.
- FTVDB keys an OTA by its md5, so the download is found by md5 and then
  pinned by the SHA-256 of a copy that matched it.
- Each `payload.bin` operation is read where it lies, so peak memory is one
  image plus one operation: about 1 GB, for the 768 MB system image.
- A passed check prints `ok` rather than the tick where the console cannot
  encode it, such as a legacy Windows code page, which would otherwise raise
  `UnicodeEncodeError`.

### The partition table

- amonet v1.1.0 renames the stock `boot_a` and `boot_b` to `boot_a_x` and
  `boot_b_x`, and adds its own `boot_a` and `boot_b` as partitions 17 and 18,
  cut from the end of userdata. The stock table is rebuilt from the Dot's own
  rather than from constants, so every offset and size is that Dot's.
- The backup table goes in before the primary: a run cut during the primary's
  write leaves it mixed, and the backup is what the next run reads instead.
- A table with no `_x` name is already stock and is kept: that is what makes a
  stopped run safe to repeat, and why v2.0.0's table needs no surgery.
- The kernel must reread the table before cache and userdata are formatted.
  Against the old one, `mke2fs` would size userdata to amonet's cut.
- The first run on a Dot saves that Dot's own table as
  `current-gpt-<serial>.bin`, and later runs keep it rather than saving the
  stock table over it.

### The writes

- boot0's header is cleared first. With no valid preloader the bootrom waits
  for a USB host instead of running anything, so a failure anywhere after it
  leaves a Dot that amonet's bootrom step can reach without opening the case.
  Forced on hardware: the bootrom appeared 4 seconds after the reboot and
  waited about 39 seconds, and amonet v2.0.0's bootrom step rewrote the whole
  chain in 22. The order of the bootchain writes is not what makes this
  survivable -- with the header gone, no slot is reachable anyway -- the
  bootrom is.
- `misc` gets zeros and, at 0x360, the boot control block amonet v1.1.0's
  bootrom step writes: `00 41 42 42 01 8f 00`. Slot a is then `prio 15 tries 0
  success 1`, and slot b is unused. Each slot byte packs `priority:4`,
  `tries:3` and `success:1`, low bits first.
- A zeroed `misc` is not safe. Stock's first boot starts slot a with a few
  tries and `success 0`, and each boot cut short spends one. On bryce, a
  gesture during the first boot and the power-ons after it left slot a at
  `tries 0 success 0`. The preloader then reset about every 30 s with the ring
  dark, never starting LK, so neither the gesture nor `FACTFACT` reached
  fastboot. It did not fall back to slot b, whose tries stayed at 3. It took
  the eMMC short to get back. A slot marked good spends no tries.
- `tee1` and `tee2` are not slot-tied: every slot-tied partition takes a letter
  from `ro.boot.slot_suffix` and TEE takes digits, and amonet's bootrom step
  writes its LK to both slots unconditionally, but its TEE payload only to
  `tee1`. So they go backup first and
  primary last, rather than by slot.
- boot is padded with zeros to the size of `boot_a`, which both slots share, so
  no byte of amonet's boot image survives and the read-back covers the whole
  partition.
- TWRP answers `ro.product.device` only because amonet's TWRP sets it in its
  `default.prop`; a recovery ramdisk has no reason to carry it otherwise.
- A push and its read-back address different block devices, whose page caches
  are not coherent, so the cache drop between them is what makes the comparison
  mean anything.
- `adb push` to a node that does not exist does not fail: adbd creates a
  regular file in TWRP's RAM-backed `/dev` and reports success. A partition's
  read-back still catches that, because it reads the raw disk at the computed
  offset rather than the node -- but only after 768 MB has gone into a tmpfs on
  a 512 MB device. So the start sector is checked first, which also catches a
  number that means a different partition in the table the kernel still holds.
- boot0 is the one write whose read-back names the node it wrote, so there a
  regular file would match itself and pass. Hence the check that it is a block
  device. For the same reason the cleared header is counted twice, bytes read
  and bytes left non-zero: a read that returned nothing would otherwise look
  like a header that cleared.
- The skip compares a mebibyte of head first, which costs 0.07 s and is enough
  because a different build differs within a kibibyte or two. Only a partition
  that looks right pays for the full comparison, 13 s against the 80 s a write
  would take.
- A restored Dot has no Wi-Fi until it is set up in the Alexa app. To root it
  again, skip that setup: on Wi-Fi it can update to a build `dot_root.py` has
  not met, or away from the build under test.
