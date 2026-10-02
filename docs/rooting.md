# Rooting

`deploy/dot_root.py` takes a Dot from stock Fire OS 6 to rooted Fire OS 5.5.5.4.
It polls the Dot every 2 seconds, does the stage for the state it finds, and
stops when the Dot is rooted.

| state | how it is recognised | what the run does |
|---|---|---|
| stock-booted | `adb get-state` says `unauthorized`, or Fire OS 6 without root | prints the fastboot gesture |
| stock-fastboot | `unlock_status` is `false` | amonet v2.0.0 fastbrick |
| v2-twrp, v2-fastboot | unlocked, `lk_build_desc` is not v1's | downgrade to amonet v1.1.0 |
| v1-fastboot | `lk_build_desc` is `f379dba-20170906_000423` | v1 TEE and TWRP, then recovery |
| v1-twrp | recovery, `ro.twrp.version` 3.2.x, `mtp` in `sys.usb.config` | Fire OS 5.5.5.4, boot and /system patches, Magisk 17.3 |
| rooted | `sys.boot_completed` is 1 and `su -c id` answers uid 0 | hides the updater and checks it |

- A probe can land part way through a boot. TWRP answers adb before it sets
  `ro.twrp.version`, and while USB drops for MTP, `adb shell` answers with
  adb's error text. An empty version would read as v2's TWRP and start a
  downgrade. So a version that does not start with a digit reads as
  `starting`.
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
- After `fastboot erase boot0` the Dot shows up only as the bootrom's serial
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
  starts before `fastboot erase boot0` and has 60 seconds to find the port.
- In verbose mode a stage prints a line when it starts and when it ends, with
  no running count.

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
  trees unpacked from the two zips are not.
- Once it sees a Dot that needs work, and before it changes it, `dot_root.py`
  downloads and checks every file: about 476 MB, 885 MB unpacked. A bad
  download then stops the run with the Dot unchanged. A rooted Dot needs no
  download.
- Nothing deletes the cache, so a later run downloads nothing. A rooted run
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
  SUBSYSTEM=="usb", ATTR{idVendor}=="0bb4", ATTR{idProduct}=="0c01", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", ATTR{idProduct}=="0003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  SUBSYSTEM=="tty", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
  ```

  Then `sudo udevadm trigger`. Fire OS 5 is `1949:0112` (`mtp,adb`, set by its
  own init scripts, the same on every Dot), TWRP `18d1:4ee2`, fastboot
  `0bb4:0c01`, and the bootrom `0e8d:0003`.
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
- From v2's TWRP the script reboots to fastboot first, and stops there if
  `lk_build_desc` is already v1.1.0's: a second erase gains nothing.
- Then `fastboot erase boot0` and `fastboot reboot` drop the Dot into its
  bootrom. The script feeds main.py the newlines its prompts read, and is done
  when main.py logs `Reboot to unlocked fastboot`.
- The bootrom is `0e8d:0003`. On macOS it is `/dev/cu.usbmodem*`. On Linux it
  is `/dev/ttyACM*`, owned by `dialout` unless the udev `tty` line gives it to
  `plugdev`, and ModemManager can grab it first. On Windows it is a COM port
  that needs MediaTek's VCOM driver (`cdc-acm.inf`, class Ports), installed by
  hand; without it `0e8d:0003` is an unknown device.
- No other mode needs a hand-installed driver. On a Windows machine that rooted
  a Dot, the driver store held only MediaTek's and Amazon's
  `FireDevicesUsbDeviceClass`, which serves amonet fastboot (`0bb4:0c01`) and
  can come through Windows Update. Windows bound adb (`1949:0112`) and
  v1.1.0's TWRP (`18d1:4ee2`) itself. macOS and Linux need no driver.
- main.py's `serial_ports()` silently skips a port it cannot open, and waits
  forever. So the script gives it 60 seconds after the reboot to log
  `Found port`, then stops it and says why a port may be missing.
- The stock payload hangs Windows' VCOM driver on any read that is a multiple
  of 64 bytes, because no short packet ends it. The script uses a patched
  payload that sends one, from `bboe/amonet-biscuit`, pinned by SHA-256. It
  works on Linux and macOS too.
- v1.1.0's `fastboot-step.sh` ships a Linux-only fastboot. The script runs its
  three commands with the host's fastboot instead: `bin/tz.img` to `tee2`,
  `bin/twrp.img` to `recovery`, then `fastboot oem reboot-recovery`.

## Install: v1's TWRP 3.2.3

- TWRP 3.2.3 prints `__bionic_open_tzdata...` into every `adb shell` output.
  Those lines are dropped before anything is parsed.
- adb answers 1.5 to 5 seconds before TWRP turns on MTP. The switch to
  `mtp,adb` drops the Dot off USB and brings it back as a new transport, and a
  push under way fails with `failed to read copy response: EOF`. So both
  scripts wait for `mtp` in `sys.usb.config`, empty until then, and
  `dot_root.py` tries each push 3 times. A push that fails, that runs past 10
  minutes, or whose md5 does not match counts as one try.
- TWRP's `adb shell` exit status is unreliable. So each device-side step is a
  pushed script that ends by printing `root-step-ok`, which the script checks.
  The pushed scripts have `\n` line endings on every host.
- `twrp install` exits 0 and prints `Done processing script file` whether the
  zip installed or not, and can lose every other line. So each install reads
  the new part of `/tmp/recovery.log` for `Updater process ended with RC=0`,
  which TWRP logs only when the zip's installer succeeds.
- TWRP 3.2.3 has no `twrp format data`. userdata is formatted with
  `mke2fs -t ext4 -b 4096 <userdata> <blocks - 256>`, which leaves 1 MiB for
  the crypto footer. busybox's fstab has no type, so the mount is
  `mount -t ext4 <dev> /data`.
- `/data` is then checked as a mountpoint. Otherwise the 397 MB Fire OS push
  lands in TWRP's RAM, TWRP dies, and the Dot reboots.
- Never `umount -a` in TWRP: it unmounts `/proc`.
- Every image, zip and database push is read back by md5 on the device.

## The boot image

- Magisk 17.3's `arm/magiskboot` is dynamically linked. `/system` is mounted
  first, and `LD_LIBRARY_PATH=/system/lib` is set on the magiskboot calls
  only. Set globally, it breaks TWRP's 64-bit tools.
- The ramdisk loses `verify` from every fstab, and `default.prop` gets
  `ro.secure=0`, `ro.debuggable=1` and `persist.sys.usb.config=mtp,adb`.
- The cmdline is the 512-byte header field at offset 64. Stock is
  `bootopt=64S3,32N2,64N2`; the script appends
  ` androidboot.selinux=permissive`.
- The patched image is padded to a 4096-byte multiple and written with `dd`.
  After `sync` the page cache is dropped, and the partition is read back by
  md5 over the image's length.

## /system

- `/system/etc/init.fosflags.sh` strips adb from the USB configuration on
  every boot after the first. The script forces its `FOS_FLAGS_ADB_ON` branch
  true and neutralizes its `unset_adb_persistent_property` calls, then checks
  both edits.
- The 4 Amazon update hosts go into `/system/etc/hosts` as `127.0.0.1`.

## Magisk

- Magisk 17.3 installs through `twrp install`, checked the same way as Fire OS.
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
  operands: the one on the path answers `conv option disabled`. So nothing
  passes `conv`, which nothing needs -- a block device has no length to
  truncate. `sgdisk`, `mke2fs`, `blockdev` and `md5sum` are looked for before
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
