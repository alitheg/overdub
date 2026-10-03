#!/usr/bin/env python3
"""Unlock and root an Echo Dot (2nd Generation) over USB, from stock Fire OS 6 or
from any point part way through, and leave it on rooted Fire OS 5.5.5.4. It
detects where the Dot is, and keeps running until the Dot is rooted: it waits
while the Dot reboots, and while you take a step it asks for. Stopped, it picks
up where it left off on the next run. It uses the one Dot on USB; set
ANDROID_SERIAL when several are. It needs Python 3.9 or later, and adb and
fastboot from Android platform-tools. docs/rooting.md says why each step is
there.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import enum
import gzip
import hashlib
import http.client
import lzma
import os
import pathlib
import re
import shlex
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.request
import zipfile
import zlib
from typing import IO, TYPE_CHECKING, BinaryIO, NoReturn, TextIO

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

AMONET_V1 = "amonet-biscuit-v1.1.0.zip"
AMONET_V1_SHA = "bd4d3a18b6b6e9ff6e49a4739159a81020673202795cb3959f7c9ff24351b663"
AMONET_V2 = "amonet-biscuit-v2.0.0.zip"
AMONET_V2_SHA = "98297293701082bc7272efe077f941c56fc7b6e1f27ef6f2e93b6e4c6fc7b62d"
ARGS = argparse.Namespace(
    delay=0, probing=False, short=False, shown=None, verbose=False
)
AS_ROOT = (
    "run this as your own user, not as root or with sudo: the downloads would"
    " belong to root, and on Linux udev rules let a user open the Dot"
)
BOOTROM_PY = """\
import os
import pathlib
import struct
import time

import common
import main
import serial
from logger import log
from serial.tools import list_ports

marker = os.environ.get("OVERDUB_ERASED")
start_payload = main.load_payload


def emmc_read(self: common.Device, idx: int) -> bytes:
    self.dev.write(struct.pack(">III", 0xF00DD00D, 0x1000, idx))
    return read_flushed(self, 0x200)


