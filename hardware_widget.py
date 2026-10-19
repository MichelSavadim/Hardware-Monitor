"""Compact Windows hardware widget for one personal workstation."""

from __future__ import annotations

import json
import logging
import math
import re
import subprocess
import sys
import time
import urllib.request
import ctypes
from ctypes import wintypes
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QVBoxLayout, QWidget

from energy_log import EnergySummary
from layered_window import present_layered


BASE = Path(__file__).resolve().parent
CONFIG = BASE / "settings.json"
DESKTOP_STATUS = BASE / "desktop-status.json"
LHM_URL = "http://127.0.0.1:8085/data.json"
LHM_DIR = BASE / "vendor" / "LibreHardwareMonitor"
_lhm_computer = None
_lhm_error_at = -float("inf")
_nvidia_error_at = -float("inf")
ACCENT = QColor("#1fcecb")
PANEL = QColor("#0b4948")

if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.FindWindowExW.argtypes = (wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
    _user32.FindWindowExW.restype = wintypes.HWND
    _user32.GetParent.argtypes = (wintypes.HWND,)
    _user32.GetParent.restype = wintypes.HWND
    _user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.ScreenToClient.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
    _user32.ScreenToClient.restype = wintypes.BOOL
    _user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.GetWindowLongW.restype = ctypes.c_int
    _user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_int)
    _user32.SetWindowLongW.restype = ctypes.c_int
    _user32.SetParent.argtypes = (wintypes.HWND, wintypes.HWND)
    _user32.SetParent.restype = wintypes.HWND
    _user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_uint)
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = (wintypes.HWND,)
    _user32.IsWindow.restype = wintypes.BOOL


def desktop_host() -> int:
    """Find Explorer's icon container, even when Explorer puts it in WorkerW."""
    if sys.platform != "win32":
        return 0
    progman = _user32.FindWindowW("Progman", None)
    host = _user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None)
    if host:
        return host
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def inspect_window(hwnd, _):
        host = _user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if host:
            found.append(host)
            return False
        return True

    _user32.EnumWindows.argtypes = (callback_type, wintypes.LPARAM)
    _user32.EnumWindows(inspect_window, 0)
    return found[0] if found else 0


def native_rect(hwnd: int) -> wintypes.RECT:
    rect = wintypes.RECT()
    _user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect

METRICS = (
    ("vram_use", "VRAM", "MiB"),
    ("vram_clock", "Clock VRAM", "MHz"),
    ("gpu_use", "Uso GPU", "%"),
    ("gpu_temp", "Temperatura GPU", "°C"),
    ("gpu_power", "Potência GPU", "W"),
    ("cpu_use", "Uso CPU", "%"),
    ("cpu_temp", "Temperatura CPU", "°C"),
    ("cpu_power", "Potência CPU", "W"),
    ("ram_use", "RAM", "GiB"),
    ("ram_clock", "Clock RAM", "MHz"),
)


