#!/usr/bin/env python3
"""Return an Echo Dot (2nd Generation) rooted on amonet v1.1.0 to stock Fire OS 6:
build 4405 (6.5.5.6), 5041 (6.5.0.5), 6302 (6.4.6.6), or 8138, 8142 or 8146
(6.5.7.4.1). Use it to test dot_root.py from a clean start. The Dot must be in
v1.1.0's TWRP 3.2.3, or booted with adb; the script reboots it to TWRP. After a
10-second countdown, which Ctrl-C cancels, it writes Amazon's preloader, LK,
TEE, boot and system for that build, the stock partition table built from the
Dot's own, and fresh cache and userdata. Root is gone afterwards; dot_root.py
puts it back. It uses the one Dot on USB; set ANDROID_SERIAL when several are.
It needs Python 3.9 or later, and adb from Android platform-tools.
docs/rooting.md says why each step is there.
"""

import argparse
import bz2
import hashlib
import http.client
import lzma
import os
import pathlib
import re
import shlex
import shutil
import struct
import subprocess
import sys
import textwrap
import threading
import time
import urllib.request
import zipfile
import zlib
from typing import NamedTuple

ARGS = argparse.Namespace(delay=0, shown=None, verbose=False)
AS_ROOT = (
    "run this as your own user, not as root or with sudo: the downloads would"
    " belong to root, and on Linux udev rules let a user open the Dot"
)


