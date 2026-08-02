#!/usr/bin/python3
"""Small GTK4 hardware monitor for Aquacomputer Quadro and AMD CPUs."""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

try:
    import psutil
except ImportError:  # pragma: no cover - PikaOS already ships psutil
    psutil = None


APP_ID = "local.quadromonitor.QuadroMonitor"
HWMON_ROOT = Path("/sys/class/hwmon")
DEDICATED_GPU_PCI = "0000:03:00.0"


def read_number(path: Optional[Path], scale: float = 1.0) -> Optional[float]:
    if path is None:
        return None
    try:
        return float(path.read_text(encoding="utf-8").strip()) / scale
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return ""


@dataclass
class FanReading:
    channel: int
    rpm: Optional[int]
    power_w: Optional[float]
    voltage_v: Optional[float]
    current_a: Optional[float]
    pwm_percent: Optional[float] = None


@dataclass
class Snapshot:
    cpu_temp: Optional[float] = None
    cpu_usage: Optional[float] = None
    liquid_temp: Optional[float] = None
    gpu_edge_temp: Optional[float] = None
    gpu_junction_temp: Optional[float] = None
    gpu_mem_temp: Optional[float] = None
    gpu_load_percent: Optional[float] = None
    gpu_power_w: Optional[float] = None
    cpu_fan_rpm: Optional[int] = None
    cpu_fan_source: str = "Płyta główna · brak czujnika RPM"
    fans: list[FanReading] = field(default_factory=list)
    motherboard_fans: list[FanReading] = field(default_factory=list)
    flow_dl_h: Optional[int] = None
    quadro_found: bool = False
    motherboard_found: bool = False
    gpu_found: bool = False
    cpu_found: bool = False
    timestamp: float = field(default_factory=time.time)


