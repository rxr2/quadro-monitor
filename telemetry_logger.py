#!/usr/bin/python3
"""Write CPU, IT8696, Quadro and LACT-equivalent GPU telemetry to CSV."""

from __future__ import annotations

import csv
import fcntl
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import psutil


HWMON_ROOT = Path("/sys/class/hwmon")
LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_PATH = LOG_DIR / "hardware-telemetry.csv"
LOCK_PATH = LOG_DIR / ".hardware-telemetry.lock"
MAX_LOG_BYTES = 50 * 1024 * 1024
BACKUP_COUNT = 5
DEDICATED_GPU_PCI = "0000:03:00.0"
SAMPLE_INTERVAL_SECONDS = 1.0

COLUMNS = [
    "timestamp",
    "cpu_temp_c",
    "cpu_usage_pct",
    "cpu_pump_rpm_it8696_fan1",
    "it8696_fan2_rpm",
    "it8696_fan3_rpm",
    "it8696_fan4_rpm",
    "cpu_fan_rpm_it8696_fan5",
    "it8696_fan6_rpm",
    "it8696_pwm1_pct",
    "it8696_pwm2_pct",
    "it8696_pwm3_pct",
    "it8696_pwm4_pct",
    "it8696_pwm5_pct",
    "it8696_pwm6_pct",
    "gpu_liquid_temp_c_quadro",
    "gpu_fan_rpm_quadro_fan1",
    "gpu_pump_rpm_quadro_fan2",
    "quadro_fan3_rpm",
    "quadro_fan4_rpm",
    "quadro_flow_dl_h",
    "lact_edge_c",
    "lact_junction_c",
    "lact_mem_c",
    "lact_gpu_fan_rpm",
]


def read_float(path: Optional[Path], divisor: float = 1.0) -> Optional[float]:
    if path is None:
        return None
    try:
        return float(path.read_text(encoding="utf-8").strip()) / divisor
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        return None


def read_int(path: Optional[Path]) -> Optional[int]:
    value = read_float(path)
    return round(value) if value is not None else None


def rounded(value: Optional[float], digits: int = 1) -> str | float:
    return "" if value is None else round(value, digits)


