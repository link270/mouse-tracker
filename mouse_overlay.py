"""
Mouse tracker overlay that visualizes the cursor, click markers, and drag trails.

Configuration lives in `config.json`; adjust values there to tweak colors, timings, or sizes.
"""

from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Sequence

from pynput import mouse, keyboard
from PySide6.QtCore import QObject, QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget


DEFAULT_CONFIG: Dict[str, object] = {
    "update_interval_ms": 16,
    "persist_duration": 3.0,
    "fade_duration": 0.75,
    "cursor_ring_radius": 24,
    "cursor_ring_thickness": 3,
    "cursor_ring_color": [0, 180, 255, 180],
    "click_radius": 18,
    "click_outline_thickness": 2,
    "click_colors": {
        "left": [0, 255, 128, 200],
        "right": [255, 80, 80, 200],
        "middle": [255, 200, 0, 200],
    },
    "drag_color": [120, 200, 255, 180],
    "drag_line_width": 4,
    "min_point_distance": 2.0,
    "exit_hotkey": "ctrl+shift+q",
    "cursor_idle_timeout": 2.5,
    "cursor_idle_fade_duration": 1.0,
    "click_effect_loop_time": 0.4,
    "click_effect_duration": 0.4,
    "click_effect_fade_duration": 0.15,
    "max_click_markers": 8,
    "cursor_draw_shrink_time": 0.12,
    "cursor_tail_color": [0, 180, 255, 140],
    "cursor_tail_width": 3,
    "cursor_tail_max_age": 0.15,
    "cursor_tail_min_distance": 2.0,
    "cursor_tail_max_length": 40.0,
}

CONFIG_PATH = Path(__file__).with_name("config.json")


class QuitDispatcher(QObject):
    quit_requested = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)


def load_config(path: Path) -> Dict[str, object]:
    override = _load_raw_config(path)
    try:
        merged = _deep_merge(DEFAULT_CONFIG, override)
        return _normalize_config(merged)
    except Exception as exc:  # noqa: BLE001 - best effort fallback for invalid configs
        print(
            f"Warning: failed to apply overrides from {path}: {exc}. "
            "Falling back to defaults.",
            file=sys.stderr,
        )
        merged_defaults = _deep_merge(DEFAULT_CONFIG, {})
        return _normalize_config(merged_defaults)


def _load_raw_config(path: Path) -> Dict[str, object]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Top-level JSON structure must be an object.")
        return data
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(
            f"Warning: could not read {path}: {exc}. Using defaults.",
            file=sys.stderr,
        )
        return {}


def _deep_merge(base: Dict[str, object], override: Dict[str, object]) -> Dict[str, object]:
    result: Dict[str, object] = {}

    for key, value in base.items():
        if isinstance(value, dict):
            result[key] = _deep_merge(value, {})  # copy nested defaults
        elif isinstance(value, list):
            result[key] = list(value)
        else:
            result[key] = value

    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        elif isinstance(value, list):
            result[key] = list(value)
        else:
            result[key] = value

    return result


