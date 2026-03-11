import csv
import os
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import hid
import keyboard
import serial


@dataclass
class AppConfig:
    scale_vendor_id: int = 0x0922
    scale_product_id: int = 0x8003
    relay_port: str = "COM3"
    relay_baud: int = 9600
    trigger_key: str = "b"
    update_interval: float = 0.1
    ping_interval: int = 60
    csv_filename: str = "fill_log.csv"
    max_failed_reads: int = 50
    grace_period: int = 120


class PumpControllerApp:
    def __init__(self, config: AppConfig):
        self.config = config
        self.scale_device: Optional[hid.device] = None
        self.target_grams: Optional[int] = None
        self.is_running = False
        self.scale_connected = False
        self.last_fill_time = 0.0
        self.failed_reads = 0

        self._ensure_log_file()
        self.relay = serial.Serial(config.relay_port, config.relay_baud, timeout=1)

        self.root = tk.Tk()
        self.root.title("Pump Controller")
        self._setup_ui()

        keyboard.on_press(self._on_key_press)

    def _setup_ui(self) -> None:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(f"{screen_width // 2}x{screen_height}")
        self.root.configure(bg="black")

        self.weight_var = tk.StringVar(value="Weight: -- g")
        tk.Label(
            self.root,
            textvariable=self.weight_var,
            font=("Arial", 48),
            fg="white",
            bg="black",
        ).pack(pady=20)

        tk.Label(
            self.root,
            text="Target Weight (g):",
            font=("Arial", 20),
            fg="white",
            bg="black",
        ).pack()

        self.target_entry = tk.Entry(self.root, font=("Arial", 24), justify="center")
        self.target_entry.bind("<Key>", self._block_trigger_key)
        self.target_entry.pack(pady=10)

        self.status_var = tk.StringVar(value="Scale status unknown")
        tk.Label(
            self.root,
            textvariable=self.status_var,
            font=("Arial", 18),
            fg="yellow",
            bg="black",
        ).pack(pady=5)

        tk.Button(
            self.root,
            text="Start / Stop Pump",
            command=self.toggle_pump,
            font=("Arial", 20),
            bg="#2d7ff9",
            fg="white",
        ).pack(pady=10)

        tk.Button(
            self.root,
            text="Reconnect Scale",
            command=self.reconnect_scale,
            font=("Arial", 18),
            bg="gray",
            fg="white",
        ).pack(pady=10)

        tk.Button(
            self.root,
            text="Check USB Devices",
            command=self.check_usb_devices,
            font=("Arial", 18),
            bg="#444",
            fg="white",
        ).pack(pady=5)

    def _ensure_log_file(self) -> None:
        if not os.path.exists(self.config.csv_filename):
            with open(self.config.csv_filename, mode="w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(["Timestamp", "Weight (g)"])

    def relay_on(self) -> None:
        self.relay.write(bytearray([0xA0, 0x01, 0x01, 0xA2]))

    def relay_off(self) -> None:
        self.relay.write(bytearray([0xA0, 0x01, 0x00, 0xA1]))

    def log_fill(self, weight: int) -> None:
        with open(self.config.csv_filename, mode="a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow([datetime.now().isoformat(), weight])

    def reconnect_scale(self) -> None:
        try:
            if self.scale_device:
                self.scale_device.close()
            self.scale_device = hid.device()
            self.scale_device.open(self.config.scale_vendor_id, self.config.scale_product_id)
            self.scale_device.set_nonblocking(True)
            self.scale_connected = True
            self.status_var.set("✅ Scale connected")
            print("✅ Scale reconnected.")
        except Exception as exc:
            self.scale_connected = False
            self.status_var.set("❌ Scale disconnected")
            print(f"❌ Failed to reconnect scale: {exc}")

    def check_usb_devices(self) -> None:
        """Starter hook for future health checks on USB peripherals."""
        try:
            devices = hid.enumerate()
            found = any(
                d["vendor_id"] == self.config.scale_vendor_id
                and d["product_id"] == self.config.scale_product_id
                for d in devices
            )
            if found:
                self.status_var.set("✅ Scale seen in USB list")
            else:
                self.status_var.set("⚠️ Scale not present in USB list")
            print(f"USB devices detected: {len(devices)}")
        except Exception as exc:
            self.status_var.set("❌ USB check failed")
            print(f"USB device check failed: {exc}")

    def is_scale_active(self) -> bool:
        if self.scale_device:
            try:
                data = self.scale_device.read(6)
                if data:
                    self.scale_connected = True
                    return True
            except Exception:
                pass
        self.scale_connected = False
        return False

    def _block_trigger_key(self, event):
        if event.char == self.config.trigger_key:
            return "break"

    def toggle_pump(self) -> None:
        if time.time() - self.last_fill_time > self.config.grace_period:
            if not self.is_scale_active():
                self.status_var.set("❌ Cannot start: Scale disconnected")
                print("Pump start blocked: Scale disconnected")
                return

        if self.is_running:
            self.is_running = False
            self.relay_off()
            self.last_fill_time = time.time()
            print("Pump stopped")
            return

        try:
            self.target_grams = int(self.target_entry.get())
            self.is_running = True
            self.relay_on()
            print(f"Pump started to reach {self.target_grams}g")
        except ValueError:
            self.weight_var.set("Invalid target")

    def _on_key_press(self, event) -> None:
        if event.name == self.config.trigger_key:
            self.toggle_pump()

    def update_weight_loop(self) -> None:
        last_ping = time.time()

        while True:
            if self.scale_device:
                try:
                    data = self.scale_device.read(6)
                    if data:
                        grams = data[4] + (data[5] << 8)
                        self.weight_var.set(f"Weight: {grams} g")

                        if self.is_running and self.target_grams is not None and grams >= self.target_grams:
                            self.is_running = False
                            self.relay_off()
                            self.log_fill(grams)
                            self.last_fill_time = time.time()
                            print(f"Target reached: {grams}g")

                        self.failed_reads = 0
                        self.scale_connected = True
                        self.status_var.set("✅ Scale connected")
                    else:
                        self.failed_reads += 1
                        if self.failed_reads >= self.config.max_failed_reads:
                            self.scale_connected = False
                            self.status_var.set("❌ Scale disconnected")
                            self.weight_var.set("Weight: -- g")

                        if time.time() - last_ping > self.config.ping_interval:
                            self.scale_device.read(6)
                            last_ping = time.time()
                except Exception:
                    self.scale_connected = False
                    self.status_var.set("❌ Scale disconnected (error)")
                    self.weight_var.set("Weight: -- g")
            else:
                self.scale_connected = False
                self.status_var.set("❌ Scale disconnected (no device)")
                self.weight_var.set("Weight: -- g")

            time.sleep(self.config.update_interval)

    def run(self) -> None:
        self.reconnect_scale()
        threading.Thread(target=self.update_weight_loop, daemon=True).start()
        self.root.mainloop()


def main() -> None:
    app = PumpControllerApp(AppConfig())
    app.run()


if __name__ == "__main__":
    main()
