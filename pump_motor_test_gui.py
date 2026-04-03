"""
Standalone GUI to exercise Arduino pump firmware over serial.

Arduino sketch: arduino/pump_motor_tester/pump_motor_tester.ino
Protocol: 115200 baud, lines ending in \\n — S <0-100>, D F, D R, STOP
"""

from __future__ import annotations

import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable, Optional

try:
    import serial  # pyright: ignore[reportMissingModuleSource]
    # pyserial ships without inline types; pyright cannot resolve submodules "from source".
    from serial.tools import list_ports  # pyright: ignore[reportMissingModuleSource]
except ModuleNotFoundError as exc:
    raise SystemExit(
        "Missing package 'pyserial' for the Python that is running this file.\n\n"
        f"  Python executable: {sys.executable}\n\n"
        "Install into that exact interpreter (copy/paste):\n\n"
        f'  "{sys.executable}" -m pip install pyserial\n\n'
        "If you use Run in the IDE, set the interpreter to the one above, or install there via "
        "Terminal with that same command."
    ) from exc

# Dymo M10 USB scale (same IDs as pump_controller.py)
DYMO_SCALE_VENDOR_ID = 0x0922
DYMO_SCALE_PRODUCT_ID = 0x8003
# Other Dymo USB scales sometimes use different PIDs — try path-based open from enumerate.
DYMO_SCALE_PRODUCT_IDS_TRY = (0x8003, 0x8004, 0x8005)
SCALE_UPDATE_INTERVAL = 0.1
SCALE_MAX_FAILED_READS = 50
SCALE_PING_INTERVAL = 60.0

DEFAULT_BAUD = 115200
FOOT_PEDAL_KEY = "b"
# Set > 0 if the USB foot switch sends duplicate key events (e.g. 0.35).
FOOT_PEDAL_DEBOUNCE_S = 0.0
FOOT_PEDAL_DEFAULT_SPEED = 25