class HwmonReader:
    """Read stable Linux hwmon attributes without requiring root privileges."""

    def __init__(self) -> None:
        self.cpu_path: Optional[Path] = None
        self.quadro_path: Optional[Path] = None
        self.motherboard_path: Optional[Path] = None
        self.gpu_path: Optional[Path] = None
        self.gpu_temperature_paths: dict[str, Path] = {}
        self.cpu_fan_path: Optional[Path] = None
        self.cpu_fan_source = "Płyta główna · brak czujnika RPM"
        if psutil:
            psutil.cpu_percent(interval=None)
        self.discover()

    @staticmethod
    def _devices() -> list[tuple[Path, str]]:
        devices: list[tuple[Path, str]] = []
        for path in sorted(HWMON_ROOT.glob("hwmon*")):
            name = read_text(path / "name")
            if name:
                devices.append((path, name))
        return devices

    def discover(self) -> None:
        self.cpu_path = None
        self.quadro_path = None
        self.motherboard_path = None
        self.gpu_path = None
        self.gpu_temperature_paths = {}
        self.cpu_fan_path = None
        self.cpu_fan_source = "Płyta główna · brak czujnika RPM"

        devices = self._devices()
        for path, name in devices:
            if name == "k10temp":
                self.cpu_path = path
            elif name == "quadro":
                self.quadro_path = path
            elif name == "it8696":
                self.motherboard_path = path
            elif name == "amdgpu":
                try:
                    pci_address = (path / "device").resolve().name
                except (FileNotFoundError, OSError):
                    pci_address = ""
                if pci_address == DEDICATED_GPU_PCI:
                    self.gpu_path = path

        if self.gpu_path:
            for label_path in self.gpu_path.glob("temp*_label"):
                label = read_text(label_path).lower()
                input_path = label_path.with_name(
                    label_path.name.removesuffix("_label") + "_input"
                )
                if label and input_path.exists():
                    self.gpu_temperature_paths[label] = input_path

        # Prefer a motherboard tachometer explicitly labelled as the CPU fan.
        for path, name in devices:
            for label_path in path.glob("fan*_label"):
                if "cpu" in read_text(label_path).lower():
                    index = label_path.name.removeprefix("fan").removesuffix("_label")
                    input_path = path / f"fan{index}_input"
                    if input_path.exists():
                        self.cpu_fan_path = input_path
                        self.cpu_fan_source = f"{name} · {read_text(label_path)}"
                        break
            if self.cpu_fan_path:
                break

        # Do not substitute a different controller here: on this machine the
        # Quadro belongs to the GPU loop, not to the CPU cooling system.
        # Physical mapping confirmed on this machine: fan1 is the CPU pump,
        # while fan5 is the CPU radiator fan tachometer.
        if self.cpu_fan_path is None and self.motherboard_path:
            fan5 = self.motherboard_path / "fan5_input"
            if fan5.exists():
                self.cpu_fan_path = fan5
                self.cpu_fan_source = "Płyta główna · IT8696 Fan 5"

    def _cpu_temperature(self) -> Optional[float]:
        if not self.cpu_path:
            return None
        # k10temp temp1 is Tctl, the correct control/monitoring temperature.
        return read_number(self.cpu_path / "temp1_input", 1000.0)

    def _gpu_temperature(self, label: str) -> Optional[float]:
        path = self.gpu_temperature_paths.get(label)
        return read_number(path, 1000.0) if path else None

    def snapshot(self) -> Snapshot:
        # hwmon numbers can change after suspend or a USB reconnect.
        if (self.cpu_path and not self.cpu_path.exists()) or (
            self.quadro_path and not self.quadro_path.exists()
        ) or (
            self.motherboard_path and not self.motherboard_path.exists()
        ) or (
            self.gpu_path and not self.gpu_path.exists()
        ):
            self.discover()
        if (
            self.cpu_path is None
            or self.quadro_path is None
            or self.motherboard_path is None
            or self.gpu_path is None
        ):
            self.discover()

        result = Snapshot(
            cpu_temp=self._cpu_temperature(),
            cpu_usage=psutil.cpu_percent(interval=None) if psutil else None,
            gpu_edge_temp=self._gpu_temperature("edge"),
            gpu_junction_temp=self._gpu_temperature("junction"),
            gpu_mem_temp=self._gpu_temperature("mem"),
            gpu_load_percent=read_number(
                self.gpu_path / "device" / "gpu_busy_percent"
                if self.gpu_path
                else None
            ),
            gpu_power_w=read_number(
                self.gpu_path / "power1_average"
                if self.gpu_path
                else None,
                1_000_000.0,
            ),
            cpu_fan_source=self.cpu_fan_source,
            quadro_found=self.quadro_path is not None,
            motherboard_found=self.motherboard_path is not None,
            gpu_found=self.gpu_path is not None,
            cpu_found=self.cpu_path is not None,
        )

        rpm = read_number(self.cpu_fan_path) if self.cpu_fan_path else None
        result.cpu_fan_rpm = round(rpm) if rpm is not None else None

        if self.motherboard_path:
            for channel in range(1, 7):
                board_rpm = read_number(
                    self.motherboard_path / f"fan{channel}_input"
                )
                pwm = read_number(self.motherboard_path / f"pwm{channel}")
                result.motherboard_fans.append(
                    FanReading(
                        channel=channel,
                        rpm=round(board_rpm) if board_rpm is not None else None,
                        power_w=None,
                        voltage_v=None,
                        current_a=None,
                        pwm_percent=(pwm * 100.0 / 255.0) if pwm is not None else None,
                    )
                )

        if not self.quadro_path:
            return result

        result.liquid_temp = read_number(self.quadro_path / "temp1_input", 1000.0)
        flow = read_number(self.quadro_path / "fan5_input")
        result.flow_dl_h = round(flow) if flow is not None else None

        for channel in range(1, 5):
            fan_rpm = read_number(self.quadro_path / f"fan{channel}_input")
            result.fans.append(
                FanReading(
                    channel=channel,
                    rpm=round(fan_rpm) if fan_rpm is not None else None,
                    power_w=read_number(
                        self.quadro_path / f"power{channel}_input", 1_000_000.0
                    ),
                    # Quadro maps Fan 1 to in0, Fan 2 to in1, etc.
                    voltage_v=read_number(
                        self.quadro_path / f"in{channel - 1}_input", 1000.0
                    ),
                    current_a=read_number(
                        self.quadro_path / f"curr{channel}_input", 1000.0
                    ),
                    pwm_percent=None,
                )
            )
        return result