def emmc_write_blocks(self: common.Device, idx: int, data: bytes) -> None:
    self.dev.write(struct.pack(">IIII", 0xF00DD00D, 0x1003, idx, len(data) // 0x200))
    self.dev.write(data)
    if self.dev.read(4) != b"\\xd0\\xd0\\xd0\\xd0":
        msg = "device failure"
        raise RuntimeError(msg)


def flash_data(
    dev: common.Device, data: bytes, start_block: int, max_size: int = 0
) -> None:
    if marker:
        pathlib.Path(marker).touch()
    data += b"\\0" * (-len(data) % 0x200)
    if max_size and len(data) > max_size:
        msg = "data too big to flash"
        raise RuntimeError(msg)
    for x in range(0, len(data), 64 * 0x200):
        dev.emmc_write_blocks(start_block + x // 0x200, data[x : x + 64 * 0x200])


def read_flushed(self: common.Device, size: int) -> bytes:
    self.dev.write(struct.pack(">IIII", 0xF00DD00D, 0x5000, 0x201000, 4))
    data = self.dev.read(size + 4)
    if len(data) != size + 4:
        msg = "read fail"
        raise RuntimeError(msg)
    return data[:size]


def rpmb_read(self: common.Device) -> bytes:
    self.dev.write(struct.pack(">II", 0xF00DD00D, 0x2000))
    return read_flushed(self, 0x100)


def find_device(self: common.Device, preloader: bool = False) -> None:
    seen = {p.device: p.pid for p in list_ports.comports() if p.vid == 0x0E8D}
    failed = {}
    log("Waiting for bootrom")
    while True:
        pids = {p.device: p.pid for p in list_ports.comports() if p.vid == 0x0E8D}
        seen = {port: pid for port, pid in seen.items() if port in pids}
        failed = {port: at for port, at in failed.items() if port in pids}
        for port, pid in sorted(pids.items()):
            if pid is None or seen.get(port) == pid:
                continue
            if pid == 0x0003:
                try:
                    self.dev = serial.Serial(port, common.BAUD, timeout=common.TIMEOUT)
                except serial.SerialException as e:
                    at = failed.setdefault(port, time.monotonic())
                    if at and time.monotonic() - at >= 1:
                        failed[port] = 0
                        log("Cannot open " + port + ": " + str(e))
                    continue
                log("Found port = " + port)
                return
            seen[port] = pid
            if pid == 0x2000:
                log("Ignoring the preloader on " + port)
        time.sleep(0.25)


def load_payload(dev: common.Device, path: str) -> None:
    start_payload(dev, path)
    try:
        dev.emmc_switch(0)
        answered = dev.emmc_read(0)[510:512] == b"\\x55\\xaa"
    except RuntimeError:
        answered = False
    if not answered:
        log("The eMMC did not answer")
        raise SystemExit(3)


common.Device.emmc_read = emmc_read
common.Device.emmc_write_blocks = emmc_write_blocks
common.Device.find_device = find_device
common.Device.rpmb_read = rpmb_read
main.flash_data = flash_data
main.load_payload = load_payload
main.main()
"""
CLEAR_BOOT0 = (
    "d=dd; toybox dd --help >/dev/null 2>&1 && d='toybox dd'; "
    "echo 0 > /sys/block/mmcblk0boot0/force_ro; "
    "$d if=/dev/zero of=/dev/block/mmcblk0boot0 bs=4096 count=1 2>/dev/null; "
    "echo 1 > /sys/block/mmcblk0boot0/force_ro; sync; "
    "echo 3 > /proc/sys/vm/drop_caches; "
    'echo "$($d if=/dev/block/mmcblk0boot0 bs=4096 count=1 2>/dev/null | wc -c)'
    " $($d if=/dev/block/mmcblk0boot0 bs=4096 count=1 2>/dev/null"
    " | tr -d '\\0' | wc -c)\""
)
CMDLINE_SIZE = 512
DATA_SH = """\
set -e
echo performance > /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor || true
umount /data /sdcard 2>/dev/null || true
d=/dev/block/platform/mtk-msdc.0/by-name/userdata
mke2fs -q -t ext4 -b 4096 "$d" $(( $(blockdev --getsize64 "$d") / 4096 - 256 ))
mount -t ext4 "$d" /data
mountpoint -q /data
echo root-step-ok
"""
DOT_TMP = pathlib.PurePosixPath("/tmp")  # ruff: ignore[hardcoded-temp-file]
FASTBOOT_MODE = (
    "Unplug the USB cable, press and hold the action button (the one with a dot),"
    " plug the cable back in, and let go when the light ring turns green."
)
FIREOS = "update-kindle-csm_biscuit-272.6.8.0_user_680767620.bin"
FIREOS_SHA = "6ababc517529938f0d1e836c3410a91df19683ae62d7fca9e2ca57320d5d2faa"
FIREOS_URL = (
    "https://d1s31zyz7dcc2d.cloudfront.net/47a1457e0802980eb32f63cd3ce355c0/" + FIREOS
)
MAGISK = "Magisk-v17.3.zip"
MAGISK_SH = """\
set -e
mountpoint -q /data
cd /; cpio -idu < /tmp/magisk.cpio 2>/dev/null
chmod 700 /data/adb; chmod -R 755 /data/adb/magisk; chmod 600 /data/adb/magisk.db
sync
echo root-step-ok
"""
MAGISK_SHA = "18e46b16b25ebe691c282fe311beccd4811cd533848a64e2efbd754fb85efde7"
MAGISK_URL = "https://github.com/topjohnwu/Magisk/releases/download/v17.3/" + MAGISK
MEGA = 1e6
MINUTE = 60
MIRROR = "https://github.com/hkfuertes/amazon_device_biscuit/releases/download/none"
MORE_THAN_ONE = (
    "more than one Dot on USB: set ANDROID_SERIAL to one's serial (adb"
    " devices lists them)"
)
NEW_GROUP = """this shell predates its user joining plugdev. Log in again, or run:

adb kill-server
"""
NO_ACCESS = """this user cannot open the Dot over USB. These commands let it:

sudo groupadd -f plugdev
sudo tee /etc/udev/rules.d/51-echo-dot.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="1949", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="18d1", ATTR{idProduct}=="4ee2", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0bb4", ATTR{idProduct}=="0c01", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", ATTR{idProduct}=="0003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="tty", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0003", MODE="0660", GROUP="plugdev", TAG+="uaccess"
EOF
sudo udevadm control --reload
sudo udevadm trigger
sudo usermod -aG plugdev "$USER"
adb kill-server

Then run it again with the new group, which a new login also has:

"""  # ruff: ignore[line-too-long]
PUSH_TRIES = 3
PYSERIAL = "pyserial-3.5-py2.py3-none-any.whl"
LOCKS = {
    name: threading.Lock()
    for name in (AMONET_V1, AMONET_V2, FIREOS, MAGISK, PYSERIAL, "v1", "v2")
}
PYSERIAL_SHA = "c4451db6ba391ca6ca299fb3ec7bae67a5c55dde170964c7a14ceefec02f2cf0"

PYSERIAL_URL = (
    "https://files.pythonhosted.org/packages/07/bc/"
    "587a445451b253b285629263eb51c2d8e9bcea4fc97826266d186f96f558/" + PYSERIAL
)

RESET_PY = """\
import struct
import serial
from serial.tools import list_ports

for port in list_ports.comports():
    if port.vid == 0x0E8D:
        try:
            with serial.Serial(port.device, 115200, timeout=1, write_timeout=1) as dev:
                dev.write(struct.pack(">II", 0xF00DD00D, 0x3000))
        except serial.SerialException:
            pass
"""
SHORT_WAIT = 5
SPINNER = "\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f"
STEPS = 9
SYSTEM = "/dev/block/other-system"

SYSTEM_LOCK = threading.Lock()
UPDATER = "com.amazon.device.software.ota"
UPDATE_HOSTS = (
    "updates.amazon.com",
    "softwareupdates.amazon.com",
    "amzndigitaldownloads.edgesuite.net",
    "amzdigital-a.akamaihd.com",
)


SYSTEM_SH = """\
set -e
mountpoint -q /system || mount /system
f=/system/etc/init.fosflags.sh
sed -i 's/if \\[ $(( $FOS_FLAGS_ADB_ON & $FOSFLAGS )) != 0 \\]; then/if true; then/; \
s/^\\( *\\)unset_adb_persistent_property$/\\1true/' "$f"
for h in {}; do
  grep -q " $h\\$" /system/etc/hosts || echo "127.0.0.1 $h" >> /system/etc/hosts
done
grep -q 'if true; then' "$f"
grep -q '^ *unset_adb_persistent_property$' "$f" && exit 1
sync; umount /system
echo root-step-ok
""".format(" ".join(UPDATE_HOSTS))
USER_SERIAL = os.environ.get("ANDROID_SERIAL")


VERIFIED: set[pathlib.Path] = set()


WAIT = 600


class ANSIColor(enum.Enum):
    RED = 31
    YELLOW = 33


class Kind(enum.Enum):
    ERROR = "error"
    INFO = "info"
    WARN = "warn"


class LK(enum.Enum):
    V1 = "f379dba-20170906_000423"
    V2 = "63cb91b-20221007_072309"


class Progress:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.ts = self.t0
        self.line = ""
        self.open = False
        self.stopped = threading.Event()
        self.ticker = None

    def begin(self, *, estimate: str = "", label: str, step: int) -> None:
        self.end()
        about = f"(~{estimate})" if estimate else ""
        self.line = f"[{step}/{STEPS}] {label:<36} {about:<8} "
        delay(f"[{step}/{STEPS}] {label}")
        self.ts = time.monotonic()
        self.open = True
        if ARGS.verbose or not sys.stdout.isatty():
            show(text=self.line.rstrip())
        else:
            self.start()

    def end(self) -> None:
        if not self.open:
            return
        self.open = False
        back = "\r" if self.halt() else ""
        total = f"({since(self.t0)} total)"
        show(text=f"{back}{self.line}{mark()} {self.seconds()} {total:>15}")

    def halt(self) -> bool:
        if not self.ticker:
            return False
        self.stopped.set()
        self.ticker.join()
        self.ticker = None
        return True

    def note(self, message: str) -> None:
        running = self.halt()
        if running:
            print()
        warn(message)
        if running:
            self.start()

    def seconds(self) -> str:
        return f"{int(time.monotonic() - self.ts):3d}s"

    def start(self) -> None:
        show(end="", flush=True, text=self.line)
        self.stopped.clear()
        self.ticker = threading.Thread(daemon=True, target=self.tick)
        self.ticker.start()

    def tick(self) -> None:
        width = 2 if mark() == "✅" else len(mark())
        frames = SPINNER if mark() == "✅" else "|/-\\"
        count = 0
        while not self.stopped.wait(0.1):
            frame = frames[count % len(frames)]
            show(
                end="",
                flush=True,
                text=f"\r{self.line}{frame:<{width}} {self.seconds()}",
            )
            count += 1


class State(enum.Enum):
    BOOTED = "booted"
    NONE = "none"
    ROOTED = "rooted"
    STARTING = "starting"
    STOCK_BOOTED = "stock-booted"
    STOCK_FASTBOOT = "stock-fastboot"
    V1_FASTBOOT = "v1-fastboot"
    V1_TWRP = "v1-twrp"
    V2_FASTBOOT = "v2-fastboot"
    V2_TWRP = "v2-twrp"


def _die(*, message: str, prefix: str = "ERROR: ") -> NoReturn:
    if PROGRESS.halt():
        print()
    if ARGS.shown not in {None, Kind.ERROR}:
        print()
    ARGS.shown = Kind.ERROR
    text = prefix + message
    if "\n" not in text:
        text = textwrap.fill(text, 79)
    if prefix and color(sys.stderr):
        text = f"\033[{ANSIColor.RED.value}m{text}\033[0m"
    raise SystemExit(text)


def boot_image(*, fireos: pathlib.Path, magisk: pathlib.Path) -> bytes:
    with zipfile.ZipFile(fireos) as z:
        stock = z.read("boot.img")
    with zipfile.ZipFile(magisk) as z:
        magiskinit = z.read("arm/magiskinit")
    kernel_size, ramdisk_size, page = (
        struct.unpack_from("<I", stock, offset)[0] for offset in (8, 16, 36)
    )
    header = bytearray(stock[:page])
    cmdline = bytes(header[64:576]).split(b"\0")[0]
    header[64:576] = (cmdline + b" androidboot.selinux=permissive").ljust(
        CMDLINE_SIZE, b"\0"
    )
    kernel = stock[page : page + kernel_size]
    start = page + -(-kernel_size // page) * page
    files = cpio_files(gzip.decompress(stock[start : start + ramdisk_size]))
    mode, prop = files[b"default.prop"]
    prop = re.sub(rb"(?m)^ro\.secure=1$", b"ro.secure=0", prop)
    prop = re.sub(rb"(?m)^ro\.debuggable=0$", b"ro.debuggable=1", prop)
    prop = re.sub(
        rb"(?m)^persist\.sys\.usb\.config=.*", b"persist.sys.usb.config=mtp,adb", prop
    )
    files[b"default.prop"] = (mode, prop)
    for name, (mode, body) in files.items():
        if name.startswith(b"fstab"):
            files[name] = (mode, body.replace(b",verify", b"").replace(b"verify,", b""))
    files[b".backup"] = (0o40000, b"")
    files[b".backup/.magisk"] = (
        0o100000,
        b"KEEPVERITY=false\nKEEPFORCEENCRYPT=false\n\0",
    )
    files[b".backup/init"] = files[b"init"]
    files[b".backup/verity_key"] = files.pop(b"verity_key")
    files[b"init"] = (0o100750, magiskinit)
    return boot_pack(
        header=header,
        kernel=magisk_kernel(kernel),
        ramdisk=gzip.compress(cpio(files), compresslevel=9, mtime=0),
    )


def boot_pack(*, header: bytes | bytearray, kernel: bytes, ramdisk: bytes) -> bytes:
    page = len(header)
    sizes = struct.pack("<I", len(kernel)), struct.pack("<I", len(ramdisk))
    out = bytearray(header)
    out[8:12], out[16:20] = sizes
    digest = hashlib.sha1(
        kernel + sizes[0] + ramdisk + sizes[1] + bytes(4), usedforsecurity=False
    )
    out[576:608] = digest.digest().ljust(32, b"\0")
    for part in (kernel, ramdisk):
        out += part.ljust(-(-len(part) // page) * page, b"\0")
    return bytes(out)


def bootrom(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    *,
    amonet: pathlib.Path,
    erase: Callable[[], str] | None,
    payload: pathlib.Path,
    wheel: pathlib.Path,
) -> None:
    shutil.copyfile(payload, amonet / "brom-payload" / "build" / "payload.bin")
    log_path = CACHE / "bootrom.log"
    env = dict(os.environ, PYTHONPATH=str(wheel), PYTHONUNBUFFERED="1")
    if ARGS.short:
        env["OVERDUB_ERASED"] = str(ERASED)
    if not erase and not ARGS.short:
        with contextlib.suppress(subprocess.TimeoutExpired):
            subprocess.run(
                [sys.executable, "-c", RESET_PY],
                check=False,
                env=env,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                timeout=30,
            )
    with log_path.open("w") as log:
        if ARGS.verbose:
            show(
                text=f"{clock()} $ {sys.executable} -c BOOTROM_PY (amonet v1.1.0"
                " bootrom step, 64 blocks per write)"
            )
        brom = subprocess.Popen(
            [sys.executable, "-c", BOOTROM_PY],
            cwd=amonet / "modules",
            env=env,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            stdout=log,
        )
        if not ARGS.short:
            brom.stdin.write(b"\n" * 5)
            brom.stdin.close()
        started = time.monotonic()
        while time.monotonic() - started < (30 if ARGS.short else 3):
            if brom.poll() is not None or (
                ARGS.short
                and "Waiting for bootrom" in log_path.read_text(errors="replace")
            ):
                break
            time.sleep(0.25)
        if brom.poll() is not None:
            _die(message=f"v1.1.0's bootrom step did not start; see {log_path}")
        if erase:
            ERASED.touch()
            try:
                failure = erase()
            except subprocess.TimeoutExpired:
                brom.kill()
                raise
            if failure:
                brom.kill()
                _die(message=failure)
        elif ARGS.short:
            say(
                code=ANSIColor.YELLOW,
                text="Short the Dot's test point and plug it in. The run waits for"
                " its bootrom, then says when the short may come off.",
            )
            status("Waiting for the bootrom.")
        else:
            PROGRESS.begin(
                estimate="40 s", label="waiting for the Dot to restart", step=3
            )
        deadline = time.monotonic() + (60 if erase else 600)
        missed = unopened = 0
        while brom.poll() is None and time.monotonic() < deadline:
            text = log_path.read_text(errors="replace")
            if "Found port" in text:
                break
            if ARGS.short and text.count("Ignoring the preloader") > missed:
                missed = text.count("Ignoring the preloader")
                if sys.stdout.isatty():
                    print()
                said = (
                    f"Short {missed} missed: the Dot started normally. Unplug, short,"
                    " and plug it in again."
                )
                show(kind=Kind.WARN, text=paint(code=ANSIColor.RED, text=said))
                status("Waiting for the bootrom.")
            if text.count("Cannot open") > unopened:
                unopened = text.count("Cannot open")
                if ARGS.short and sys.stdout.isatty():
                    print()
                said = "Cannot open" + text.split("Cannot open")[-1].splitlines()[0]
                said += ". The run keeps trying."
                if ARGS.short:
                    warn(said)
                    status("Waiting for the bootrom.")
                else:
                    PROGRESS.note(said)
            time.sleep(1)
        else:
            if brom.poll() is None:
                brom.kill()
                _die(
                    message="the Dot's bootrom did not show up as a serial port.\n"
                    + no_port_help()
                )
        if ARGS.short and brom.poll() is None:
            countdown()
            with contextlib.suppress(OSError):
                brom.stdin.write(b"\n" * 5)
                brom.stdin.close()
        if not erase:
            PROGRESS.begin(
                estimate="30 s", label="finishing the downgrade to v1.1.0", step=3
            )
        try:
            brom.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            brom.kill()
            _die(message=f"v1.1.0's bootrom step did not finish; see {log_path}")
    if brom.returncode != 0:
        said = log_path.read_text(errors="replace")
        if ARGS.short and (
            "The eMMC did not answer" in said or "expected pattern" in said
        ):
            _die(
                message="the Dot's eMMC did not answer, most likely because the"
                " short was still on. Nothing was written. Unplug the Dot and"
                " run dot_root.py --short again."
            )
        _die(message=f"v1.1.0's bootrom step failed; see {log_path}")
    if "Reboot to unlocked fastboot" not in log_path.read_text(errors="replace"):
        _die(message=f"v1.1.0's bootrom step did not finish; see {log_path}")
    ERASED.unlink(missing_ok=True)
    for _ in range(30):
        try:
            if in_fastboot() and getvar("lk_build_desc") == LK.V1.value:
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    else:
        _die(message="the Dot did not come back in v1.1.0's fastboot")


def build_system(target: pathlib.Path) -> None:
    part = CACHE / "system.part"
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir()
    digest = hashlib.md5(usedforsecurity=False)
    fireos = fetch(name=FIREOS, url=FIREOS_URL, want=FIREOS_SHA)
    with zipfile.ZipFile(fireos) as z:
        words = z.read("system.transfer.list").decode().split()
        commands = dict(zip(words[4::2], words[5::2]))
        if words[0] != "3" or set(commands) != {"erase", "new"}:
            _die(message=f"{FIREOS} has a transfer list this does not read")
        blocks = int(commands["erase"].split(",")[-1])
        bounds = [int(n) for n in commands["new"].split(",")[1:]]
        ranges = [*zip(bounds[::2], bounds[1::2]), (blocks, blocks)]
        image = part / "system.img.gz"
        with z.open("system.new.dat") as dat:  # ruff: ignore[multiple-with-statements]
            with gzip.open(image, "wb", compresslevel=6) as out:
                for chunk in system_chunks(dat=dat, ranges=ranges):
                    digest.update(chunk)
                    out.write(chunk)
    (part / "md5").write_text(f"{digest.hexdigest()} {blocks}\n")
    for path in part.iterdir():
        with path.open("rb+") as f:
            os.fsync(f.fileno())
    part.replace(target)


def cache_dir() -> pathlib.Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or pathlib.Path.home()
    else:
        base = os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
    return pathlib.Path(base) / "overdub-root"


CACHE = cache_dir()


ERASED = CACHE / "boot0-erased"


def cache_note() -> None:
    if CACHE.is_dir():
        size = sum(f.stat().st_size for f in CACHE.rglob("*") if f.is_file())
        path, home = str(CACHE), str(pathlib.Path.home())
        if os.name != "nt" and path.startswith(home + os.sep):
            path = "~" + path[len(home) :]
        show(
            text=f"{path} holds {size // 1000000} MB of downloads and images for"
            " the next run. It is safe to delete."
        )


def check_adb() -> None:
    words = run(args=["adb", "version"], timeout=30).stdout.split()
    version = (
        words[4] if words[:4] == ["Android", "Debug", "Bridge", "version"] else "?"
    )
    parts = version.split(".")
    if not all(part.isdigit() for part in parts) or tuple(map(int, parts)) < (1, 0, 36):
        _die(
            message=f"adb reports version {version}; this needs 1.0.36"
            " (platform-tools r24) or newer"
        )


def check_user() -> None:
    if os.name != "nt" and os.geteuid() == 0:
        _die(message=AS_ROOT)
    if not sys.platform.startswith("linux"):
        return
    import grp  # ruff: ignore[import-outside-top-level]
    import pwd  # ruff: ignore[import-outside-top-level]

    try:
        plugdev = grp.getgrnam("plugdev")
    except KeyError:
        _die(message=NO_ACCESS + rerun())
    if plugdev.gr_gid in os.getgroups():
        return
    if pwd.getpwuid(os.getuid()).pw_name in plugdev.gr_mem:
        _die(message=NEW_GROUP + rerun())
    _die(message=NO_ACCESS + rerun())


def clock() -> str:
    return time.strftime("%H:%M:%S")


def color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.name == "nt" and "WT_SESSION" not in os.environ:
        return False
    return stream.isatty()


def countdown() -> None:
    text = "The bootrom answered. The short may come off now; continuing{}."
    if not sys.stdout.isatty():
        warn(text.format(f" in {SHORT_WAIT} s"))
        time.sleep(SHORT_WAIT)
        return
    for left in range(SHORT_WAIT, 0, -1):
        status(text.format(f" in {left} s"))
        time.sleep(1)
    status(text.format(""))
    print()


def cpio(files: dict[bytes, tuple[int, bytes]]) -> bytes:
    out = bytearray()
    for inode, name in enumerate([*sorted(files), b"TRAILER!!!"], start=300000):
        mode, body = files.get(name, (0, b""))
        fields = (inode, mode, 0, 0, 1, 0, len(body), 0, 0, 0, 0, len(name) + 1, 0)
        out += b"070701" + b"".join(b"%08x" % field for field in fields) + name + b"\0"
        out += bytes(-len(out) % 4) + body
        out += bytes(-len(out) % 4)
    return bytes(out)


def cpio_files(data: bytes) -> dict[bytes, tuple[int, bytes]]:
    files = {}
    at = 0
    while True:
        if data[at : at + 6] != b"070701":
            _die(message=f"{FIREOS}'s ramdisk is not a newc cpio archive")
        fields = [int(data[at + 6 + 8 * i : at + 14 + 8 * i], 16) for i in range(13)]
        name = data[at + 110 : at + 109 + fields[11]]
        at = (at + 110 + fields[11] + 3) & ~3
        body = data[at : at + fields[6]]
        at = (at + fields[6] + 3) & ~3
        if name == b"TRAILER!!!":
            return files
        files[name] = (fields[1], body)


def delay(label: str) -> None:
    if not ARGS.delay:
        return
    for left in range(ARGS.delay, 0, -1):
        show(
            end="",
            flush=True,
            text=f"\r{clock()} next: {label}; starting in {left:2d}s",
        )
        time.sleep(1)
    show(text=f"\r{clock()} next: {label}; starting now      ")


def devices(args: list[str]) -> str:
    for _ in range(5):
        out = run(args=args, timeout=30).stdout
        lines = out.splitlines()
        if not any("no permissions" in line for line in lines) or any(
            line.split()[1:2] in (["device"], ["recovery"], ["fastboot"])
            for line in lines
        ):
            return out
        time.sleep(1)
    _die(message=NO_ACCESS + rerun())


def downgrade(*, from_twrp: bool) -> None:
    amonet = unpack(
        dirname="v1", name=AMONET_V1, url=MIRROR + "/" + AMONET_V1, want=AMONET_V1_SHA
    )
    wheel = fetch(name=PYSERIAL, url=PYSERIAL_URL, want=PYSERIAL_SHA)
    if from_twrp:
        lk = rshell(command="getprop ro.boot.lk_build_desc", timeout=30)
    else:
        if getvar("unlock_status").lower() != "true":
            _die(message="not in amonet's fastboot")
        lk = getvar("lk_build_desc")
    if not lk:
        _die(
            message="the Dot did not report its bootloader version;"
            " boot0 was not erased. Run dot_root.py again."
        )
    if lk == LK.V1.value:
        _die(
            message="the Dot already runs amonet v1.1.0's bootloader."
            " Run dot_root.py again."
        )
    PROGRESS.begin(estimate="45 s", label="downgrading to amonet v1.1.0", step=3)
    bootrom(
        amonet=amonet,
        erase=erase_from_twrp if from_twrp else erase_by_fastboot,
        payload=v2_payload(),
        wheel=wheel,
    )
    v1_recovery()


def erase_by_fastboot() -> str:
    for args in (["fastboot", "erase", "boot0"], ["fastboot", "reboot"]):
        result = run(args=args, timeout=60)
        if result.returncode != 0:
            return f"{' '.join(args)} failed:\n{result.stdout}"
    return ""


def erase_from_twrp() -> str:
    answer = rshell(command=CLEAR_BOOT0, timeout=60).split("\n")[-1].split()
    if answer != ["4096", "0"]:
        return (
            "boot0's header did not read back as cleared, so the Dot was not"
            " restarted. Run dot_root.py again."
        )
    run(args=["adb", "reboot"], timeout=60)
    return ""


def fastbrick() -> None:
    amonet = unpack(
        dirname="v2", name=AMONET_V2, url=MIRROR + "/" + AMONET_V2, want=AMONET_V2_SHA
    )
    if getvar("product") != "BISCUIT":
        _die(message="fastboot reports a product other than BISCUIT")
    lk = getvar("lk_build_desc")
    if not lk:
        _die(
            message="fastboot did not report the bootloader version;"
            " the Dot was not modified"
        )
    image = "bin/fastbrick.img"
    if lk == LK.V2.value:
        image = "bin/fastbrick-20221007.img"
    PROGRESS.begin(estimate="10 s", label="unlocking with amonet v2.0.0", step=1)
    for attempt in range(10):
        if attempt:
            time.sleep(2)
        args = ["fastboot", "-S", "256M", "flash", "brick", image]
        try:
            out = run(args=args, cwd=amonet, timeout=8).stdout
            started = False
        except subprocess.TimeoutExpired as e:
            out = (e.output or b"").decode("utf-8", "replace")
            started = True
        if "eMMC-RO" in out:
            _die(message="the Dot's eMMC is read-only; it was not modified")
        if "Device mismatch" in out:
            _die(message="the payload rejected this device; it was not modified")
        if started:
            PROGRESS.begin(
                estimate="40 s", label="waiting for v2.0.0 recovery to start", step=2
            )
            return
    _die(message="the unlock did not start after 10 attempts")


def fetch(*, name: str, url: str, want: str) -> pathlib.Path:
    with hold(LOCKS[name]):
        CACHE.mkdir(exist_ok=True, parents=True)
        path = CACHE / name
        if path in VERIFIED or (path.is_file() and sha256(path) == want):
            VERIFIED.add(path)
            return path
        part = CACHE / (name + ".part")
        try:
            with urllib.request.urlopen(url, timeout=60) as response:  # ruff: ignore[multiple-with-statements]
                with part.open("wb") as out:
                    done, total = save(
                        label="downloading " + name, out=out, response=response
                    )
        except (OSError, http.client.HTTPException) as error:
            _die(message=f"downloading {name} failed: {error!r}")
        if total and done != total:
            _die(
                message=f"downloading {name} stopped after {done} of {total} bytes."
                " Run dot_root.py again."
            )
        if sha256(part) != want:
            _die(message=f"{name} does not hash to {want}")
        part.replace(path)
        VERIFIED.add(path)
        return path


def getvar(name: str) -> str:
    try:
        out = run(args=["fastboot", "getvar", name], timeout=30).stdout
    except subprocess.TimeoutExpired:
        return ""
    for line in out.splitlines():
        if line.startswith(name + ":"):
            return line[len(name) + 1 :].strip()
    return ""


def hide_updater() -> None:
    def hidden() -> bool:
        out = rshell(command=f"su -c 'dumpsys package {UPDATER}'", timeout=60)
        return any(
            line.strip().startswith("User 0:") and "hidden=true" in line
            for line in out.split("\n")
        )

    if not hidden():
        rshell(command=f"su -c 'pm hide {UPDATER}'", timeout=60)
    if not hidden():
        _die(
            message=f"{UPDATER} is not hidden; an update would replace the"
            " boot image and remove root"
        )


@contextlib.contextmanager
def hold(lock: threading.Lock) -> Iterator[None]:
    while not lock.acquire(timeout=0.5):
        pass
    try:
        yield
    finally:
        lock.release()


def in_fastboot() -> bool:
    out = devices(["fastboot", "devices"])
    serials = [
        line.split()[0]
        for line in out.splitlines()
        if line.split()[1:2] == ["fastboot"]
    ]
    if USER_SERIAL:
        return USER_SERIAL in serials
    if len(serials) > 1:
        _die(message=MORE_THAN_ONE)
    return bool(serials)


def install_fireos() -> None:
    fireos = fetch(name=FIREOS, url=FIREOS_URL, want=FIREOS_SHA)
    magisk = fetch(name=MAGISK, url=MAGISK_URL, want=MAGISK_SHA)
    with tempfile.TemporaryDirectory(dir=CACHE) as tmp:
        work = pathlib.Path(tmp)
        PROGRESS.begin(estimate="5 s", label="formatting userdata", step=5)
        if not rscript(body=DATA_SH, name="data.sh", work=work):
            _die(message="userdata did not format and mount")
        PROGRESS.begin(
            estimate="100 s", label="writing Fire OS 5.5.5.4's /system", step=6
        )
        write_system()
        if not rscript(body=SYSTEM_SH, name="system.sh", work=work):
            _die(message="patching /system failed")

        PROGRESS.begin(estimate="5 s", label="writing the boot image", step=7)
        data = boot_image(fireos=fireos, magisk=magisk)
        boot = work / "boot.img"
        boot.write_bytes(data.ljust(-(-len(data) // 4096) * 4096, b"\0"))
        final = DOT_TMP / "boot.img"
        push_checked(local=boot, remote=final)
        rshell(
            command=f"dd if={final} of=/dev/block/other-boot bs=1048576 2>/dev/null;"
            " sync; echo 3 > /proc/sys/vm/drop_caches",
            timeout=120,
        )
        blocks = boot.stat().st_size // 4096
        read_back = (
            f"dd if=/dev/block/other-boot bs=4096 count={blocks} 2>/dev/null | md5sum"
        )
        want = md5(boot)
        written = rshell(command=read_back, timeout=120).split("\n")[-1].split(" ")[0]
        if written != want:
            _die(
                message="the patched boot image did not verify on the Dot: read"
                f" {written or 'nothing'}, expected {want}"
            )

        PROGRESS.begin(estimate="5 s", label="installing Magisk 17.3", step=8)
        install_magisk(magisk=magisk, work=work)
    PROGRESS.begin(
        estimate="4 min", label="waiting for rooted Fire OS 5 to boot", step=9
    )
    run(args=["adb", "reboot"])


def install_magisk(*, magisk: pathlib.Path, work: pathlib.Path) -> None:
    db = work / "magisk.db"
    magisk_db(db)
    files = magisk_files(db=db.read_bytes(), magisk=magisk)
    archive = work / "magisk.cpio"
    archive.write_bytes(cpio(files))
    push_checked(local=archive, remote=DOT_TMP / "magisk.cpio")
    if not rscript(body=MAGISK_SH, name="magisk.sh", work=work):
        _die(message="Magisk 17.3 did not install")
    names = sorted(name for name, (mode, _) in files.items() if stat.S_ISREG(mode))
    want = hashlib.md5(
        b"".join(files[name][1] for name in names), usedforsecurity=False
    ).hexdigest()
    paths = " ".join(name.decode() for name in names)
    got = rshell(command=f"cd /; cat {paths} | md5sum").split("\n")[-1].split(" ")[0]
    if got != want:
        _die(message="Magisk 17.3's files did not verify on the Dot")


def magisk_binary(magiskinit: bytes) -> bytes:
    at = magiskinit.find(b"\xfd7zXZ\0")
    while at != -1:
        with contextlib.suppress(lzma.LZMAError):
            binary = lzma.LZMADecompressor().decompress(magiskinit[at:])
            if binary.startswith(b"\x7fELF"):
                return binary
        at = magiskinit.find(b"\xfd7zXZ\0", at + 1)
    _die(message=f"{MAGISK}'s magiskinit holds no magisk binary")


def magisk_db(path: pathlib.Path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE policies (uid INT, package_name TEXT, policy INT, "
        "until INT, logging INT, notification INT)"
    )
    db.execute("INSERT INTO policies VALUES (2000, 'com.android.shell', 2, 0, 1, 0)")
    db.commit()
    db.close()


def magisk_files(*, db: bytes, magisk: pathlib.Path) -> dict[bytes, tuple[int, bytes]]:
    files = {
        b"data/adb": (0o40700, b""),
        b"data/adb/magisk": (0o40755, b""),
        b"data/adb/magisk/chromeos": (0o40755, b""),
        b"data/adb/magisk.db": (0o100600, db),
    }
    with zipfile.ZipFile(magisk) as z:
        for info in z.infolist():
            folder, _, name = info.filename.partition("/")
            if folder in {"arm", "common"}:
                path = name
            elif folder == "chromeos":
                path = info.filename
            else:
                continue
            files[f"data/adb/magisk/{path}".encode()] = (0o100755, z.read(info))
        script = z.read("META-INF/com/google/android/update-binary").decode()
    packed = re.search(r"^BB_ARM=(\S+)", script, re.MULTILINE)
    if not packed:
        _die(message=f"{MAGISK} has no busybox in its installer")
    files[b"data/adb/magisk/busybox"] = (
        0o100755,
        lzma.decompress(base64.b64decode(packed.group(1))),
    )
    files[b"data/adb/magisk/magisk"] = (
        0o100755,
        magisk_binary(files[b"data/adb/magisk/magiskinit"][1]),
    )
    return files


def magisk_kernel(kernel: bytes) -> bytes:
    stream = zlib.decompressobj(31)
    image = stream.decompress(kernel[512:])
    image = image.replace(b"skip_initramfs\0", b"want_initramfs\0")
    body = gzip.compress(image, compresslevel=9, mtime=0) + stream.unused_data
    return kernel[:4] + struct.pack("<I", len(body)) + kernel[8:512] + body


def main() -> None:  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print each adb and fastboot command, its exit status and the time,"
        " and each state the Dot reaches",
    )
    parser.add_argument(
        "--short",
        action="store_true",
        help="for a Dot that shows no light and needs its test point shorted:"
        " wait for its bootrom, say when the short may come off, then root it",
    )
    parser.add_argument(
        "--delay",
        const=10,
        default=0,
        help="count down before each stage, 10 seconds unless given, so a"
        " recording shows the Dot settled in each state; implies --verbose",
        metavar="SECONDS",
        nargs="?",
        type=int,
    )
    options = parser.parse_args()
    ARGS.delay = options.delay
    ARGS.short = options.short
    ARGS.verbose = options.verbose or options.delay > 0
    for tool in ("adb", "fastboot"):
        if not shutil.which(tool):
            _die(message=tool + " not found: install Android platform-tools")
    check_user()
    check_adb()
    usage = run(args=["fastboot", "--help"], timeout=30).stdout
    if not any(line.split()[:1] == ["-S"] for line in usage.splitlines()):
        _die(
            message="this fastboot has no -S option: install a newer"
            " Android platform-tools"
        )
    done: set[State] = set()
    guided = False
    resumed = False
    seen = None
    shown = None
    deadline = None
    while True:
        current = state()
        if DOWNLOADER.ident is None and current not in {
            State.BOOTED,
            State.ROOTED,
            State.STARTING,
        }:
            DOWNLOADER.start()
        if current not in {State.NONE, State.STARTING}:
            ERASED.unlink(missing_ok=True)
        if current != State.NONE:
            ARGS.short = False
        if current == State.NONE and (ERASED.exists() or ARGS.short) and not resumed:
            prefetch()
            resumed = True
            done.add(State.V1_FASTBOOT)
            if not ARGS.short:
                say(
                    code=ANSIColor.YELLOW,
                    text="The last run stopped during the downgrade to amonet"
                    " v1.1.0. The Dot cannot start until the downgrade is done,"
                    " so this run finishes it.",
                )
            bootrom(
                amonet=unpack(
                    dirname="v1",
                    name=AMONET_V1,
                    url=MIRROR + "/" + AMONET_V1,
                    want=AMONET_V1_SHA,
                ),
                erase=None,
                payload=v2_payload(),
                wheel=fetch(name=PYSERIAL, url=PYSERIAL_URL, want=PYSERIAL_SHA),
            )
            v1_recovery()
            seen = None
            continue
        if current != seen:
            seen = current
            deadline = time.monotonic() + WAIT
            if ARGS.verbose and current != shown:
                shown = current
                show(text=f"{clock()} state: {current.value}")
            if current not in done and current not in {
                State.NONE,
                State.STARTING,
                State.BOOTED,
            }:
                PROGRESS.end()
            if current == State.BOOTED and not PROGRESS.open:
                show(text="The Dot is starting Fire OS. Waiting for it to finish.")
            elif current == State.STOCK_BOOTED:
                guided = True
                say(
                    text="This Dot appears to be unmodified. To unlock and root it,"
                    " start it in fastboot mode. " + FASTBOOT_MODE
                )
            elif current == State.NONE and not done and guided:
                show(text="Waiting for the Dot in fastboot mode, with a green ring.")
            elif current == State.NONE and not done:
                guided = True
                say(
                    text="Waiting for a Dot on USB. Connect it with a USB cable."
                    " A Dot that runs Amazon's own software needs fastboot mode. "
                    + FASTBOOT_MODE
                    + " Ctrl-C stops the script."
                )
        if current == State.ROOTED:
            hide_updater()
            version = rshell(command="getprop ro.build.version.name")
            selinux = rshell(command="getenforce")
            warn(f"The Dot is rooted: {version}, SELinux {selinux}, {UPDATER} hidden.")
            warn("Install overdub with deploy/install.py <name>.")
            cache_note()
            return
        if current in done or current in {
            State.NONE,
            State.STOCK_BOOTED,
            State.BOOTED,
            State.STARTING,
        }:
            if (
                current not in {State.NONE, State.STOCK_BOOTED}
                and time.monotonic() > deadline
            ):
                if current == State.BOOTED:
                    _die(
                        message="Fire OS has not finished booting with root."
                        " Reboot to recovery and run dot_root.py again."
                    )
                _die(
                    message=f"the Dot has been {current.value} for"
                    f" {WAIT // 60} minutes. Run dot_root.py again."
                )
            time.sleep(2)
            continue
        if not done:
            prefetch()
            warn("Keep the Dot plugged in until dot_root.py finishes.")
        done.add(current)
        if current == State.STOCK_FASTBOOT:
            fastbrick()
        elif current == State.V2_TWRP:
            downgrade(from_twrp=True)
            done.update((State.V2_FASTBOOT, State.V1_FASTBOOT))
        elif current == State.V2_FASTBOOT:
            downgrade(from_twrp=False)
            done.update((State.V2_TWRP, State.V1_FASTBOOT))
        elif current == State.V1_FASTBOOT:
            v1_recovery()
        elif current == State.V1_TWRP:
            install_fireos()
        seen = None


def mark() -> str:
    try:
        "\u2705".encode(sys.stdout.encoding or "ascii")
    except (LookupError, UnicodeEncodeError):
        return "done"
    return "\u2705"


def md5(path: pathlib.Path) -> str:
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def no_port_help() -> str:
    if sys.platform.startswith("linux"):
        return (
            "No new /dev/ttyACM* port could be opened. Stop ModemManager if it runs,\n"
            "and check that /etc/udev/rules.d/51-echo-dot.rules has this line:\n\n"
            'SUBSYSTEM=="tty", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0003",'
            ' MODE="0660", GROUP="plugdev", TAG+="uaccess"'
        )
    if sys.platform == "darwin":
        return "No new /dev/cu.usbmodem* port appeared."
    return "No new COM port appeared. Windows may need a driver for USB ID 0e8d:0003."


def on_usb(line: str) -> bool:
    if os.name != "nt":
        return " usb:" in line
    parts = line.split()
    return (
        parts[1:2] in (["device"], ["recovery"], ["unauthorized"])
        and ":" not in parts[0]
        and not parts[0].startswith("emulator-")
    )


def paint(*, code: ANSIColor, text: str) -> str:
    return f"\033[{code.value}m{text}\033[0m" if color(sys.stdout) else text


def prebuild() -> None:
    with contextlib.suppress(Exception, SystemExit):
        system_image()


def predownload() -> None:
    with contextlib.suppress(Exception, SystemExit):
        prefetch()


DOWNLOADER = threading.Thread(daemon=True, target=predownload)


def prefetch() -> None:
    if DOWNLOADER.is_alive() and threading.current_thread() is threading.main_thread():
        show(text="Finishing the downloads.")
    unpack(
        dirname="v2", name=AMONET_V2, url=MIRROR + "/" + AMONET_V2, want=AMONET_V2_SHA
    )
    unpack(
        dirname="v1", name=AMONET_V1, url=MIRROR + "/" + AMONET_V1, want=AMONET_V1_SHA
    )
    fetch(name=PYSERIAL, url=PYSERIAL_URL, want=PYSERIAL_SHA)
    fetch(name=FIREOS, url=FIREOS_URL, want=FIREOS_SHA)
    fetch(name=MAGISK, url=MAGISK_URL, want=MAGISK_SHA)
    threading.Thread(daemon=True, target=prebuild).start()


def probe() -> State:  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
    if in_fastboot():
        unlock = getvar("unlock_status").lower()
        if unlock == "false":
            return State.STOCK_FASTBOOT
        if unlock != "true":
            return State.STARTING
        lk = getvar("lk_build_desc")
        if not lk:
            return State.STARTING
        if lk == LK.V1.value:
            return State.V1_FASTBOOT
        return State.V2_FASTBOOT
    if not usb_serial():
        return State.NONE
    adb_state = run(args=["adb", "get-state"], timeout=30).stdout
    if "unauthorized" in adb_state:
        return State.STOCK_BOOTED
    adb_state = adb_state.strip()
    if adb_state == "recovery":
        version = rshell(command="getprop ro.twrp.version", timeout=30)
        if not version[:1].isdigit():
            return State.STARTING
        if version.startswith("3.2."):
            if "mtp" not in rshell(command="getprop sys.usb.config", timeout=30):
                return State.STARTING
            return State.V1_TWRP
        return State.V2_TWRP
    if adb_state == "device":
        booted = rshell(command="getprop sys.boot_completed", timeout=30) == "1"
        if booted and "uid=0" in rshell(command="su -c id", timeout=30):
            return State.ROOTED
        if rshell(command="getprop ro.build.version.name", timeout=30).startswith(
            "Fire OS 6"
        ):
            return State.STOCK_BOOTED
        return State.BOOTED
    return State.NONE


def push_checked(*, local: pathlib.Path, remote: str | pathlib.PurePosixPath) -> None:
    for attempt in range(PUSH_TRIES):
        if attempt:
            reconnect(remote)
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            result = run(args=["adb", "push", local, remote], timeout=600)
            if result.returncode != 0:
                said = result.stdout
                continue
            if rshell(command=f"md5sum {remote}", timeout=300).split(" ")[0] == md5(
                local
            ):
                return
            said = "its md5 read back did not match"
        except subprocess.TimeoutExpired as error:
            said = (
                f"{' '.join(map(str, error.cmd))} did not finish in"
                f" {error.timeout:.0f} seconds"
            )
    _die(
        message=f"{remote} did not arrive intact after {PUSH_TRIES} tries;"
        f" the last: {said}"
    )


def reconnect(remote: str | pathlib.PurePosixPath) -> None:
    PROGRESS.note(
        f"{remote} did not arrive; waiting for the Dot to reconnect to try again."
    )
    try:
        run(args=["adb", "wait-for-recovery"], timeout=120)
    except subprocess.TimeoutExpired:
        _die(message="the Dot did not reconnect over USB in recovery")
    time.sleep(5)


def rerun() -> str:
    return "sg plugdev -c " + shlex.quote(shlex.join([sys.executable, *sys.argv]))


def rscript(*, body: str, name: str, work: pathlib.Path) -> bool:
    local = work / name
    with local.open("w", newline="\n") as f:
        f.write(body)
    remote = DOT_TMP / "root-step.sh"
    push_checked(local=local, remote=remote)
    out = rshell(command=f"sh {remote}; rm -f {remote}", timeout=300)
    return out.split("\n")[-1] == "root-step-ok"


def rshell(*, command: str, timeout: float | None = None) -> str:
    out = run(args=["adb", "shell", command], timeout=timeout).stdout
    return "\n".join(
        line for line in out.split("\n") if not line.startswith("__bionic_open_tzdata")
    ).strip()


def run(
    *,
    args: list[str | pathlib.PurePath],
    check: bool = False,
    cwd: pathlib.Path | None = None,
    stdin: BinaryIO | int = subprocess.DEVNULL,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    if ARGS.verbose and not ARGS.probing:
        show(text=f"{clock()} $ {' '.join(map(str, args))}")
    result = subprocess.run(
        args,
        check=False,
        cwd=cwd,
        stderr=subprocess.STDOUT,
        stdin=stdin,
        stdout=subprocess.PIPE,
        timeout=timeout,
    )
    result.stdout = result.stdout.decode("utf-8", "replace").replace("\r", "")
    if ARGS.verbose and not ARGS.probing:
        show(text=f"{clock()}   exit {result.returncode}")
    if check and result.returncode != 0:
        _die(message=f"{' '.join(map(str, args))} failed:\n{result.stdout}")
    return result


def save(
    *, label: str, out: BinaryIO, response: http.client.HTTPResponse
) -> tuple[int, int]:
    total = int(response.headers.get("Content-Length") or 0)
    div, unit = (1e3, "KB") if 0 < total < MEGA else (MEGA, "MB")

    def meter(done: int) -> str:
        if total:
            return (
                f"{done / div:.1f} of {total / div:.1f} {unit} ({100 * done // total}%)"
            )
        return f"{done / div:.1f} {unit}"

    room = 78 - (len(meter(total)) if total else 12)
    if len(label) > room:
        label = label[: room - 3] + "..."
    done = 0
    loud = threading.current_thread() is threading.main_thread() and sys.stdout.isatty()
    if loud:
        show(end="", flush=True, text=f"{label:<{room}} {meter(0):>{78 - room}}")
    for block in iter(lambda: response.read(1 << 20), b""):
        out.write(block)
        done += len(block)
        if loud:
            show(
                end="", flush=True, text=f"\r{label:<{room}} {meter(done):>{78 - room}}"
            )
    if loud:
        print()
    return done, total


def say(*, code: ANSIColor | None = None, text: str) -> None:
    text = textwrap.fill(text, 79)
    show(
        kind=Kind.WARN if code else Kind.INFO,
        text=paint(code=code, text=text) if code else text,
    )


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def show(*, kind: Kind = Kind.INFO, text: str, **options: str | bool) -> None:
    if ARGS.shown not in {None, kind}:
        print()
    ARGS.shown = kind
    print(text, **options)


def since(start: float) -> str:
    seconds = int(time.monotonic() - start)
    if seconds < MINUTE:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def state() -> State:
    ARGS.probing = True
    try:
        current = probe()
    except subprocess.TimeoutExpired:
        current = State.STARTING
    finally:
        ARGS.probing = False
    return current


def status(text: str) -> None:
    if sys.stdout.isatty():
        show(
            end="",
            flush=True,
            kind=Kind.WARN,
            text="\r" + paint(code=ANSIColor.YELLOW, text=text.ljust(79)),
        )
    else:
        warn(text)


def system_chunks(*, dat: IO[bytes], ranges: list[tuple[int, int]]) -> Iterator[bytes]:
    at = 0
    for start, end in ranges:
        for offset in range(at * 4096, start * 4096, 1 << 20):
            yield bytes(min(1 << 20, start * 4096 - offset))
        for offset in range(start * 4096, end * 4096, 1 << 20):
            n = min(1 << 20, end * 4096 - offset)
            chunk = dat.read(n)
            if len(chunk) != n:
                _die(message=f"{FIREOS}'s system.new.dat is short")
            yield chunk
        at = end


def system_image() -> tuple[pathlib.Path, str, int]:
    target = CACHE / f"system-{FIREOS_SHA[:12]}"
    with hold(SYSTEM_LOCK):
        if not (target / "md5").is_file():
            shutil.rmtree(target, ignore_errors=True)
            build_system(target)
    want, blocks = (target / "md5").read_text().split()
    return target / "system.img.gz", want, int(blocks)


def unpack(*, dirname: str, name: str, url: str, want: str) -> pathlib.Path:
    with hold(LOCKS[dirname]):
        archive = fetch(name=name, url=url, want=want)
        target = CACHE / dirname
        if not target.is_dir():
            part = CACHE / (dirname + ".part")
            shutil.rmtree(part, ignore_errors=True)
            with zipfile.ZipFile(archive) as z:
                z.extractall(part)
            part.replace(target)
        return target / "amonet"


def usb_serial() -> str | None:
    if USER_SERIAL:
        if ":" in USER_SERIAL:
            _die(
                message="ANDROID_SERIAL names a network device;"
                " this needs the Dot on USB"
            )
        devices(["adb", "devices", "-l"])
        return USER_SERIAL
    out = devices(["adb", "devices", "-l"])
    usb = [
        line.split()[0]
        for line in out.splitlines()[1:]
        if on_usb(line) and "no permissions" not in line
    ]
    if len(usb) > 1:
        _die(message=MORE_THAN_ONE)
    if usb:
        os.environ["ANDROID_SERIAL"] = usb[0]
        return usb[0]
    os.environ.pop("ANDROID_SERIAL", None)
    return None


def v1_recovery() -> None:
    amonet = unpack(
        dirname="v1", name=AMONET_V1, url=MIRROR + "/" + AMONET_V1, want=AMONET_V1_SHA
    )
    PROGRESS.begin(
        estimate="30 s", label="waiting for v1.1.0 recovery to start", step=4
    )
    run(
        args=["fastboot", "-S", "256M", "flash", "tee2", "bin/tz.img"],
        check=True,
        cwd=amonet,
        timeout=120,
    )
    run(
        args=["fastboot", "-S", "256M", "flash", "recovery", "bin/twrp.img"],
        check=True,
        cwd=amonet,
        timeout=120,
    )
    run(args=["fastboot", "oem", "reboot-recovery"], check=True, cwd=amonet, timeout=60)


def v2_payload() -> pathlib.Path:
    amonet = unpack(
        dirname="v2", name=AMONET_V2, url=MIRROR + "/" + AMONET_V2, want=AMONET_V2_SHA
    )
    return amonet / "brom-payload" / "build" / "payload.bin"


def warn(text: str) -> None:
    show(
        kind=Kind.WARN, text=paint(code=ANSIColor.YELLOW, text=textwrap.fill(text, 79))
    )


PROGRESS = Progress()


def write_system() -> None:  # ruff: ignore[complex-structure]
    image, want, blocks = system_image()
    status = DOT_TMP / "system-status"
    ready = rshell(
        command=f"umount /system 2>/dev/null; rm -f {status};"
        f" [ -b {SYSTEM} ] && ! mountpoint -q /system && echo ready"
    )
    if ready.split("\n")[-1] != "ready":
        _die(message=f"{SYSTEM} is not a block device, or /system stayed mounted")
    stream = f"gunzip -c | dd of={SYSTEM} bs=1048576 2>/dev/null; echo $? > {status}"
    read_back = (
        "sync; echo 3 > /proc/sys/vm/drop_caches;"
        f" dd if={SYSTEM} bs=4096 count={blocks} 2>/dev/null | md5sum"
    )
    for attempt in range(PUSH_TRIES):
        if attempt:
            reconnect(SYSTEM)
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            rshell(command=f"rm -f {status}")
            with image.open("rb") as f:
                result = run(
                    args=["adb", "exec-in", "sh -c " + shlex.quote(stream)],
                    stdin=f,
                    timeout=600,
                )
            if result.returncode != 0:
                said = result.stdout
                continue
            code = ""
            for _ in range(60):
                code = rshell(command=f"cat {status} 2>/dev/null || true")
                if code:
                    break
                time.sleep(1)
            if code != "0":
                said = f"dd ended with {code or 'nothing after 60 s'}"
                continue
            if rshell(command=read_back, timeout=300).split(" ")[0] == want:
                return
            said = "its md5 read back did not match"
        except subprocess.TimeoutExpired as error:
            said = (
                f"{' '.join(map(str, error.cmd))} did not finish in"
                f" {error.timeout:.0f} seconds"
            )
    if said == "its md5 read back did not match":
        (image.parent / "md5").unlink(missing_ok=True)
        said += ". The cached image was discarded, so the next run rebuilds it"
    _die(
        message=f"{SYSTEM} was not written intact after {PUSH_TRIES} tries;"
        f" the last: {said}"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _die(message="stopped", prefix="")
    except subprocess.TimeoutExpired as error:
        _die(
            message=f"{' '.join(map(str, error.cmd))} did not finish in"
            f" {error.timeout:.0f} seconds"
        )
