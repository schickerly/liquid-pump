"""
Auto-fill: Dymo M10 scale + Arduino pump (S/D/STOP protocol).

Starts at 33% forward (targets over 800 g ramp to 66% over 2 s, capped by slider), slows near target,
targets over 2000 g use a 1 s time-based final slowdown, stops at target, brief reverse to reduce drips.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from datetime import date, datetime
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional

try:
    import serial  # pyright: ignore[reportMissingModuleSource]
    from serial.tools import list_ports  # pyright: ignore[reportMissingModuleSource]
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing pyserial.\n\n"
        f"  Python: {sys.executable}\n"
        f'  Install: "{sys.executable}" -m pip install pyserial'
    ) from exc

# --- Serial (Arduino pump_motor_tester.ino) ---
DEFAULT_BAUD = 115200

# --- Fill control ---
FILL_SPEED_START_PCT = 33
# Targets above this use a higher bulk cap and a ramp-up from FILL_SPEED_START_PCT.
FILL_LARGE_TARGET_THRESHOLD_G = 800
FILL_LARGE_TARGET_BULK_CAP_PCT = 66
FILL_LARGE_TARGET_RAMP_UP_S = 2.0
# Above this, end-of-fill slowdown is time-based (wall clock), not the gram band curve.
FILL_VERY_LARGE_TARGET_THRESHOLD_G = 2000
FILL_VERY_LARGE_RAMP_DOWN_S = 1.0
# >2000 g time rampdown ends at this % (not FILL_SPEED_MIN_PCT — avoids crawling at 5%).
FILL_VERY_LARGE_RAMP_DOWN_MIN_PCT = FILL_SPEED_START_PCT
# Floor speed in the last part of the approach band (lower = less overshoot if scale lags).
FILL_SPEED_MIN_PCT = 5
# Slow-down band: at least this many grams before target, or this fraction of target (whichever is larger).
FILL_APPROACH_MIN_G = 55
FILL_APPROACH_FRAC = 0.22
# After fill starts, wait this long before enabling the stuck-weight safety.
FILL_STUCK_GRACE_S = 2.5
# If scale weight does not increase by at least this many grams within FILL_STUCK_ABORT_S, abort fill.
FILL_STUCK_DELTA_G = 1
FILL_STUCK_ABORT_S = 0.5
# Longer period gives the scale time to update before the next speed check.
FILL_CONTROL_PERIOD_S = 0.12
# After target hit: pause, short reverse, then stop.
DRIP_SETTLE_S = 0.08
DRIP_REVERSE_SPEED_PCT = 18
# End-of-fill reverse to pull liquid back.
DRIP_REVERSE_DURATION_S = 0.8

# --- Dymo M10 HID ---
DYMO_SCALE_VENDOR_ID = 0x0922
DYMO_SCALE_PRODUCT_IDS_TRY = (0x8003, 0x8004, 0x8005)
SCALE_UPDATE_INTERVAL = 0.1
SCALE_MAX_FAILED_READS = 50
SCALE_PING_INTERVAL = 60.0
# Extra USB read bursts to discourage scale auto-sleep (display may still time out on hardware).
SCALE_KEEPALIVE_INTERVAL_S = 45.0
SCALE_KEEPALIVE_READ_BURST = 30
# Fill requires a recent scale reading (swap container / loose USB → must reconnect).
GRAM_STALE_MAX_S = 5.0
# After auto-reconnect on fill start, wait this long for a live weight before giving up.
FILL_SCALE_AUTO_RECONNECT_WAIT_S = 3.0
# During fill: stop pump if scale powers off, sleeps, or USB drops (e.g. while priming).
FILL_SCALE_NO_GRAMS_ABORT_S = 1.5
FILL_SCALE_STALE_PACKET_ABORT_S = 2.0

# --- Purge (line clear) — speed comes from UI slider (same as fill max). ---
USER_PUMP_SPEED_SLIDER_MIN = 10
USER_PUMP_SPEED_SLIDER_MAX = 100
# Forward prime (line fill / de-air) — fixed speed, not the slider.
PRIME_SPEED_PCT = 100

FOOT_PEDAL_KEY = "b"
# Short debounce avoids double start when both global hook and Tk see the same pedal press.
FOOT_PEDAL_DEBOUNCE_S = 0.15

# GitHub fine-grained read-only PAT — set to the token’s expiry date (or “created + 90 days”).
# Edit this when you rotate the token on the field PC. Warning appears TOKEN_WARN_DAYS_BEFORE days before.
TOKEN_EXPIRES_ON = date(2026, 7, 1)
TOKEN_WARN_DAYS_BEFORE = 30


def _latest_build_label_text() -> str:
    """Filesystem mtime of running .py or frozen EXE (local time); updates after git pull replaces files."""
    if getattr(sys, "frozen", False):
        path = sys.executable
        kind = "EXE"
    else:
        path = __file__
        kind = "script"
    try:
        ts = os.path.getmtime(path)
        stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except OSError:
        stamp = "—"
    return f"Latest build ({kind}): {stamp}"


def _approach_zone_g(target_g: int) -> int:
    return max(FILL_APPROACH_MIN_G, int(target_g * FILL_APPROACH_FRAC))


def _approach_zone_for_fill(target_g: int) -> int:
    """Gram band for end-of-fill slowdown; large targets use half the band (faster ramp-down)."""
    z = _approach_zone_g(target_g)
    if target_g > FILL_LARGE_TARGET_THRESHOLD_G:
        return max(1, z // 2)
    return z


def _speed_for_remaining(
    remaining_g: int, target_g: int, max_pct: int, approach_zone_g: Optional[int] = None
) -> int:
    max_pct = max(FILL_SPEED_MIN_PCT + 1, min(100, int(max_pct)))
    zone = _approach_zone_g(target_g) if approach_zone_g is None else approach_zone_g
    if remaining_g >= zone:
        return max_pct
    if remaining_g <= 0:
        return FILL_SPEED_MIN_PCT
    frac = remaining_g / zone
    v = FILL_SPEED_MIN_PCT + (max_pct - FILL_SPEED_MIN_PCT) * frac
    return int(round(max(FILL_SPEED_MIN_PCT, min(max_pct, v))))


def _fill_effective_max_pct(target_g: int, user_max_pct: int, elapsed_s: float) -> int:
    """Bulk ceiling: ≤800 g uses slider % only; >800 g uses max(66, slider), ramping 33→bulk over FILL_LARGE_TARGET_RAMP_UP_S."""
    user_max_pct = max(FILL_SPEED_MIN_PCT + 1, min(100, int(user_max_pct)))
    if target_g <= FILL_LARGE_TARGET_THRESHOLD_G:
        return user_max_pct
    bulk_cap = max(FILL_LARGE_TARGET_BULK_CAP_PCT, user_max_pct)
    if elapsed_s >= FILL_LARGE_TARGET_RAMP_UP_S:
        return bulk_cap
    if bulk_cap <= FILL_SPEED_START_PCT:
        return bulk_cap
    t = elapsed_s / FILL_LARGE_TARGET_RAMP_UP_S
    return int(round(FILL_SPEED_START_PCT + (bulk_cap - FILL_SPEED_START_PCT) * t))


def _beep_fill_done() -> None:
    """Short beep when a fill finishes successfully (Windows speaker API + fallbacks)."""
    try:
        if sys.platform == "win32":
            import winsound

            try:
                winsound.Beep(1000, 200)
            except RuntimeError:
                winsound.MessageBeep(winsound.MB_OK)
        else:
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass


class PumpFillGui:
    def __init__(self) -> None:
        self.ser: Optional[serial.Serial] = None
        self._serial_lock = threading.Lock()
        self._reader_stop = threading.Event()

        self.scale_device: Any = None
        self._scale_stop = threading.Event()
        self._scale_failed_reads = 0
        self._scale_last_ping = 0.0
        self._grams_lock = threading.Lock()
        self._latest_grams: Optional[int] = None

        self._fill_thread: Optional[threading.Thread] = None
        self._fill_abort = threading.Event()
        self._fill_active = False
        self._fill_start_lock = threading.Lock()

        self._purge_thread: Optional[threading.Thread] = None
        self._purge_stop = threading.Event()
        self._purge_active = False

        self._prime_thread: Optional[threading.Thread] = None
        self._prime_stop = threading.Event()
        self._prime_active = False

        self._last_good_gram_mono = 0.0
        self._last_scale_keepalive_mono = 0.0

        self._pedal_last_fire = 0.0
        self._keyboard_unhook: Optional[Callable[[], None]] = None
        self._foot_pedal_tk_fallback = False

        self._last_sent_speed: Optional[int] = None
        self._user_speed_lock = threading.Lock()
        self._user_speed_cached = FILL_SPEED_START_PCT
        self._syncing_user_speed = False

        self.root = tk.Tk()
        self.root.title("Pump fill (scale + Arduino)")
        self.root.configure(bg="#0f1419")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        pad = {"padx": 10, "pady": 6}
        frm = tk.Frame(self.root, bg="#0f1419")
        frm.pack(fill=tk.BOTH, expand=True, **pad)

        row0 = tk.Frame(frm, bg="#0f1419")
        row0.pack(fill=tk.X, **pad)
        tk.Label(row0, text="COM", fg="white", bg="#0f1419").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(row0, textvariable=self.port_var, width=22, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=6)
        tk.Button(row0, text="Refresh", command=self._refresh_ports, bg="#333", fg="white").pack(
            side=tk.LEFT
        )
        self.connect_btn = tk.Button(
            row0, text="Connect", command=self._toggle_connect, bg="#2d7ff9", fg="white", width=10
        )
        self.connect_btn.pack(side=tk.LEFT, padx=8)
        self.status_var = tk.StringVar(value="Serial: disconnected")
        tk.Label(row0, textvariable=self.status_var, fg="#9ab", bg="#0f1419").pack(
            side=tk.LEFT, padx=8
        )
        self.clock_var = tk.StringVar(value="")
        tk.Label(
            row0,
            textvariable=self.clock_var,
            fg="#9ab",
            bg="#0f1419",
            font=("Segoe UI", 80),
        ).pack(side=tk.RIGHT, padx=4)

        scale_frm = tk.Frame(frm, bg="#0f1419")
        scale_frm.pack(fill=tk.X, **pad)
        scale_top = tk.Frame(scale_frm, bg="#0f1419")
        scale_top.pack(fill=tk.X)
        self.weight_var = tk.StringVar(value="Weight: -- g")
        tk.Label(
            scale_top,
            textvariable=self.weight_var,
            font=("Segoe UI", 32),
            fg="white",
            bg="#0f1419",
        ).pack(side=tk.LEFT, anchor=tk.W)
        speed_box = tk.Frame(scale_top, bg="#0f1419")
        speed_box.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(16, 0))
        tk.Label(
            speed_box,
            text=f"Pump speed % (default {FILL_SPEED_START_PCT})",
            fg="#9ab",
            bg="#0f1419",
            font=("Segoe UI", 9),
        ).pack(anchor=tk.E)
        self.user_fill_speed_var = tk.IntVar(value=FILL_SPEED_START_PCT)
        self.user_speed_entry_var = tk.StringVar(value=str(FILL_SPEED_START_PCT))
        speed_row = tk.Frame(speed_box, bg="#0f1419")
        speed_row.pack(anchor=tk.E, fill=tk.X)
        self.user_speed_scale = tk.Scale(
            speed_row,
            from_=USER_PUMP_SPEED_SLIDER_MIN,
            to=USER_PUMP_SPEED_SLIDER_MAX,
            orient=tk.HORIZONTAL,
            variable=self.user_fill_speed_var,
            resolution=1,
            tickinterval=10,
            bg="#1a222c",
            fg="white",
            highlightthickness=0,
            troughcolor="#333",
            length=280,
            showvalue=0,
        )
        self.user_speed_scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        speed_entry = tk.Entry(
            speed_row,
            textvariable=self.user_speed_entry_var,
            width=5,
            justify="center",
            font=("Segoe UI", 11),
            bg="#1a222c",
            fg="white",
            insertbackground="white",
        )
        speed_entry.pack(side=tk.RIGHT, padx=(8, 0))
        speed_entry.bind("<Return>", self._commit_user_speed_entry)
        speed_entry.bind("<FocusOut>", self._commit_user_speed_entry)
        self.user_fill_speed_var.trace_add("write", self._on_user_speed_var_changed)
        self._refresh_user_speed_cache()
        row_s = tk.Frame(scale_frm, bg="#0f1419")
        row_s.pack(fill=tk.X)
        self.scale_status_var = tk.StringVar(value="Scale: …")
        tk.Label(row_s, textvariable=self.scale_status_var, fg="#8ab", bg="#0f1419").pack(
            side=tk.LEFT
        )
        tk.Button(row_s, text="Reconnect scale", command=self._reconnect_scale, bg="#333", fg="white").pack(
            side=tk.LEFT, padx=8
        )
        tk.Button(row_s, text="Diagnose HID", command=self._diagnose_scale_usb, bg="#333", fg="white").pack(
            side=tk.LEFT
        )

        self._tick_clock()

        fill_frm = tk.Frame(frm, bg="#0f1419")
        fill_frm.pack(fill=tk.X, **pad)
        tk.Label(
            fill_frm,
            text="Target weight (g) — stops when scale reading reaches this",
            fg="#9ab",
            bg="#0f1419",
        ).pack(anchor=tk.W)
        self.target_entry = tk.Entry(fill_frm, font=("Segoe UI", 22), justify="center", width=12)
        self.target_entry.pack(anchor=tk.W, pady=4)
        self.target_entry.bind("<KeyPress>", self._target_entry_key)

        tk.Label(
            fill_frm,
            text=(
                f"Max pump speed ({USER_PUMP_SPEED_SLIDER_MIN}–{USER_PUMP_SPEED_SLIDER_MAX}%), default {FILL_SPEED_START_PCT}% "
                f"(slider or type exact % in box) — "
                f"targets ≤{FILL_LARGE_TARGET_THRESHOLD_G} g use this for fill (change anytime, including during fill); "
                f"targets above that ramp to ≥{FILL_LARGE_TARGET_BULK_CAP_PCT}% as configured. "
                f"Ramps down to {FILL_SPEED_MIN_PCT}% near target. Purge uses this in reverse."
            ),
            fg="#9ab",
            bg="#0f1419",
            font=("Segoe UI", 9),
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(6, 0))

        self.fill_status_var = tk.StringVar(value="Fill: idle")
        tk.Label(
            fill_frm,
            textvariable=self.fill_status_var,
            fg="#7c4",
            bg="#0f1419",
            font=("Segoe UI", 12),
        ).pack(anchor=tk.W)
        self.pump_speed_var = tk.StringVar(value="Pump: —")
        tk.Label(
            fill_frm,
            textvariable=self.pump_speed_var,
            fg="#abc",
            bg="#0f1419",
            font=("Segoe UI", 11),
        ).pack(anchor=tk.W)

        btn_row = tk.Frame(fill_frm, bg="#0f1419")
        btn_row.pack(fill=tk.X, pady=8)
        tk.Button(
            btn_row,
            text="Start fill",
            command=self._start_fill,
            bg="#1a6b3a",
            fg="white",
            font=("Segoe UI", 12),
            width=12,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_row,
            text="Stop / abort",
            command=self._stop_fill_and_purge,
            bg="#822",
            fg="white",
            font=("Segoe UI", 12),
            width=12,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_row,
            text="Start purge",
            command=self._start_purge,
            bg="#664422",
            fg="white",
            font=("Segoe UI", 12),
            width=12,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_row,
            text="Stop purge",
            command=self._stop_purge,
            bg="#553311",
            fg="white",
            font=("Segoe UI", 12),
            width=10,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_row,
            text="Prime start",
            command=self._start_prime,
            bg="#1a5c66",
            fg="white",
            font=("Segoe UI", 12),
            width=11,
        ).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            btn_row,
            text="Prime stop",
            command=self._stop_prime,
            bg="#0f4a52",
            fg="white",
            font=("Segoe UI", 12),
            width=10,
        ).pack(side=tk.LEFT)

        tk.Label(
            frm,
            text=(
                f"Foot pedal '{FOOT_PEDAL_KEY}': start/stop fill or purge; 'b' never types in target box. "
                "Purge speed = slider (change while purging). Prime uses target weight (g), forward @ "
                f"{PRIME_SPEED_PCT}% with same slow-down near target; stops if scale disconnects. "
                "Global hook may need Run as administrator."
            ),
            fg="#567",
            bg="#0f1419",
            font=("Segoe UI", 9),
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=4)

        tk.Label(
            frm,
            text=_latest_build_label_text(),
            fg="#8ab",
            bg="#0f1419",
            font=("Consolas", 9),
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=4, pady=(2, 0))

        self.token_reminder_label = tk.Label(
            frm,
            text="",
            fg="#678",
            bg="#0f1419",
            font=("Segoe UI", 9),
            wraplength=520,
            justify=tk.LEFT,
        )
        self.token_reminder_label.pack(anchor=tk.W, padx=4, pady=(0, 4))
        self._update_token_reminder()
        self._schedule_token_reminder_tick()

        tk.Label(frm, text="Log", fg="#567", bg="#0f1419").pack(anchor=tk.W)
        self.log = tk.Text(frm, height=10, bg="#0a0e12", fg="#8f8", font=("Consolas", 9), wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        self._refresh_ports()
        self._register_tk_pedal()
        self._setup_foot_pedal()
        self._reconnect_scale()
        threading.Thread(target=self._scale_thread, daemon=True).start()

    def _update_token_reminder(self) -> None:
        today = date.today()
        exp = TOKEN_EXPIRES_ON
        days_left = (exp - today).days
        if days_left < 0:
            text = (
                f"Git read-only token expired on {exp.isoformat()} — create a new fine-grained PAT, "
                f"run scripts\\setup_readonly_git_cred.ps1, and set TOKEN_EXPIRES_ON in pump_fill_gui.py"
            )
            fg = "#ff6666"
        elif days_left <= TOKEN_WARN_DAYS_BEFORE:
            text = (
                f"Git token expires in {days_left} day(s) ({exp.isoformat()}) — rotate PAT and update TOKEN_EXPIRES_ON"
            )
            fg = "#ffaa44"
        else:
            text = (
                f"Git token reminder: expires {exp.isoformat()} "
                f"({days_left} days). Warning {TOKEN_WARN_DAYS_BEFORE} days before."
            )
            fg = "#6a7a8c"
        self.token_reminder_label.config(text=text, fg=fg)

    def _schedule_token_reminder_tick(self) -> None:
        self._update_token_reminder()
        self.root.after(3_600_000, self._schedule_token_reminder_tick)

    # --- logging ---
    def _log(self, line: str) -> None:
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)

    # --- serial ---
    def _refresh_ports(self) -> None:
        names = [p.device for p in list_ports.comports()]
        self.port_combo["values"] = names
        if names and not self.port_var.get():
            self.port_var.set(names[0])

    def _toggle_connect(self) -> None:
        if self.ser and self.ser.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            self.status_var.set("Serial: pick COM port")
            return
        try:
            self.ser = serial.Serial(port, DEFAULT_BAUD, timeout=0.2)
            self._reader_stop.clear()
            threading.Thread(target=self._read_loop, daemon=True).start()
            self.connect_btn.config(text="Disconnect", bg="#555")
            self.status_var.set(f"Serial: {port} @ {DEFAULT_BAUD}")
            self._log(f"# serial connected {port}")
        except Exception as exc:
            self.status_var.set(f"Serial: failed {exc}")
            self._log(f"# serial error: {exc}")
            self.ser = None

    def _disconnect(self) -> None:
        self._stop_fill_and_purge()
        self._reader_stop.set()
        try:
            self._serial_write("STOP", log=False)
        except Exception:
            pass
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.connect_btn.config(text="Connect", bg="#2d7ff9")
        self.status_var.set("Serial: disconnected")
        self._log("# serial disconnected")

    def _serial_write(self, line: str, log: bool = True) -> None:
        with self._serial_lock:
            if not self.ser or not self.ser.is_open:
                return
            self.ser.write((line.strip() + "\n").encode("ascii", errors="ignore"))
        if log:
            self.root.after(0, lambda l=line: self._log(f"> {l.strip()}"))

    def _read_loop(self) -> None:
        while not self._reader_stop.is_set() and self.ser and self.ser.is_open:
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self.root.after(0, lambda l=line: self._log(f"< {l}"))
            except Exception:
                break

    # --- scale HID (same strategy as pump_motor_test_gui) ---
    @staticmethod
    def _hid_path_for_open(path: Any) -> Any:
        if path is None:
            return None
        if isinstance(path, str):
            return path
        if isinstance(path, bytes):
            return path.decode("utf-8", errors="surrogateescape")
        return str(path)

    def _open_scale_hid(self, hid: Any) -> Any:
        vid = DYMO_SCALE_VENDOR_ID

        def try_open_path(info: dict[str, Any]) -> Any:
            raw_path = info.get("path")
            if not raw_path:
                return None
            candidates = [raw_path]
            alt = self._hid_path_for_open(raw_path)
            if alt not in candidates:
                candidates.append(alt)
            for p in candidates:
                try:
                    dev = hid.device()
                    dev.open_path(p)
                    dev.set_nonblocking(True)
                    return dev
                except Exception:
                    continue
            return None

        for pid in DYMO_SCALE_PRODUCT_IDS_TRY:
            try:
                dev = hid.device()
                dev.open(vid, pid)
                dev.set_nonblocking(True)
                return dev
            except Exception:
                continue
        for pid in DYMO_SCALE_PRODUCT_IDS_TRY:
            for info in hid.enumerate(vid, pid):
                dev = try_open_path(info)
                if dev is not None:
                    return dev
        try:
            candidates = list(hid.enumerate(vid, 0))
        except TypeError:
            candidates = [i for i in hid.enumerate() if i.get("vendor_id") == vid]
        for info in candidates:
            name = (
                f"{info.get('manufacturer_string') or ''} {info.get('product_string') or ''}"
            ).lower()
            if not any(k in name for k in ("scale", "m10", "postal", "mail")):
                continue
            dev = try_open_path(info)
            if dev is not None:
                return dev
        return None

    def _reconnect_scale(self) -> None:
        try:
            import hid
        except ImportError:
            self.scale_status_var.set("Scale: pip install hidapi")
            self._log(f'# "{sys.executable}" -m pip install hidapi')
            return
        try:
            if self.scale_device:
                try:
                    self.scale_device.close()
                except Exception:
                    pass
                self.scale_device = None
            dev = self._open_scale_hid(hid)
            if dev is None:
                raise OSError("No Dymo scale found (quit Dymo app, replug USB)")
            self.scale_device = dev
            self._scale_failed_reads = 0
            self.scale_status_var.set("Scale: connected, waiting for weight")
            self._log("# scale connected")
        except Exception as exc:
            self.scale_device = None
            self.scale_status_var.set(f"Scale: {exc}")
            self._log(f"# scale: {exc}")

    def _target_entry_key(self, event: tk.Event) -> str | None:
        """Foot-pedal 'b' runs fill/purge logic but must not appear in the target field."""
        if (event.char or "").lower() == "b" or (event.keysym or "").lower() == "b":
            self._foot_pedal_action()
            return "break"
        return None

    def _diagnose_scale_usb(self) -> None:
        try:
            import hid
        except ImportError:
            self._log(f'# hidapi: "{sys.executable}" -m pip install hidapi')
            return
        self._log("# --- Dymo HID ---")
        try:
            rows = list(hid.enumerate(DYMO_SCALE_VENDOR_ID, 0))
        except TypeError:
            rows = [i for i in hid.enumerate() if i.get("vendor_id") == DYMO_SCALE_VENDOR_ID]
        for info in rows:
            self._log(
                f"#   PID={info.get('product_id', 0):#06x} "
                f"{info.get('product_string')!r}"
            )
        if not rows:
            self._log("#   (none)")
        self._log("# ---")

    def _scale_thread(self) -> None:
        self._scale_last_ping = time.time()
        while not self._scale_stop.is_set():
            if self.scale_device:
                try:
                    data = self.scale_device.read(64)
                    if data and len(data) >= 6:
                        # Dymo packets often include a report-id/status byte first:
                        # [report/status, status, unit, factor, value_lo, value_hi]
                        # Older code used data[4:6] successfully, so unit/factor live at [2]/[3].
                        unit = data[2]
                        factor_raw = data[3]
                        raw_val = int.from_bytes(bytes((data[4], data[5])), "little", signed=True)
                        try:
                            # Factor is a base-10 exponent (negative powers are common for ounces).
                            scale = 10 ** (factor_raw if factor_raw < 0x80 else factor_raw - 0x100)
                        except Exception:
                            scale = 1
                        grams_f: Optional[float]
                        if unit == 0x02:  # grams
                            grams_f = raw_val * scale
                        elif unit == 0x0B:  # ounces (pounds mode reports in oz with a scale)
                            ounces = raw_val * scale
                            grams_f = ounces * 28.349523125
                        else:
                            grams_f = None

                        if grams_f is not None:
                            grams = int(round(grams_f))
                        else:
                            grams = None

                        if grams is not None and grams >= 0:
                            self._last_good_gram_mono = time.monotonic()
                            with self._grams_lock:
                                self._latest_grams = grams
                            self.root.after(0, lambda g=grams: self.weight_var.set(f"Weight: {g} g"))
                            self.root.after(0, lambda: self.scale_status_var.set("Scale: connected"))
                            self._scale_failed_reads = 0
                        else:
                            self._scale_failed_reads += 1
                            if self._scale_failed_reads >= SCALE_MAX_FAILED_READS:
                                with self._grams_lock:
                                    self._latest_grams = None
                                self.root.after(
                                    0, lambda: self.scale_status_var.set("Scale: unsupported unit / no weight")
                                )
                                self.root.after(0, lambda: self.weight_var.set("Weight: -- g"))
                    else:
                        self._scale_failed_reads += 1
                        if self._scale_failed_reads >= SCALE_MAX_FAILED_READS:
                            with self._grams_lock:
                                self._latest_grams = None
                            self.root.after(
                                0, lambda: self.scale_status_var.set("Scale: no data")
                            )
                            self.root.after(0, lambda: self.weight_var.set("Weight: -- g"))
                        if time.time() - self._scale_last_ping > SCALE_PING_INTERVAL:
                            try:
                                self.scale_device.read(6)
                            except Exception:
                                pass
                            self._scale_last_ping = time.time()
                except Exception:
                    with self._grams_lock:
                        self._latest_grams = None
                    self.root.after(0, lambda: self.scale_status_var.set("Scale: read error"))
            else:
                time.sleep(SCALE_UPDATE_INTERVAL)
                continue

            if self.scale_device:
                now_m = time.monotonic()
                if now_m - self._last_scale_keepalive_mono >= SCALE_KEEPALIVE_INTERVAL_S:
                    self._last_scale_keepalive_mono = now_m
                    try:
                        for _ in range(SCALE_KEEPALIVE_READ_BURST):
                            if self._scale_stop.is_set():
                                break
                            self.scale_device.read(64)
                            time.sleep(0.004)
                    except Exception:
                        pass

            time.sleep(SCALE_UPDATE_INTERVAL)

        # clock update loop (runs on main thread via Tk .after())

    def _tick_clock(self) -> None:
        try:
            now = time.strftime("%H:%M:%S")
        except Exception:
            now = ""
        self.clock_var.set(now)
        self.root.after(1000, self._tick_clock)

    def _get_grams(self) -> Optional[int]:
        with self._grams_lock:
            return self._latest_grams

    def _on_user_speed_var_changed(self, *_: object) -> None:
        """Sync entry text to slider and refresh the thread-safe speed cache."""
        if self._syncing_user_speed:
            return
        self._syncing_user_speed = True
        try:
            self.user_speed_entry_var.set(str(self.user_fill_speed_var.get()))
            self._refresh_user_speed_cache()
        finally:
            self._syncing_user_speed = False

    def _commit_user_speed_entry(self, _event: Optional[object] = None) -> None:
        """Apply typed pump speed % (slider min–max); invalid input reverts to the slider value."""
        if self._syncing_user_speed:
            return
        raw = self.user_speed_entry_var.get().strip()
        try:
            v = int(raw, 10)
        except ValueError:
            self._syncing_user_speed = True
            try:
                self.user_speed_entry_var.set(str(self.user_fill_speed_var.get()))
            finally:
                self._syncing_user_speed = False
            return
        clamped = max(
            USER_PUMP_SPEED_SLIDER_MIN,
            min(USER_PUMP_SPEED_SLIDER_MAX, v),
        )
        self._syncing_user_speed = True
        try:
            self.user_fill_speed_var.set(clamped)
            self.user_speed_entry_var.set(str(clamped))
            self._refresh_user_speed_cache()
        finally:
            self._syncing_user_speed = False

    def _refresh_user_speed_cache(self) -> None:
        """Keep slider value in a lock-protected int so fill/purge threads never read Tk vars off the main thread."""
        try:
            v = int(self.user_fill_speed_var.get())
        except (tk.TclError, ValueError, TypeError):
            v = FILL_SPEED_START_PCT
        clamped = max(
            FILL_SPEED_MIN_PCT + 1,
            min(USER_PUMP_SPEED_SLIDER_MAX, max(USER_PUMP_SPEED_SLIDER_MIN, v)),
        )
        with self._user_speed_lock:
            self._user_speed_cached = clamped

    def _user_pump_speed_pct(self) -> int:
        with self._user_speed_lock:
            return self._user_speed_cached

    def _parse_target_weight(self) -> Optional[int]:
        raw = self.target_entry.get().strip()
        if not raw:
            return None
        try:
            v = int(raw, 10)
        except ValueError:
            return None
        return v if v > 0 else None

    def _scale_ready_for_fill(self) -> bool:
        if self.scale_device is None:
            return False
        if self._get_grams() is None:
            return False
        if time.monotonic() - self._last_good_gram_mono > GRAM_STALE_MAX_S:
            return False
        return True

    def _wait_scale_ready_for_fill(self) -> bool:
        deadline = time.monotonic() + FILL_SCALE_AUTO_RECONNECT_WAIT_S
        while time.monotonic() < deadline:
            if self._scale_ready_for_fill():
                return True
            time.sleep(0.05)
        return self._scale_ready_for_fill()

    # --- fill ---
    def _start_fill(self) -> None:
        with self._fill_start_lock:
            if self._fill_active:
                self._log("# fill already running")
                return
            if self._purge_active:
                self._log("# stop purge before starting fill")
                self.fill_status_var.set("Fill: stop purge first")
                return
            if self._prime_active:
                self._log("# stop prime before starting fill")
                self.fill_status_var.set("Fill: stop prime first")
                return
            if not self.ser or not self.ser.is_open:
                self._log("# connect serial first")
                self.fill_status_var.set("Fill: connect serial")
                return
            if not self._scale_ready_for_fill():
                self._log("# fill: scale not ready — auto-reconnecting")
                self.fill_status_var.set("Fill: connecting scale…")
                self._reconnect_scale()
                if not self._wait_scale_ready_for_fill():
                    self._log(
                        "# scale still not ready after auto-reconnect (replug USB or use Reconnect scale)"
                    )
                    self.fill_status_var.set("Fill: scale not ready")
                    return
                self._log("# fill: scale ready after auto-reconnect")
            target = self._parse_target_weight()
            if target is None:
                self._log("# enter a target weight (g), whole number > 0")
                self.fill_status_var.set("Fill: enter target (g)")
                return
            g = self._get_grams()
            if g is None:
                self._log("# no scale reading")
                self.fill_status_var.set("Fill: waiting for scale")
                return
            if g >= target:
                self._log("# already at or above target")
                self.fill_status_var.set("Fill: at target")
                return

            self._fill_abort.clear()
            self._fill_active = True
            self._last_sent_speed = None
            self.fill_status_var.set("Fill: running")
            self._fill_thread = threading.Thread(
                target=self._run_fill, args=(target, g), daemon=True
            )
            self._fill_thread.start()
            self._log(
                f"# fill start → target {target} g (from {g} g), max pump {self._user_pump_speed_pct()}%"
            )

    def _abort_fill(self) -> None:
        if not self._fill_active:
            return
        self._fill_abort.set()
        self._serial_write("STOP")
        self._log("# fill abort")
        self.root.after(0, lambda: self.fill_status_var.set("Fill: aborted"))
        self.root.after(0, lambda: self.pump_speed_var.set("Pump: —"))

    def _stop_fill_and_purge(self) -> None:
        self._abort_fill()
        self._stop_purge()
        self._stop_prime()

    def _run_prime(self, target_g: int, start_g: int) -> None:
        last_sent: Optional[int] = None
        try:
            zone = _approach_zone_for_fill(target_g)
            prime_t0 = time.monotonic()
            self._serial_write("D F", log=True)
            self.root.after(0, lambda: self.fill_status_var.set("Prime: running"))
            use_time_ramp_down = target_g > FILL_VERY_LARGE_TARGET_THRESHOLD_G
            t_ramp_down_start: Optional[float] = None
            speed_ramp_down_start: Optional[int] = None
            no_gram_since: Optional[float] = None
            while not self._prime_stop.is_set():
                if self.scale_device is None:
                    self._log("# prime abort: scale USB disconnected")
                    self._prime_stop.set()
                    self.root.after(
                        0, lambda: self.fill_status_var.set("Prime: scale lost — pump stopped")
                    )
                    break
                now = time.monotonic()
                if (
                    self._last_good_gram_mono > 0
                    and now - self._last_good_gram_mono > FILL_SCALE_STALE_PACKET_ABORT_S
                ):
                    self._log("# prime abort: scale stopped sending (off / sleep / loose USB)")
                    self._prime_stop.set()
                    self.root.after(
                        0, lambda: self.fill_status_var.set("Prime: scale lost — pump stopped")
                    )
                    break

                g = self._get_grams()
                if g is None:
                    if no_gram_since is None:
                        no_gram_since = time.monotonic()
                    elif time.monotonic() - no_gram_since > FILL_SCALE_NO_GRAMS_ABORT_S:
                        self._log("# prime abort: no scale weight for too long")
                        self._prime_stop.set()
                        self.root.after(
                            0, lambda: self.fill_status_var.set("Prime: scale lost — pump stopped")
                        )
                        break
                    time.sleep(FILL_CONTROL_PERIOD_S)
                    continue
                no_gram_since = None

                if g >= target_g:
                    self._log(f"# prime: reached target {g} g")
                    break

                remaining = target_g - g
                eff_max = _fill_effective_max_pct(
                    target_g, PRIME_SPEED_PCT, time.monotonic() - prime_t0
                )
                if use_time_ramp_down and remaining < zone:
                    if t_ramp_down_start is None:
                        t_ramp_down_start = time.monotonic()
                        speed_ramp_down_start = max(
                            FILL_SPEED_MIN_PCT + 1, min(100, int(eff_max))
                        )
                    elapsed_rd = time.monotonic() - t_ramp_down_start
                    vl_floor = FILL_VERY_LARGE_RAMP_DOWN_MIN_PCT
                    if elapsed_rd >= FILL_VERY_LARGE_RAMP_DOWN_S:
                        speed = vl_floor
                    else:
                        frac = 1.0 - elapsed_rd / FILL_VERY_LARGE_RAMP_DOWN_S
                        speed = int(
                            round(
                                vl_floor + (speed_ramp_down_start - vl_floor) * frac
                            )
                        )
                        speed = max(
                            vl_floor,
                            min(speed_ramp_down_start, speed),
                        )
                else:
                    speed = _speed_for_remaining(remaining, target_g, eff_max, zone)
                if speed != last_sent:
                    self._serial_write(f"S {speed}", log=True)
                    last_sent = speed
                rd_note = (
                    f"{FILL_VERY_LARGE_RAMP_DOWN_S:.0f}s time rampdown"
                    if use_time_ramp_down and remaining < zone
                    else f"slow band {zone} g"
                )
                self.root.after(
                    0,
                    lambda sp=speed, rem=remaining, note=rd_note: self.pump_speed_var.set(
                        f"Pump: {sp}% forward (prime)  ( {rem} g to go, {note} )"
                    ),
                )
                time.sleep(FILL_CONTROL_PERIOD_S)
        finally:
            self._serial_write("STOP", log=False)
            self._serial_write("D F", log=False)
            self._prime_active = False
            self.root.after(0, lambda: self.pump_speed_var.set("Pump: —"))
            self.root.after(0, lambda: self.fill_status_var.set("Prime: idle"))
            self._log("# prime stopped")

    def _start_prime(self) -> None:
        if self._prime_active:
            self._log("# prime already running")
            return
        if self._fill_active:
            self._log("# stop fill before prime")
            return
        if self._purge_active:
            self._log("# stop purge before prime")
            return
        if not self.ser or not self.ser.is_open:
            self._log("# connect serial for prime")
            return
        target = self._parse_target_weight()
        if target is None:
            self._log("# prime: enter target weight (g), same field as fill")
            self.fill_status_var.set("Prime: enter target (g)")
            return
        if not self._scale_ready_for_fill():
            self._log("# prime: scale not ready — auto-reconnecting")
            self.fill_status_var.set("Prime: connecting scale…")
            self._reconnect_scale()
            if not self._wait_scale_ready_for_fill():
                self._log("# prime: scale still not ready after auto-reconnect")
                self.fill_status_var.set("Prime: scale not ready")
                return
        g = self._get_grams()
        if g is None:
            self._log("# prime: no scale reading")
            self.fill_status_var.set("Prime: waiting for scale")
            return
        if g >= target:
            self._log("# prime: already at or above target")
            self.fill_status_var.set("Prime: at target")
            return
        self._prime_stop.clear()
        self._prime_active = True
        self._prime_thread = threading.Thread(
            target=self._run_prime, args=(target, g), daemon=True
        )
        self._prime_thread.start()
        self._log(
            f"# prime start → target {target} g (from {g} g), max {PRIME_SPEED_PCT}% forward "
            "(stops at target, on scale loss, or Prime stop)"
        )

    def _stop_prime(self) -> None:
        if not self._prime_active:
            return
        self._prime_stop.set()
        self._serial_write("STOP", log=False)
        self._serial_write("D F", log=False)

    def _run_purge(self) -> None:
        try:
            self._serial_write("D R", log=True)
            self.root.after(0, lambda: self.fill_status_var.set("Purge: running"))
            last_sp: Optional[int] = None
            while not self._purge_stop.is_set():
                sp = self._user_pump_speed_pct()
                if sp != last_sp:
                    self._serial_write(f"S {sp}", log=True)
                    last_sp = sp
                    self.root.after(
                        0,
                        lambda s=sp: self.pump_speed_var.set(f"Pump: {s}% reverse (purge)"),
                    )
                time.sleep(0.05)
        finally:
            self._serial_write("STOP", log=False)
            self._serial_write("D F", log=False)
            self._purge_active = False
            self.root.after(0, lambda: self.pump_speed_var.set("Pump: —"))
            self.root.after(0, lambda: self.fill_status_var.set("Purge: idle"))
            self._log("# purge stopped")

    def _start_purge(self) -> None:
        if self._purge_active:
            self._log("# purge already running")
            return
        if self._prime_active:
            self._log("# stop prime before purge")
            return
        if self._fill_active:
            self._log("# stop fill before purge")
            return
        if not self.ser or not self.ser.is_open:
            self._log("# connect serial for purge")
            return
        self._purge_stop.clear()
        self._purge_active = True
        self._purge_thread = threading.Thread(target=self._run_purge, daemon=True)
        self._purge_thread.start()
        self._log(f"# purge: reverse @ {self._user_pump_speed_pct()}% (Stop purge when done)")

    def _stop_purge(self) -> None:
        if not self._purge_active:
            return
        self._purge_stop.set()
        self._serial_write("STOP", log=False)
        self._serial_write("D F", log=False)

    def _run_fill(self, target_g: int, start_g: int) -> None:
        try:
            zone = _approach_zone_for_fill(target_g)
            self._serial_write("D F", log=True)
            fill_t0 = time.monotonic()
            if target_g > FILL_LARGE_TARGET_THRESHOLD_G:
                u = self._user_pump_speed_pct()
                top = max(FILL_LARGE_TARGET_BULK_CAP_PCT, u)
                self._log(
                    f"# large target: ramp {FILL_SPEED_START_PCT}%→{top}% over {FILL_LARGE_TARGET_RAMP_UP_S:.0f}s "
                    f"(floor {FILL_LARGE_TARGET_BULK_CAP_PCT}%, slider {u}%), slow band {zone} g (½ base for speed)"
                )
            if target_g > FILL_VERY_LARGE_TARGET_THRESHOLD_G:
                self._log(
                    f"# very large target: final slowdown = {FILL_VERY_LARGE_RAMP_DOWN_S:.0f}s time ramp "
                    f"(starts when < {zone} g to go)"
                )
            no_gram_since: Optional[float] = None
            last_g_for_increase = start_g
            last_increase_mono = time.monotonic()
            use_time_ramp_down = target_g > FILL_VERY_LARGE_TARGET_THRESHOLD_G
            t_ramp_down_start: Optional[float] = None
            speed_ramp_down_start: Optional[int] = None
            while not self._fill_abort.is_set():
                if self.scale_device is None:
                    self._log("# fill abort: scale USB disconnected")
                    self._fill_abort.set()
                    self.root.after(
                        0, lambda: self.fill_status_var.set("Fill: scale lost — pump stopped")
                    )
                    break
                now = time.monotonic()
                if (
                    self._last_good_gram_mono > 0
                    and now - self._last_good_gram_mono > FILL_SCALE_STALE_PACKET_ABORT_S
                ):
                    self._log("# fill abort: scale stopped sending (off / sleep / loose USB)")
                    self._fill_abort.set()
                    self.root.after(
                        0, lambda: self.fill_status_var.set("Fill: scale lost — pump stopped")
                    )
                    break

                g = self._get_grams()
                if g is None:
                    if no_gram_since is None:
                        no_gram_since = time.monotonic()
                    elif time.monotonic() - no_gram_since > FILL_SCALE_NO_GRAMS_ABORT_S:
                        self._log("# fill abort: no scale weight for too long")
                        self._fill_abort.set()
                        self.root.after(
                            0, lambda: self.fill_status_var.set("Fill: scale lost — pump stopped")
                        )
                        break
                    time.sleep(FILL_CONTROL_PERIOD_S)
                    continue
                no_gram_since = None

                # Abort if grams are not increasing for too long (overflow / stuck safety),
                # but ignore the first moment after pump start while fluid is still reaching the bottle.
                if g > last_g_for_increase + FILL_STUCK_DELTA_G:
                    last_g_for_increase = g
                    last_increase_mono = now
                elif (
                    now - fill_t0 >= FILL_STUCK_GRACE_S
                    and now - last_increase_mono > FILL_STUCK_ABORT_S
                ):
                    self._log("# fill abort: scale weight not increasing (stuck / overflow safety)")
                    self._fill_abort.set()
                    self.root.after(
                        0, lambda: self.fill_status_var.set("Fill: grams not increasing — pump stopped")
                    )
                    break

                if g >= target_g:
                    self._log(f"# target reached {g} g")
                    break
                remaining = target_g - g
                eff_max = _fill_effective_max_pct(
                    target_g, self._user_pump_speed_pct(), time.monotonic() - fill_t0
                )
                if use_time_ramp_down and remaining < zone:
                    if t_ramp_down_start is None:
                        t_ramp_down_start = time.monotonic()
                        speed_ramp_down_start = max(
                            FILL_SPEED_MIN_PCT + 1, min(100, int(eff_max))
                        )
                    elapsed_rd = time.monotonic() - t_ramp_down_start
                    vl_floor = FILL_VERY_LARGE_RAMP_DOWN_MIN_PCT
                    if elapsed_rd >= FILL_VERY_LARGE_RAMP_DOWN_S:
                        speed = vl_floor
                    else:
                        frac = 1.0 - elapsed_rd / FILL_VERY_LARGE_RAMP_DOWN_S
                        speed = int(
                            round(
                                vl_floor + (speed_ramp_down_start - vl_floor) * frac
                            )
                        )
                        speed = max(
                            vl_floor,
                            min(speed_ramp_down_start, speed),
                        )
                else:
                    speed = _speed_for_remaining(remaining, target_g, eff_max, zone)
                if speed != self._last_sent_speed:
                    self._serial_write(f"S {speed}", log=True)
                    self._last_sent_speed = speed
                if use_time_ramp_down and remaining < zone:
                    rd_note = f"{FILL_VERY_LARGE_RAMP_DOWN_S:.0f}s time rampdown"
                else:
                    rd_note = f"slow band {zone} g"
                self.root.after(
                    0,
                    lambda sp=speed, rem=remaining, note=rd_note: self.pump_speed_var.set(
                        f"Pump: {sp}% forward  ( {rem} g to go, {note} )"
                    ),
                )
                time.sleep(FILL_CONTROL_PERIOD_S)

            if self._fill_abort.is_set():
                return

            self._serial_write("STOP", log=True)
            time.sleep(DRIP_SETTLE_S)
            if self._fill_abort.is_set():
                return
            self._log("# drip: reverse pulse")
            self.root.after(0, lambda: self.fill_status_var.set("Fill: drip reverse"))
            self._serial_write("D R", log=True)
            self._serial_write(f"S {DRIP_REVERSE_SPEED_PCT}", log=True)
            self.root.after(
                0,
                lambda: self.pump_speed_var.set(f"Pump: {DRIP_REVERSE_SPEED_PCT}% reverse (drip)"),
            )
            t0 = time.monotonic()
            while time.monotonic() - t0 < DRIP_REVERSE_DURATION_S:
                if self._fill_abort.is_set():
                    break
                time.sleep(0.02)
            self._serial_write("STOP", log=True)
            self._serial_write("D F", log=True)
            self._last_sent_speed = None
            self.root.after(0, lambda: self.pump_speed_var.set("Pump: —"))
            if self._fill_abort.is_set():
                self.root.after(0, lambda: self.fill_status_var.set("Fill: aborted"))
                self._log("# fill aborted (during drip)")
            else:
                self.root.after(0, lambda: self.fill_status_var.set("Fill: done"))
                self._log("# fill complete")
                _beep_fill_done()
        finally:
            self._fill_active = False
            if self._fill_abort.is_set():
                self._serial_write("STOP", log=False)
                self._serial_write("D F", log=False)
                self.root.after(0, lambda: self.pump_speed_var.set("Pump: —"))

    # --- foot pedal ---
    def _register_tk_pedal(self) -> None:
        """Always register: global keyboard often needs admin on Windows; Tk sees 'b' when this window is focused."""
        if self._foot_pedal_tk_fallback:
            return
        self._foot_pedal_tk_fallback = True

        def on_b(event: tk.Event) -> None:
            w = self.root.focus_get()
            if w == self.target_entry or w == self.log:
                return
            if w is not None and w.winfo_class() == "Text":
                return
            if (event.keysym or "").lower() == "b" or (event.char or "").lower() == "b":
                self._foot_pedal_action()

        self.root.bind_all("<KeyPress-b>", on_b)
        self._log("# pedal: Tk binding active (click this app, not the log, then press pedal)")

    def _setup_foot_pedal(self) -> None:
        try:
            import keyboard

            def hook_fn(event: object) -> bool:
                try:
                    et = getattr(event, "event_type", None)
                    if et != keyboard.KEY_DOWN and et != "down":
                        return True
                    name = (getattr(event, "name", None) or "").lower()
                    if name != FOOT_PEDAL_KEY:
                        return True
                except Exception:
                    return True
                self.root.after(0, self._foot_pedal_action)
                return True

            self._keyboard_unhook = keyboard.hook(hook_fn)
            self._log("# pedal: global hook (keyboard.hook) — if dead, run app as Administrator")
        except ImportError:
            self._log(f'# pedal: global hook unavailable — "{sys.executable}" -m pip install keyboard')
        except Exception as exc:
            self._log(f"# pedal: global hook failed ({exc}) — use Tk: click app window, then pedal")

    def _foot_pedal_action(self) -> None:
        now = time.monotonic()
        if (now - self._pedal_last_fire) < FOOT_PEDAL_DEBOUNCE_S:
            return
        self._pedal_last_fire = now
        if self._purge_active:
            self._stop_purge()
            return
        if self._prime_active:
            self._stop_prime()
            return
        if self._fill_active:
            self._abort_fill()
            return
        self._start_fill()

    def _on_close(self) -> None:
        self._fill_abort.set()
        self._purge_stop.set()
        self._prime_stop.set()
        self._scale_stop.set()
        try:
            if self.scale_device:
                self.scale_device.close()
        except Exception:
            pass
        try:
            if self._keyboard_unhook is not None:
                self._keyboard_unhook()
        except Exception:
            pass
        if self._foot_pedal_tk_fallback:
            try:
                self.root.unbind_all("<KeyPress-b>")
            except Exception:
                pass
        self._reader_stop.set()
        try:
            self._serial_write("STOP", log=False)
            self._serial_write("D F", log=False)
        except Exception:
            pass
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    PumpFillGui().run()


if __name__ == "__main__":
    main()
