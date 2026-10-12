#!/usr/bin/env python3
"""Framework 13 LED panel.

Adjusts the ChromeOS-EC LEDs exposed under /sys/class/leds:

  chromeos:white:power          power-button LED, one channel (white)
  chromeos:multicolor:charging  side LED, six colour channels

Each LED is normally driven by the EC itself via the "chromeos-auto"
trigger.  Taking manual control means writing "none" to its trigger,
which gives up the firmware's charge-state signalling on that LED until
you hand it back.

Writing these attributes needs group access to them; run install.sh once.

  ledpanel.py            open the panel
  ledpanel.py --apply    apply the saved settings and exit (no GUI)
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

LEDS = Path("/sys/class/leds")
AUTO = "chromeos-auto"
STATE = (
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    / "ledpanel"
    / "state.json"
)
ATTRS = ("brightness", "multi_intensity", "trigger")

BLINK_CONFIG = STATE.parent / "blink.json"
BLINK_UNIT = "ledblink.service"
APPLY_UNIT = "ledpanel-apply.service"
HERE = Path(__file__).resolve().parent

# Disk activity is driven by diskblink.py, not by the kernel: the kernel's
# disk-activity trigger is only fired by libata/SCSI/MMC, never by NVMe.
SOFTWARE_TRIGGERS = [
    ("sw:both", "disk activity (read + write)"),
    ("sw:read", "disk reads"),
    ("sw:write", "disk writes"),
]

# Kernel triggers worth offering, in the order they should appear.
TRIGGER_LABELS = [
    ("phy0rx", "wifi receive"),
    ("phy0tx", "wifi transmit"),
    ("phy0assoc", "wifi associated"),
    ("ACAD-online", "AC adapter connected"),
    ("BAT1-charging", "battery charging"),
    ("BAT1-full", "battery full"),
    ("BAT1-charging-or-full", "battery charging or full"),
    ("bluetooth-power", "bluetooth on"),
    ("rfkill-any", "any radio blocked"),
    ("usb-host", "USB host activity"),
    ("panic", "kernel panic"),
]

# Swatches for the channel names the EC reports.
SWATCH = {
    "red": "#e2483d",
    "green": "#3fa34d",
    "blue": "#3d7fe2",
    "yellow": "#e0c43a",
    "white": "#f2f2f2",
    "amber": "#e09a3a",
}


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def install_unit(name):
    """Make sure a unit file is in place (and current) before we use it."""
    target = Path.home() / ".config" / "systemd" / "user" / name
    source = HERE / name
    if source.exists() and (not target.exists()
                            or target.read_text() != source.read_text()):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text())
        systemctl("daemon-reload")


def running_software_trigger(dev):
    """If the blink daemon is driving this LED, which mode is it in?"""
    if not BLINK_CONFIG.exists() or systemctl("is-active", "--quiet", BLINK_UNIT).returncode:
        return None
    try:
        cfg = json.loads(BLINK_CONFIG.read_text())
    except (OSError, ValueError):
        return None
    for entry in cfg.get("leds", []):
        if entry.get("dev") == dev:
            return "sw:" + entry.get("mode", "both")
    return None


class Led:
    """One sysfs LED."""

    def __init__(self, dev, label, note=""):
        self.dev = dev
        self.label = label
        self.note = note
        self.path = LEDS / dev

    def exists(self):
        return self.path.is_dir()

    def _read(self, attr):
        try:
            return (self.path / attr).read_text().strip()
        except OSError:
            # brightness reads back ENODATA while the EC trigger owns the LED.
            return None

    def _write(self, attr, value):
        (self.path / attr).write_text(str(value))

    def writable(self):
        return all(os.access(self.path / a, os.W_OK) for a in ATTRS)

    def channels(self):
        raw = self._read("multi_index")
        return raw.split() if raw else []

    def max_brightness(self):
        return int(self._read("max_brightness") or 100)

    def max_intensity(self):
        raw = self._read("multi_max_intensity")
        return [int(x) for x in raw.split()] if raw else []

    def intensity(self):
        raw = self._read("multi_intensity")
        return [int(x) for x in raw.split()] if raw else []

    def brightness(self):
        raw = self._read("brightness")
        return int(raw) if raw is not None else None

    def trigger(self):
        for tok in (self._read("trigger") or "").split():
            if tok.startswith("[") and tok.endswith("]"):
                return tok[1:-1]
        return None

    def available_triggers(self):
        return [t.strip("[]") for t in (self._read("trigger") or "").split()]

    def to_trigger(self, name, intensities=None):
        """Hand the LED to a kernel trigger (disk activity, etc).

        multi_intensity survives a trigger change but brightness does not, so
        the colour has to go in first; the trigger then drives brightness.
        """
        if intensities:
            self._write("multi_intensity", " ".join(str(int(i)) for i in intensities))
        self._write("trigger", name)

    def to_manual(self):
        if self.trigger() != "none":
            self._write("trigger", "none")

    def to_auto(self):
        self._write("trigger", AUTO)

    def apply(self, brightness, intensities=None):
        """Take manual control and set colour + brightness."""
        self.to_manual()
        if intensities:
            self._write("multi_intensity", " ".join(str(int(i)) for i in intensities))
        self._write("brightness", int(brightness))


POWER = Led("chromeos:white:power", "Power button",
            "white only, and on/off only — the EC ignores intermediate brightness")
CHARGE = Led("chromeos:multicolor:charging", "Side / charging",
             "pick a colour; levels within a colour are on/off only")


class LedFrame(ttk.LabelFrame):
    """One LED: mode, a row of state presets, and raw levels behind a toggle."""

    def __init__(self, master, led, on_change, on_blink=lambda: None):
        super().__init__(master, text=led.label, padding=10)
        self.led = led
        self.on_change = on_change
        self.on_blink = on_blink
        self._pending = None

        self.names = led.channels()
        self.multi = len(self.names) > 1
        self.maxes = led.max_intensity() or [100] * len(self.names)
        self.maxb = led.max_brightness()

        if led.note:
            ttk.Label(self, text=led.note, foreground="#888").pack(anchor="w", pady=(0, 8))

        # --- mode
        active = led.trigger()
        self.triggers = SOFTWARE_TRIGGERS + [
            (name, label) for name, label in TRIGGER_LABELS
            if name in led.available_triggers()]
        software = running_software_trigger(led.dev)
        if software:
            start, active = "activity", software
        elif active in (None, "none"):
            start = "manual"
        elif active == AUTO:
            start = "auto"
        else:
            start = "activity"
        self.mode = tk.StringVar(value=start)

        modes = ttk.Frame(self)
        modes.pack(anchor="w")
        ttk.Label(modes, text="Mode").pack(side="left", padx=(0, 8))
        for text, value in (("Firmware auto", "auto"), ("Manual", "manual"), ("Activity", "activity")):
            ttk.Radiobutton(modes, text=text, value=value, variable=self.mode,
                            command=self._mode_changed).pack(side="left", padx=(0, 10))

        # --- which kernel trigger drives Activity mode
        self.trigger_label = tk.StringVar()
        trig = ttk.Frame(self)
        trig.pack(anchor="w", pady=(6, 0))
        ttk.Label(trig, text="Blink on").pack(side="left", padx=(0, 8))
        self.trigger_box = ttk.Combobox(
            trig, textvariable=self.trigger_label, state="readonly", width=28,
            values=[label for _, label in self.triggers])
        self.trigger_box.pack(side="left")
        self.trigger_box.bind("<<ComboboxSelected>>", lambda _e: self._trigger_changed())
        chosen = next((l for n, l in self.triggers if n == active), None)
        self.trigger_label.set(chosen or (self.triggers[0][1] if self.triggers else ""))

        # --- raw state, the single source of truth for what we write
        self.bvar = tk.DoubleVar(value=0)
        self.ivars = [tk.DoubleVar(value=v) for v in (led.intensity() or [0] * len(self.names))]

        # --- preset buttons: Off, then On (single colour) or each colour
        self.sel = tk.IntVar(value=-1)
        presets = ttk.Frame(self)
        presets.pack(anchor="w", pady=(10, 0))
        ttk.Label(presets, text="State" if not self.multi else "Colour").pack(side="left", padx=(0, 8))
        self.buttons = {}
        choices = [(-1, "off")] + (
            [(i, n) for i, n in enumerate(self.names)] if self.multi else [(0, "on")]
        )
        for index, name in choices:
            btn = tk.Button(
                presets, text=name, width=6, bd=2, relief="raised", highlightthickness=0,
                bg=SWATCH.get(name, "#4a4a4a" if name == "off" else "#dddddd"),
                fg="#111" if name in ("white", "yellow", "amber", "on") else "#fff",
                command=lambda i=index: self._select(i),
            )
            btn.pack(side="left", padx=2)
            self.buttons[index] = btn

        # --- raw levels, collapsed by default
        self.show_raw = tk.BooleanVar(value=False)
        ttk.Checkbutton(self, text="Raw levels", variable=self.show_raw,
                        command=self._toggle_raw).pack(anchor="w", pady=(10, 0))
        self.raw = ttk.Frame(self)
        ttk.Label(self.raw, foreground="#888", wraplength=440, justify="left",
                  text="The EC appears to ignore anything between 0 and full; these are "
                       "here to experiment with, not because they are known to work.").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(4, 6))
        self.scales = []
        self._slider(1, "brightness", self.bvar, self.maxb)
        for i, name in enumerate(self.names):
            self._slider(2 + i, name, self.ivars[i], self.maxes[i])
        self.raw.columnconfigure(1, weight=1)

        self.live = ttk.Label(self, text="", foreground="#888")
        self.live.pack(anchor="w", pady=(8, 0))
        self._sync_enabled()

    def _slider(self, row, label, var, maximum):
        ttk.Label(self.raw, text=label, width=11).grid(row=row, column=0, sticky="w")
        scale = ttk.Scale(self.raw, from_=0, to=maximum, variable=var, orient="horizontal",
                          command=lambda _=None: self._slid())
        scale.grid(row=row, column=1, sticky="ew", padx=6)
        value = ttk.Label(self.raw, width=4, anchor="e")
        value.grid(row=row, column=2, sticky="e")
        var.trace_add("write", lambda *_: value.configure(text=str(int(var.get()))))
        value.configure(text=str(int(var.get())))
        self.scales.append(scale)

    def _toggle_raw(self):
        if self.show_raw.get():
            self.raw.pack(fill="x")
            self.live.pack_forget()
            self.live.pack(anchor="w", pady=(8, 0))
        else:
            self.raw.pack_forget()

    # --- behaviour
    def _select(self, index):
        self.sel.set(index)
        if index < 0:
            self.bvar.set(0)
        else:
            self.bvar.set(self.maxb)
            for i, var in enumerate(self.ivars):
                var.set(self.maxes[i] if i == index else 0)
        if self.mode.get() == "activity":
            # recolour the blink rather than dropping out of Activity mode
            self._sync_enabled()
            self._trigger_changed()
            return
        if self.mode.get() != "manual":
            self.mode.set("manual")
        self._sync_enabled()
        self._push_now()

    def _highlight(self):
        mode = self.mode.get()
        # In Activity mode the colour buttons still matter: they choose what
        # colour the trigger blinks in. "off" does not, so it stays disabled.
        for index, btn in self.buttons.items():
            on = index == self.sel.get() and mode in ("manual", "activity")
            usable = mode == "manual" or (mode == "activity" and index >= 0)
            btn.configure(relief="sunken" if on else "raised",
                          state="normal" if usable else "disabled")

    def _chosen_trigger(self):
        label = self.trigger_label.get()
        return next((n for n, l in self.triggers if l == label), None)

    def _blink_colour(self):
        """Intensities to blink in; a trigger on an all-zero colour is invisible."""
        values = [v.get() for v in self.ivars]
        if any(values):
            return values
        return [self.maxes[0]] + [0] * (len(self.ivars) - 1)

    def _trigger_changed(self):
        if self.mode.get() != "activity":
            self.mode.set("activity")
        self._sync_enabled()
        name = self._chosen_trigger()
        if not name:
            return
        try:
            self.on_blink()                      # start/stop the daemon first
            if not name.startswith("sw:"):
                self.led.to_trigger(name, self._blink_colour())
        except OSError as exc:
            self.on_change(error=exc)
            return
        self.on_change()

    def _mode_changed(self):
        self._sync_enabled()
        name = self._chosen_trigger()
        try:
            # Resync the daemon before touching sysfs: stopping it is
            # synchronous and restores the trigger, so our write must come
            # after, and starting it means we must not write at all.
            self.on_blink()
            if self.mode.get() == "auto":
                self.led.to_auto()
            elif self.mode.get() == "activity":
                if name and not name.startswith("sw:"):
                    self.led.to_trigger(name, self._blink_colour())
            else:
                self._push()
        except OSError as exc:
            self.on_change(error=exc)
            return
        self.on_change()

    def _sync_enabled(self):
        mode = self.mode.get()
        state = "normal" if mode == "manual" else "disabled"
        for scale in self.scales:
            scale.configure(state=state)
        self.trigger_box.configure(
            state="readonly" if mode == "activity" and self.triggers else "disabled")
        self._highlight()

    def _slid(self):
        if self.mode.get() != "manual":
            return
        self.sel.set(-2)  # no preset matches a hand-dragged level
        self._highlight()
        if self._pending is not None:
            self.after_cancel(self._pending)
        self._pending = self.after(60, self._push_now)

    def _push_now(self):
        self._pending = None
        try:
            self._push()
        except OSError as exc:
            self.on_change(error=exc)
            return
        self.on_change()

    def _push(self):
        self.led.apply(self.bvar.get(), [v.get() for v in self.ivars])

    def refresh(self):
        brightness = self.led.brightness()
        bits = [f"trigger={self.led.trigger() or '?'}",
                f"brightness={'firmware' if brightness is None else brightness}"]
        if self.multi:
            bits.append("intensity=" + " ".join(str(i) for i in self.led.intensity()))
        self.live.configure(text="live:  " + "   ".join(bits))

    # --- persistence
    def snapshot(self):
        return {"mode": self.mode.get(),
                "brightness": int(self.bvar.get()),
                "intensity": [int(v.get()) for v in self.ivars],
                "trigger": self._chosen_trigger()}

    def load(self, data):
        self.mode.set(data.get("mode", "auto"))
        label = next((l for n, l in self.triggers if n == data.get("trigger")), None)
        if label:
            self.trigger_label.set(label)
        self.bvar.set(data.get("brightness", 0))
        for var, value in zip(self.ivars, data.get("intensity", [])):
            var.set(value)
        self._mode_changed()


class Panel(ttk.Frame):
    def __init__(self, master, leds):
        super().__init__(master, padding=12)
        self.banner = ttk.Label(self, wraplength=460, foreground="#c0392b", justify="left")
        self.banner.pack(anchor="w", fill="x")

        self.frames = []
        for led in leds:
            frame = LedFrame(self, led, self.after_change, self.sync_blink)
            frame.pack(fill="x", pady=(8, 0))
            self.frames.append(frame)

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", pady=(12, 0))
        self.autostart = tk.BooleanVar(
            value=systemctl("is-enabled", "--quiet", APPLY_UNIT).returncode == 0)
        ttk.Checkbutton(buttons, text="Restore these settings at login",
                        variable=self.autostart,
                        command=self.apply_autostart).pack(side="left")
        ttk.Button(buttons, text="All back to firmware",
                   command=self.all_auto).pack(side="right")

        self.status = ttk.Label(self, foreground="#888")
        self.status.pack(anchor="w", pady=(8, 0))

        self.check_perms()
        self._tick_id = None
        self._save_id = None
        self.tick()
        self.bind("<Destroy>", lambda e: self.stop() if e.widget is self else None)

    def check_perms(self):
        bad = [f.led.dev for f in self.frames if not f.led.writable()]
        if bad:
            self.banner.configure(
                text="No write access to " + ", ".join(bad)
                + "\nRun  ~/ledpanel/install.sh  once (it needs sudo), then reopen this panel."
            )
        else:
            self.banner.configure(text="")

    def apply_autostart(self):
        """Enable or disable the two user units that restore this at login."""
        on = self.autostart.get()
        for unit in (APPLY_UNIT, BLINK_UNIT):
            install_unit(unit)
        if on:
            systemctl("enable", APPLY_UNIT)
        else:
            systemctl("disable", APPLY_UNIT)
            systemctl("disable", BLINK_UNIT)
        self.sync_blink()
        self.save(announce=False)
        self.status.configure(
            text="settings will be restored at login" if on
                 else "settings will NOT be restored at login",
            foreground="#888")

    def _schedule_save(self):
        """Debounced autosave, so dragging a slider is not a write storm."""
        if getattr(self, "_save_id", None) is not None:
            self.after_cancel(self._save_id)
        self._save_id = self.after(400, lambda: self.save(announce=False))

    def sync_blink(self):
        """Start, restart or stop the disk-activity daemon to match the UI."""
        targets = []
        for frame in self.frames:
            name = frame._chosen_trigger()
            if frame.mode.get() == "activity" and name and name.startswith("sw:"):
                targets.append({"dev": frame.led.dev,
                                "mode": name.split(":", 1)[1],
                                "intensity": [int(i) for i in frame._blink_colour()]})
        if not targets:
            systemctl("disable", BLINK_UNIT)
            systemctl("stop", BLINK_UNIT)
            return
        BLINK_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        BLINK_CONFIG.write_text(json.dumps(
            {"leds": targets, "interval": 0.05, "hold": 0.08}, indent=2) + "\n")
        install_unit(BLINK_UNIT)
        # enable == starts at login; restart == takes effect right now
        systemctl("enable" if self.autostart.get() else "disable", BLINK_UNIT)
        result = systemctl("restart", BLINK_UNIT)
        if result.returncode:
            self.status.configure(
                text="could not start the blink daemon: " + result.stderr.strip(),
                foreground="#c0392b")

    def after_change(self, error=None):
        if error is not None:
            self.status.configure(text=f"write failed: {error}", foreground="#c0392b")
        else:
            self.status.configure(text="", foreground="#888")
        for frame in self.frames:
            frame.refresh()
        self._schedule_save()

    def tick(self):
        if not self.winfo_exists():
            return
        for frame in self.frames:
            frame.refresh()
        self._tick_id = self.after(1000, self.tick)

    def stop(self):
        if getattr(self, "_tick_id", None) is not None:
            self.after_cancel(self._tick_id)
            self._tick_id = None

    def all_auto(self):
        for frame in self.frames:
            frame.mode.set("auto")
            frame._mode_changed()

    def save(self, announce=True):
        self._save_id = None
        STATE.parent.mkdir(parents=True, exist_ok=True)
        data = {f.led.dev: f.snapshot() for f in self.frames}
        STATE.write_text(json.dumps(data, indent=2) + "\n")
        if announce:
            self.status.configure(text=f"saved to {STATE}", foreground="#888")

    def load(self):
        if not STATE.exists():
            self.status.configure(text="nothing saved yet", foreground="#888")
            return
        data = json.loads(STATE.read_text())
        for frame in self.frames:
            if frame.led.dev in data:
                frame.load(data[frame.led.dev])
        self.status.configure(text=f"loaded {STATE}", foreground="#888")


def write_blink_config(data):
    """Regenerate the daemon's config from the saved settings.

    state.json is the single source of truth; blink.json is derived from it so
    the two cannot drift apart.  Returns True if any LED wants the daemon.
    """
    targets = [
        {"dev": dev,
         "mode": entry["trigger"].split(":", 1)[1],
         "intensity": [int(i) for i in entry.get("intensity") or []]}
        for dev, entry in data.items()
        if entry.get("mode") == "activity"
        and (entry.get("trigger") or "").startswith("sw:")
    ]
    if not targets:
        return False
    BLINK_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    BLINK_CONFIG.write_text(json.dumps(
        {"leds": targets, "interval": 0.05, "hold": 0.08}, indent=2) + "\n")
    return True


def apply_saved():
    """--apply: push the saved settings, for the systemd user unit."""
    if not STATE.exists():
        print(f"ledpanel: nothing saved at {STATE}", file=sys.stderr)
        return 0
    data = json.loads(STATE.read_text())
    wants_daemon = write_blink_config(data)
    print(f"ledpanel: blink daemon {'configured' if wants_daemon else 'not needed'}")
    for led in (POWER, CHARGE):
        entry = data.get(led.dev)
        if not entry or not led.exists():
            continue
        try:
            mode = entry.get("mode")
            if mode == "manual":
                led.apply(entry.get("brightness", 100), entry.get("intensity") or None)
            elif mode == "activity" and entry.get("trigger"):
                if entry["trigger"].startswith("sw:"):
                    continue          # ledblink.service drives this one
                led.to_trigger(entry["trigger"], entry.get("intensity") or None)
            else:
                led.to_auto()
        except OSError as exc:
            print(f"ledpanel: {led.dev}: {exc}", file=sys.stderr)
            return 1
    return 0


def main():
    if "--apply" in sys.argv[1:]:
        return apply_saved()

    leds = [led for led in (POWER, CHARGE) if led.exists()]
    if not leds:
        print("ledpanel: no chromeos EC LEDs found under /sys/class/leds", file=sys.stderr)
        return 1

    root = tk.Tk()
    root.title("Framework LED panel")
    root.minsize(500, 0)
    root.resizable(True, False)
    Panel(root, leds).pack(fill="both", expand=True)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