DISK = "/dev/block/mmcblk0"
FLUSH = "sync && echo 3 > /proc/sys/vm/drop_caches && echo flushed"
FTVDB = "https://ftvdb.com/echo/firmware/com.amazon.biscuit.android.os/"
IMAGES = ("preloader", "lk", "tee", "boot", "system")
MORE_THAN_ONE = (
    "more than one Dot on USB: set ANDROID_SERIAL to one's serial"
    " (adb devices lists them)"
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

"""
STATUS = "/tmp/dd.status"
UNMOUNT = (
    'for m in $(grep "^/dev/block/mmcblk0" /proc/mounts | cut -d" " -f2); '
    'do umount "$m"; done'
)
USER_SERIAL = os.environ.get("ANDROID_SERIAL")


WRITES = (
    ("system", "system_a", "write system image to system_a (768 MB)", "3-4 min"),
    ("system", "system_b", "write system image to system_b (768 MB)", "3-4 min"),
    ("boot", "boot_a", "write boot image to boot_a", "10 s"),
    ("boot", "boot_b", "write boot image to boot_b", "10 s"),
    ("tee", "tee1", "write TEE image to tee1", "5 s"),
    ("tee", "tee2", "write TEE image to tee2", "5 s"),
    ("lk", "lk_a", "write LK image to lk_a", "5 s"),
    ("lk", "lk_b", "write LK image to lk_b", "5 s"),
    ("expdb", "expdb", "zero expdb (amonet payload)", "5 s"),
    ("misc", "misc", "zero misc (slot metadata)", "5 s"),
)


class Build(NamedTuple):
    ftvdb_version: str
    ns: str
    number: str
    date: str
    md5: str
    sha256: str


BUILDS = {
    "4405": Build(
        date="2023-01-21",
        ftvdb_version="6-5-5-6",
        md5="570f3f6b28f94323e01e95561c87887f",
        ns="NS6556",
        number="8289072516",
        sha256="6839b0a1e5c4f6aa57f89ea7ca67c85aa69f8fe32e5ddc252764b0d96be5bf69",
    ),
    "5041": Build(
        date="2023-04-06",
        ftvdb_version="6-5-0-5",
        md5="1a25d21e0363158fe0c14cb4d41b843e",
        ns="NS6505",
        number="8960323972",
        sha256="80d98d3b57bc654d435b384f096dea46edd2b86e9f8adaaf91a7b0c3001e9716",
    ),
    "6302": Build(
        date="2025-04-13",
        ftvdb_version="6-4-6-6",
        md5="62da0c58f8c3e5c8094f302346da754f",
        ns="NS6466",
        number="11712110212",
        sha256="6d395345b1d1db373f9d714172c2ac5de4b5dd3e52d1ed1c3f37c2f007537667",
    ),
    "8138": Build(
        date="2026-08-31",
        ftvdb_version="6574-1",
        md5="723886117a7a4a8a543c2ec955dcd54e",
        ns="NS65741",
        number="13222529668",
        sha256="d2a61dd2af1d322e9ecfb4643670fbe416bc2442410f1aebe9748e2438f6d55f",
    ),
    "8142": Build(
        date="2026-09-09",
        ftvdb_version="6574-1",
        md5="ead2ea9a9ca2fa1c708381a07c605356",
        ns="NS65741",
        number="13222530692",
        sha256="ed4ddb01cd53e38751bb6274ad1a8255043e0795843d6ac2d44cc2803b2179a0",
    ),
    "8146": Build(
        date="2026-09-17",
        ftvdb_version="6574-1",
        md5="8ca06ee4ef2806c974d2944b7fadd543",
        ns="NS65741",
        number="13222531716",
        sha256="90832e86498c5e803974c30359aea71c1129757be0ddc59d831a94b27f81487f",
    ),
}


class Progress:
    def __init__(self, steps):
        self.step = 0
        self.steps = steps
        self.t0 = time.monotonic()
        self.ts = self.t0
        self.line = ""
        self.stopped = threading.Event()
        self.ticker = None

    def begin(self, label, estimate):
        self.step += 1
        about = f"(~{estimate})"
        self.line = f"[{self.step:2d}/{self.steps}] {label:<44} {about:<10} ... "
        delay(f"[{self.step:2d}/{self.steps}] {label}")
        self.ts = time.monotonic()
        if ARGS.verbose:
            show(self.line.rstrip())
            return
        show(self.line, end="", flush=True)
        self.stopped.clear()
        self.ticker = threading.Thread(daemon=True, target=self.tick)
        self.ticker.start()

    def end(self):
        back = "\r" if self.halt() else ""
        show(f"{back}{self.line}done in {self.seconds()}, {self.minutes()}m total")

    def fail(self, message):
        if self.halt():
            print()
        die(message)

    def halt(self):
        if not self.ticker:
            return False
        self.stopped.set()
        self.ticker.join()
        self.ticker = None
        return True

    def minutes(self):
        return int((time.monotonic() - self.t0) // 60)

    def seconds(self):
        return f"{int(time.monotonic() - self.ts):3d}s"

    def tick(self):
        while not self.stopped.wait(1):
            show(f"\r{self.line}{self.seconds()}", end="", flush=True)


def cache_dir():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or pathlib.Path.home()
    else:
        base = os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
    return pathlib.Path(base) / "overdub-stock"


CACHE = cache_dir()


def cache_note():
    if CACHE.is_dir():
        size = sum(f.stat().st_size for f in CACHE.rglob("*") if f.is_file())
        path, home = str(CACHE), str(pathlib.Path.home())
        if os.name != "nt" and path.startswith(home + os.sep):
            path = "~" + path[len(home) :]
        show(
            f"{path} holds {size // 1000000} MB of downloads and images for the"
            " next run. It is safe to delete."
        )


def check_adb():
    words = run(["adb", "version"], timeout=30).stdout.split()
    version = (
        words[4] if words[:4] == ["Android", "Debug", "Bridge", "version"] else "?"
    )
    parts = version.split(".")
    if not all(part.isdigit() for part in parts) or tuple(map(int, parts)) < (1, 0, 36):
        die(
            f"adb reports version {version}; this needs 1.0.36 (platform-tools r24)"
            " or newer"
        )


def check_user():
    if os.name != "nt" and os.geteuid() == 0:
        die(AS_ROOT)
    if not sys.platform.startswith("linux"):
        return
    import grp
    import pwd

    try:
        plugdev = grp.getgrnam("plugdev")
    except KeyError:
        die(NO_ACCESS + rerun())
    if plugdev.gr_gid in os.getgroups():
        return
    if pwd.getpwuid(os.getuid()).pw_name in plugdev.gr_mem:
        die(NEW_GROUP + rerun())
    die(NO_ACCESS + rerun())


def clock():
    return time.strftime("%H:%M:%S")


def color(stream):
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.name == "nt" and "WT_SESSION" not in os.environ:
        return False
    return stream.isatty()


def command(args, **options):
    if ARGS.verbose:
        show(f"{clock()} $ {' '.join(map(str, args))}")
    result = subprocess.run(args, check=False, **options)
    if ARGS.verbose:
        show(f"{clock()}   exit {result.returncode}")
    return result


def delay(label):
    if not ARGS.delay:
        return
    for left in range(ARGS.delay, 0, -1):
        show(f"\r{clock()} next: {label}; starting in {left:2d}s", end="", flush=True)
        time.sleep(1)
    show(f"\r{clock()} next: {label}; starting now      ")


def devices(args):
    for _ in range(5):
        out = run(args, timeout=30).stdout
        lines = out.splitlines()
        if not any("no permissions" in line for line in lines) or any(
            line.split()[1:2] in (["device"], ["recovery"], ["fastboot"])
            for line in lines
        ):
            return out
        time.sleep(1)
    die(NO_ACCESS + rerun())


def die(message, prefix="ERROR: "):
    if ARGS.shown not in (None, "error"):
        print()
    ARGS.shown = "error"
    text = prefix + message
    if "\n" not in text:
        text = textwrap.fill(text, 79)
    if prefix and color(sys.stderr):
        text = f"\033[31m{text}\033[0m"
    sys.exit(text)


def digest(path, kind):
    h = hashlib.new(kind)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def download(build):
    b = BUILDS[build]
    CACHE.mkdir(exist_ok=True, parents=True)
    ota = (
        CACHE
        / f"update-kindle-biscuit_puffin-{b.ns}_user_{build}_{b.number.zfill(13)}.bin"
    )
    if not ota.is_file() or digest(ota, "sha256") != b.sha256:
        page = (
            f"{FTVDB}{b.md5}-{b.number}-fire-os-{b.ftvdb_version}-{b.ns.lower()}"
            f"-{build}-{b.date}/"
        )
        request = urllib.request.Request(page, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                links = re.findall(
                    r'https://[^"<> ]+\.bin', response.read().decode(errors="replace")
                )
            if not links:
                die("no download link on " + page)
            part = CACHE / (ota.name + ".part")
            response = urllib.request.urlopen(links[0], timeout=60)
            with response, part.open("wb") as out:
                done, total = save(response, out, f"downloading OTA {build}")
        except (OSError, http.client.HTTPException) as e:
            die(f"the download failed: {e!r}")
        if total and done != total:
            die(
                f"the download stopped after {done} of {total} bytes."
                " Run dot_restore_stock.py again."
            )
        part.replace(ota)
    if digest(ota, "sha256") != b.sha256:
        die(f"{ota} does not hash to {b.sha256}")
    passed(f"OTA {build} verified")
    return ota


def extract(ota, work):
    with zipfile.ZipFile(ota) as z:
        with z.open("payload.bin") as f:
            h = f.read(24)
            if h[:4] != b"CrAU" or struct.unpack(">Q", h[4:12])[0] != 2:
                die("the OTA's payload.bin is not a version 2 update payload")
            msize = struct.unpack(">Q", h[12:20])[0]
            sig = struct.unpack(">I", h[20:24])[0]
            manifest = f.read(msize)
        base = 24 + msize + sig
        block = 4096
        partitions = {}
        for fn, _, v in fields(manifest):
            if fn == 3:
                block = v
            if fn == 13:
                d, ops = {}, []
                for a, _, c in fields(v):
                    if a == 8:
                        ops.append(c)
                    else:
                        d[a] = c
                partitions[d[1].decode()] = (
                    {a: c for a, _, c in fields(d[7])},
                    ops,
                )
        with z.open("payload.bin") as payload:
            for name in IMAGES:
                if name not in partitions:
                    die(f"the OTA has no {name} image")
                info, ops = partitions[name]
                path = work / (name + ".img")
                if path.is_file() and digest(path, "sha256") == info[2].hex():
                    passed(f"{name:>{max(map(len, IMAGES))}} matches the manifest")
                    continue
                img = bytearray(info[1])
                for op in ops:
                    o, extents = {}, []
                    for a, _, c in fields(op):
                        if a == 6:
                            extents.append({x: y for x, _, y in fields(c)})
                        else:
                            o[a] = c
                    payload.seek(base + o.get(2, 0))
                    blob = payload.read(o.get(3, 0))
                    kind = o[1]
                    if kind == 0:
                        raw = blob
                    elif kind == 1:
                        raw = bz2.decompress(blob)
                    elif kind == 8:
                        raw = lzma.decompress(blob)
                    else:
                        die(f"{name} has op type {kind}")
                    pos = 0
                    for e in extents:
                        n = e[2] * block
                        at = e.get(1, 0) * block
                        img[at : at + n] = raw[pos : pos + n]
                        pos += n
                path.write_bytes(memoryview(img)[: info[1]])
                if digest(path, "sha256") != info[2].hex():
                    die(name + " does not match the manifest")
                passed(f"{name:>{max(map(len, IMAGES))}} matches the manifest")


def fields(b):
    i = 0
    while i < len(b):
        k, i = varint(b, i)
        fn, wt = k >> 3, k & 7
        if wt == 0:
            v, i = varint(b, i)
        elif wt == 2:
            n, i = varint(b, i)
            v = b[i : i + n]
            i += n
        elif wt == 1:
            v = b[i : i + 8]
            i += 8
        elif wt == 5:
            v = b[i : i + 4]
            i += 4
        else:
            die(f"the OTA's manifest has wire type {wt}")
        yield fn, wt, v


def gpt_intact(hdr, entries):
    if hdr[:8] != b"EFI PART" or struct.unpack("<I", hdr[12:16])[0] != 92:
        return False
    if struct.unpack("<II", hdr[80:88]) != (128, 128):
        return False
    h = bytearray(hdr[:92])
    h[16:20] = bytes(4)
    return (
        zlib.crc32(h) & 0xFFFFFFFF == struct.unpack("<I", hdr[16:20])[0]
        and zlib.crc32(entries[: 128 * 128]) & 0xFFFFFFFF
        == struct.unpack("<I", hdr[88:92])[0]
    )


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("build", choices=sorted(BUILDS))
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print each adb command, its exit status and the time",
    )
    parser.add_argument(
        "--delay",
        const=5,
        default=0,
        help="count down before each step, 5 seconds unless given, so a"
        " recording shows the Dot at each step; implies --verbose",
        metavar="SECONDS",
        nargs="?",
        type=int,
    )
    options = parser.parse_args()
    build = options.build
    ARGS.delay = options.delay
    ARGS.verbose = options.verbose or options.delay > 0
    if not shutil.which("adb"):
        die("adb not found: install Android platform-tools")
    check_user()
    check_adb()
    work = CACHE / ("stock-" + build)
    work.mkdir(exist_ok=True, parents=True)
    extract(download(build), work)

    if not usb_serial():
        die(
            "no Dot found on USB. Connect the rooted Dot with a USB cable, booted or"
            " in TWRP."
        )
    adb_state = run(["adb", "get-state"], timeout=30).stdout.strip()
    if adb_state == "device":
        show("Restarting the Dot into recovery (TWRP). Waiting for it to start.")
        run(["adb", "reboot", "recovery"], timeout=60)
        time.sleep(15)
        try:
            run(["adb", "wait-for-recovery"], timeout=300)
        except subprocess.TimeoutExpired:
            die("the Dot did not reach recovery (TWRP) within 5 minutes")
    elif adb_state != "recovery":
        die(
            "the Dot is neither in recovery (TWRP) nor accessible via adb. If it is on"
            " stock Fire OS already, there is nothing to restore; dot_root.py roots it."
        )
    deadline = time.monotonic() + 30
    version = ""
    while not version or "mtp" not in rshell("getprop sys.usb.config"):
        if time.monotonic() > deadline:
            die("TWRP did not finish starting within 30 seconds")
        time.sleep(1)
        version = rshell("getprop ro.twrp.version")
        if not version[:1].isdigit():
            version = ""
        elif not version.startswith("3.2."):
            die("this needs amonet v1.1.0's TWRP 3.2.3")
    device = rshell("getprop ro.product.device")
    if device != "biscuit":
        die(f"this is not an Echo Dot (2nd Gen): TWRP reports the device {device!r}")

    raw = read_sectors(0, 34)
    if not gpt_intact(raw[512:1024], raw[1024:]):
        size = rshell(f"blockdev --getsize64 {DISK}")
        if not size.isdigit():
            die("could not read the Dot's disk size")
        tail = read_sectors(int(size) // 512 - 33, 33)
        raw = raw[:512] + tail[-512:] + tail[:-512]
        show("the primary partition table is damaged; using the backup")
    saved = work / f"current-gpt-{os.environ['ANDROID_SERIAL']}.bin"
    if not saved.exists():
        saved.write_bytes(raw)
    primary, backup, backup_sector, parts = stock_gpt(raw)
    files = {}
    for name, data in (("gpt-primary.bin", primary), ("gpt-backup.bin", backup)):
        files[name] = work / name
        files[name].write_bytes(data)
    boot = work / "boot.img"
    boot_size = parts["boot_a"][2] * 512
    files["boot"] = work / "boot16.img"
    files["boot"].write_bytes(boot.read_bytes().ljust(boot_size, b"\0"))
    files["expdb"] = work / "expdb.zero"
    files["expdb"].write_bytes(b"\0" * (parts["expdb"][2] * 512))
    files["misc"] = work / "misc.zero"
    files["misc"].write_bytes(b"\0" * (parts["misc"][2] * 512))
    for image in ("system", "tee", "lk"):
        files[image] = work / (image + ".img")
    for key, part, _, _ in WRITES:
        if files[key].stat().st_size > parts[part][2] * 512:
            die(f"{files[key].name} does not fit {part}")
    boot0 = rshell("cat /sys/block/mmcblk0boot0/size")
    if (
        not boot0.isdigit()
        or (work / "preloader.img").stat().st_size != int(boot0) * 512
    ):
        die(f"preloader.img is not the size of boot0 ({boot0} sectors)")
    passed("stock partition table built from this Dot's own")
    warn(
        f"About to overwrite this Dot's bootloaders, system and data with stock {build}."
    )
    warn("Root is gone afterwards; dot_root.py puts it back.")
    try:
        for left in range(10, 0, -1):
            show(
                "\r" + paint(f"Starting in {left:2d} s. Ctrl-C cancels.", 33),
                "warn",
                end="",
                flush=True,
            )
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        die("stopped; nothing was written", prefix="")
    show("\r" + paint("Starting now.                     ", 33), "warn")

    progress = Progress(15)
    try:
        restore(progress, build, work, files, parts, backup_sector)
    except subprocess.TimeoutExpired as error:
        progress.fail(
            f"{' '.join(map(str, error.cmd))} did not finish in {error.timeout:.0f}"
            " seconds. Do not reboot; run dot_restore_stock.py again."
        )
    except KeyboardInterrupt:
        if progress.step == 15:
            die(f"stopped; stock {build} is in place", prefix="")
        progress.fail(
            "stopped part way. Do not reboot; run dot_restore_stock.py again."
        )


def md5_mismatch(command, want):
    got = (rshell(command).split("\n")[-1].split(" ") + [""])[0]
    return "" if got == want else f": read {got or 'nothing'}, expected {want}"


def on_usb(line):
    if os.name != "nt":
        return " usb:" in line
    parts = line.split()
    return (
        parts[1:2] in (["device"], ["recovery"], ["unauthorized"])
        and ":" not in parts[0]
        and not parts[0].startswith("emulator-")
    )


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if color(sys.stdout) else text


def passed(message):
    try:
        "\u2705".encode(sys.stdout.encoding or "ascii")
        mark = "\u2705"
    except (LookupError, UnicodeEncodeError):
        mark = "ok"
    show(f"{mark} {message}")


def read_sectors(start, count):
    raw = command(
        [
            "adb",
            "exec-out",
            f"dd if={DISK} bs=512 skip={start} count={count} 2>/dev/null",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        timeout=60,
    ).stdout
    if len(raw) != count * 512:
        die(f"read {len(raw)} bytes of the Dot's partition table, not {count * 512}")
    return raw


def rerun():
    return "sg plugdev -c " + shlex.quote(shlex.join([sys.executable, *sys.argv]))


def restore(progress, build, work, files, parts, backup_sector):
    rshell(UNMOUNT)
    for key, part, label, estimate in WRITES:
        write(progress, files[key], parts[part][1], label, estimate)
    write(
        progress,
        files["gpt-backup.bin"],
        backup_sector,
        "write stock partition table (backup)",
        "5 s",
    )
    write(
        progress,
        files["gpt-primary.bin"],
        0,
        "write stock partition table (primary)",
        "5 s",
    )

    progress.begin("format cache and userdata", "30 s")
    if "No problems found" not in rshell("sgdisk --verify " + DISK):
        progress.fail("sgdisk does not accept the new table; do not reboot")
    rshell(UNMOUNT + "; blockdev --rereadpt " + DISK)
    if rshell('grep -c "mmcblk0p1[78]$" /proc/partitions') != "0":
        progress.fail(
            "the kernel still sees amonet's partitions; do not reboot, reread the table first"
        )
    number, _, sectors = parts["userdata"]
    if not rshell(f'grep " {sectors // 2} mmcblk0p{number}$" /proc/partitions'):
        progress.fail("userdata is not its stock size; do not reboot")
    cache = parts["cache"][0]
    out = rshell(
        f"mke2fs -q -t ext4 {DISK}p{cache} && mke2fs -q -t ext4 {DISK}p{number}"
        " && echo formatted"
    )
    if out.split("\n")[-1] != "formatted":
        progress.fail("cache and userdata did not format; do not reboot")
    progress.end()

    progress.begin("write preloader to boot0", "5 s")
    preloader = work / "preloader.img"
    if run(["adb", "push", preloader, "/tmp/pl.img"], timeout=120).returncode != 0:
        progress.fail("the preloader did not reach the Dot; do not reboot")
    rshell(
        "echo 0 > /sys/block/mmcblk0boot0/force_ro; "
        "dd if=/tmp/pl.img of=/dev/block/mmcblk0boot0 bs=1048576 2>/dev/null; "
        "echo 1 > /sys/block/mmcblk0boot0/force_ro; sync; "
        "echo 3 > /proc/sys/vm/drop_caches"
    )
    wrong = md5_mismatch("md5sum /dev/block/mmcblk0boot0", digest(preloader, "md5"))
    if wrong:
        progress.fail(
            f"boot0 does not match the {build} preloader{wrong}; do not reboot"
        )
    progress.end()

    progress.begin("reboot into stock " + build, "1.5 min to an orange ring")
    progress.halt()
    print()
    passed(f"stock {build} is in place after {progress.minutes()}m.")
    warn(
        "If you will root it again, do not set it up in the Alexa app first: on Wi-Fi"
        " it can take an update to a build dot_root.py has not met."
    )
    try:
        run(["adb", "shell", "-n", "reboot"], timeout=60)
    except subprocess.TimeoutExpired:
        pass
    cache_note()


def rshell(command, timeout=300):
    out = run(["adb", "shell", "-n", command], timeout=timeout).stdout.replace("\r", "")
    return "\n".join(
        line for line in out.split("\n") if not line.startswith("__bionic_open_tzdata")
    ).strip()


def run(args, timeout=None):
    return command(
        args,
        errors="replace",
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def save(response, out, label):
    total = int(response.headers.get("Content-Length") or 0)
    div, unit = (1e3, "KB") if 0 < total < 1e6 else (1e6, "MB")

    def meter(done):
        if total:
            return (
                f"{done / div:.1f} of {total / div:.1f} {unit} ({100 * done // total}%)"
            )
        return f"{done / div:.1f} {unit}"

    room = 78 - (len(meter(total)) if total else 12)
    if len(label) > room:
        label = label[: room - 3] + "..."
    done = 0
    show(f"{label:<{room}} {meter(0):>{78 - room}}", end="", flush=True)
    for block in iter(lambda: response.read(1 << 20), b""):
        out.write(block)
        done += len(block)
        show(f"\r{label:<{room}} {meter(done):>{78 - room}}", end="", flush=True)
    print()
    return done, total


def show(text, kind="info", **options):
    if ARGS.shown not in (None, kind):
        print()
    ARGS.shown = kind
    print(text, **options)


def stock_gpt(raw):
    mbr, hdr, entries = (
        raw[:512],
        bytearray(raw[512:1024]),
        raw[1024 : 1024 + 128 * 128],
    )
    if not gpt_intact(hdr, entries):
        die("neither copy of the Dot's partition table is intact")
    last_usable = struct.unpack("<Q", hdr[48:56])[0]
    backup_lba = max(struct.unpack("<QQ", hdr[24:40]))
    names = [
        entries[i * 128 + 56 : (i + 1) * 128].decode("utf-16le").rstrip("\0")
        for i in range(128)
    ]
    amonet = any(name.endswith("_x") for name in names)
    new = bytearray(128 * 128)
    k = 0
    for i in range(128):
        e = bytearray(entries[i * 128 : (i + 1) * 128])
        if e[:16] == b"\0" * 16:
            continue
        name = names[i]
        if amonet and name in ("boot_a", "boot_b"):
            continue
        if name.endswith("_x"):
            name = name[:-2]
            e[56:128] = name.encode("utf-16le").ljust(72, b"\0")
        if name == "userdata":
            e[40:48] = struct.pack("<Q", last_usable)
        new[k * 128 : (k + 1) * 128] = e
        k += 1
    if k != 16:
        die(f"the stock table would have {k} partitions, not 16")
    entries_crc = zlib.crc32(new) & 0xFFFFFFFF

    def header(my, alternate, at):
        h = bytearray(hdr[:92])
        h[16:20] = b"\0" * 4
        h[24:32] = struct.pack("<Q", my)
        h[32:40] = struct.pack("<Q", alternate)
        h[72:80] = struct.pack("<Q", at)
        h[88:92] = struct.pack("<I", entries_crc)
        h[16:20] = struct.pack("<I", zlib.crc32(h) & 0xFFFFFFFF)
        return bytes(h).ljust(512, b"\0")

    parts = {}
    for i in range(k):
        e = new[i * 128 : (i + 1) * 128]
        first, last = struct.unpack("<QQ", e[32:48])
        parts[e[56:128].decode("utf-16le").rstrip("\0")] = (
            i + 1,
            first,
            last - first + 1,
        )
    primary = mbr + header(1, backup_lba, 2) + bytes(new)
    backup = bytes(new) + header(backup_lba, 1, backup_lba - 32)
    return primary, backup, backup_lba - 32, parts


def usb_serial():
    if USER_SERIAL:
        if ":" in USER_SERIAL:
            die("ANDROID_SERIAL names a network device; this needs the Dot on USB")
        devices(["adb", "devices", "-l"])
        return USER_SERIAL
    out = devices(["adb", "devices", "-l"])
    usb = [
        line.split()[0]
        for line in out.splitlines()[1:]
        if on_usb(line) and "no permissions" not in line
    ]
    if len(usb) > 1:
        die(MORE_THAN_ONE)
    if usb:
        os.environ["ANDROID_SERIAL"] = usb[0]
        return usb[0]
    os.environ.pop("ANDROID_SERIAL", None)
    return None


def varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


def warn(text):
    show(paint(textwrap.fill(text, 79), 33), "warn")


def write(progress, path, sector, label, estimate):
    n = path.stat().st_size
    if sector % 8 == 0 and n % 4096 == 0:
        bs, seek, count = 4096, sector // 8, n // 4096
    else:
        bs, seek, count = 512, sector, n // 512
    progress.begin(label, estimate)
    if rshell(f"rm -f {STATUS} && echo cleared").split("\n")[-1] != "cleared":
        progress.fail(f"{STATUS} could not be cleared before {label}; do not reboot")
    with path.open("rb") as f:
        result = command(
            [
                "adb",
                "exec-in",
                f"dd of={DISK} bs={bs} seek={seek} 2>/dev/null;"
                f" echo $? > {STATUS}.part && mv {STATUS}.part {STATUS}",
            ],
            stderr=subprocess.STDOUT,
            stdin=f,
            stdout=subprocess.PIPE,
            timeout=900,
        )
    if result.returncode != 0:
        progress.fail(
            f"{label} failed; do not reboot:\n" + result.stdout.decode(errors="replace")
        )
    deadline = time.monotonic() + 120
    while True:
        status = rshell(f"cat {STATUS} 2>/dev/null || echo running").split("\n")[-1]
        if status.isdigit():
            break
        if time.monotonic() > deadline:
            progress.fail(f"{label} did not finish within 2 minutes; do not reboot")
        time.sleep(1)
    if status != "0":
        progress.fail(f"{label} failed on the Dot: dd exited {status}; do not reboot")
    if rshell(FLUSH).split("\n")[-1] != "flushed":
        progress.fail(label + " could not be flushed; do not reboot")
    wrong = md5_mismatch(
        f"dd if={DISK} bs={bs} skip={seek} count={count} 2>/dev/null | md5sum",
        digest(path, "md5"),
    )
    if wrong:
        progress.fail(f"{label} did not verify{wrong}; do not reboot")
    progress.end()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        die("stopped; nothing was written", prefix="")
    except subprocess.TimeoutExpired as error:
        die(
            f"{' '.join(map(str, error.cmd))} did not finish in {error.timeout:.0f}"
            " seconds; nothing was written"
        )