class MetricCard(Gtk.Box):
    def __init__(self, title: str, icon_name: str) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add_css_class("card")
        self.add_css_class("metric-card")

        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.add_css_class("metric-icon")
        title_label = Gtk.Label(label=title, xalign=0)
        title_label.add_css_class("metric-title")
        heading.append(icon)
        heading.append(title_label)
        self.append(heading)

        self.value = Gtk.Label(label="—", xalign=0)
        self.value.add_css_class("metric-value")
        self.append(self.value)

        self.detail = Gtk.Label(label="Oczekiwanie na dane", xalign=0)
        self.detail.add_css_class("dim-label")
        self.detail.set_ellipsize(3)
        self.append(self.detail)

    def update_value(
        self, value: str, detail: str, level: str = "normal"
    ) -> None:
        self.value.set_text(value)
        self.detail.set_text(detail)
        for css_class in ("warning-value", "danger-value", "ok-value"):
            self.value.remove_css_class(css_class)
        if level == "warning":
            self.value.add_css_class("warning-value")
        elif level == "danger":
            self.value.add_css_class("danger-value")
        elif level == "ok":
            self.value.add_css_class("ok-value")


class HistoryGraph(Gtk.DrawingArea):
    def __init__(
        self,
        colors: list[tuple[float, float, float]],
        lower: float = 0.0,
        upper: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.colors = colors
        self.lower = lower
        self.upper = upper
        self.series = [deque(maxlen=120) for _ in colors]
        self.set_content_height(185)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)

    def add_sample(self, *values: Optional[float]) -> None:
        for history, value in zip(self.series, values):
            history.append(float("nan") if value is None else float(value))
        self.queue_draw()

    def _draw(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        dark = Adw.StyleManager.get_default().get_dark()
        grid = (1.0, 1.0, 1.0, 0.08) if dark else (0.0, 0.0, 0.0, 0.08)
        left, top, right, bottom = 8.0, 8.0, 8.0, 10.0
        plot_w = max(1.0, width - left - right)
        plot_h = max(1.0, height - top - bottom)

        cr.set_line_width(1.0)
        cr.set_source_rgba(*grid)
        for index in range(5):
            y = top + (plot_h * index / 4)
            cr.move_to(left, y)
            cr.line_to(left + plot_w, y)
        cr.stroke()

        finite_values = [
            value
            for history in self.series
            for value in history
            if not math.isnan(value)
        ]
        ceiling = self.upper
        if ceiling is None:
            observed = max(finite_values, default=1.0)
            ceiling = max(1000.0, math.ceil(observed * 1.25 / 250.0) * 250.0)
        value_range = max(1.0, ceiling - self.lower)

        for history, color in zip(self.series, self.colors):
            if len(history) < 2:
                continue
            cr.set_source_rgb(*color)
            cr.set_line_width(2.5)
            started = False
            denominator = max(1, history.maxlen - 1)
            start_index = history.maxlen - len(history)
            for offset, value in enumerate(history):
                if math.isnan(value):
                    started = False
                    continue
                x = left + plot_w * (start_index + offset) / denominator
                normalized = min(1.0, max(0.0, (value - self.lower) / value_range))
                y = top + plot_h * (1.0 - normalized)
                if not started:
                    cr.move_to(x, y)
                    started = True
                else:
                    cr.line_to(x, y)
            cr.stroke()


class FanRow(Gtk.Box):
    def __init__(self, channel: int, title_text: Optional[str] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.set_margin_top(7)
        self.set_margin_bottom(7)

        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        label_box.set_size_request(105, -1)
        title = Gtk.Label(label=title_text or f"Fan {channel}", xalign=0)
        title.add_css_class("fan-title")
        self.detail = Gtk.Label(label="—", xalign=0)
        self.detail.add_css_class("caption")
        self.detail.add_css_class("dim-label")
        label_box.append(title)
        label_box.append(self.detail)
        self.append(label_box)

        self.progress = Gtk.ProgressBar()
        self.progress.set_hexpand(True)
        self.progress.set_valign(Gtk.Align.CENTER)
        self.append(self.progress)

        self.rpm = Gtk.Label(label="— RPM", xalign=1)
        self.rpm.add_css_class("fan-rpm")
        self.rpm.set_size_request(105, -1)
        self.append(self.rpm)

    def update_reading(self, reading: FanReading) -> None:
        if reading.rpm is None:
            self.rpm.set_text("— RPM")
            self.progress.set_fraction(0.0)
        else:
            self.rpm.set_text(f"{reading.rpm:,} RPM".replace(",", " "))
            self.progress.set_fraction(min(1.0, reading.rpm / 4000.0))

        details: list[str] = []
        if reading.power_w is not None:
            details.append(f"{reading.power_w:.2f} W")
        if reading.voltage_v is not None:
            details.append(f"{reading.voltage_v:.2f} V")
        if reading.current_a is not None:
            details.append(f"{reading.current_a:.2f} A")
        if reading.pwm_percent is not None:
            details.append(f"PWM {reading.pwm_percent:.0f}%")
        self.detail.set_text("  ·  ".join(details) if details else "Brak telemetrii")


def section_title(title: str, subtitle: str) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    title_label = Gtk.Label(label=title, xalign=0)
    title_label.add_css_class("section-title")
    subtitle_label = Gtk.Label(label=subtitle, xalign=0)
    subtitle_label.add_css_class("dim-label")
    subtitle_label.set_wrap(True)
    box.append(title_label)
    box.append(subtitle_label)
    return box


def legend_item(color_class: str, text: str) -> Gtk.Box:
    item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    dot = Gtk.Label(label="●")
    dot.add_css_class(color_class)
    label = Gtk.Label(label=text)
    label.add_css_class("caption")
    item.append(dot)
    item.append(label)
    return item


class MonitorWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app)
        self.set_title("Quadro Monitor")
        self.set_default_size(1000, 760)
        self.set_size_request(680, 560)
        self.reader = HwmonReader()
        self._timer_id: Optional[int] = None

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        window_title = Adw.WindowTitle(
            title="Quadro Monitor", subtitle="Aquacomputer Quadro + Ryzen 7 9800X3D"
        )
        header.set_title_widget(window_title)

        self.status_label = Gtk.Label(label="● Łączenie…")
        self.status_label.add_css_class("status-pill")
        header.pack_start(self.status_label)

        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Odśwież teraz")
        refresh.connect("clicked", lambda *_args: self.refresh())
        header.pack_end(refresh)
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp()
        clamp.set_maximum_size(1120)
        clamp.set_tightening_threshold(900)
        scrolled.set_child(clamp)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        content.set_margin_top(24)
        content.set_margin_bottom(28)
        content.set_margin_start(20)
        content.set_margin_end(20)
        clamp.set_child(content)

        self.info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.info_box.add_css_class("info-box")
        info_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        self.info_label = Gtk.Label(
            label="Chłodzenie CPU: IT8696 Fan 1 to pompa, Fan 5 to wentylator. Quadro obsługuje osobny układ GPU: Fan 1 to wentylator, Fan 2 to pompa.",
            xalign=0,
        )
        self.info_label.set_wrap(True)
        self.info_label.set_hexpand(True)
        self.info_box.append(info_icon)
        self.info_box.append(self.info_label)
        content.append(self.info_box)

        content.append(section_title("Podgląd na żywo", "Najważniejsze parametry systemu"))
        cards = Gtk.Grid(column_spacing=12, row_spacing=12)
        cards.set_column_homogeneous(True)
        self.cpu_temp_card = MetricCard("Temperatura CPU", "temperature-symbolic")
        self.cpu_usage_card = MetricCard("Obciążenie CPU", "speedometer-symbolic")
        self.cpu_fan_card = MetricCard("CPU Fan", "emblem-system-symbolic")
        self.gpu_fan_card = MetricCard("GPU Fan · Quadro 1", "emblem-system-symbolic")
        self.pump_card = MetricCard("Pompa GPU · Quadro 2", "media-playback-start-symbolic")
        self.liquid_card = MetricCard("Ciecz GPU · Sensor 1", "weather-showers-symbolic")
        self.gpu_edge_card = MetricCard("GPU Edge", "temperature-symbolic")
        self.gpu_junction_card = MetricCard("GPU Junction", "temperature-symbolic")
        self.gpu_mem_card = MetricCard("GPU VRAM · Mem", "temperature-symbolic")
        self.gpu_load_card = MetricCard("Obciążenie GPU", "speedometer-symbolic")
        self.gpu_power_card = MetricCard("Moc GPU", "battery-level-100-symbolic")
        cards.attach(self.cpu_temp_card, 0, 0, 2, 1)
        cards.attach(self.cpu_usage_card, 2, 0, 2, 1)
        cards.attach(self.cpu_fan_card, 4, 0, 2, 1)
        cards.attach(self.liquid_card, 0, 1, 2, 1)
        cards.attach(self.gpu_fan_card, 2, 1, 2, 1)
        cards.attach(self.pump_card, 4, 1, 2, 1)
        cards.attach(self.gpu_edge_card, 0, 2, 2, 1)
        cards.attach(self.gpu_junction_card, 2, 2, 2, 1)
        cards.attach(self.gpu_mem_card, 4, 2, 2, 1)
        cards.attach(self.gpu_load_card, 0, 3, 3, 1)
        cards.attach(self.gpu_power_card, 3, 3, 3, 1)
        content.append(cards)

        graphs_heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        graphs_heading.append(
            section_title("Historia · 2 minuty", "Próbka co sekundę, od najstarszej do najnowszej")
        )
        graphs_heading.get_first_child().set_hexpand(True)
        legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        legend.append(legend_item("cpu-color", "CPU"))
        legend.append(legend_item("liquid-color", "Ciecz"))
        legend.append(legend_item("fan-color", "GPU Fan 1"))
        graphs_heading.append(legend)
        content.append(graphs_heading)

        graph_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        graph_grid.set_column_homogeneous(True)
        temp_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        temp_card.add_css_class("card")
        temp_title = Gtk.Label(label="Temperatury  ·  20–100°C", xalign=0)
        temp_title.add_css_class("graph-title")
        temp_card.append(temp_title)
        self.temp_graph = HistoryGraph(
            colors=[(0.98, 0.39, 0.26), (0.18, 0.62, 0.94)], lower=20.0, upper=100.0
        )
        temp_card.append(self.temp_graph)

        fan_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        fan_card.add_css_class("card")
        fan_title = Gtk.Label(label="GPU Fan · Quadro 1  ·  RPM", xalign=0)
        fan_title.add_css_class("graph-title")
        fan_card.append(fan_title)
        self.fan_graph = HistoryGraph(colors=[(0.42, 0.78, 0.39)], lower=0.0)
        fan_card.append(self.fan_graph)
        graph_grid.attach(temp_card, 0, 0, 1, 1)
        graph_grid.attach(fan_card, 1, 0, 1, 1)
        content.append(graph_grid)

        gpu_graph_heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        gpu_graph_heading.append(
            section_title(
                "Temperatury GPU · 2 minuty",
                "RX 7900 XTX · te same czujniki edge, junction i mem co w LACT",
            )
        )
        gpu_graph_heading.get_first_child().set_hexpand(True)
        gpu_legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        gpu_legend.append(legend_item("gpu-edge-color", "Edge"))
        gpu_legend.append(legend_item("gpu-junction-color", "Junction"))
        gpu_legend.append(legend_item("gpu-mem-color", "Mem"))
        gpu_graph_heading.append(gpu_legend)
        content.append(gpu_graph_heading)

        gpu_temp_graph_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        gpu_temp_graph_card.add_css_class("card")
        gpu_temp_graph_title = Gtk.Label(
            label="RX 7900 XTX  ·  20–110°C", xalign=0
        )
        gpu_temp_graph_title.add_css_class("graph-title")
        gpu_temp_graph_card.append(gpu_temp_graph_title)
        self.gpu_temp_graph = HistoryGraph(
            colors=[
                (0.96, 0.64, 0.16),
                (0.93, 0.20, 0.27),
                (0.55, 0.36, 0.84),
            ],
            lower=20.0,
            upper=110.0,
        )
        gpu_temp_graph_card.append(self.gpu_temp_graph)
        content.append(gpu_temp_graph_card)

        gpu_activity_grid = Gtk.Grid(column_spacing=12, row_spacing=12)
        gpu_activity_grid.set_column_homogeneous(True)

        gpu_load_graph_card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        gpu_load_graph_card.add_css_class("card")
        gpu_load_graph_title = Gtk.Label(
            label="Obciążenie GPU  ·  0–100%", xalign=0
        )
        gpu_load_graph_title.add_css_class("graph-title")
        gpu_load_graph_card.append(gpu_load_graph_title)
        self.gpu_load_graph = HistoryGraph(
            colors=[(0.20, 0.75, 0.95)], lower=0.0, upper=100.0
        )
        gpu_load_graph_card.append(self.gpu_load_graph)

        gpu_power_graph_card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4
        )
        gpu_power_graph_card.add_css_class("card")
        gpu_power_graph_title = Gtk.Label(
            label="Moc GPU PPT  ·  0–400 W", xalign=0
        )
        gpu_power_graph_title.add_css_class("graph-title")
        gpu_power_graph_card.append(gpu_power_graph_title)
        self.gpu_power_graph = HistoryGraph(
            colors=[(0.96, 0.53, 0.18)], lower=0.0, upper=400.0
        )
        gpu_power_graph_card.append(self.gpu_power_graph)

        gpu_activity_grid.attach(gpu_load_graph_card, 0, 0, 1, 1)
        gpu_activity_grid.attach(gpu_power_graph_card, 1, 0, 1, 1)
        content.append(gpu_activity_grid)

        content.append(
            section_title(
                "Płyta główna · IT8696",
                "Aktywne tachometry chłodzenia CPU: pompa na Fan 1 oraz wentylator na Fan 5",
            )
        )
        motherboard_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        motherboard_card.add_css_class("card")
        self.motherboard_rows = [
            FanRow(1, "Pompa CPU · IT8696 Fan 1"),
            FanRow(5, "Wentylator CPU · IT8696 Fan 5"),
        ]
        for index, row in enumerate(self.motherboard_rows):
            motherboard_card.append(row)
            if index != len(self.motherboard_rows) - 1:
                motherboard_card.append(
                    Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                )
        content.append(motherboard_card)

        content.append(
            section_title(
                "Aquacomputer Quadro",
                "Układ GPU: Fan 1 jako wentylator oraz Fan 2 jako pompa",
            )
        )
        quadro_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        quadro_card.add_css_class("card")
        self.fan_rows: list[FanRow] = []
        channel_names = ("Fan 1 · Wentylator GPU", "Fan 2 · Pompa GPU")
        for channel, channel_name in enumerate(channel_names, start=1):
            row = FanRow(channel, channel_name)
            self.fan_rows.append(row)
            quadro_card.append(row)
            if channel != len(channel_names):
                quadro_card.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        flow_separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        quadro_card.append(flow_separator)
        flow_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        flow_row.set_margin_top(12)
        flow_icon = Gtk.Image.new_from_icon_name("weather-showers-symbolic")
        flow_title = Gtk.Label(label="Czujnik przepływu", xalign=0)
        flow_title.set_hexpand(True)
        self.flow_value = Gtk.Label(label="— dL/h", xalign=1)
        self.flow_value.add_css_class("fan-rpm")
        flow_row.append(flow_icon)
        flow_row.append(flow_title)
        flow_row.append(self.flow_value)
        quadro_card.append(flow_row)
        content.append(quadro_card)

        self.updated_label = Gtk.Label(label="", xalign=1)
        self.updated_label.add_css_class("caption")
        self.updated_label.add_css_class("dim-label")
        content.append(self.updated_label)

        toolbar.set_content(scrolled)
        self.set_content(toolbar)

        self.refresh()
        self._timer_id = GLib.timeout_add_seconds(1, self.refresh)

    @staticmethod
    def _temp_level(value: Optional[float], warning: float, danger: float) -> str:
        if value is None:
            return "normal"
        if value >= danger:
            return "danger"
        if value >= warning:
            return "warning"
        return "ok"

    def refresh(self) -> bool:
        snapshot = self.reader.snapshot()

        if snapshot.cpu_temp is None:
            self.cpu_temp_card.update_value("—", "Brak czujnika k10temp")
        else:
            self.cpu_temp_card.update_value(
                f"{snapshot.cpu_temp:.1f}°C",
                "AMD k10temp · Tctl",
                self._temp_level(snapshot.cpu_temp, 75.0, 88.0),
            )

        if snapshot.cpu_usage is None:
            self.cpu_usage_card.update_value("—", "Brak modułu psutil")
        else:
            usage_level = "danger" if snapshot.cpu_usage >= 95 else "warning" if snapshot.cpu_usage >= 80 else "ok"
            self.cpu_usage_card.update_value(
                f"{snapshot.cpu_usage:.0f}%", "16 wątków logicznych", usage_level
            )

        if snapshot.cpu_fan_rpm is None:
            self.cpu_fan_card.update_value("Niedostępny", snapshot.cpu_fan_source)
        else:
            fan_level = "warning" if snapshot.cpu_fan_rpm == 0 else "ok"
            self.cpu_fan_card.update_value(
                f"{snapshot.cpu_fan_rpm:,} RPM".replace(",", " "),
                snapshot.cpu_fan_source,
                fan_level,
            )

        gpu_fan = snapshot.fans[0] if snapshot.fans else None
        if gpu_fan is None or gpu_fan.rpm is None:
            self.gpu_fan_card.update_value("—", "GPU · Quadro Fan 1")
        else:
            gpu_fan_level = "warning" if gpu_fan.rpm == 0 else "ok"
            self.gpu_fan_card.update_value(
                f"{gpu_fan.rpm:,} RPM".replace(",", " "),
                "GPU · Quadro Fan 1",
                gpu_fan_level,
            )

        pump = snapshot.fans[1] if len(snapshot.fans) > 1 else None
        if pump is None or pump.rpm is None:
            self.pump_card.update_value("—", "Quadro · Fan 2")
        else:
            pump_level = "danger" if pump.rpm == 0 else "ok"
            self.pump_card.update_value(
                f"{pump.rpm:,} RPM".replace(",", " "),
                "Quadro · Fan 2",
                pump_level,
            )

        if snapshot.liquid_temp is None:
            self.liquid_card.update_value("—", "Quadro niedostępne")
        else:
            self.liquid_card.update_value(
                f"{snapshot.liquid_temp:.1f}°C",
                "Aquacomputer Quadro",
                self._temp_level(snapshot.liquid_temp, 40.0, 48.0),
            )

        gpu_metrics = (
            (
                self.gpu_edge_card,
                snapshot.gpu_edge_temp,
                "LACT · Edge",
                75.0,
                90.0,
            ),
            (
                self.gpu_junction_card,
                snapshot.gpu_junction_temp,
                "LACT · Junction / Hotspot",
                90.0,
                105.0,
            ),
            (
                self.gpu_mem_card,
                snapshot.gpu_mem_temp,
                "LACT · Mem / VRAM",
                85.0,
                100.0,
            ),
        )
        for card, value, detail, warning, danger in gpu_metrics:
            if value is None:
                card.update_value("—", "RX 7900 XTX niedostępny")
            else:
                card.update_value(
                    f"{value:.1f}°C",
                    detail,
                    self._temp_level(value, warning, danger),
                )

        if snapshot.gpu_load_percent is None:
            self.gpu_load_card.update_value("—", "amdgpu niedostępne")
        else:
            self.gpu_load_card.update_value(
                f"{snapshot.gpu_load_percent:.0f}%",
                "amdgpu · gpu_busy_percent",
                "ok",
            )

        if snapshot.gpu_power_w is None:
            self.gpu_power_card.update_value("—", "Sensor PPT niedostępny")
        else:
            self.gpu_power_card.update_value(
                f"{snapshot.gpu_power_w:.1f} W",
                "amdgpu · PPT power average",
                self._temp_level(snapshot.gpu_power_w, 330.0, 385.0),
            )

        for row, reading in zip(self.fan_rows, snapshot.fans):
            row.update_reading(reading)

        board_by_channel = {
            reading.channel: reading for reading in snapshot.motherboard_fans
        }
        for row, channel in zip(self.motherboard_rows, (1, 5)):
            reading = board_by_channel.get(channel)
            if reading is not None:
                row.update_reading(reading)

        if snapshot.flow_dl_h is None:
            self.flow_value.set_text("— dL/h")
        else:
            self.flow_value.set_text(f"{snapshot.flow_dl_h} dL/h")

        self.temp_graph.add_sample(snapshot.cpu_temp, snapshot.liquid_temp)
        self.fan_graph.add_sample(gpu_fan.rpm if gpu_fan is not None else None)
        self.gpu_temp_graph.add_sample(
            snapshot.gpu_edge_temp,
            snapshot.gpu_junction_temp,
            snapshot.gpu_mem_temp,
        )
        self.gpu_load_graph.add_sample(snapshot.gpu_load_percent)
        self.gpu_power_graph.add_sample(snapshot.gpu_power_w)

        if (
            snapshot.quadro_found
            and snapshot.cpu_found
            and snapshot.motherboard_found
            and snapshot.gpu_found
        ):
            self.status_label.set_text("● Aktywny")
            self.status_label.remove_css_class("status-error")
            self.status_label.add_css_class("status-ok")
        else:
            missing = []
            if not snapshot.quadro_found:
                missing.append("Quadro")
            if not snapshot.cpu_found:
                missing.append("CPU")
            if not snapshot.motherboard_found:
                missing.append("IT8696")
            if not snapshot.gpu_found:
                missing.append("GPU")
            self.status_label.set_text(f"● Brak: {', '.join(missing)}")
            self.status_label.remove_css_class("status-ok")
            self.status_label.add_css_class("status-error")

        timestamp = time.strftime("%H:%M:%S", time.localtime(snapshot.timestamp))
        self.updated_label.set_text(f"Ostatni odczyt: {timestamp}  ·  odświeżanie co 1 s")
        return GLib.SOURCE_CONTINUE


class QuadroMonitorApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("activate", self.on_activate)

    @staticmethod
    def _load_css() -> None:
        css_path = Path(__file__).with_name("style.css")
        provider = Gtk.CssProvider()
        provider.load_from_path(str(css_path))
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def on_activate(self, _app: Adw.Application) -> None:
        self._load_css()
        window = self.get_active_window()
        if window is None:
            window = MonitorWindow(self)
        window.present()


def main() -> int:
    app = QuadroMonitorApplication()
    return app.run(None)


if __name__ == "__main__":
    raise SystemExit(main())
