#!/usr/bin/env python3
"""Return an Echo Dot (2nd Generation) rooted on amonet v1.1 or v2 to stock."""

from __future__ import annotations

import argparse
import bz2
import contextlib
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
from typing import TYPE_CHECKING, BinaryIO, NamedTuple, NoReturn, TextIO

if TYPE_CHECKING:
    from collections.abc import Iterator

ARGS = argparse.Namespace(dd="dd", delay=0, shown=None, verbose=False)
AS_ROOT = (
    "run this as your own user, not as root or with sudo: the downloads would"
    " belong to root, and on Linux udev rules let a user open the Dot"
)


BCB = b"\0ABB\x01\x8f\0"
BCB_OFFSET = 0x360
BLOCK_SIZE_FIELD = 3
CHAIN_TEE = ("tee2", "tee1")
CLEAR_BOOT0 = (
    "echo 0 > /sys/block/mmcblk0boot0/force_ro; "
    "{dd} if=/dev/zero of=/dev/block/mmcblk0boot0 bs=4096 count=1 2>/dev/null; "
    "echo 1 > /sys/block/mmcblk0boot0/force_ro; sync; "
    "echo 3 > /proc/sys/vm/drop_caches; "
    'echo "$({dd} if=/dev/block/mmcblk0boot0 bs=4096 count=1 2>/dev/null | wc -c)'
    " $({dd} if=/dev/block/mmcblk0boot0 bs=4096 count=1 2>/dev/null"
    " | tr -d '\\0' | wc -c)\""
)
DISK = "/dev/block/mmcblk0"
DOT_TMP = pathlib.PurePosixPath("/tmp")  # ruff: ignore[hardcoded-temp-file]
DST_EXTENTS_FIELD = 6
FLUSH = "sync && echo 3 > /proc/sys/vm/drop_caches && echo flushed"
FTVDB = "https://ftvdb.com/echo/firmware/com.amazon.biscuit.android.os/"
GPT_HEADER_SIZE = 92
HEAD_CHECK = 1 << 20
IMAGES = ("preloader", "lk", "tee", "boot", "system")
MEGA = 1e6
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
OPERATIONS_FIELD = 8
PARTITIONS_FIELD = 13
PAYLOAD_VERSION = 2
REPLACE = 0
REPLACE_BZ = 1
REPLACE_XZ = 8
STEPS = 16
STOCK_PARTITIONS = 16
TWRP_VERSIONS = ("3.2.", "3.7.")
UNMOUNT = (
    'for m in $(grep "^/dev/block" /proc/mounts | cut -d" " -f2); do umount "$m";'
    ' done; echo "left:$(grep "^/dev/block" /proc/mounts | cut -d" " -f2'
    ' | tr "\\n" " ")"'
)
USER_SERIAL = os.environ.get("ANDROID_SERIAL")
VARINT_MORE = 0x80
WIRE_FIXED32 = 5
WIRE_FIXED64 = 1
WIRE_LEN = 2
WIRE_VARINT = 0
WRITES = (
    ("system", "system_a", "write system image to system_a (768 MB)", "3-4 min"),
    ("system", "system_b", "write system image to system_b (768 MB)", "3-4 min"),
    ("boot", "boot_a", "write boot image to boot_a", "10 s"),
    ("boot", "boot_b", "write boot image to boot_b", "10 s"),
    ("misc", "misc", "write misc, slot a marked good", "5 s"),
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
    def __init__(self, steps: int) -> None:
        self.step = 0
        self.steps = steps
        self.t0 = time.monotonic()
        self.ts = self.t0
        self.line = ""
        self.stopped = threading.Event()
        self.ticker = None

    def begin(self, *, estimate: str, label: str) -> None:
        self.step += 1
        about = f"(~{estimate})"
        self.line = f"[{self.step:2d}/{self.steps}] {label:<44} {about:<10} ... "
        delay(f"[{self.step:2d}/{self.steps}] {label}")
        self.ts = time.monotonic()
        if ARGS.verbose:
            show(text=self.line.rstrip())
            return
        show(end="", flush=True, text=self.line)
        self.stopped.clear()
        self.ticker = threading.Thread(daemon=True, target=self.tick)
        self.ticker.start()

    def end(self) -> None:
        back = "\r" if self.halt() else ""
        show(text=f"{back}{self.line}done in {self.seconds()}, {self.minutes()}m total")

    def fail(self, message: str, *, bootable: bool = False) -> NoReturn:
        if self.halt():
            print()
        if not bootable and 0 < self.step < self.steps:
            message += (
                ". boot0 has no preloader until the last step, so the Dot will not"
                " start at all until this run finishes"
            )
        _die(message=message)

    def halt(self) -> bool:
        if not self.ticker:
            return False
        self.stopped.set()
        self.ticker.join()
        self.ticker = None
        return True

    def minutes(self) -> int:
        return int((time.monotonic() - self.t0) // 60)

    def seconds(self) -> str:
        return f"{int(time.monotonic() - self.ts):3d}s"

    def skip(self) -> None:
        back = "\r" if self.halt() else ""
        show(text=f"{back}{self.line}already correct, {self.minutes()}m total")

    def tick(self) -> None:
        while not self.stopped.wait(1):
            show(end="", flush=True, text=f"\r{self.line}{self.seconds()}")


def _die(*, message: str, prefix: str = "ERROR: ") -> NoReturn:
    if ARGS.shown not in {None, "error"}:
        print()
    ARGS.shown = "error"
    text = prefix + message
    if "\n" not in text:
        text = textwrap.fill(text, 79)
    if prefix and color(sys.stderr):
        text = f"\033[31m{text}\033[0m"
    raise SystemExit(text)


def cache_dir() -> pathlib.Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or pathlib.Path.home()
    else:
        base = os.environ.get("XDG_CACHE_HOME") or pathlib.Path.home() / ".cache"
    return pathlib.Path(base) / "overdub-stock"


CACHE = cache_dir()


def cache_note() -> None:
    if CACHE.is_dir():
        size = sum(f.stat().st_size for f in CACHE.rglob("*") if f.is_file())
        path, home = str(CACHE), str(pathlib.Path.home())
        if os.name != "nt" and path.startswith(home + os.sep):
            path = "~" + path[len(home) :]
        show(
            text=f"{path} holds {size // 1000000} MB of downloads and images for the"
            " next run. It is safe to delete."
        )


def chain_writes() -> tuple[tuple[str, str, str, str], ...]:
    live = "lk_b" if rshell(command="getprop ro.boot.slot_suffix") == "_b" else "lk_a"
    spare = "lk_a" if live == "lk_b" else "lk_b"
    first, last = CHAIN_TEE
    return (
        ("lk", spare, f"write LK image to {spare} (spare slot)", "5 s"),
        ("tee", first, f"write TEE image to {first} (backup)", "5 s"),
        ("expdb", "expdb", "zero expdb (amonet kaeru payload)", "5 s"),
        ("lk", live, f"write LK image to {live} (live slot)", "5 s"),
        ("tee", last, f"write TEE image to {last} (primary)", "5 s"),
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


def clear_boot0(*, progress: Progress) -> None:
    answer = rshell(command=CLEAR_BOOT0.format(dd=ARGS.dd)).split("\n")[-1].split()
    if answer == ["4096", "0"]:
        return
    read, *still_set = answer or [""]
    if read == "4096" and still_set:
        progress.fail(
            "boot0's header did not clear, so a failure from here would brick"
            " rather than fall into the bootrom; nothing else was written",
            bootable=True,
        )
    progress.fail(
        "boot0 did not read back, so whether its header cleared is unknown;"
        " nothing else was written"
    )


def clock() -> str:
    return time.strftime("%H:%M:%S")


def color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    if os.name == "nt" and "WT_SESSION" not in os.environ:
        return False
    return stream.isatty()


def command(
    *,
    args: list[str | pathlib.PurePath],
    stderr: int | None = None,
    stdin: int | BinaryIO | None = None,
    stdout: int | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if ARGS.verbose:
        show(text=f"{clock()} $ {' '.join(map(str, args))}")
    result = subprocess.run(
        args,
        check=False,
        stderr=stderr,
        stdin=stdin,
        stdout=stdout,
        timeout=timeout,
    )
    if ARGS.verbose:
        show(text=f"{clock()}   exit {result.returncode}")
    return result


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


def digest(*, kind: str, limit: int = 0, path: pathlib.Path) -> str:
    h = hashlib.new(kind)
    left = limit or path.stat().st_size
    with path.open("rb") as f:
        while left > 0:
            block = f.read(min(1 << 20, left))
            if not block:
                break
            left -= len(block)
            h.update(block)
    return h.hexdigest()


def download(build: str) -> pathlib.Path:
    b = BUILDS[build]
    CACHE.mkdir(exist_ok=True, parents=True)
    ota = (
        CACHE
        / f"update-kindle-biscuit_puffin-{b.ns}_user_{build}_{b.number.zfill(13)}.bin"
    )
    if not ota.is_file() or digest(kind="sha256", path=ota) != b.sha256:
        page = (
            f"{FTVDB}{b.md5}-{b.number}-fire-os-{b.ftvdb_version}-{b.ns.lower()}"
            f"-{build}-{b.date}/"
        )
        request = urllib.request.Request(page, headers={"User-Agent": "Mozilla/5.0"})
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            with urllib.request.urlopen(request, timeout=60) as response:
                links = re.findall(
                    r'https://[^"<> ]+\.bin', response.read().decode(errors="replace")
                )
            if not links:
                _die(message="no download link on " + page)
            part = CACHE / (ota.name + ".part")
            response = urllib.request.urlopen(links[0], timeout=60)
            with response, part.open("wb") as out:
                done, total = save(
                    label=f"downloading OTA {build}", out=out, response=response
                )
        except (OSError, http.client.HTTPException) as e:
            _die(message=f"the download failed: {e!r}")
        if total and done != total:
            _die(
                message=f"the download stopped after {done} of {total} bytes."
                " Run dot_restore_stock.py again."
            )
        part.replace(ota)
    if digest(kind="sha256", path=ota) != b.sha256:
        _die(message=f"{ota} does not hash to {b.sha256}")
    passed(f"OTA {build} verified")
    return ota


def extract(*, ota: pathlib.Path, work: pathlib.Path) -> None:  # ruff: ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
    with zipfile.ZipFile(ota) as z:  # ruff: ignore[too-many-nested-blocks]
        with z.open("payload.bin") as f:
            h = f.read(24)
            if h[:4] != b"CrAU" or struct.unpack(">Q", h[4:12])[0] != PAYLOAD_VERSION:
                _die(message="the OTA's payload.bin is not a version 2 update payload")
            msize = struct.unpack(">Q", h[12:20])[0]
            sig = struct.unpack(">I", h[20:24])[0]
            manifest = f.read(msize)
        base = 24 + msize + sig
        block = 4096
        partitions = {}
        for fn, _, v in fields(manifest):
            if fn == BLOCK_SIZE_FIELD:
                block = v
            if fn == PARTITIONS_FIELD:
                d, ops = {}, []
                for a, _, c in fields(v):
                    if a == OPERATIONS_FIELD:
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
                    _die(message=f"the OTA has no {name} image")
                info, ops = partitions[name]
                path = work / (name + ".img")
                if path.is_file() and digest(kind="sha256", path=path) == info[2].hex():
                    passed(f"{name:>{max(map(len, IMAGES))}} matches the manifest")
                    continue
                img = bytearray(info[1])
                for op in ops:
                    o, extents = {}, []
                    for a, _, c in fields(op):
                        if a == DST_EXTENTS_FIELD:
                            extents.append({x: y for x, _, y in fields(c)})
                        else:
                            o[a] = c
                    payload.seek(base + o.get(2, 0))
                    blob = payload.read(o.get(3, 0))
                    kind = o[1]
                    if kind == REPLACE:
                        raw = blob
                    elif kind == REPLACE_BZ:
                        raw = bz2.decompress(blob)
                    elif kind == REPLACE_XZ:
                        raw = lzma.decompress(blob)
                    else:
                        _die(message=f"{name} has op type {kind}")
                    pos = 0
                    for e in extents:
                        n = e[2] * block
                        at = e.get(1, 0) * block
                        img[at : at + n] = raw[pos : pos + n]
                        pos += n
                path.write_bytes(memoryview(img)[: info[1]])
                if digest(kind="sha256", path=path) != info[2].hex():
                    _die(message=name + " does not match the manifest")
                passed(f"{name:>{max(map(len, IMAGES))}} matches the manifest")


def fields(b: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    i = 0
    while i < len(b):
        k, i = varint(b=b, i=i)
        fn, wt = k >> 3, k & 7
        if wt == WIRE_VARINT:
            v, i = varint(b=b, i=i)
        elif wt == WIRE_LEN:
            n, i = varint(b=b, i=i)
            v = b[i : i + n]
            i += n
        elif wt == WIRE_FIXED64:
            v = b[i : i + 8]
            i += 8
        elif wt == WIRE_FIXED32:
            v = b[i : i + 4]
            i += 4
        else:
            _die(message=f"the OTA's manifest has wire type {wt}")
        yield fn, wt, v


def gpt_intact(*, entries: bytes, hdr: bytes) -> bool:
    if hdr[:8] != b"EFI PART" or struct.unpack("<I", hdr[12:16])[0] != GPT_HEADER_SIZE:
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


def main() -> None:  # ruff: ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
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
        _die(message="adb not found: install Android platform-tools")
    check_user()
    check_adb()
    work = CACHE / ("stock-" + build)
    work.mkdir(exist_ok=True, parents=True)
    extract(ota=download(build), work=work)

    if not usb_serial():
        _die(
            message="no Dot found on USB. Connect the rooted Dot with a USB cable,"
            " booted or in TWRP."
        )
    adb_state = run(args=["adb", "get-state"], timeout=30).stdout.strip()
    if adb_state == "device":
        show(text="Restarting the Dot into recovery (TWRP). Waiting for it to start.")
        run(args=["adb", "reboot", "recovery"], timeout=60)
        time.sleep(15)
        try:
            run(args=["adb", "wait-for-recovery"], timeout=300)
        except subprocess.TimeoutExpired:
            _die(message="the Dot did not reach recovery (TWRP) within 5 minutes")
    elif adb_state != "recovery":
        _die(
            message="the Dot is neither in recovery (TWRP) nor accessible via adb."
            " If it is on stock Fire OS already, there is nothing to restore;"
            " dot_root.py roots it."
        )
    deadline = time.monotonic() + 30
    version = ""
    while not version or "mtp" not in rshell(command="getprop sys.usb.config"):
        if time.monotonic() > deadline:
            _die(message="TWRP did not finish starting within 30 seconds")
        time.sleep(1)
        version = rshell(command="getprop ro.twrp.version")
        if not version[:1].isdigit():
            version = ""
        elif not version.startswith(TWRP_VERSIONS):
            _die(message="this needs amonet's TWRP: v1.1.0's 3.2.3 or v2.0.0's 3.7.0")
    if (
        rshell(command="toybox dd --help >/dev/null 2>&1 && echo yes").split("\n")[-1]
        == "yes"
    ):
        ARGS.dd = "toybox dd"
    tools = rshell(
        command="m=; for t in sgdisk mke2fs blockdev md5sum; do"
        ' command -v "$t" >/dev/null 2>&1 || which "$t" >/dev/null 2>&1'
        ' || m="$m $t"; done; echo "tools:$m"'
    ).split("\n")[-1]
    if not tools.startswith("tools:"):
        _die(message="the Dot did not answer which tools it has: " + tools)
    if tools != "tools:":
        _die(message="this TWRP has no" + tools[len("tools:") :])
    device = rshell(command="getprop ro.product.device")
    if device != "biscuit":
        _die(
            message="this is not an Echo Dot (2nd Gen): TWRP reports the"
            f" device {device!r}"
        )

    raw = read_sectors(count=34, start=0)
    if not gpt_intact(entries=raw[1024:], hdr=raw[512:1024]):
        size = rshell(command=f"blockdev --getsize64 {DISK}")
        if not size.isdigit():
            _die(message="could not read the Dot's disk size")
        tail = read_sectors(count=33, start=int(size) // 512 - 33)
        raw = raw[:512] + tail[-512:] + tail[:-512]
        show(text="the primary partition table is damaged; using the backup")
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
    misc = bytearray(parts["misc"][2] * 512)
    misc[BCB_OFFSET : BCB_OFFSET + len(BCB)] = BCB
    files["misc"] = work / "misc.img"
    files["misc"].write_bytes(misc)
    for image in ("system", "tee", "lk"):
        files[image] = work / (image + ".img")
    for key, part, _, _ in (*WRITES, *chain_writes()):
        if files[key].stat().st_size > parts[part][2] * 512:
            _die(message=f"{files[key].name} does not fit {part}")
    if (
        rshell(command="[ -b /dev/block/mmcblk0boot0 ] && echo block").split("\n")[-1]
        != "block"
    ):
        _die(
            message="/dev/block/mmcblk0boot0 is not a block device, so the"
            " preloader would be written to a file and read back from it"
        )
    boot0 = rshell(command="cat /sys/block/mmcblk0boot0/size")
    if (
        not boot0.isdigit()
        or (work / "preloader.img").stat().st_size != int(boot0) * 512
    ):
        _die(message=f"preloader.img is not the size of boot0 ({boot0} sectors)")
    passed("stock partition table built from this Dot's own")
    warn(
        "About to overwrite this Dot's bootloaders, system and data with"
        f" stock {build}."
    )
    warn("Root is gone afterwards; dot_root.py puts it back.")
    try:
        for left in range(10, 0, -1):
            show(
                end="",
                flush=True,
                kind="warn",
                text="\r"
                + paint(code=33, text=f"Starting in {left:2d} s. Ctrl-C cancels."),
            )
            time.sleep(1)
    except KeyboardInterrupt:
        print()
        _die(message="stopped; nothing was written", prefix="")
    show(
        kind="warn",
        text="\r" + paint(code=33, text="Starting now.                     "),
    )

    progress = Progress(STEPS)
    try:
        restore(
            backup_sector=backup_sector,
            build=build,
            files=files,
            parts=parts,
            progress=progress,
            work=work,
        )
    except subprocess.TimeoutExpired as error:
        progress.fail(
            f"{' '.join(map(str, error.cmd))} did not finish in {error.timeout:.0f}"
            " seconds. Do not reboot; run dot_restore_stock.py again."
        )
    except KeyboardInterrupt:
        if progress.step == STEPS:
            _die(message=f"stopped; stock {build} is in place", prefix="")
        progress.fail(
            "stopped part way. Do not reboot; run dot_restore_stock.py again."
        )


def md5_mismatch(*, command: str, want: str) -> str:
    got = [*rshell(command=command).split("\n")[-1].split(" "), ""][0]
    return "" if got == want else f": read {got or 'nothing'}, expected {want}"


def on_usb(line: str) -> bool:
    if os.name != "nt":
        return " usb:" in line
    parts = line.split()
    return (
        parts[1:2] in (["device"], ["recovery"], ["unauthorized"])
        and ":" not in parts[0]
        and not parts[0].startswith("emulator-")
    )


def paint(*, code: int, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if color(sys.stdout) else text


def passed(message: str) -> None:
    try:
        "\u2705".encode(sys.stdout.encoding or "ascii")
        mark = "\u2705"
    except (LookupError, UnicodeEncodeError):
        mark = "ok"
    show(text=f"{mark} {message}")


def read_sectors(*, count: int, start: int) -> bytes:
    raw = command(
        args=[
            "adb",
            "exec-out",
            f"{ARGS.dd} if={DISK} bs=512 skip={start} count={count} 2>/dev/null",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        timeout=60,
    ).stdout
    if len(raw) != count * 512:
        _die(
            message=f"read {len(raw)} bytes of the Dot's partition table,"
            f" not {count * 512}"
        )
    return raw


def rerun() -> str:
    return "sg plugdev -c " + shlex.quote(shlex.join([sys.executable, *sys.argv]))


def restore(  # ruff: ignore[too-many-arguments]
    *,
    backup_sector: int,
    build: str,
    files: dict[str, pathlib.Path],
    parts: dict[str, tuple[int, int, int]],
    progress: Progress,
    work: pathlib.Path,
) -> None:
    unmount()

    progress.begin(estimate="5 s", label="clear the preloader header (boot0)")
    clear_boot0(progress=progress)
    progress.end()

    for key, part, label, estimate in WRITES:
        write(
            estimate=estimate,
            label=label,
            number=parts[part][0],
            path=files[key],
            progress=progress,
            sector=parts[part][1],
        )
    write(
        estimate="5 s",
        label="write stock partition table (backup)",
        path=files["gpt-backup.bin"],
        progress=progress,
        sector=backup_sector,
    )
    write(
        estimate="5 s",
        label="write stock partition table (primary)",
        path=files["gpt-primary.bin"],
        progress=progress,
        sector=0,
    )

    progress.begin(estimate="30 s", label="format cache and userdata")
    if "No problems found" not in rshell(command="sgdisk --verify " + DISK):
        progress.fail("sgdisk does not accept the new table; do not reboot")
    unmount(progress=progress)
    rshell(command="blockdev --rereadpt " + DISK)
    if rshell(command='grep -c "mmcblk0p1[78]$" /proc/partitions') != "0":
        progress.fail(
            "the kernel still sees amonet's partitions; do not reboot,"
            " reread the table first"
        )
    number, _, sectors = parts["userdata"]
    if not rshell(command=f'grep " {sectors // 2} mmcblk0p{number}$" /proc/partitions'):
        progress.fail("userdata is not its stock size; do not reboot")
    cache = parts["cache"][0]
    out = rshell(
        command=f"mke2fs -q -t ext4 {DISK}p{cache} && mke2fs -q -t ext4 {DISK}p{number}"
        " && echo formatted"
    )
    if out.split("\n")[-1] != "formatted":
        progress.fail("cache and userdata did not format; do not reboot")
    progress.end()

    for key, part, label, estimate in chain_writes():
        write(
            estimate=estimate,
            label=label,
            number=parts[part][0],
            path=files[key],
            progress=progress,
            sector=parts[part][1],
        )

    progress.begin(estimate="5 s", label="write preloader to boot0")
    preloader = work / "preloader.img"
    staged = DOT_TMP / "pl.img"
    if run(args=["adb", "push", preloader, staged], timeout=120).returncode != 0:
        progress.fail("the preloader did not reach the Dot; do not reboot")
    rshell(
        command="echo 0 > /sys/block/mmcblk0boot0/force_ro; "
        f"{ARGS.dd} if={staged} of=/dev/block/mmcblk0boot0 bs=1048576 2>/dev/null; "
        "echo 1 > /sys/block/mmcblk0boot0/force_ro; sync; "
        "echo 3 > /proc/sys/vm/drop_caches"
    )
    wrong = md5_mismatch(
        command="md5sum /dev/block/mmcblk0boot0",
        want=digest(kind="md5", path=preloader),
    )
    if wrong:
        progress.fail(
            f"boot0 does not match the {build} preloader{wrong}; do not reboot"
        )
    progress.end()

    progress.begin(
        estimate="1.5 min to an orange ring", label="reboot into stock " + build
    )
    progress.halt()
    print()
    passed(f"stock {build} is in place after {progress.minutes()}m.")
    warn(
        "If you will root it again, do not set it up in the Alexa app first: on Wi-Fi"
        " it can take an update to a build dot_root.py has not met."
    )
    with contextlib.suppress(subprocess.TimeoutExpired):
        run(args=["adb", "shell", "-n", "reboot"], timeout=60)
    cache_note()


def rshell(*, command: str, timeout: float = 300) -> str:
    out = run(args=["adb", "shell", "-n", command], timeout=timeout).stdout
    return "\n".join(
        line for line in out.split("\n") if not line.startswith("__bionic_open_tzdata")
    ).strip()


def run(
    *, args: list[str | pathlib.PurePath], timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    result = command(
        args=args,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        timeout=timeout,
    )
    result.stdout = result.stdout.decode("utf-8", "replace").replace("\r", "")
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
    show(end="", flush=True, text=f"{label:<{room}} {meter(0):>{78 - room}}")
    for block in iter(lambda: response.read(1 << 20), b""):
        out.write(block)
        done += len(block)
        show(end="", flush=True, text=f"\r{label:<{room}} {meter(done):>{78 - room}}")
    print()
    return done, total


def show(*, kind: str = "info", text: str, **options: str | bool) -> None:
    if ARGS.shown not in {None, kind}:
        print()
    ARGS.shown = kind
    print(text, **options)


def stock_gpt(  # ruff: ignore[too-many-locals]
    raw: bytes,
) -> tuple[bytes, bytes, int, dict[str, tuple[int, int, int]]]:
    mbr, hdr, entries = (
        raw[:512],
        bytearray(raw[512:1024]),
        raw[1024 : 1024 + 128 * 128],
    )
    if not gpt_intact(entries=entries, hdr=hdr):
        _die(message="neither copy of the Dot's partition table is intact")
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
        if amonet and name in {"boot_a", "boot_b"}:
            continue
        if name.endswith("_x"):
            name = name[:-2]
            e[56:128] = name.encode("utf-16le").ljust(72, b"\0")
        if name == "userdata":
            e[40:48] = struct.pack("<Q", last_usable)
        new[k * 128 : (k + 1) * 128] = e
        k += 1
    if k != STOCK_PARTITIONS:
        _die(
            message=f"the stock table would have {k} partitions, not {STOCK_PARTITIONS}"
        )
    entries_crc = zlib.crc32(new) & 0xFFFFFFFF

    def header(*, alternate: int, at: int, my: int) -> bytes:
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
    primary = mbr + header(alternate=backup_lba, at=2, my=1) + bytes(new)
    backup = bytes(new) + header(alternate=1, at=backup_lba - 32, my=backup_lba)
    return primary, backup, backup_lba - 32, parts


def unmount(*, progress: Progress | None = None) -> None:
    left = rshell(command=UNMOUNT).split("\n")[-1]
    if left.startswith("left:"):
        if not left[len("left:") :].strip():
            return
        message = "still mounted: " + left[len("left:") :].strip()
    else:
        message = "the Dot did not answer what is mounted: " + left
    if progress:
        progress.fail(message + "; do not reboot")
    _die(message=message + "; nothing was written")


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


def varint(*, b: bytes, i: int) -> tuple[int, int]:
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < VARINT_MORE:
            return r, i


def warn(text: str) -> None:
    show(kind="warn", text=paint(code=33, text=textwrap.fill(text, 79)))


def write(  # ruff: ignore[too-many-arguments]
    *,
    estimate: str,
    label: str,
    number: int | None = None,
    path: pathlib.Path,
    progress: Progress,
    sector: int,
) -> None:
    n = path.stat().st_size
    if sector % 8 == 0 and n % 4096 == 0:
        bs, offset, count = 4096, sector // 8, n // 4096
    else:
        bs, offset, count = 512, sector, n // 512
    verify = (
        f"{ARGS.dd} if={DISK} bs={bs} skip={offset} count={count} 2>/dev/null | md5sum"
    )
    want = digest(kind="md5", path=path)
    head = min(n, HEAD_CHECK)
    progress.begin(estimate=estimate, label=label)
    same_head = head == n or not md5_mismatch(
        command=f"{ARGS.dd} if={DISK} bs={bs} skip={offset} count={head // bs}"
        " 2>/dev/null | md5sum",
        want=digest(kind="md5", limit=head, path=path),
    )
    if same_head and not md5_mismatch(command=verify, want=want):
        progress.skip()
        return
    if number is not None:
        start = rshell(command=f"cat /sys/class/block/mmcblk0p{number}/start")
        if start.split("\n")[-1].strip() != str(sector):
            progress.fail(
                f"{DISK}p{number} starts at {start or 'nothing'}, not {sector},"
                " so the running partition table is not the one this expects"
            )
        pushed = run(args=["adb", "push", path, f"{DISK}p{number}"], timeout=1800)
        if pushed.returncode != 0:
            progress.fail(f"{label} failed; do not reboot:\n{pushed.stdout}")
    else:
        staged = DOT_TMP / path.name
        pushed = run(args=["adb", "push", path, staged], timeout=120)
        if pushed.returncode != 0:
            progress.fail(
                f"{label} did not reach the Dot; do not reboot:\n{pushed.stdout}"
            )
        done = rshell(
            command=f"{ARGS.dd} if={staged} of={DISK} bs={bs} seek={offset}"
            f" && rm -f {staged} && echo written"
        )
        if done.split("\n")[-1] != "written":
            progress.fail(f"{label} failed; do not reboot:\n{done}")
    if rshell(command=FLUSH).split("\n")[-1] != "flushed":
        progress.fail(label + " could not be flushed; do not reboot")
    wrong = md5_mismatch(command=verify, want=want)
    if wrong:
        progress.fail(f"{label} did not verify{wrong}; do not reboot")
    progress.end()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        _die(message="stopped; nothing was written", prefix="")
    except subprocess.TimeoutExpired as error:
        _die(
            message=f"{' '.join(map(str, error.cmd))} did not finish in"
            f" {error.timeout:.0f} seconds; nothing was written"
        )