class HardwareSources:
    def __init__(self) -> None:
        self.cpu: Optional[Path] = None
        self.board: Optional[Path] = None
        self.quadro: Optional[Path] = None
        self.gpu: Optional[Path] = None
        self.gpu_temperatures: dict[str, Path] = {}
        self.last_discovery = 0.0
        psutil.cpu_percent(interval=None)
        self.discover()

    @staticmethod
    def _name(path: Path) -> str:
        try:
            return (path / "name").read_text(encoding="utf-8").strip()
        except (FileNotFoundError, PermissionError, OSError):
            return ""

    @staticmethod
    def _pci_address(path: Path) -> str:
        try:
            return (path / "device").resolve().name
        except (FileNotFoundError, OSError):
            return ""

    def discover(self) -> None:
        self.cpu = None
        self.board = None
        self.quadro = None
        self.gpu = None
        self.gpu_temperatures = {}

        for path in sorted(HWMON_ROOT.glob("hwmon*")):
            name = self._name(path)
            if name == "k10temp":
                self.cpu = path
            elif name == "it8696":
                self.board = path
            elif name == "quadro":
                self.quadro = path
            elif name == "amdgpu" and self._pci_address(path) == DEDICATED_GPU_PCI:
                self.gpu = path

        if self.gpu:
            for label_path in self.gpu.glob("temp*_label"):
                try:
                    label = label_path.read_text(encoding="utf-8").strip().lower()
                except (FileNotFoundError, PermissionError, OSError):
                    continue
                input_path = label_path.with_name(
                    label_path.name.removesuffix("_label") + "_input"
                )
                if input_path.exists():
                    self.gpu_temperatures[label] = input_path

        self.last_discovery = time.monotonic()

    def _refresh_paths_if_needed(self) -> None:
        required = (self.cpu, self.board, self.quadro, self.gpu)
        if (
            time.monotonic() - self.last_discovery >= 30.0
            or any(path is None or not path.exists() for path in required)
        ):
            self.discover()

    def sample(self) -> dict[str, object]:
        self._refresh_paths_if_needed()
        row: dict[str, object] = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "cpu_temp_c": rounded(
                read_float(self.cpu / "temp1_input" if self.cpu else None, 1000.0)
            ),
            "cpu_usage_pct": rounded(psutil.cpu_percent(interval=None)),
        }

        for channel in range(1, 7):
            rpm = read_int(self.board / f"fan{channel}_input" if self.board else None)
            pwm = read_float(self.board / f"pwm{channel}" if self.board else None)
            if channel == 1:
                rpm_column = "cpu_pump_rpm_it8696_fan1"
            elif channel == 5:
                rpm_column = "cpu_fan_rpm_it8696_fan5"
            else:
                rpm_column = f"it8696_fan{channel}_rpm"
            row[rpm_column] = "" if rpm is None else rpm
            row[f"it8696_pwm{channel}_pct"] = rounded(
                pwm * 100.0 / 255.0 if pwm is not None else None
            )

        row.update(
            {
                "gpu_liquid_temp_c_quadro": rounded(
                    read_float(
                        self.quadro / "temp1_input" if self.quadro else None,
                        1000.0,
                    )
                ),
                "gpu_fan_rpm_quadro_fan1": read_int(
                    self.quadro / "fan1_input" if self.quadro else None
                )
                or 0,
                "gpu_pump_rpm_quadro_fan2": read_int(
                    self.quadro / "fan2_input" if self.quadro else None
                )
                or 0,
                "quadro_fan3_rpm": read_int(
                    self.quadro / "fan3_input" if self.quadro else None
                )
                or 0,
                "quadro_fan4_rpm": read_int(
                    self.quadro / "fan4_input" if self.quadro else None
                )
                or 0,
                "quadro_flow_dl_h": read_int(
                    self.quadro / "fan5_input" if self.quadro else None
                )
                or 0,
                "lact_edge_c": rounded(
                    read_float(self.gpu_temperatures.get("edge"), 1000.0)
                ),
                "lact_junction_c": rounded(
                    read_float(self.gpu_temperatures.get("junction"), 1000.0)
                ),
                "lact_mem_c": rounded(
                    read_float(self.gpu_temperatures.get("mem"), 1000.0)
                ),
                "lact_gpu_fan_rpm": read_int(
                    self.gpu / "fan1_input" if self.gpu else None
                )
                or 0,
            }
        )
        return row


def rotate_logs() -> None:
    if not LOG_PATH.exists() or LOG_PATH.stat().st_size < MAX_LOG_BYTES:
        return
    oldest = LOG_PATH.with_name(f"{LOG_PATH.name}.{BACKUP_COUNT}")
    oldest.unlink(missing_ok=True)
    for index in range(BACKUP_COUNT - 1, 0, -1):
        source = LOG_PATH.with_name(f"{LOG_PATH.name}.{index}")
        target = LOG_PATH.with_name(f"{LOG_PATH.name}.{index + 1}")
        if source.exists():
            source.replace(target)
    LOG_PATH.replace(LOG_PATH.with_name(f"{LOG_PATH.name}.1"))


def open_log():
    rotate_logs()
    needs_header = not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0
    handle = LOG_PATH.open("a", encoding="utf-8", newline="", buffering=1)
    writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
    if needs_header:
        writer.writeheader()
        handle.flush()
    return handle, writer


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    lock_handle = LOCK_PATH.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("Telemetry logger is already running", file=sys.stderr)
        return 1

    stop_event = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    sources = HardwareSources()
    handle, writer = open_log()
    next_sample = time.monotonic()
    samples_since_rotation_check = 0
    try:
        while not stop_event.is_set():
            writer.writerow(sources.sample())
            handle.flush()
            samples_since_rotation_check += 1
            if samples_since_rotation_check >= 60:
                samples_since_rotation_check = 0
                if LOG_PATH.stat().st_size >= MAX_LOG_BYTES:
                    handle.close()
                    handle, writer = open_log()

            next_sample += SAMPLE_INTERVAL_SECONDS
            delay = next_sample - time.monotonic()
            if delay < 0:
                next_sample = time.monotonic()
                delay = 0
            stop_event.wait(delay)
    finally:
        handle.close()
        lock_handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