def _normalize_config(raw: Dict[str, object]) -> Dict[str, object]:
    config = dict(raw)
    config["update_interval_ms"] = int(config["update_interval_ms"])
    config["persist_duration"] = float(config["persist_duration"])
    config["fade_duration"] = float(config["fade_duration"])
    config["cursor_ring_radius"] = float(config["cursor_ring_radius"])
    config["cursor_ring_thickness"] = int(config["cursor_ring_thickness"])
    config["click_radius"] = float(config["click_radius"])
    config["click_outline_thickness"] = int(config["click_outline_thickness"])
    config["drag_line_width"] = int(config["drag_line_width"])
    config["min_point_distance"] = float(config["min_point_distance"])
    config["cursor_idle_timeout"] = float(config.get("cursor_idle_timeout", 0.0))
    config["cursor_idle_fade_duration"] = float(config.get("cursor_idle_fade_duration", 0.0))
    config["click_effect_loop_time"] = float(config.get("click_effect_loop_time", 0.0))
    config["click_effect_duration"] = float(config.get("click_effect_duration", 0.0))
    config["click_effect_fade_duration"] = float(config.get("click_effect_fade_duration", 0.0))
    config["max_click_markers"] = int(config.get("max_click_markers", 0))
    config["cursor_draw_shrink_time"] = float(config.get("cursor_draw_shrink_time", 0.0))
    config["cursor_tail_width"] = int(config.get("cursor_tail_width", 0))
    config["cursor_tail_max_age"] = float(config.get("cursor_tail_max_age", 0.0))
    config["cursor_tail_min_distance"] = float(config.get("cursor_tail_min_distance", 0.0))
    config["cursor_tail_max_length"] = float(config.get("cursor_tail_max_length", 0.0))

    config["cursor_ring_color"] = _to_qcolor(config["cursor_ring_color"])
    config["drag_color"] = _to_qcolor(config["drag_color"])
    config["cursor_tail_color"] = _to_qcolor(config.get("cursor_tail_color", config["cursor_ring_color"]))

    click_colors_raw = config.get("click_colors", {})
    if not isinstance(click_colors_raw, dict):
        raise TypeError("click_colors must be an object mapping button names to colors.")
    config["click_colors"] = {
        name: _to_qcolor(color_value) for name, color_value in click_colors_raw.items()
    }

    hotkey_raw = config.get("exit_hotkey", "")
    config["exit_hotkey"] = _normalize_hotkey(hotkey_raw)

    return config


def _to_qcolor(value: object) -> QColor:
    if isinstance(value, QColor):
        return QColor(value)  # copy to avoid shared state
    if isinstance(value, str):
        color = QColor(value)
        if not color.isValid():
            raise ValueError(f"Invalid color string: {value}")
        return color
    if isinstance(value, Sequence):
        components = list(value)
        if len(components) not in (3, 4):
            raise ValueError(f"Color sequence must have 3 or 4 components, got {components}")
        r, g, b = (int(components[0]), int(components[1]), int(components[2]))
        a = int(components[3]) if len(components) == 4 else 255
        return QColor(r, g, b, a)
    raise TypeError(f"Unsupported color specification: {value!r}")