def read_settings() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(settings: dict) -> None:
    CONFIG.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_number(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", str(value))
    return float(match.group().replace(",", ".")) if match else None


def lhm_sensors(node: dict, parents: tuple[str, ...] = ()):
    """Yield leaf readings with their full path from LHM's data.json tree."""
    name = str(node.get("Text", ""))
    path = parents + (name,)
    children = node.get("Children") or []
    if "Value" in node and not children:
        value = parse_number(node["Value"])
        if value is not None:
            yield path, value
    for child in children:
        if isinstance(child, dict):
            yield from lhm_sensors(child, path)


def choose_sensor(sensors: list, category: str, names: tuple[str, ...]) -> float | None:
    candidates = []
    for path, value in sensors:
        full = " ".join(path).lower()
        leaf = path[-1].lower()
        if "cpu" not in full and "ryzen" not in full:
            continue
        if category not in (part.lower() for part in path):
            continue
        for rank, name in enumerate(names):
            if leaf == name or leaf.startswith(name + " "):
                candidates.append((rank, value))
                break
    return min(candidates)[1] if candidates else None


SENSOR_SELECTORS = {
    "cpu_temp": ("temperatures", ("core (tctl/tdie)", "core (tdie)", "core (tctl)", "cpu (tctl/tdie)", "cpu package")),
    "cpu_power": ("powers", ("package", "cpu package", "cpu ppt", "cpu cores")),
    "ram_clock": ("clocks", ("memory", "memory clock")),
}


def selected_lhm_sensors(sensors: list) -> dict[str, float]:
    result = {}
    for key, (category, names) in SENSOR_SELECTORS.items():
        value = choose_sensor(sensors, category, names)
        if value is not None and value > 0:
            result[key] = value
    return result


def read_lhm_http() -> dict[str, float]:
    try:
        with urllib.request.urlopen(LHM_URL, timeout=0.6) as response:
            tree = json.load(response)
    except (OSError, ValueError):
        return {}
    return selected_lhm_sensors(list(lhm_sensors(tree)))


def read_lhm_direct() -> dict[str, float]:
    global _lhm_computer, _lhm_error_at
    if not (LHM_DIR / "LibreHardwareMonitorLib.dll").exists():
        return {}
    try:
        if _lhm_computer is None:
            sys.path.insert(0, str(LHM_DIR))
            import clr
            clr.AddReference(str(LHM_DIR / "LibreHardwareMonitorLib.dll"))
            from LibreHardwareMonitor.Hardware import Computer
            _lhm_computer = Computer()
            _lhm_computer.IsCpuEnabled = True
            _lhm_computer.IsMemoryEnabled = True
            _lhm_computer.Open()
        sensors = []
        for hardware in _lhm_computer.Hardware:
            hardware.Update()
            for sensor in hardware.Sensors:
                if sensor.Value is None:
                    continue
                sensors.append(((str(hardware.Name), str(sensor.SensorType) + "s", str(sensor.Name)), float(sensor.Value)))
        readings = selected_lhm_sensors(sensors)
        if "ram_clock" not in readings:
            # This workstation's Zen 3 SMU table is 0x00380905. LHM 0.9.6
            # leaves it unmapped, but its live report exposes memory clock at 51.
            report = str(_lhm_computer.GetReport())
            if "PM table version: 0x00380905" in report:
                match = re.search(r"(?m)^\s*51\s+0x0CC\s+([-+]?\d+(?:[.,]\d+)?)\s*$", report)
                if match:
                    value = parse_number(match.group(1))
                    if value is not None and value > 0:
                        readings["ram_clock"] = value
        return readings
    except Exception:
        if time.monotonic() - _lhm_error_at > 60:
            logging.exception("Falha ao ler sensores do LibreHardwareMonitor")
            _lhm_error_at = time.monotonic()
        return {}


def read_lhm() -> dict[str, float]:
    readings = read_lhm_direct()
    if len(readings) < len(SENSOR_SELECTORS):
        readings.update(read_lhm_http())
    return readings


def read_nvidia() -> dict[str, float]:
    global _nvidia_error_at
    fields = "memory.used,clocks.mem,utilization.gpu,temperature.gpu,power.draw"
    try:
        completed = subprocess.run(
            ["nvidia-smi", f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        values = [parse_number(part) for part in completed.stdout.splitlines()[0].split(",")]
        return {key: value for key, value in zip(
            ("vram_use", "vram_clock", "gpu_use", "gpu_temp", "gpu_power"), values
        ) if value is not None}
    except (OSError, subprocess.SubprocessError, IndexError):
        if time.monotonic() - _nvidia_error_at > 60:
            logging.exception("Falha ao executar nvidia-smi")
            _nvidia_error_at = time.monotonic()
        return {}


def collect_basic() -> dict[str, float]:
    readings = {
        "cpu_use": psutil.cpu_percent(interval=None),
        "ram_use": psutil.virtual_memory().used / 1024**3,
    }
    readings.update(read_nvidia())
    return readings


def collect() -> dict[str, float]:
    readings = collect_basic()
    readings.update(read_lhm())
    return readings


def outlined_text(painter: QPainter, x: float, y: float, text: str) -> None:
    painter.setPen(QColor(0, 0, 0, 200))
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        painter.drawText(QPoint(int(x + dx), int(y + dy)), text)
    painter.setPen(Qt.GlobalColor.white)
    painter.drawText(QPoint(int(x), int(y)), text)


class MetricRow(QWidget):
    def __init__(self, key: str, label: str, unit: str, parent=None):
        super().__init__(parent)
        self.key, self.label, self.unit = key, label, unit
        self.value: float | None = None
        self.samples: deque[float | None] = deque(maxlen=90)
        self.setFixedHeight(76)

    def add_sample(self, value: float | None) -> None:
        self.value = value
        self.samples.append(value)
        self.repaint()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        outlined_text(painter, 10, 17, self.label)
        if self.value is None:
            display = "—"
        elif self.unit in ("GiB", "W"):
            display = f"{self.value:.1f} {self.unit}"
        else:
            display = f"{self.value:.0f} {self.unit}"
        width = painter.fontMetrics().horizontalAdvance(display)
        outlined_text(painter, self.width() - width - 10, 17, display)

        rect = QRectF(8, 24, self.width() - 16, 46)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 105))
        painter.drawRoundedRect(rect, 5, 5)
        valid = [v for v in self.samples if v is not None and math.isfinite(v)]
        if len(valid) < 2:
            return
        low = 0.0
        high = max(valid) * 1.15
        if self.unit == "%":
            high = 100.0
        if high <= low:
            high = 1.0
        step = (rect.width() - 10) / (self.samples.maxlen - 1)
        path = QPainterPath()
        drawing = False
        for index, value in enumerate(self.samples):
            if value is None or not math.isfinite(value):
                drawing = False
                continue
            x = rect.left() + 5 + index * step
            y = rect.bottom() - 5 - min(1.0, max(0.0, (value - low) / (high - low))) * (rect.height() - 10)
            if drawing:
                path.lineTo(x, y)
            else:
                path.moveTo(x, y)
                drawing = True
        painter.setClipRect(rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(ACCENT, 2.2))
        painter.drawPath(path)


class HardwareWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = read_settings()
        self.enabled = set(self.settings.get("enabled", [key for key, _, _ in METRICS]))
        self.move_mode = bool(self.settings.get("move_mode", True))
        self.on_top = bool(self.settings.get("on_top", False))
        self.desktop_parent = 0
        self.position_ready = False
        self.drag_origin: QPoint | None = None
        self.rows = {}
        self.basic_executor = ThreadPoolExecutor(max_workers=1)
        self.sensor_executor = ThreadPoolExecutor(max_workers=1)
        self.basic_pending = None
        self.sensor_pending = None
        self.latest_power = {}
        self.latest_power_time = {}
        self.logged_metrics = set()
        self.last_present_error = None
        self.last_energy_error_at = -float("inf")
        self.energy_log = EnergySummary(BASE / "consumo_atual.json")
        self.setWindowTitle("Hardware Monitor")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(284)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 30, 8, 8)
        self.layout.setSpacing(3)
        for key, label, unit in METRICS:
            row = MetricRow(key, label, unit, self)
            self.rows[key] = row
            self.layout.addWidget(row)
        self.setup_tray()
        self.update_visibility()
        self.update_flags()
        position = self.settings.get("position")
        if isinstance(position, list) and len(position) == 2:
            self.move(*position)
        else:
            screen = QApplication.primaryScreen().availableGeometry()
            self.move(screen.right() - self.width() - 30, screen.top() + 30)
        self.position_ready = True
        if not self.on_top:
            self.attach_desktop()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(1000)
        self.tick(initial=True)

    def setup_tray(self) -> None:
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(PANEL)
        painter.setPen(QPen(ACCENT, 2))
        painter.drawRoundedRect(QRectF(2, 2, 28, 28), 6, 6)
        painter.setFont(QFont("Segoe UI", 17, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "H")
        painter.end()
        self.tray = QSystemTrayIcon(QIcon(pixmap), self)
        self.menu = QMenu()
        self.move_action = self.menu.addAction("Modo de mover")
        self.move_action.setCheckable(True)
        self.move_action.setChecked(self.move_mode)
        self.move_action.triggered.connect(self.toggle_move)
        self.top_action = self.menu.addAction("Sobrepor janelas")
        self.top_action.setCheckable(True)
        self.top_action.setChecked(self.on_top)
        self.top_action.triggered.connect(self.toggle_top)
        self.menu.addSeparator()
        metrics_menu = self.menu.addMenu("Monitores")
        self.metrics_menu = metrics_menu
        for key, label, _ in METRICS:
            action = metrics_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key in self.enabled)
            action.triggered.connect(lambda checked, metric=key: self.toggle_metric(metric, checked))
        self.menu.addSeparator()
        self.menu.addAction("Sair", QApplication.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("Hardware Monitor")
        self.tray.show()

    def persist(self) -> None:
        position = self.global_position()
        self.settings.update({
            "enabled": [key for key, _, _ in METRICS if key in self.enabled],
            "move_mode": self.move_mode,
            "on_top": self.on_top,
            "position": [position.x(), position.y()],
        })
        save_settings(self.settings)

    def update_visibility(self) -> None:
        count = 0
        for key, row in self.rows.items():
            if key in self.enabled:
                if self.layout.indexOf(row) < 0:
                    self.layout.insertWidget(count, row)
                row.show()
                count += 1
            else:
                row.hide()
                if self.layout.indexOf(row) >= 0:
                    self.layout.removeWidget(row)
        self.setFixedHeight(39 + count * 76 + max(0, count - 1) * 3)
        self.layout.invalidate()
        self.layout.activate()
        self.repaint()
        if self.desktop_parent:
            QTimer.singleShot(0, self.present_desktop)

    def update_flags(self) -> None:
        position = self.global_position() if self.isVisible() else self.pos()
        if self.desktop_parent:
            self.detach_desktop()
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        self.move(position)
        if not self.on_top and self.position_ready:
            self.attach_desktop()

    def global_position(self) -> QPoint:
        if sys.platform == "win32" and self.isVisible():
            rect = native_rect(int(self.winId()))
            return QPoint(rect.left, rect.top)
        return self.pos()

    def attach_desktop(self) -> None:
        """Make the widget a child of the icon layer so Win+D cannot minimize it."""
        if sys.platform != "win32" or self.on_top:
            return
        host = desktop_host()
        if not host:
            self.desktop_parent = 0
            DESKTOP_STATUS.write_text(json.dumps({"state": "host_not_found"}), encoding="utf-8")
            return
        hwnd = int(self.winId())
        if _user32.GetParent(hwnd) == host:
            self.desktop_parent = host
        else:
            rect = native_rect(hwnd)
            style = _user32.GetWindowLongW(hwnd, -16)
            child_style = (style & ~0x80000000) | 0x40000000 | 0x04000000
            _user32.SetWindowLongW(hwnd, -16, ctypes.c_int32(child_style).value)
            _user32.SetParent(hwnd, host)
            if _user32.GetParent(hwnd) != host:
                _user32.SetWindowLongW(hwnd, -16, style)
                self.desktop_parent = 0
                DESKTOP_STATUS.write_text(json.dumps({"state": "set_parent_failed", "error": ctypes.get_last_error()}), encoding="utf-8")
                return
            point = wintypes.POINT(rect.left, rect.top)
            _user32.ScreenToClient(host, ctypes.byref(point))
            _user32.SetWindowPos(hwnd, 0, point.x, point.y,
                                 rect.right - rect.left, rect.bottom - rect.top,
                                 0x0010 | 0x0020 | 0x0040)
            self.desktop_parent = host
        _user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        DESKTOP_STATUS.write_text(json.dumps({"state": "attached", "hwnd": hwnd, "host": int(host),
                                             "parent": int(_user32.GetParent(hwnd))}), encoding="utf-8")

    def detach_desktop(self) -> None:
        if sys.platform != "win32" or not self.desktop_parent:
            return
        hwnd = int(self.winId())
        rect = native_rect(hwnd)
        _user32.SetParent(hwnd, 0)
        style = _user32.GetWindowLongW(hwnd, -16)
        popup_style = (style & ~0x40000000) | 0x80000000
        _user32.SetWindowLongW(hwnd, -16, ctypes.c_int32(popup_style).value)
        _user32.SetWindowPos(hwnd, 0, rect.left, rect.top,
                             rect.right - rect.left, rect.bottom - rect.top,
                             0x0010 | 0x0020 | 0x0040)
        self.desktop_parent = 0

    def ensure_desktop_visible(self) -> None:
        if self.on_top or sys.platform != "win32":
            return
        if not self.desktop_parent or not _user32.IsWindow(self.desktop_parent) or \
                _user32.GetParent(int(self.winId())) != self.desktop_parent:
            self.attach_desktop()
        elif self.isVisible():
            _user32.SetWindowPos(int(self.winId()), 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)

    def toggle_move(self, checked: bool) -> None:
        self.move_mode = checked
        self.update()
        self.persist()

    def toggle_top(self, checked: bool) -> None:
        self.on_top = checked
        self.update_flags()
        self.persist()

    def toggle_metric(self, key: str, checked: bool) -> None:
        if checked:
            self.enabled.add(key)
        else:
            self.enabled.discard(key)
        self.update_visibility()
        self.persist()

    def tick(self, initial: bool = False) -> None:
        if self.basic_pending is not None and self.basic_pending.done():
            try:
                readings = self.basic_pending.result()
            except Exception:
                logging.exception("Falha na coleta básica")
                readings = {}
            for key in ("vram_use", "vram_clock", "gpu_use", "gpu_temp", "gpu_power", "cpu_use", "ram_use"):
                row = self.rows[key]
                row.add_sample(readings.get(key))
            self._log_new_metrics(readings)
            self._remember_power(readings)
            self.basic_pending = None
        if self.sensor_pending is not None and self.sensor_pending.done():
            try:
                readings = self.sensor_pending.result()
            except Exception:
                logging.exception("Falha na coleta do LibreHardwareMonitor")
                readings = {}
            for key in ("cpu_temp", "cpu_power", "ram_clock"):
                self.rows[key].add_sample(readings.get(key))
            self._log_new_metrics(readings)
            self._remember_power(readings)
            self.sensor_pending = None
        if not initial:
            now = time.monotonic()
            powers = {key: self.latest_power[key] for key in ("cpu_power", "gpu_power")
                      if key in self.latest_power and now - self.latest_power_time[key] <= 3}
            try:
                self.energy_log.record(powers)
            except OSError:
                if now - self.last_energy_error_at > 60:
                    logging.exception("Não foi possível gravar o resumo de energia; a interface continuará atualizando")
                    self.last_energy_error_at = now
            self.repaint()
            self.present_desktop()
        if self.basic_pending is None:
            self.basic_pending = self.basic_executor.submit(collect_basic)
        if self.sensor_pending is None:
            self.sensor_pending = self.sensor_executor.submit(read_lhm)

    def _remember_power(self, readings: dict) -> None:
        for key in ("cpu_power", "gpu_power"):
            if key in readings:
                self.latest_power[key] = readings[key]
                self.latest_power_time[key] = time.monotonic()

    def _log_new_metrics(self, readings: dict) -> None:
        newly_available = set(readings) - self.logged_metrics
        if newly_available:
            logging.info("Métricas recebidas pela interface: %s", {key: readings[key] for key in sorted(newly_available)})
            self.logged_metrics.update(newly_available)

    def present_desktop(self) -> None:
        if not self.desktop_parent:
            return
        success, error = present_layered(int(self.winId()), self.grab())
        status = 0 if success else error
        if status != self.last_present_error:
            logging.info("Atualização da superfície do desktop: sucesso=%s, erro=%s", success, error)
            self.last_present_error = status

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(PANEL)
        color.setAlpha(213)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        outlined_text(painter, 16, 21, "HARDWARE MONITOR")
        self.draw_header_icons(painter)

    def draw_header_icons(self, painter: QPainter) -> None:
        gear_x = self.width() - 53
        pin_x = self.width() - 25
        painter.setPen(QPen(Qt.GlobalColor.white, 1.7))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center = QPoint(gear_x, 16)
        painter.drawEllipse(center, 6, 6)
        painter.drawEllipse(center, 2, 2)
        for dx, dy in ((-11, 0), (11, 0), (0, -11), (0, 11), (-8, -8), (8, -8), (-8, 8), (8, 8)):
            length = math.hypot(dx, dy)
            painter.drawLine(
                QPoint(int(gear_x + dx * 0.68), int(16 + dy * 0.68)),
                QPoint(int(gear_x + dx / length * 11), int(16 + dy / length * 11)),
            )
        painter.setPen(QPen(ACCENT if not self.move_mode else Qt.GlobalColor.white, 2))
        painter.drawLine(pin_x - 5, 10, pin_x + 5, 10)
        painter.drawLine(pin_x - 4, 10, pin_x - 3, 18)
        painter.drawLine(pin_x + 4, 10, pin_x + 3, 18)
        painter.drawLine(pin_x - 6, 18, pin_x + 6, 18)
        painter.drawLine(pin_x, 18, pin_x, 27)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if event.position().y() <= 31 and event.position().x() >= self.width() - 68:
            if event.position().x() < self.width() - 39:
                self.metrics_menu.popup(self.mapToGlobal(QPoint(self.width() - 185, 31)))
            else:
                self.move_action.setChecked(not self.move_mode)
                self.toggle_move(not self.move_mode)
            return
        if self.move_mode:
            self.drag_origin = event.globalPosition().toPoint() - self.global_position()

    def mouseMoveEvent(self, event) -> None:
        if self.drag_origin is not None:
            target = event.globalPosition().toPoint() - self.drag_origin
            if self.desktop_parent:
                point = wintypes.POINT(target.x(), target.y())
                _user32.ScreenToClient(self.desktop_parent, ctypes.byref(point))
                self.move(point.x, point.y)
            else:
                self.move(target)

    def mouseReleaseEvent(self, event) -> None:
        if self.drag_origin is not None:
            self.drag_origin = None
            self.persist()

    def closeEvent(self, event) -> None:
        logging.info("Janela do widget fechada")
        self.persist()
        self.timer.stop()
        try:
            self.energy_log.close()
        except OSError:
            logging.exception("Falha ao gravar o último resumo de energia")
        self.basic_executor.shutdown(wait=False, cancel_futures=True)
        self.sensor_executor.shutdown(wait=False, cancel_futures=True)
        self.tray.hide()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    for font_file in ("segoeui.ttf", "segoeuib.ttf"):
        font_path = Path("C:/Windows/Fonts") / font_file
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    app.setQuitOnLastWindowClosed(False)
    widget = HardwareWidget()
    app.aboutToQuit.connect(widget.close)
    widget.show()
    widget.desktop_timer = QTimer(widget)
    widget.desktop_timer.timeout.connect(widget.ensure_desktop_visible)
    widget.desktop_timer.start(400)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
