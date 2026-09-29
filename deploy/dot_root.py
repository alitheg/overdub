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
import contextlib
import hashlib
import http.client
import os
import pathlib
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import urllib.request
import zipfile
from typing import BinaryIO, NoReturn, TextIO

AMONET_V1 = "amonet-biscuit-v1.1.0.zip"
AMONET_V1_SHA = "bd4d3a18b6b6e9ff6e49a4739159a81020673202795cb3959f7c9ff24351b663"
AMONET_V2 = "amonet-biscuit-v2.0.0.zip"
AMONET_V2_SHA = "98297293701082bc7272efe077f941c56fc7b6e1f27ef6f2e93b6e4c6fc7b62d"
ARGS = argparse.Namespace(delay=0, probing=False, shown=None, verbose=False)
AS_ROOT = (
    "run this as your own user, not as root or with sudo: the downloads would"
    " belong to root, and on Linux udev rules let a user open the Dot"
)
BOOT_SH = """\
set -e
mountpoint -q /system || mount /system
rm -rf /tmp/bp; mkdir /tmp/bp; cd /tmp/bp; chmod 755 /tmp/magiskboot
dd if=/dev/block/other-boot of=boot.img bs=1048576 2>/dev/null
LD_LIBRARY_PATH=/system/lib /tmp/magiskboot --unpack boot.img >/dev/null 2>&1
mkdir r; cd r; cpio -id < ../ramdisk.cpio 2>/dev/null
for f in fstab*; do sed -i 's/,verify//g; s/verify,//g' "$f"; done
sed -i -e 's/^ro.secure=1$/ro.secure=0/' -e 's/^ro.debuggable=0$/ro.debuggable=1/' \\
  -e 's/^persist.sys.usb.config=.*/persist.sys.usb.config=mtp,adb/' default.prop
find . | cpio -o -H newc > ../ramdisk.cpio 2>/dev/null; cd ..
LD_LIBRARY_PATH=/system/lib /tmp/magiskboot --repack boot.img new.img >/dev/null 2>&1
cd /; umount /system
echo root-step-ok
"""
CMDLINE_SIZE = 512
DATA_SH = """\
set -e
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
LK_V1 = "f379dba-20170906_000423"
LK_V2 = "63cb91b-20221007_072309"
MAGISK = "Magisk-v17.3.zip"
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
PAYLOAD_NAME = "payload.bin"
PAYLOAD_SHA = "a23a3dc5baf0c255f31e8c915dc00d3c82978e4a180aa441ac444c79afdaa5cf"
PAYLOAD_URL = (
    "https://github.com/bboe/amonet-biscuit/releases/download/payload-v1/payload.bin"
)
PUSH_TRIES = 3
PYSERIAL = "pyserial-3.5-py2.py3-none-any.whl"
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


WAIT = 600


class Progress:
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.ts = self.t0
        self.line = ""
        self.open = False
        self.stopped = threading.Event()
        self.ticker = None

    def begin(self, *, estimate: str = "", label: str) -> None:
        self.end()
        about = f"(~{estimate})" if estimate else ""
        self.line = f"{label:<44} {about:<10} ... "
        delay(label)
        self.ts = time.monotonic()
        self.open = True
        if ARGS.verbose:
            show(text=self.line.rstrip())
        else:
            self.start()

    def end(self) -> None:
        if not self.open:
            return
        self.open = False
        back = "\r" if self.halt() else ""
        show(text=f"{back}{self.line}done in {self.seconds()}, {since(self.t0)} total")

    def halt(self) -> bool:
        if not self.ticker:
            return False
        self.stopped.set()
        self.ticker.join()
        self.ticker = None
        return True

    def note(self, message: str) -> None:
        running = self.halt()
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
        while not self.stopped.wait(1):
            show(end="", flush=True, text=f"\r{self.line}{self.seconds()}")


def _die(*, message: str, prefix: str = "ERROR: ") -> NoReturn:
    if PROGRESS.halt():
        print()
    if ARGS.shown not in {None, "error"}:
        print()
    ARGS.shown = "error"
    text = prefix + message
    if "\n" not in text:
        text = textwrap.fill(text, 79)
    if prefix and color(sys.stderr):
        text = f"\033[31m{text}\033[0m"
    raise SystemExit(text)


def bootrom(  # ruff: ignore[complex-structure, too-many-branches, too-many-statements]
    *, amonet: pathlib.Path, erase: bool, payload: pathlib.Path, wheel: pathlib.Path
) -> None:
    shutil.copyfile(payload, amonet / "brom-payload" / "build" / "payload.bin")
    log_path = CACHE / "bootrom.log"
    env = dict(os.environ, PYTHONPATH=str(wheel), PYTHONUNBUFFERED="1")
    if not erase:
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
                text=f"{clock()} $ {sys.executable} main.py (amonet v1.1.0"
                " bootrom step)"
            )
        brom = subprocess.Popen(
            [sys.executable, "main.py"],
            cwd=amonet / "modules",
            env=env,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            stdout=log,
        )
        brom.stdin.write(b"\n" * 5)
        brom.stdin.close()
        time.sleep(3)
        if brom.poll() is not None:
            _die(message=f"v1.1.0's bootrom step did not start; see {log_path}")
        if erase:
            ERASED.touch()
            for args in (["fastboot", "erase", "boot0"], ["fastboot", "reboot"]):
                try:
                    result = run(args=args, timeout=60)
                except subprocess.TimeoutExpired:
                    brom.kill()
                    raise
                if result.returncode != 0:
                    brom.kill()
                    _die(message=f"{' '.join(args)} failed:\n{result.stdout}")
        else:
            PROGRESS.begin(estimate="40 s", label="waiting for the Dot to restart")
        deadline = time.monotonic() + (60 if erase else 600)
        while brom.poll() is None and time.monotonic() < deadline:
            if "Found port" in log_path.read_text(errors="replace"):
                break
            time.sleep(1)
        else:
            if brom.poll() is None:
                brom.kill()
                _die(
                    message="the Dot's bootrom did not show up as a serial port.\n"
                    + no_port_help()
                )
        if not erase:
            PROGRESS.begin(
                estimate="5 min", label="finishing the downgrade to amonet v1.1.0"
            )
        try:
            brom.wait(timeout=1800)
        except subprocess.TimeoutExpired:
            brom.kill()
            _die(message=f"v1.1.0's bootrom step did not finish; see {log_path}")
    if brom.returncode != 0:
        _die(message=f"v1.1.0's bootrom step failed; see {log_path}")
    if "Reboot to unlocked fastboot" not in log_path.read_text(errors="replace"):
        _die(message=f"v1.1.0's bootrom step did not finish; see {log_path}")
    ERASED.unlink(missing_ok=True)
    for _ in range(30):
        try:
            if in_fastboot() and getvar("lk_build_desc") == LK_V1:
                break
        except subprocess.TimeoutExpired:
            pass
        time.sleep(2)
    else:
        _die(message="the Dot did not come back in v1.1.0's fastboot")


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
            text=f"{path} holds {size // 1000000} MB of downloads for the next run."
            " It is safe to delete."
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
    payload = fetch(name=PAYLOAD_NAME, url=PAYLOAD_URL, want=PAYLOAD_SHA)
    if from_twrp:
        say(text="Restarting the Dot into fastboot mode. Waiting for it to start.")
        run(args=["adb", "reboot", "bootloader"], check=True)
        for _ in range(30):
            try:
                if in_fastboot():
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(2)
        else:
            _die(message="the Dot did not reach fastboot within 60 seconds")
    if getvar("unlock_status").lower() != "true":
        _die(message="not in amonet's fastboot")
    lk = getvar("lk_build_desc")
    if not lk:
        _die(
            message="fastboot did not report the bootloader version;"
            " boot0 was not erased. Run dot_root.py again."
        )
    if lk == LK_V1:
        _die(
            message="the Dot already runs amonet v1.1.0's bootloader."
            " Run dot_root.py again."
        )
    PROGRESS.begin(estimate="5 min", label="downgrading to amonet v1.1.0; LED ring off")
    bootrom(amonet=amonet, erase=True, payload=payload, wheel=wheel)
    v1_recovery()


def fastbrick() -> None:  # ruff: ignore[complex-structure]
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
    if lk == LK_V2:
        image = "bin/fastbrick-20221007.img"
    PROGRESS.begin(estimate="10 s", label="unlocking with amonet v2.0.0")
    for attempt in range(10):
        if attempt:
            time.sleep(2)
        args = ["fastboot", "-S", "256M", "flash", "brick", image]
        try:
            out = run(args=args, cwd=amonet, timeout=8).stdout
            started = False
        except subprocess.TimeoutExpired as e:
            out = e.output or ""
            if isinstance(out, bytes):
                out = out.decode(errors="replace")
            started = True
        if "eMMC-RO" in out:
            _die(message="the Dot's eMMC is read-only; it was not modified")
        if "Device mismatch" in out:
            _die(message="the payload rejected this device; it was not modified")
        if started:
            PROGRESS.begin(
                estimate="40 s", label="exploit running; waiting for recovery"
            )
            return
    _die(message="the unlock did not start after 10 attempts")


def fetch(*, name: str, url: str, want: str) -> pathlib.Path:
    CACHE.mkdir(exist_ok=True, parents=True)
    path = CACHE / name
    if path.is_file() and sha256(path) == want:
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
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        PROGRESS.begin(estimate="5 s", label="formatting userdata")
        if not rscript(body=DATA_SH, name="data.sh", work=work):
            _die(message="userdata did not format and mount")
        PROGRESS.begin(estimate="80 s", label="pushing Fire OS 5.5.5.4 (397 MB)")
        push_checked(local=fireos, remote="/data/fireos.zip")
        PROGRESS.begin(estimate="2-3 min", label="installing Fire OS 5.5.5.4")
        twrp_install(name="Fire OS 5.5.5.4", path="/data/fireos.zip")
        rshell(command="rm -f /data/fireos.zip")

        PROGRESS.begin(estimate="15 s", label="patching the boot image")
        magiskboot = work / "magiskboot"
        with zipfile.ZipFile(magisk) as z:
            magiskboot.write_bytes(z.read("arm/magiskboot"))
        push_checked(local=magiskboot, remote=DOT_TMP / "magiskboot")
        if not rscript(body=BOOT_SH, name="boot.sh", work=work):
            _die(message="patching the boot image failed")
        boot = work / "boot.img"
        run(
            args=["adb", "pull", DOT_TMP / "bp" / "new.img", boot],
            check=True,
            timeout=120,
        )
        patch_cmdline(boot)
        data = boot.read_bytes()
        boot.write_bytes(data.ljust(-(-len(data) // 4096) * 4096, b"\0"))
        final = DOT_TMP / "bp" / "final.img"
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

        PROGRESS.begin(estimate="5 s", label="patching /system")
        if not rscript(body=SYSTEM_SH, name="system.sh", work=work):
            _die(message="patching /system failed")

        PROGRESS.begin(estimate="20 s", label="installing Magisk 17.3")
        magisk_zip = DOT_TMP / "magisk.zip"
        push_checked(local=magisk, remote=magisk_zip)
        twrp_install(name="Magisk 17.3", path=magisk_zip)
        db = work / "magisk.db"
        magisk_db(db)
        rshell(command="mkdir -p /data/adb; chmod 700 /data/adb")
        push_checked(local=db, remote="/data/adb/magisk.db")
        rshell(command="chmod 600 /data/adb/magisk.db; sync")
    PROGRESS.begin(estimate="4 min", label="first boot of Fire OS 5")
    run(args=["adb", "reboot"])


def magisk_db(path: pathlib.Path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE policies (uid INT, package_name TEXT, policy INT, "
        "until INT, logging INT, notification INT)"
    )
    db.execute("INSERT INTO policies VALUES (2000, 'com.android.shell', 2, 0, 1, 0)")
    db.commit()
    db.close()


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
    done = set()
    guided = False
    seen = None
    shown = None
    deadline = None
    while True:
        current = state()
        if current not in {"none", "starting"}:
            ERASED.unlink(missing_ok=True)
        if current == "none" and ERASED.exists() and "bootrom" not in done:
            prefetch()
            done.update(("bootrom", "v1-fastboot"))
            say(
                code=33,
                text="The last run stopped during the downgrade to amonet v1.1.0."
                " The Dot cannot start until the downgrade is done,"
                " so this run finishes it.",
            )
            bootrom(
                amonet=unpack(
                    dirname="v1",
                    name=AMONET_V1,
                    url=MIRROR + "/" + AMONET_V1,
                    want=AMONET_V1_SHA,
                ),
                erase=False,
                payload=fetch(name=PAYLOAD_NAME, url=PAYLOAD_URL, want=PAYLOAD_SHA),
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
                show(text=f"{clock()} state: {current}")
            if current not in done and current not in {"none", "starting", "booted"}:
                PROGRESS.end()
            if current == "booted" and not PROGRESS.open:
                show(text="The Dot is starting Fire OS. Waiting for it to finish.")
            elif current == "stock-booted":
                guided = True
                say(
                    text="This Dot appears to be unmodified. To unlock and root it,"
                    " start it in fastboot mode. " + FASTBOOT_MODE
                )
            elif current == "none" and not done and guided:
                show(text="Waiting for the Dot in fastboot mode, with a green ring.")
            elif current == "none" and not done:
                guided = True
                say(
                    text="Waiting for a Dot on USB. Connect it with a USB cable."
                    " A Dot that runs Amazon's own software needs fastboot mode. "
                    + FASTBOOT_MODE
                    + " Ctrl-C stops the script."
                )
        if current == "rooted":
            hide_updater()
            version = rshell(command="getprop ro.build.version.name")
            selinux = rshell(command="getenforce")
            warn(f"The Dot is rooted: {version}, SELinux {selinux}, {UPDATER} hidden.")
            warn("Install overdub with deploy/install.py <name>.")
            cache_note()
            return
        if current in done or current in {"none", "stock-booted", "booted", "starting"}:
            if current not in {"none", "stock-booted"} and time.monotonic() > deadline:
                if current == "booted":
                    _die(
                        message="Fire OS has not finished booting with root."
                        " Reboot to recovery and run dot_root.py again."
                    )
                _die(
                    message=f"the Dot has been {current} for {WAIT // 60} minutes."
                    " Run dot_root.py again."
                )
            time.sleep(2)
            continue
        if not done:
            prefetch()
            warn("Keep the Dot plugged in until dot_root.py finishes.")
        done.add(current)
        if current == "stock-fastboot":
            fastbrick()
        elif current == "v2-twrp":
            downgrade(from_twrp=True)
            done.update(("v2-fastboot", "v1-fastboot"))
        elif current == "v2-fastboot":
            downgrade(from_twrp=False)
            done.update(("v2-twrp", "v1-fastboot"))
        elif current == "v1-fastboot":
            v1_recovery()
        elif current == "v1-twrp":
            install_fireos()
        seen = None


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


def paint(*, code: int, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if color(sys.stdout) else text


def patch_cmdline(path: pathlib.Path) -> None:
    data = bytearray(path.read_bytes())
    if data[:8] != b"ANDROID!":
        _die(message="the patched boot image is not a boot image")
    cmdline = bytes(data[64:576]).split(b"\0")[0]
    if b"androidboot.selinux=permissive" not in cmdline:
        cmdline = (cmdline + b" androidboot.selinux=permissive").strip()
    if len(cmdline) >= CMDLINE_SIZE:
        _die(message="the boot cmdline is too long")
    data[64:576] = cmdline.ljust(512, b"\0")
    path.write_bytes(data)


def prefetch() -> None:
    unpack(
        dirname="v2", name=AMONET_V2, url=MIRROR + "/" + AMONET_V2, want=AMONET_V2_SHA
    )
    unpack(
        dirname="v1", name=AMONET_V1, url=MIRROR + "/" + AMONET_V1, want=AMONET_V1_SHA
    )
    fetch(name=PYSERIAL, url=PYSERIAL_URL, want=PYSERIAL_SHA)
    fetch(name=PAYLOAD_NAME, url=PAYLOAD_URL, want=PAYLOAD_SHA)
    fetch(name=FIREOS, url=FIREOS_URL, want=FIREOS_SHA)
    fetch(name=MAGISK, url=MAGISK_URL, want=MAGISK_SHA)


def probe() -> str:  # ruff: ignore[complex-structure, too-many-return-statements, too-many-branches]
    if in_fastboot():
        unlock = getvar("unlock_status").lower()
        if unlock == "false":
            return "stock-fastboot"
        if unlock != "true":
            return "starting"
        lk = getvar("lk_build_desc")
        if not lk:
            return "starting"
        if lk == LK_V1:
            return "v1-fastboot"
        return "v2-fastboot"
    if not usb_serial():
        return "none"
    adb_state = run(args=["adb", "get-state"], timeout=30).stdout
    if "unauthorized" in adb_state:
        return "stock-booted"
    adb_state = adb_state.strip()
    if adb_state == "recovery":
        version = rshell(command="getprop ro.twrp.version", timeout=30)
        if not version[:1].isdigit():
            return "starting"
        if version.startswith("3.2."):
            if "mtp" not in rshell(command="getprop sys.usb.config", timeout=30):
                return "starting"
            return "v1-twrp"
        return "v2-twrp"
    if adb_state == "device":
        booted = rshell(command="getprop sys.boot_completed", timeout=30) == "1"
        if booted and "uid=0" in rshell(command="su -c id", timeout=30):
            return "rooted"
        if rshell(command="getprop ro.build.version.name", timeout=30).startswith(
            "Fire OS 6"
        ):
            return "stock-booted"
        return "booted"
    return "none"


def push_checked(*, local: pathlib.Path, remote: str | pathlib.PurePosixPath) -> None:
    for attempt in range(PUSH_TRIES):
        if attempt:
            PROGRESS.note(
                f"{remote} did not arrive; waiting for the Dot to reconnect"
                " to try again."
            )
            try:
                run(args=["adb", "wait-for-recovery"], timeout=120)
            except subprocess.TimeoutExpired:
                _die(message="the Dot did not reconnect over USB in recovery")
            time.sleep(5)
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
    out = run(args=["adb", "shell", command], timeout=timeout).stdout.replace("\r", "")
    return "\n".join(
        line for line in out.split("\n") if not line.startswith("__bionic_open_tzdata")
    ).strip()


def run(
    *,
    args: list[str | pathlib.PurePath],
    check: bool = False,
    cwd: pathlib.Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    if ARGS.verbose and not ARGS.probing:
        show(text=f"{clock()} $ {' '.join(map(str, args))}")
    result = subprocess.run(
        args,
        check=False,
        cwd=cwd,
        errors="replace",
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
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
    show(end="", flush=True, text=f"{label:<{room}} {meter(0):>{78 - room}}")
    for block in iter(lambda: response.read(1 << 20), b""):
        out.write(block)
        done += len(block)
        show(end="", flush=True, text=f"\r{label:<{room}} {meter(done):>{78 - room}}")
    print()
    return done, total


def say(*, code: int = 0, text: str) -> None:
    text = textwrap.fill(text, 79)
    show(
        kind="warn" if code else "info",
        text=paint(code=code, text=text) if code else text,
    )


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def show(*, kind: str = "info", text: str, **options: str | bool) -> None:
    if ARGS.shown not in {None, kind}:
        print()
    ARGS.shown = kind
    print(text, **options)


def since(start: float) -> str:
    seconds = int(time.monotonic() - start)
    if seconds < MINUTE:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60}s"


def state() -> str:
    ARGS.probing = True
    try:
        current = probe()
    except subprocess.TimeoutExpired:
        current = "starting"
    finally:
        ARGS.probing = False
    return current


def twrp_install(*, name: str, path: str | pathlib.PurePosixPath) -> None:
    recovery_log = DOT_TMP / "recovery.log"
    log = rshell(
        command=f"n=$(wc -l < {recovery_log}); twrp install {path} >/dev/null;"
        f" tail -n +$((n + 1)) {recovery_log}",
        timeout=900,
    )
    if "Updater process ended with RC=0" not in log:
        tail = "\n".join(log.split("\n")[-15:])
        _die(message=f"{name} did not install. TWRP's log ends:\n{tail}")


def unpack(*, dirname: str, name: str, url: str, want: str) -> pathlib.Path:
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
    PROGRESS.begin(estimate="30 s", label="waiting for v1.1.0 recovery to start")
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


def warn(text: str) -> None:
    show(kind="warn", text=paint(code=33, text=textwrap.fill(text, 79)))


PROGRESS = Progress()


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