class PumpMotorTestGui:
    def __init__(self) -> None:
        self.ser: Optional[serial.Serial] = None
        self._reader_stop = threading.Event()
        self._pedal_last_fire = 0.0
        self._keyboard_unhook: Optional[Callable[[], None]] = None
        self._foot_pedal_tk_fallback = False
        self._suppress_speed_send = False
        self.scale_device: Any = None
        self._scale_stop = threading.Event()
        self._scale_failed_reads = 0
        self._scale_last_ping = 0.0

        self.root = tk.Tk()
        self.root.title("Pump motor test (Arduino)")
        self.root.configure(bg="#1a1a1a")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        pad = {"padx": 10, "pady": 6}
        frm = tk.Frame(self.root, bg="#1a1a1a")
        frm.pack(fill=tk.BOTH, expand=True, **pad)

        row0 = tk.Frame(frm, bg="#1a1a1a")
        row0.pack(fill=tk.X, **pad)
        tk.Label(row0, text="COM port", fg="white", bg="#1a1a1a").pack(side=tk.LEFT)
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(row0, textvariable=self.port_var, width=28, state="readonly")
        self.port_combo.pack(side=tk.LEFT, padx=8)
        tk.Button(row0, text="Refresh", command=self._refresh_ports, bg="#444", fg="white").pack(
            side=tk.LEFT
        )

        row1 = tk.Frame(frm, bg="#1a1a1a")
        row1.pack(fill=tk.X, **pad)
        self.connect_btn = tk.Button(
            row1, text="Connect", command=self._toggle_connect, bg="#2d7ff9", fg="white", width=12
        )
        self.connect_btn.pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Disconnected")
        tk.Label(row1, textvariable=self.status_var, fg="#ccc", bg="#1a1a1a").pack(
            side=tk.LEFT, padx=16
        )

        scale_frm = tk.Frame(frm, bg="#1a1a1a")
        scale_frm.pack(fill=tk.X, **pad)
        tk.Label(
            scale_frm,
            text="Dymo M10 (USB)",
            fg="#888",
            bg="#1a1a1a",
            font=("Segoe UI", 10),
        ).pack(anchor=tk.W)
        self.weight_var = tk.StringVar(value="Weight: -- g")
        tk.Label(
            scale_frm,
            textvariable=self.weight_var,
            font=("Segoe UI", 28),
            fg="white",
            bg="#1a1a1a",
        ).pack(anchor=tk.W, pady=(0, 4))
        row_scale = tk.Frame(scale_frm, bg="#1a1a1a")
        row_scale.pack(fill=tk.X)
        self.scale_status_var = tk.StringVar(value="Scale: starting…")
        tk.Label(
            row_scale,
            textvariable=self.scale_status_var,
            fg="#aaa",
            bg="#1a1a1a",
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT)
        tk.Button(
            row_scale,
            text="Reconnect scale",
            command=self._reconnect_scale,
            bg="#444",
            fg="white",
        ).pack(side=tk.LEFT, padx=12)
        tk.Button(
            row_scale,
            text="Diagnose USB HID",
            command=self._diagnose_scale_usb,
            bg="#444",
            fg="white",
        ).pack(side=tk.LEFT, padx=4)

        tk.Label(frm, text="Speed (0–100%)", fg="white", bg="#1a1a1a", font=("Segoe UI", 11)).pack(
            anchor=tk.W, **pad
        )
        self.speed_var = tk.IntVar(value=0)
        self.speed_scale = tk.Scale(
            frm,
            from_=0,
            to=100,
            orient=tk.HORIZONTAL,
            variable=self.speed_var,
            command=lambda _: self._send_speed(),
            bg="#2a2a2a",
            fg="white",
            highlightthickness=0,
            troughcolor="#444",
            length=420,
        )
        self.speed_scale.pack(fill=tk.X, **pad)

        dir_frm = tk.Frame(frm, bg="#1a1a1a")
        dir_frm.pack(fill=tk.X, **pad)
        self.dir_var = tk.StringVar(value="F")
        tk.Radiobutton(
            dir_frm,
            text="Forward",
            variable=self.dir_var,
            value="F",
            command=self._send_direction,
            fg="white",
            bg="#1a1a1a",
            selectcolor="#333",
            activebackground="#1a1a1a",
            activeforeground="white",
        ).pack(side=tk.LEFT, padx=12)
        tk.Radiobutton(
            dir_frm,
            text="Reverse",
            variable=self.dir_var,
            value="R",
            command=self._send_direction,
            fg="white",
            bg="#1a1a1a",
            selectcolor="#333",
            activebackground="#1a1a1a",
            activeforeground="white",
        ).pack(side=tk.LEFT, padx=12)

        tk.Button(frm, text="STOP (S 0)", command=self._stop, bg="#a33", fg="white", width=20).pack(
            **pad
        )

        tk.Label(
            frm,
            text=(
                f"Foot pedal: key '{FOOT_PEDAL_KEY}' toggles run/stop when serial connected. "
                "Needs pip install keyboard (global hook) or click the window (not the log) for fallback."
            ),
            fg="#888",
            bg="#1a1a1a",
            font=("Segoe UI", 9),
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, padx=10)

        tk.Label(frm, text="Serial log", fg="#888", bg="#1a1a1a").pack(anchor=tk.W, padx=10)
        self.log = tk.Text(frm, height=12, bg="#111", fg="#0f0", font=("Consolas", 10), wrap=tk.WORD)
        self.log.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._refresh_ports()
        self._setup_foot_pedal_hook()
        self._reconnect_scale()
        threading.Thread(target=self._update_weight_loop, daemon=True).start()

    def _setup_foot_pedal_hook(self) -> None:
        try:
            import keyboard

            def on_press(event: object) -> None:
                try:
                    name = getattr(event, "name", None)
                except Exception:
                    return
                if name != FOOT_PEDAL_KEY:
                    return
                self.root.after(0, self._foot_pedal_toggle)

            self._keyboard_unhook = keyboard.on_press(on_press)
            self._log("# foot pedal: global hook active (keyboard)")
        except ImportError:
            self._log(
                f'# foot pedal: missing keyboard for:\n#   {sys.executable}\n'
                f'# fix: "{sys.executable}" -m pip install keyboard'
            )
            self._setup_foot_pedal_tk_fallback()
        except Exception as exc:
            self._log(f"# foot pedal: global hook failed: {exc}")
            if "access" in str(exc).lower() or "denied" in str(exc).lower():
                self._log("# foot pedal: try Run as administrator, or use fallback below")
            self._setup_foot_pedal_tk_fallback()

    def _setup_foot_pedal_tk_fallback(self) -> None:
        if self._foot_pedal_tk_fallback:
            return
        self._foot_pedal_tk_fallback = True

        def on_b(event: tk.Event) -> None:
            w = self.root.focus_get()
            if w is not None:
                cls = w.winfo_class()
                if cls in ("Text", "Entry", "TCombobox", "TEntry"):
                    return
            if (event.keysym or "").lower() == "b":
                self._foot_pedal_toggle()

        self.root.bind_all("<KeyPress-b>", on_b)
        self._log(
            "# foot pedal: Tk fallback — click a label/button (not the black log), then press pedal"
        )

    def _log(self, line: str) -> None:
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)

    def _refresh_ports(self) -> None:
        names = [p.device for p in list_ports.comports()]
        self.port_combo["values"] = names
        if names and not self.port_var.get():
            self.port_var.set(names[0])

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
        """Return opened hid.device() or None. Windows often needs open_path from enumerate()."""
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

        # 1) Classic open by VID/PID
        for pid in DYMO_SCALE_PRODUCT_IDS_TRY:
            try:
                dev = hid.device()
                dev.open(vid, pid)
                dev.set_nonblocking(True)
                self._log(f"# scale opened via open({vid:#06x}, {pid:#06x})")
                return dev
            except Exception:
                continue

        # 2) Same PID(s) but explicit path (fixes many Windows open() failures)
        for pid in DYMO_SCALE_PRODUCT_IDS_TRY:
            for info in hid.enumerate(vid, pid):
                dev = try_open_path(info)
                if dev is not None:
                    self._log(f"# scale opened via open_path PID={pid:#06x}")
                    return dev

        # 3) Any Dymo (0x0922) device whose name looks like a scale (skip label printers)
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
                self._log(
                    f"# scale opened via name match "
                    f"PID={info.get('product_id'):#06x} {info.get('product_string')!r}"
                )
                return dev

        return None

    def _reconnect_scale(self) -> None:
        try:
            import hid
        except ImportError:
            self.scale_status_var.set("Scale: missing hidapi — see log for pip command")
            self.weight_var.set("Weight: -- g")
            self._log(
                f'# scale: missing hidapi for:\n#   {sys.executable}\n'
                f'# fix: "{sys.executable}" -m pip install hidapi'
            )
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
                raise OSError(
                    "No Dymo scale HID found. Quit Dymo desktop apps, unplug/replug USB, "
                    "then click Diagnose USB HID."
                )
            self.scale_device = dev
            self._scale_failed_reads = 0
            self.scale_status_var.set("Scale: connected")
            self._log("# scale connected")
        except Exception as exc:
            self.scale_device = None
            self.scale_status_var.set(f"Scale: not connected ({exc})")
            self.weight_var.set("Weight: -- g")
            self._log(f"# scale open failed: {exc}")

    def _diagnose_scale_usb(self) -> None:
        try:
            import hid
        except ImportError:
            self._log(
                f'# diagnose: missing hidapi for:\n#   {sys.executable}\n'
                f'# fix: "{sys.executable}" -m pip install hidapi'
            )
            return
        self._log("# --- HID devices (vendor 0x0922 Dymo) ---")
        found = False
        try:
            rows = list(hid.enumerate(DYMO_SCALE_VENDOR_ID, 0))
        except TypeError:
            rows = [i for i in hid.enumerate() if i.get("vendor_id") == DYMO_SCALE_VENDOR_ID]
        for info in rows:
            found = True
            vid = info.get("vendor_id", 0)
            pid = info.get("product_id", 0)
            self._log(
                f"#   VID={vid:#06x} PID={pid:#06x} "
                f"prod={info.get('product_string')!r} "
                f"mfr={info.get('manufacturer_string')!r}"
            )
        if not found:
            self._log("#   (none) — scale unplugged, bad cable, or not recognized")
        self._log("# Close Dymo Connect / M10 software if installed; it can lock the device.")
        self._log("# --- end ---")

    def _update_weight_loop(self) -> None:
        self._scale_last_ping = time.time()
        while not self._scale_stop.is_set():
            if self.scale_device:
                try:
                    data = self.scale_device.read(64)
                    if data and len(data) >= 6:
                        grams = data[4] + (data[5] << 8)
                        self.root.after(
                            0, lambda g=grams: self.weight_var.set(f"Weight: {g} g")
                        )
                        self.root.after(
                            0, lambda: self.scale_status_var.set("Scale: connected")
                        )
                        self._scale_failed_reads = 0
                    else:
                        self._scale_failed_reads += 1
                        if self._scale_failed_reads >= SCALE_MAX_FAILED_READS:
                            self.root.after(
                                0,
                                lambda: self.scale_status_var.set("Scale: no data (check USB)"),
                            )
                            self.root.after(
                                0, lambda: self.weight_var.set("Weight: -- g")
                            )
                        if time.time() - self._scale_last_ping > SCALE_PING_INTERVAL:
                            try:
                                self.scale_device.read(6)
                            except Exception:
                                pass
                            self._scale_last_ping = time.time()
                except Exception:
                    self.root.after(
                        0,
                        lambda: self.scale_status_var.set("Scale: read error"),
                    )
                    self.root.after(0, lambda: self.weight_var.set("Weight: -- g"))
            else:
                time.sleep(SCALE_UPDATE_INTERVAL)
                continue
            time.sleep(SCALE_UPDATE_INTERVAL)

    def _toggle_connect(self) -> None:
        if self.ser and self.ser.is_open:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        port = self.port_var.get().strip()
        if not port:
            self.status_var.set("Pick a COM port")
            return
        try:
            self.ser = serial.Serial(port, DEFAULT_BAUD, timeout=0.2)
            self._reader_stop.clear()
            threading.Thread(target=self._read_loop, daemon=True).start()
            self.connect_btn.config(text="Disconnect", bg="#666")
            self.status_var.set(f"Connected {port} @ {DEFAULT_BAUD}")
            self._log(f"# connected {port}")
        except Exception as exc:
            self.status_var.set(f"Open failed: {exc}")
            self._log(f"# error: {exc}")
            self.ser = None

    def _disconnect(self) -> None:
        self._reader_stop.set()
        try:
            if self.ser and self.ser.is_open:
                self._write_line("STOP")
        except Exception:
            pass
        try:
            if self.ser:
                self.ser.close()
        except Exception:
            pass
        self.ser = None
        self.connect_btn.config(text="Connect", bg="#2d7ff9")
        self.status_var.set("Disconnected")
        self._log("# disconnected")

    def _write_line(self, s: str) -> None:
        if not self.ser or not self.ser.is_open:
            self.status_var.set("Not connected")
            return
        data = (s.strip() + "\n").encode("ascii", errors="ignore")
        self.ser.write(data)
        self._log(f"> {s.strip()}")

    def _send_speed(self) -> None:
        if self._suppress_speed_send:
            return
        if not self.ser or not self.ser.is_open:
            return
        v = int(self.speed_var.get())
        self._write_line(f"S {v}")

    def _send_direction(self) -> None:
        if not self.ser or not self.ser.is_open:
            return
        d = self.dir_var.get()
        self._write_line(f"D {d}")

    def _stop(self) -> None:
        self._suppress_speed_send = True
        try:
            self.speed_var.set(0)
            self.speed_scale.set(0)
        finally:
            self._suppress_speed_send = False
        self._write_line("STOP")

    def _foot_pedal_toggle(self) -> None:
        if not self.ser or not self.ser.is_open:
            return
        now = time.monotonic()
        if FOOT_PEDAL_DEBOUNCE_S > 0 and (now - self._pedal_last_fire) < FOOT_PEDAL_DEBOUNCE_S:
            return
        self._pedal_last_fire = now

        current = int(self.speed_var.get())
        if current > 0:
            self._stop()
            self._log("# foot pedal: stop")
            return

        target = int(self.speed_var.get())
        if target <= 0:
            target = FOOT_PEDAL_DEFAULT_SPEED
        self._suppress_speed_send = True
        try:
            self.speed_var.set(target)
            self.speed_scale.set(target)
        finally:
            self._suppress_speed_send = False
        self._send_direction()
        self._send_speed()
        self._log(f"# foot pedal: run at {target}%")

    def _read_loop(self) -> None:
        while not self._reader_stop.is_set() and self.ser and self.ser.is_open:
            try:
                raw = self.ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").rstrip()
                if line:
                    self.root.after(0, lambda l=line: self._log(f"< {l}"))
            except Exception as exc:
                self.root.after(0, lambda e=exc: self._log(f"< read error: {e}"))
                break

    def _on_close(self) -> None:
        self._scale_stop.set()
        try:
            if self.scale_device:
                self.scale_device.close()
        except Exception:
            pass
        self.scale_device = None
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
        self._disconnect()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    PumpMotorTestGui().run()


if __name__ == "__main__":
    main()