def _normalize_hotkey(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return ""
        tokens = [
            token.strip().lower()
            for token in cleaned.replace("<", "").replace(">", "").split("+")
            if token.strip()
        ]
        if not tokens:
            return ""
        mapping = {
            "ctrl": "<ctrl>",
            "control": "<ctrl>",
            "shift": "<shift>",
            "alt": "<alt>",
            "option": "<alt>",
            "cmd": "<cmd>",
            "command": "<cmd>",
            "win": "<cmd>",
            "super": "<cmd>",
            "esc": "escape",
            "escape": "escape",
            "enter": "enter",
            "return": "enter",
            "space": "space",
        }
        formatted = [mapping.get(token, token) for token in tokens]
        return "+".join(formatted)
    raise TypeError("exit_hotkey must be defined as a string.")


@dataclass
class ClickMarker:
    position: QPointF
    color: QColor
    button: str
    loop_time: float
    duration: float
    created_at: float = field(default_factory=time.time)
    release_at: Optional[float] = None


@dataclass
class Stroke:
    points: List[QPointF]
    color: QColor
    created_at: float = field(default_factory=time.time)
    active: bool = True


class OverlayWindow(QWidget):
    def __init__(self, config: dict):
        super().__init__()
        self.config = config
        self._lock = Lock()
        self._quit_dispatcher = QuitDispatcher(self)
        self._quit_dispatcher.quit_requested.connect(self._quit_app)

        self.virtual_geometry = self._compute_virtual_geometry()
        self._init_window()

        self.cursor_pos = QPointF(0, 0)
        self.click_initial_position = QPointF(0, 0)
        self.click_markers: List[ClickMarker] = []
        self.completed_strokes: List[Stroke] = []
        self.active_stroke: Optional[Stroke] = None

        self.left_button_down = False
        self._min_point_distance_sq = float(self.config["min_point_distance"]) ** 2
        self._hotkey_listener: Optional[keyboard.GlobalHotKeys] = None
        self._shutdown_requested = False
        self._last_cursor_global: Optional[tuple[int, int]] = None
        self._cursor_last_moved = time.time()
        self._left_press_time: Optional[float] = None
        self.button_down = {"left": False, "right": False, "middle": False}
        self._press_markers: Dict[str, Optional[ClickMarker]] = {"left": None, "right": None, "middle": None}
        self.cursor_tail: List[tuple[float, QPointF]] = []
        self._cursor_tail_min_distance_sq = float(self.config["cursor_tail_min_distance"]) ** 2

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start(self.config["update_interval_ms"])

        self._listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
        )
        self._listener.start()
        self._start_hotkey_listener()

    def _compute_virtual_geometry(self):
        screens = QGuiApplication.screens()
        if not screens:
            return QGuiApplication.primaryScreen().geometry()

        geometry = screens[0].geometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.geometry())
        return geometry

    def _init_window(self):
        self.setGeometry(self.virtual_geometry)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setWindowFlag(Qt.WindowStaysOnTopHint)
        self.setWindowFlag(Qt.Tool)  # Hide from taskbar

    def _global_to_local(self, x: float, y: float) -> QPointF:
        return QPointF(
            x - self.virtual_geometry.x(),
            y - self.virtual_geometry.y(),
        )

    def _on_timer_tick(self):
        global_pos = QCursor.pos()
        coords = (global_pos.x(), global_pos.y())
        now = time.time()
        with self._lock:
            if self._last_cursor_global != coords:
                self._last_cursor_global = coords
                self._cursor_last_moved = now
            self.cursor_pos = self._global_to_local(coords[0], coords[1])
            self._update_cursor_tail(now)
            self._prune_expired_artifacts()
        self.update()

    def _prune_expired_artifacts(self):
        now = time.time()
        persist = self.config["persist_duration"]

        self.click_markers = [
            marker
            for marker in self.click_markers
            if self._click_marker_visible(marker, now)
        ]

        self.completed_strokes = [
            stroke
            for stroke in self.completed_strokes
            if (now - stroke.created_at) <= persist
        ]

    def _on_move(self, x: float, y: float):
        if not self.left_button_down:
            return
        point = self._global_to_local(x, y)
        diff = point - self.click_initial_position
        with self._lock:
            if not self.active_stroke:
                if(abs(diff.x()) > 10 or abs(diff.y()) > 10):
                    stroke_color = QColor(self.config["drag_color"])
                    stroke = Stroke(points=[point], color=stroke_color)
                    self.active_stroke = stroke
                    self._left_press_time = time.time()
                    current_marker = self._press_markers.get("left")
                    if current_marker and current_marker in self.click_markers:
                        self.click_markers.remove(current_marker)
                    self._press_markers["left"] = None
                else:
                    return
            self._append_point_to_active_stroke(point)

    def _on_click(self, x: float, y: float, button, pressed: bool):
        button_name = self._button_name(button)
        if not button_name:
            return

        position = self._global_to_local(x, y)
        now = time.time()
        if pressed:
            if button_name == "left":
                with self._lock:
                    self.left_button_down = True
                    self._left_press_time = now
            with self._lock:
                self.button_down[button_name] = True
            if not self.active_stroke:
                self.click_initial_position = position
                marker_color = self.config["click_colors"].get(button_name)
                if marker_color:
                    duration = self._click_effect_duration(button_name)
                    loop_time = self._click_effect_loop_time(button_name)
                    marker = ClickMarker(
                        position=position,
                        color=QColor(marker_color),
                        button=button_name,
                        loop_time=loop_time,
                        duration=duration,
                    )
                    with self._lock:
                        self.click_markers.append(marker)
                        self._enforce_click_marker_limit()
                        self._press_markers[button_name] = marker
        else:
            self.click_initial_position = QPointF(0, 0)
            if button_name == "left":
                with self._lock:
                    self.left_button_down = False
                    self._left_press_time = None
                    if self.active_stroke:
                        self._append_point_to_active_stroke(position)
                        if len(self.active_stroke.points) > 1:
                            self.active_stroke.active = False
                            self.active_stroke.created_at = time.time()
                            self.completed_strokes.append(self.active_stroke)
                    self.active_stroke = None
            with self._lock:
                self.button_down[button_name] = False
                self._mark_button_released(button_name, now)

    @staticmethod
    def _button_name(button) -> Optional[str]:
        try:
            if button == mouse.Button.left:
                return "left"
            if button == mouse.Button.right:
                return "right"
            if button == mouse.Button.middle:
                return "middle"
        except AttributeError:
            return None
        return None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        now = time.time()
        persist = self.config["persist_duration"]
        fade = max(0.0, min(self.config["fade_duration"], persist))

        with self._lock:
            cursor_pos = QPointF(self.cursor_pos)
            cursor_tail_snapshot = [(ts, QPointF(point)) for ts, point in self.cursor_tail]
            click_markers_snapshot = list(self.click_markers)
            strokes_snapshot = list(self.completed_strokes)
            active_stroke = self.active_stroke.points.copy() if self.active_stroke else None
            active_color = QColor(self.active_stroke.color) if self.active_stroke else None

        self._draw_cursor_tail(painter, cursor_tail_snapshot, now)
        self._draw_cursor_ring(painter, cursor_pos, now, click_markers_snapshot)
        self._draw_click_effects(painter, click_markers_snapshot, now)
        self._draw_strokes(painter, strokes_snapshot, now, persist, fade)
        if active_stroke:
            self._draw_active_stroke(painter, active_stroke, active_color)

    def _draw_cursor_ring(
        self,
        painter: QPainter,
        position: QPointF,
        now: float,
        markers: List[ClickMarker],
    ):
        alpha_scale = self._cursor_idle_alpha(now)
        if alpha_scale <= 0.0:
            return

        draw_progress = self._draw_mode_progress(now)
        fade_factor = self._ring_effect_fade(markers, now)
        color = QColor(self.config["cursor_ring_color"])
        color.setAlpha(int(color.alpha() * alpha_scale * fade_factor))

        base_radius = self.config["cursor_ring_radius"]
        base_thickness = self.config["cursor_ring_thickness"]

        radius = base_radius
        thickness = base_thickness
        left_pressed_idle = self.button_down.get("left") and self.active_stroke is None
        if draw_progress > 0.0:
            shrink = 1.0 - 0.75 * draw_progress
            radius = max(3.0, base_radius * shrink)
            thickness = max(1, int(base_thickness * (1.0 - 0.6 * draw_progress)))
        elif left_pressed_idle:
            radius = max(4.0, base_radius * 0.55)
            thickness = max(1, int(base_thickness * 0.6))

        pen = QPen(color)
        pen.setWidth(thickness)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(position, radius, radius)

        if draw_progress > 0.0:
            dot_color = QColor(color)
            dot_color.setAlpha(int(color.alpha() * (0.65 + 0.35 * draw_progress)))
            dot_radius = max(2.5, base_radius * (0.45 + 0.25 * draw_progress))
            painter.setPen(Qt.NoPen)
            painter.setBrush(dot_color)
            painter.drawEllipse(position, dot_radius, dot_radius)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(pen)
        elif left_pressed_idle:
            painter.setPen(Qt.NoPen)
            painter.setBrush(Qt.NoBrush)

    def _draw_cursor_tail(self, painter: QPainter, samples: List[tuple[float, QPointF]], now: float):
        base_width = self.config["cursor_tail_width"]
        if base_width <= 0 or len(samples) < 2:
            return

        base_color = QColor(self.config["cursor_tail_color"])
        if base_color.alpha() <= 0:
            return

        draw_progress = self._draw_mode_progress(now)
        width = max(2, int(base_width * (0.9 - 0.3 * draw_progress)) + 2)

        pen = QPen(base_color)
        pen.setWidth(width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setBrush(Qt.NoBrush)

        max_age = max(1e-6, self.config["cursor_tail_max_age"])
        alpha_modifier = 0.6 + 0.4 * (1.0 - draw_progress)

        for idx in range(1, len(samples)):
            t0, p0 = samples[idx - 1]
            t1, p1 = samples[idx]
            age = min(now - t0, now - t1)
            if age >= max_age:
                continue
            alpha_scale = max(0.0, 1.0 - (age / max_age))
            segment_color = QColor(base_color)
            segment_color.setAlpha(int(base_color.alpha() * alpha_scale * alpha_modifier))
            pen.setColor(segment_color)
            painter.setPen(pen)
            painter.drawLine(p0, p1)

    def _draw_click_effects(self, painter: QPainter, markers: List[ClickMarker], now: float):
        if not markers:
            return
        for marker in markers:
            progress, strength, completed = self._click_effect_phase(marker, now)
            if completed:
                continue
            if marker.button == "left":
                self._draw_left_click_ripple(painter, marker.position, marker.color, progress, strength)
            elif marker.button == "right":
                self._draw_right_click_corners(painter, marker.position, marker.color, progress, strength)
            elif marker.button == "middle":
                self._draw_middle_click_cross(painter, marker.position, marker.color, progress, strength)
            else:
                self._draw_generic_click_indicator(painter, marker.position, marker.color, progress, strength)

    def _draw_left_click_ripple(
        self,
        painter: QPainter,
        position: QPointF,
        base_color: QColor,
        progress: float,
        strength: float,
    ):
        painter.setBrush(Qt.NoBrush)
        base_radius = self.config["click_radius"]
        outline = max(1, self.config["click_outline_thickness"])
        ripple_count = 3
        step = 0.18
        for idx in range(ripple_count):
            start = idx * step
            if progress < start:
                continue
            local = (progress - start) / max(1e-6, 1.0 - start)
            if local > 1.0:
                continue
            ring_color = QColor(base_color)
            ring_color.setAlpha(int(base_color.alpha() * max(0.0, (1.0 - local)) * strength))
            radius = base_radius * (0.15 + 1.8 * local + 0.28 * idx)
            pen = QPen(ring_color)
            pen.setWidth(max(1, int(outline * (1.0 - 0.5 * local))))
            painter.setPen(pen)
            painter.drawEllipse(position, radius, radius)

    def _draw_right_click_corners(
        self,
        painter: QPainter,
        position: QPointF,
        base_color: QColor,
        progress: float,
        strength: float,
    ):
        pulse = 1.0 + 0.2 * math.sin(math.pi * progress)
        alpha = max(0.0, 1.0 - progress)
        color = QColor(base_color)
        color.setAlpha(int(base_color.alpha() * alpha * strength))

        base_length = self.config["click_radius"] * 1.15 * pulse
        tick_length = base_length * 0.55
        pen = QPen(color)
        pen.setWidth(max(1, self.config["click_outline_thickness"]))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        for dx in (-1, 1):
            for dy in (-1, 1):
                corner = QPointF(position.x() + dx * base_length, position.y() + dy * base_length)
                horiz_end = QPointF(corner.x() - dx * tick_length, corner.y())
                vert_end = QPointF(corner.x(), corner.y() - dy * tick_length)
                painter.drawLine(corner, horiz_end)
                painter.drawLine(corner, vert_end)

    def _draw_middle_click_cross(
        self,
        painter: QPainter,
        position: QPointF,
        base_color: QColor,
        progress: float,
        strength: float,
    ):
        pulse = 1.0 + 0.2 * math.sin(math.pi * progress)
        alpha = max(0.0, 1.0 - progress)
        color = QColor(base_color)
        color.setAlpha(int(base_color.alpha() * alpha * strength))

        half = self.config["click_radius"] * 0.85 * pulse
        pen = QPen(color)
        pen.setWidth(max(1, self.config["click_outline_thickness"]))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        painter.drawLine(
            QPointF(position.x() - half, position.y() - half),
            QPointF(position.x() + half, position.y() + half),
        )
        painter.drawLine(
            QPointF(position.x() - half, position.y() + half),
            QPointF(position.x() + half, position.y() - half),
        )

    def _is_button_effect_active(self, button: str) -> bool:
        if button == "left":
            return self.left_button_down and self.active_stroke is None
        if button in self.button_down:
            return self.button_down[button]
        return False

    def _draw_generic_click_indicator(
        self,
        painter: QPainter,
        position: QPointF,
        base_color: QColor,
        progress: float,
        strength: float,
    ):
        color = QColor(base_color)
        color.setAlpha(int(base_color.alpha() * max(0.0, 1.0 - progress) * strength))
        pen = QPen(color)
        pen.setWidth(max(1, self.config["click_outline_thickness"]))
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        radius = self.config["click_radius"] * (1.0 - 0.3 * progress)
        painter.drawEllipse(position, radius, radius)

    def _click_marker_visible(self, marker: ClickMarker, now: float) -> bool:
        _, strength, completed = self._click_effect_phase(marker, now)
        return not completed

    def _click_effect_phase(self, marker: ClickMarker, now: float) -> tuple[float, float, bool]:
        loop_time = max(1e-6, marker.loop_time)
        elapsed = max(0.0, now - marker.created_at)
        progress = (elapsed / loop_time) % 1.0

        duration = max(0.0, marker.duration)
        active = self._is_button_effect_active(marker.button)
        if active:
            strength = 1.0
            completed = False
        else:
            fade = max(0.0, self.config["click_effect_fade_duration"])
            release_time = marker.release_at
            if release_time is None:
                release_time = marker.created_at
            release_elapsed = max(0.0, now - release_time)

            if release_elapsed <= duration:
                strength = 1.0
                completed = False
            elif fade > 0.0 and release_elapsed <= (duration + fade):
                fade_elapsed = release_elapsed - duration
                strength = max(0.0, 1.0 - (fade_elapsed / fade))
                completed = False
            else:
                strength = 0.0
                completed = True
        return progress, strength, completed

    def _ring_effect_fade(self, markers: List[ClickMarker], now: float) -> float:
        fade = 1.0
        for button in ("right", "middle"):
            marker = self._find_marker(markers, button)
            if not marker:
                continue
            progress, strength, completed = self._click_effect_phase(marker, now)
            if completed and strength <= 0.0:
                continue
            effect_strength = max(strength, progress)
            fade *= max(0.3, 1.0 - 0.6 * effect_strength)
        return fade

    @staticmethod
    def _find_marker(markers: List[ClickMarker], button: str) -> Optional[ClickMarker]:
        for marker in markers:
            if marker.button == button:
                return marker
        return None

    def _draw_strokes(self, painter: QPainter, strokes, now: float, persist: float, fade: float):
        if not strokes:
            return

        pen = QPen()
        pen.setWidth(self.config["drag_line_width"])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        for stroke in strokes:
            age = now - stroke.created_at
            if age > persist or len(stroke.points) < 2:
                continue

            alpha_scale = self._alpha_scale(age, persist, fade)
            color = QColor(stroke.color)
            color.setAlpha(int(color.alpha() * alpha_scale))
            pen.setColor(color)
            painter.setPen(pen)

            path = QPainterPath()
            path.moveTo(stroke.points[0])
            for point in stroke.points[1:]:
                path.lineTo(point)
            painter.drawPath(path)

    def _draw_active_stroke(self, painter: QPainter, points: List[QPointF], color: QColor):
        if len(points) < 2:
            return
        pen = QPen(color)
        pen.setWidth(self.config["drag_line_width"])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)

        path = QPainterPath()
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        painter.drawPath(path)

    def _cursor_idle_alpha(self, now: float) -> float:
        timeout = max(0.0, self.config["cursor_idle_timeout"])
        fade = max(0.0, self.config["cursor_idle_fade_duration"])
        idle_time = max(0.0, now - self._cursor_last_moved)

        if idle_time <= timeout:
            return 1.0
        if fade <= 0.0:
            return 0.0

        excess = idle_time - timeout
        if excess >= fade:
            return 0.0

        return max(0.0, 1.0 - (excess / fade))

    def _draw_mode_progress(self, now: float) -> float:
        if not self.left_button_down or self._left_press_time is None or self.active_stroke is None:
            return 0.0
        shrink_time = max(1e-6, self.config["cursor_draw_shrink_time"])
        return max(0.0, min(1.0, (now - self._left_press_time) / shrink_time))

    def _click_effect_loop_time(self, button: str) -> float:
        loop_time = max(1e-6, self.config["click_effect_loop_time"])
        return loop_time

    def _click_effect_duration(self, button: str) -> float:
        duration = max(0.0, self.config["click_effect_duration"])
        return duration

    @staticmethod
    def _alpha_scale(age: float, persist: float, fade: float) -> float:
        if age <= (persist - fade):
            return 1.0
        if fade <= 0.0:
            return 0.0 if age > persist else 1.0
        remaining = persist - age
        if remaining <= 0.0:
            return 0.0
        return max(0.0, min(1.0, remaining / fade))

    def closeEvent(self, event):
        self._listener.stop()
        self._listener.join(timeout=0.2)
        if self._hotkey_listener:
            self._hotkey_listener.stop()
            self._hotkey_listener = None

        super().closeEvent(event)

    def _mark_button_released(self, button: str, release_time: float):
        tracked = self._press_markers.get(button)
        for marker in self.click_markers:
            if marker.button == button:
                if marker.release_at is None or marker.release_at < release_time:
                    marker.release_at = release_time
        if tracked and tracked in self.click_markers:
            tracked.release_at = release_time
        self._press_markers[button] = None

    def _enforce_click_marker_limit(self):
        limit = self.config.get("max_click_markers", 0)
        if limit and limit > 0:
            excess = len(self.click_markers) - limit
            if excess > 0:
                removed = self.click_markers[:excess]
                del self.click_markers[:excess]
                for button, marker in self._press_markers.items():
                    if marker in removed:
                        self._press_markers[button] = None

    def _append_point_to_active_stroke(self, point: QPointF):
        if not self.active_stroke:
            return
        points = self.active_stroke.points
        if not points:
            points.append(point)
            return
        last_point = points[-1]
        dx = point.x() - last_point.x()
        dy = point.y() - last_point.y()
        if (dx * dx + dy * dy) < self._min_point_distance_sq:
            return
        points.append(point)

    def _update_cursor_tail(self, now: float):
        max_age = self.config["cursor_tail_max_age"]
        width = self.config["cursor_tail_width"]
        if max_age <= 0.0 or width <= 0:
            if self.cursor_tail:
                self.cursor_tail.clear()
            return

        position = QPointF(self.cursor_pos)

        if not self.cursor_tail:
            self.cursor_tail.append((now, position))
        else:
            _, last_point = self.cursor_tail[-1]
            dx = position.x() - last_point.x()
            dy = position.y() - last_point.y()
            if (dx * dx + dy * dy) >= self._cursor_tail_min_distance_sq:
                self.cursor_tail.append((now, position))

        self._trim_cursor_tail(now)

    def _trim_cursor_tail(self, now: float):
        max_age = self.config["cursor_tail_max_age"]
        if max_age <= 0.0:
            self.cursor_tail.clear()
            return

        while self.cursor_tail and (now - self.cursor_tail[0][0]) > max_age:
            self.cursor_tail.pop(0)

        draw_progress = self._draw_mode_progress(now)
        base_length = self.config["cursor_tail_max_length"]
        max_length = max(0.0, base_length * (0.9 - 0.3 * draw_progress))
        if max_length <= 0.0 or len(self.cursor_tail) < 2:
            return

        total_length = 0.0
        cutoff_index = 0
        for idx in range(len(self.cursor_tail) - 1, 0, -1):
            p_curr = self.cursor_tail[idx][1]
            p_prev = self.cursor_tail[idx - 1][1]
            segment = math.hypot(p_curr.x() - p_prev.x(), p_curr.y() - p_prev.y())
            total_length += segment
            if total_length > max_length:
                cutoff_index = idx
                break

        if cutoff_index > 0:
            del self.cursor_tail[:cutoff_index]

    def _start_hotkey_listener(self):
        spec = self.config.get("exit_hotkey")
        if not isinstance(spec, str) or not spec:
            return
        try:
            self._hotkey_listener = keyboard.GlobalHotKeys({spec: self._request_quit})
            self._hotkey_listener.start()
        except Exception as exc:  # noqa: BLE001 - best effort warning, keep app running
            print(
                f"Warning: unable to register exit hotkey '{spec}': {exc}",
                file=sys.stderr,
            )
            self._hotkey_listener = None

    def _request_quit(self):
        self._quit_dispatcher.quit_requested.emit()

    def _quit_app(self):
        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        if self.isVisible():
            self.close()
        app = QApplication.instance()
        if app:
            app.quit()


def main():
    config = load_config(CONFIG_PATH)
    app = QApplication(sys.argv)
    overlay = OverlayWindow(config)
    overlay.show()
    app.aboutToQuit.connect(overlay.close)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
