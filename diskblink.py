#!/usr/bin/env python3
"""Blink an LED on disk activity, the way an HDD activity light works.

The kernel ships a `disk-activity` LED trigger, but it is only ever fired by
the libata / SCSI / MMC drivers.  The NVMe driver never calls it, so on an
NVMe-only machine that trigger is accepted and then stays silent forever.
This polls /proc/diskstats instead and drives the LED directly.

  diskblink.py --led chromeos:white:power --mode both
  diskblink.py --config ~/.config/ledpanel/blink.json
"""

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

LEDS = Path("/sys/class/leds")
DISKSTATS = Path("/proc/diskstats")
AUTO = "chromeos-auto"

# /proc/diskstats column offsets (after major, minor, name)
SECTORS_READ = 5
SECTORS_WRITTEN = 9


def physical_disks():
    """Real block devices, skipping loop/zram/ram and partitions."""
    out = []
    for entry in sorted(Path("/sys/block").iterdir()):
        if entry.name.startswith(("loop", "zram", "ram", "dm-", "md")):
            continue
        if (entry / "device").exists():
            out.append(entry.name)
    return out


def read_counter(devices, mode):
    """Total sectors moved across the watched devices."""
    total = 0
    try:
        text = DISKSTATS.read_text()
    except OSError:
        return 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) <= SECTORS_WRITTEN or parts[2] not in devices:
            continue
        if mode in ("read", "both"):
            total += int(parts[SECTORS_READ])
        if mode in ("write", "both"):
            total += int(parts[SECTORS_WRITTEN])
    return total


class Target:
    """One LED being driven."""

    def __init__(self, dev, intensity=None):
        self.dev = dev
        self.path = LEDS / dev
        self.intensity = intensity
        self.max = int((self.path / "max_brightness").read_text().strip())
        self.lit = None

    def grab(self):
        if self.intensity:
            (self.path / "multi_intensity").write_text(
                " ".join(str(int(i)) for i in self.intensity))
        (self.path / "trigger").write_text("none")
        self.lit = None
        self.set(False)

    def set(self, on):
        if on == self.lit:
            return                      # only write on a real transition
        (self.path / "brightness").write_text(str(self.max if on else 0))
        self.lit = on

    def release(self):
        try:
            (self.path / "trigger").write_text(AUTO)
        except OSError:
            pass


class Blinker:
    """Turns a rising activity counter into on/off decisions.

    `hold` keeps the LED lit briefly after activity stops, so a single short
    burst is actually visible instead of being a sub-frame flicker.
    """

    def __init__(self, hold=0.08):
        self.hold = hold
        self.prev = None
        self.off_at = 0.0
        self.on = False

    def step(self, counter, now):
        active = self.prev is not None and counter != self.prev
        self.prev = counter
        if active:
            self.off_at = now + self.hold
            self.on = True
        elif self.on and now >= self.off_at:
            self.on = False
        return self.on


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, help="JSON config; overrides the flags below")
    ap.add_argument("--led", action="append", default=[], help="LED name under /sys/class/leds")
    ap.add_argument("--mode", choices=("read", "write", "both"), default="both")
    ap.add_argument("--devices", help="comma-separated block devices (default: autodetect)")
    ap.add_argument("--interval", type=float, default=0.05, help="poll seconds (default 0.05)")
    ap.add_argument("--hold", type=float, default=0.08, help="minimum lit seconds (default 0.08)")
    args = ap.parse_args()

    interval, hold, mode = args.interval, args.hold, args.mode
    devices = args.devices.split(",") if args.devices else physical_disks()
    targets = [Target(dev) for dev in args.led]

    if args.config:
        cfg = json.loads(args.config.read_text())
        interval = cfg.get("interval", interval)
        hold = cfg.get("hold", hold)
        if cfg.get("devices"):
            devices = cfg["devices"]
        targets = [Target(t["dev"], t.get("intensity")) for t in cfg.get("leds", [])]
        modes = {t.get("mode", "both") for t in cfg.get("leds", [])}
        mode = modes.pop() if len(modes) == 1 else "both"

    if not targets:
        print("diskblink: no LEDs given", file=sys.stderr)
        return 2
    if not devices:
        print("diskblink: no block devices to watch", file=sys.stderr)
        return 2

    print(f"diskblink: watching {','.join(devices)} ({mode}) -> "
          f"{', '.join(t.dev for t in targets)}", flush=True)

    for t in targets:
        t.grab()

    running = True

    def stop(_sig, _frm):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    blinker = Blinker(hold)
    try:
        while running:
            lit = blinker.step(read_counter(devices, mode), time.monotonic())
            for t in targets:
                t.set(lit)
            time.sleep(interval)
    finally:
        for t in targets:
            t.set(False)
            t.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
