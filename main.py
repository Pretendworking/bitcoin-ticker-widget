import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, QEvent, QPoint, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QPainter,
    QPen,
    QPainterPath,
    QPixmap,
    QPolygonF,
)
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QPushButton,
    QSlider,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


# Price data now comes only from the CoinMarketCap relay. The provider key stays
# on the relay server; this desktop app contains neither a CoinGecko fallback nor
# a shared CoinMarketCap API key.
DEFAULT_PRICE_URL = "http://127.0.0.1:8765/v1/btc-usd/history"


def resource_path(filename):
    """Find read-only app assets beside a script or a packaged executable."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        base = Path(bundle_dir)
    else:
        base = Path(sys.argv[0]).resolve().parent
    return base / filename


def load_price_url():
    # Environment configuration takes priority for launchers and development.
    configured = os.environ.get("BTC_TICKER_PRICE_URL")
    if configured:
        return configured.strip()
    # A release build can ship this file with its hosted HTTPS relay URL so
    # customers do not need to configure Windows environment variables.
    config_path = resource_path("btc_ticker_config.json")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        configured = config.get("price_url") if isinstance(config, dict) else None
        if isinstance(configured, str) and configured.strip():
            return configured.strip()
    except (OSError, json.JSONDecodeError):
        pass
    return DEFAULT_PRICE_URL


PRICE_URL = load_price_url()
PROVIDER_NAME = "CoinMarketCap"
PROVIDER_URL = "https://coinmarketcap.com/"
ATTRIBUTION_TEXT = "Data provided by CoinMarketCap.com"
DEFAULTS = {
    "background": "#111827",
    "accent": "#63d9ff",
    "border": "#63d9ff",
    "text": "#f4f7fb",
    "up_color": "#55dfa0",
    "down_color": "#ff7777",
    "change_decimals": 2,
    "border_width": 3,
    "width": 370,
    "height": 245,
    "opacity": 96,
    "refresh_seconds": 60,
    "change_period": 24,
    "change_type": "percentage",
    "keep_on_top": False,
    "show_change": True,
    "price_only": False,
}
REFRESH_OPTIONS = (60, 120, 300, 900)
CHANGE_PERIODS = (1, 4, 8, 24)


def handle_command(args):
    if args == ["add", "cmd"]:
        subprocess.Popen(["cmd.exe"])
        return True
    return False


def format_price(price):
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:,.6f}".rstrip("0").rstrip(".")


def as_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def normalize_change_hours(value):
    if isinstance(value, str):
        value = value.strip().lower().removesuffix("h")
    try:
        hours = int(value)
    except (TypeError, ValueError):
        return DEFAULTS["change_period"]
    return hours if hours in CHANGE_PERIODS else DEFAULTS["change_period"]


class ClickableLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SettingsStore:
    def __init__(self):
        app_data = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        self.path = app_data / "Bitcoin Ticker" / "btc_ticker_settings.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Preserve existing preferences when moving from a script/old portable build.
        legacy_path = resource_path("btc_ticker_settings.json")
        if not self.path.exists() and legacy_path.is_file():
            try:
                shutil.copy2(legacy_path, self.path)
            except OSError:
                pass
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def contains(self, key):
        return key in self.data

    def setValue(self, key, value):
        self.data[key] = value

    def sync(self):
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bitcoin Ticker Settings")
        self.setMinimumWidth(350)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.colors = {
            key: QColor(settings.value(key, DEFAULTS[key]))
            for key in (
                "background", "accent", "border", "text", "up_color", "down_color"
            )
        }
        self.setStyleSheet("QDialog { font-family: Segoe UI; }")

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.color_buttons = {}
        for key, label in (
            ("background", "Background color"),
            ("accent", "Accent color"),
            ("border", "Outline color"),
            ("text", "Text color"),
            ("up_color", "Positive change color"),
            ("down_color", "Negative change color"),
        ):
            button = QPushButton()
            button.clicked.connect(
                lambda checked=False, color_key=key: self.choose_color(color_key)
            )
            self.color_buttons[key] = button
            form.addRow(label, button)
            self.update_color_button(key)

        self.width_input = QSpinBox()
        self.width_input.setRange(320, 800)
        self.width_input.setSuffix(" px")
        self.width_input.setValue(int(settings.value("width", DEFAULTS["width"])))
        form.addRow("Width", self.width_input)

        self.height_input = QSpinBox()
        self.height_input.setRange(235, 600)
        self.height_input.setSuffix(" px")
        self.height_input.setValue(int(settings.value("height", DEFAULTS["height"])))
        form.addRow("Height", self.height_input)

        self.border_width = QSpinBox()
        self.border_width.setRange(1, 6)
        self.border_width.setSuffix(" px")
        self.border_width.setValue(
            int(settings.value("border_width", DEFAULTS["border_width"]))
        )
        form.addRow("Outline thickness", self.border_width)

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(60, 100)
        self.opacity_slider.setValue(
            int(settings.value("opacity", DEFAULTS["opacity"]))
        )
        self.opacity_value = QLabel(f"{self.opacity_slider.value()}%")
        self.opacity_slider.valueChanged.connect(
            lambda value: self.opacity_value.setText(f"{value}%")
        )
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(self.opacity_slider, 1)
        opacity_row.addWidget(self.opacity_value)
        form.addRow("Opacity", opacity_row)

        self.refresh_combo = QComboBox()
        for seconds, label in (
            (60, "1 minute"),
            (120, "2 minutes"),
            (300, "5 minutes"),
            (900, "15 minutes"),
        ):
            self.refresh_combo.addItem(label, seconds)
        current_refresh = int(
            settings.value("refresh_seconds", DEFAULTS["refresh_seconds"])
        )
        index = self.refresh_combo.findData(current_refresh)
        self.refresh_combo.setCurrentIndex(max(index, 0))
        form.addRow("Refresh every", self.refresh_combo)

        self.change_period_combo = QComboBox()
        for hours in CHANGE_PERIODS:
            self.change_period_combo.addItem(f"{hours} hour" if hours == 1 else f"{hours} hours", hours)
        current_period = normalize_change_hours(
            settings.value("change_period", DEFAULTS["change_period"])
        )
        period_index = self.change_period_combo.findData(current_period)
        self.change_period_combo.setCurrentIndex(max(period_index, 0))
        form.addRow("Price change period", self.change_period_combo)

        self.change_type_combo = QComboBox()
        self.change_type_combo.addItem("Percentage change", "percentage")
        self.change_type_combo.addItem("Price change ($)", "price")
        current_type = settings.value("change_type", DEFAULTS["change_type"])
        type_index = self.change_type_combo.findData(current_type)
        self.change_type_combo.setCurrentIndex(max(type_index, 0))
        form.addRow("Change display", self.change_type_combo)

        self.change_decimals = QSpinBox()
        self.change_decimals.setRange(0, 4)
        self.change_decimals.setValue(
            int(settings.value("change_decimals", DEFAULTS["change_decimals"]))
        )
        form.addRow("Percentage decimals", self.change_decimals)

        self.keep_on_top = QCheckBox("Keep ticker above other windows")
        self.keep_on_top.setChecked(False)
        self.keep_on_top.setEnabled(False)
        self.keep_on_top.setToolTip(
            "Click the ticker to bring it forward; click another window to put it in front."
        )
        form.addRow("Visibility", self.keep_on_top)

        self.show_change = QCheckBox("Show price change")
        self.show_change.setChecked(
            as_bool(settings.value("show_change", DEFAULTS["show_change"]))
        )
        form.addRow("Display", self.show_change)

        self.price_only = QCheckBox("Show price only (hide labels and chart)")
        self.price_only.setChecked(
            as_bool(settings.value("price_only", DEFAULTS["price_only"]))
        )
        form.addRow("Widget view", self.price_only)
        layout.addLayout(form)

        footer = QHBoxLayout()
        defaults_button = QPushButton("Defaults")
        defaults_button.clicked.connect(self.reset_defaults)
        footer.addWidget(defaults_button)
        reset_position_button = QPushButton("Reset position")
        reset_position_button.setToolTip("Move the ticker back onto the screen")
        reset_position_button.clicked.connect(self.reset_widget_position)
        footer.addWidget(reset_position_button)
        footer.addStretch(1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons)
        layout.addLayout(footer)

    def choose_color(self, key):
        color = QColorDialog.getColor(self.colors[key], self, "Choose a color")
        if color.isValid():
            self.colors[key] = color
            self.update_color_button(key)

    def reset_widget_position(self):
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "reset_widget_position"):
            parent.reset_widget_position()

    def update_color_button(self, key):
        color = self.colors[key]
        foreground = "white" if color.lightness() < 128 else "black"
        self.color_buttons[key].setText(color.name().upper())
        self.color_buttons[key].setStyleSheet(
            f"background-color: {color.name()}; color: {foreground};"
        )

    def reset_defaults(self):
        self.colors = {
            key: QColor(DEFAULTS[key])
            for key in (
                "background", "accent", "border", "text", "up_color", "down_color"
            )
        }
        for key in self.colors:
            self.update_color_button(key)
        self.width_input.setValue(DEFAULTS["width"])
        self.height_input.setValue(DEFAULTS["height"])
        self.border_width.setValue(DEFAULTS["border_width"])
        self.opacity_slider.setValue(DEFAULTS["opacity"])
        self.refresh_combo.setCurrentIndex(0)
        self.change_period_combo.setCurrentIndex(CHANGE_PERIODS.index(24))
        self.change_type_combo.setCurrentIndex(0)
        self.change_decimals.setValue(DEFAULTS["change_decimals"])
        self.keep_on_top.setChecked(DEFAULTS["keep_on_top"])
        self.show_change.setChecked(DEFAULTS["show_change"])
        self.price_only.setChecked(DEFAULTS["price_only"])


class PriceChart(QWidget):
    """Small 24-hour BTC price chart, painted without an extra dependency."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.line_color = QColor("#63d9ff")
        self.setMinimumHeight(44)
        self.setMaximumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_values(self, values, color):
        self.values = [float(value) for value in values if isinstance(value, (int, float))]
        self.line_color = QColor(color)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if len(self.values) < 2:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(2, 4, -2, -4)
        low, high = min(self.values), max(self.values)
        span = high - low or max(abs(high) * 0.001, 1.0)
        points = QPolygonF()
        for index, value in enumerate(self.values):
            x = area.left() + area.width() * index / (len(self.values) - 1)
            y = area.bottom() - (value - low) / span * area.height()
            points.append(QPointF(x, y))
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1, Qt.PenStyle.DotLine))
        painter.drawLine(round(area.left()), round(area.center().y()), round(area.right()), round(area.center().y()))
        path = QPainterPath()
        path.moveTo(points[0])
        for index in range(1, points.size()):
            path.lineTo(points[index])
        fill = QPainterPath(path)
        fill.lineTo(area.right(), area.bottom())
        fill.lineTo(area.left(), area.bottom())
        fill.closeSubpath()
        tint = QColor(self.line_color)
        tint.setAlpha(36)
        painter.fillPath(fill, tint)
        painter.setPen(QPen(self.line_color, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline(points)


class ResizeHandle(QWidget):
    """Corner grip with a visible diagonal resize pattern."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.press_position = None
        self.start_size = None
        self.hovered = False
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setToolTip("Drag to resize the ticker")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 26))
            painter.drawRoundedRect(QRectF(self.rect()), 5, 5)
        parent = self.parentWidget()
        color = parent.colors["accent"] if parent is not None and hasattr(parent, "colors") else QColor("#aab6c5")
        pen = QPen(color, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(7, 17, 17, 7)
        painter.drawLine(12, 18, 18, 12)
        painter.drawLine(16, 18, 18, 16)

    def enterEvent(self, event):
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = event.globalPosition().toPoint()
            self.start_size = self.parentWidget().size()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.press_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            delta = event.globalPosition().toPoint() - self.press_position
            parent = self.parentWidget()
            parent.resize(
                max(parent.minimumWidth(), self.start_size.width() + delta.x()),
                max(parent.minimumHeight(), self.start_size.height() + delta.y()),
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = None
            self.start_size = None
            event.accept()
            return
        super().mouseReleaseEvent(event)


class BitcoinTicker(QWidget):
    def __init__(self):
        super().__init__()
        self.settings = SettingsStore()
        self.drag_position = None
        self.last_success_at = None
        self.request_in_flight = False
        self.last_price = None
        self.quitting = False
        self.settings_dialog_active = False
        self.compact_mode = False
        self.price_only = False
        self.has_tray = QSystemTrayIcon.isSystemTrayAvailable()
        logo_path = resource_path("bitcoin_ticker_icon.png")
        self.bitcoin_logo = QPixmap(str(logo_path)).scaled(
            42,
            42,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.setObjectName("btcTicker")
        self.setWindowTitle("Bitcoin Ticker")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(320, 235)
        self.resize(
            int(self.settings.value("width", DEFAULTS["width"])),
            int(self.settings.value("height", DEFAULTS["height"])),
        )
        self.full_size = (self.width(), self.height())
        if self.settings.contains("x") and self.settings.contains("y"):
            self.move(int(self.settings.value("x")), int(self.settings.value("y")))
        else:
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 24, area.top() + 40)

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(18, 14, 8, 8)
        self.main_layout.setSpacing(6)
        layout = self.main_layout

        self.title_label = QLabel("BITCOIN")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)

        self.pair_label = QLabel("BTC / USD")
        self.pair_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.pair_label)

        self.price_label = QLabel("Loading…")
        self.price_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.price_label, 1)

        self.change_label = QWidget()
        self.change_label.setObjectName("changeControls")
        change_layout = QHBoxLayout(self.change_label)
        change_layout.setContentsMargins(0, 0, 0, 0)
        change_layout.setSpacing(2)
        change_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.change_period_button = ClickableLabel("24H")
        self.change_period_button.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.change_period_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_period_button.setToolTip("Click to cycle through 1H, 4H, 8H, and 24H")
        self.change_period_button.setAccessibleName("Change time period")
        self.change_period_button.clicked.connect(self.cycle_change_period)
        change_layout.addWidget(self.change_period_button)

        self.change_value_button = ClickableLabel("Waiting for quote")
        self.change_value_button.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.change_value_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.change_value_button.setToolTip("Click to switch between price change and percentage change")
        self.change_value_button.setAccessibleName("Switch change display")
        self.change_value_button.clicked.connect(self.toggle_change_type)
        change_layout.addWidget(self.change_value_button)
        layout.addWidget(self.change_label)

        self.chart = PriceChart(self)
        self.chart.setToolTip("Bitcoin price movement over the selected change period")
        layout.addWidget(self.chart)

        self.status_label = QLabel(f"Connecting to {PROVIDER_NAME}…")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

        self.attribution_label = QLabel(
            f'<a href="{PROVIDER_URL}">{ATTRIBUTION_TEXT}</a>'
        )
        self.attribution_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.attribution_label.setOpenExternalLinks(True)
        self.attribution_label.setToolTip("Market data provider")
        layout.addWidget(self.attribution_label)

        footer = QHBoxLayout()
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.open_settings)
        footer.addWidget(self.settings_button)

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh_price)
        footer.addWidget(self.refresh_button)
        self.compact_button = QPushButton("—")
        self.compact_button.setFixedSize(32, 30)
        self.compact_button.setToolTip("Collapse to a small BTC price strip")
        self.compact_button.clicked.connect(self.toggle_compact_mode)
        footer.addStretch(1)

        self.hide_button = QPushButton("×")
        self.hide_button.setFixedSize(32, 30)
        self.hide_button.setToolTip(
            "Quit Bitcoin ticker"
        )
        self.hide_button.clicked.connect(self.quit_app)
        footer.addWidget(self.hide_button)
        footer.addWidget(self.compact_button)
        self.size_grip = ResizeHandle(self)
        footer.addWidget(self.size_grip)
        self.expand_button = QPushButton("⤢")
        self.expand_button.setFixedSize(32, 30)
        self.expand_button.setToolTip("Expand ticker")
        self.expand_button.clicked.connect(self.toggle_compact_mode)
        self.expand_button.hide()
        footer.addWidget(self.expand_button)
        layout.addLayout(footer)

        self.logo_button = QPushButton(self)
        self.logo_button.setObjectName("logoButton")
        self.logo_button.setFlat(True)
        self.logo_button.setIcon(QIcon(self.bitcoin_logo))
        self.logo_button.setIconSize(self.bitcoin_logo.size())
        self.logo_button.setFixedSize(46, 46)
        self.logo_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logo_button.setToolTip("Open Bitcoin market page")
        self.logo_button.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://coinmarketcap.com/currencies/bitcoin/"))
        )

        self.apply_settings()
        self.position_logo_button()
        self.setup_tray()

        self.network = QNetworkAccessManager(self)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh_price)
        self.set_refresh_interval()
        self.refresh_timer.start()
        self.refresh_price()

    def setup_tray(self):
        self.tray_icon = None
        if not self.has_tray:
            return

        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("Bitcoin Ticker — double-click to show")
        menu = QMenu()
        show_action = menu.addAction("Show ticker")
        show_action.triggered.connect(self.show_from_tray)
        settings_action = menu.addAction("Settings")
        settings_action.triggered.connect(self.open_settings)
        refresh_action = menu.addAction("Refresh price")
        refresh_action.triggered.connect(self.refresh_price)
        menu.addSeparator()
        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self.tray_activated)
        self.tray_icon.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_from_tray()

    def show_from_tray(self):
        self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def quit_app(self):
        self.quitting = True
        self.close()
        if self.tray_icon is not None:
            self.tray_icon.hide()
        QApplication.instance().quit()

    def apply_settings(self):
        self.colors = {
            key: QColor(self.settings.value(key, DEFAULTS[key]))
            for key in (
                "background", "accent", "border", "text", "up_color", "down_color"
            )
        }
        self.keep_on_top = False
        self.settings.setValue("keep_on_top", False)
        try:
            self.settings.sync()
        except OSError:
            pass
        self.show_change = as_bool(
            self.settings.value("show_change", DEFAULTS["show_change"])
        )
        self.opacity = int(self.settings.value("opacity", DEFAULTS["opacity"]))

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        self.setWindowFlags(flags)

        accent = self.colors["accent"].name()
        text = self.colors["text"].name()
        self.setStyleSheet(f"""
            QWidget#btcTicker {{
                background-color: transparent;
                color: {text};
                border: none;
                font-family: Segoe UI;
            }}
            QWidget#btcTicker QLabel {{ background: transparent; border: none; }}
            QWidget#btcTicker QPushButton {{
                background-color: rgba(255, 255, 255, 18);
                color: {text};
                border: 1px solid rgba(255, 255, 255, 35);
                border-radius: 8px;
                padding: 5px 10px;
            }}
            QWidget#btcTicker QPushButton:hover {{
                background-color: rgba(255, 255, 255, 42);
                border-color: {accent};
            }}
            QWidget#btcTicker QPushButton#hideButton:hover {{
                background-color: #d94b5b;
                border-color: #d94b5b;
            }}
            QWidget#btcTicker QPushButton#logoButton {{
                background: transparent;
                border: none;
                padding: 0;
            }}
            QWidget#btcTicker QPushButton#logoButton:hover {{ background: transparent; border: none; }}
        """)
        self.title_label.setStyleSheet(
            f"font-size: 13px; font-weight: bold; letter-spacing: 2px; color: {accent};"
        )
        self.pair_label.setStyleSheet(f"font-size: 11px; color: {text};")
        self.price_label.setStyleSheet(
            f"font-size: 34px; font-weight: bold; color: {text};"
        )
        self.change_period_button.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {text}; "
            "background: transparent; border: none; padding: 3px 4px;"
        )
        if self.last_price is None:
            self.change_value_button.setStyleSheet(
                f"font-size: 12px; color: {text}; background: transparent; "
                "border: none; padding: 3px 4px;"
            )
        self.status_label.setStyleSheet(f"font-size: 10px; color: {text};")
        self.attribution_label.setStyleSheet(
            f"font-size: 10px; color: {text}; background: transparent;"
        )
        self.change_label.setVisible(self.show_change)
        if hasattr(self, "hide_button"):
            self.hide_button.setObjectName("hideButton")
        self.apply_display_mode()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = QColor(self.colors["background"])
        background.setAlpha(round(255 * self.opacity / 100))
        pen = QPen(self.colors["border"])
        pen.setWidth(int(self.settings.value("border_width", DEFAULTS["border_width"])))
        painter.setPen(pen)
        painter.setBrush(background)
        inset = pen.widthF() / 2
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(inset, inset, -inset, -inset), 20, 20
        )
    def position_logo_button(self):
        if hasattr(self, "logo_button"):
            self.logo_button.move(self.width() - self.logo_button.width() - 8, 6)
            self.logo_button.raise_()

    def apply_display_mode(self):
        selected = as_bool(
            self.settings.value("price_only", DEFAULTS["price_only"])
        )
        changed = selected != self.price_only
        self.price_only = selected
        self.setMinimumSize(200, 80) if selected else self.setMinimumSize(320, 235)
        if selected:
            self.main_layout.setContentsMargins(8, 6, 4, 6)
            self.main_layout.setSpacing(3)
        if changed:
            if selected:
                self.resize(min(max(self.width(), 250), 320), 100)
            else:
                self.resize(*self.full_size)
        if self.compact_mode:
            return
        self.title_label.setVisible(not selected)
        self.pair_label.setVisible(not selected)
        self.change_label.setVisible(not selected and self.show_change)
        self.chart.setVisible(not selected)
        self.status_label.setVisible(not selected)
        self.attribution_label.show()
        self.logo_button.show()
        self.position_logo_button()

    def set_refresh_interval(self):
        seconds = int(
            self.settings.value("refresh_seconds", DEFAULTS["refresh_seconds"])
        )
        if seconds not in REFRESH_OPTIONS:
            seconds = DEFAULTS["refresh_seconds"]
        self.refresh_timer.setInterval(seconds * 1000)

    def cycle_change_period(self):
        current = normalize_change_hours(
            self.settings.value("change_period", DEFAULTS["change_period"])
        )
        next_index = (CHANGE_PERIODS.index(current) + 1) % len(CHANGE_PERIODS)
        self.settings.setValue("change_period", CHANGE_PERIODS[next_index])
        try:
            self.settings.sync()
        except OSError as error:
            self.status_label.setText("Period changed, but could not be saved")
            self.status_label.setToolTip(str(error))
        if self.last_price is not None:
            self.display_price(self.last_price)

    def toggle_change_type(self):
        current = self.settings.value("change_type", DEFAULTS["change_type"])
        next_type = "price" if current != "price" else "percentage"
        self.settings.setValue("change_type", next_type)
        try:
            self.settings.sync()
        except OSError as error:
            self.status_label.setText("Display changed, but could not be saved")
            self.status_label.setToolTip(str(error))
        if self.last_price is not None:
            self.display_price(self.last_price)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, "main_layout"):
            return
        self.position_logo_button()
        if self.compact_mode:
            return
        narrow = self.width() < 310
        if narrow:
            self.settings_button.setText("⚙")
            self.settings_button.setToolTip("Settings")
            self.settings_button.setFixedWidth(32)
            self.refresh_button.setText("↻")
            self.refresh_button.setToolTip("Refresh price")
            self.refresh_button.setFixedWidth(32)
        else:
            self.settings_button.setText("Settings")
            self.settings_button.setToolTip("Open ticker settings")
            self.settings_button.setMinimumWidth(0)
            self.settings_button.setMaximumWidth(16777215)
            self.refresh_button.setText("Refresh")
            self.refresh_button.setToolTip("Refresh price now")
            self.refresh_button.setMinimumWidth(0)
            self.refresh_button.setMaximumWidth(16777215)
        if self.price_only:
            self.main_layout.setContentsMargins(8 if narrow else 12, 6, 4, 6)
            self.main_layout.setSpacing(3)
            return
        short = self.height() < 205
        self.main_layout.setContentsMargins(
            10 if narrow else 18,
            8 if short else 14,
            6 if narrow else 8,
            6 if short else 8,
        )
        self.main_layout.setSpacing(4 if short else 6)
        self.title_label.setVisible(not short)
        price_size = 22 if self.width() < 280 else 26 if narrow else 34
        self.price_label.setStyleSheet(
            f"font-size: {price_size}px; font-weight: bold; color: {self.colors['text'].name()};"
        )
        self.chart.setMinimumHeight(24 if self.height() < 190 else 28 if short else 44)
        self.chart.setMaximumHeight(30 if short else 56)

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if (
            not self.price_only
            and not self.compact_mode
            and hasattr(dialog.width_input, "setValue")
        ):
            dialog.width_input.setValue(self.width())
            dialog.height_input.setValue(self.height())
        dialog.adjustSize()
        screen = self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            frame = self.frameGeometry()
            x = frame.right() + 12
            if x + dialog.width() > area.right():
                x = frame.left() - dialog.width() - 12
            x = max(area.left(), min(x, area.right() - dialog.width() + 1))
            y = max(area.top(), min(frame.top(), area.bottom() - dialog.height() + 1))
            dialog.move(QPoint(x, y))
        self.settings_dialog_active = True
        try:
            dialog.raise_()
            dialog.activateWindow()
            result = dialog.exec()
        finally:
            self.settings_dialog_active = False
            # Return focus to the ticker after closing Settings. The normal
            # deactivation behavior will lower it again when another window
            # is clicked.
            self.raise_()
            self.activateWindow()
        if result != QDialog.DialogCode.Accepted:
            return

        for key, color in dialog.colors.items():
            self.settings.setValue(key, color.name())
        self.settings.setValue("width", dialog.width_input.value())
        self.settings.setValue("height", dialog.height_input.value())
        self.settings.setValue("border_width", dialog.border_width.value())
        self.settings.setValue("opacity", dialog.opacity_slider.value())
        self.settings.setValue("change_decimals", dialog.change_decimals.value())
        self.settings.setValue("refresh_seconds", dialog.refresh_combo.currentData())
        self.settings.setValue(
            "change_period", dialog.change_period_combo.currentData()
        )
        self.settings.setValue(
            "change_type", dialog.change_type_combo.currentData()
        )
        self.settings.setValue("keep_on_top", dialog.keep_on_top.isChecked())
        self.settings.setValue("show_change", dialog.show_change.isChecked())
        price_only_checkbox = getattr(dialog, "price_only", None)
        price_only_selected = (
            price_only_checkbox.isChecked() if price_only_checkbox is not None else self.price_only
        )
        self.settings.setValue("price_only", price_only_selected)
        save_error = None
        try:
            self.settings.sync()
        except OSError as error:
            save_error = str(error)

        self.full_size = (dialog.width_input.value(), dialog.height_input.value())
        if not price_only_selected:
            self.resize(*self.full_size)
        self.show_change = dialog.show_change.isChecked()
        self.apply_settings()
        self.set_refresh_interval()
        if self.last_price is not None:
            self.display_price(self.last_price)
        self.refresh_price()
        self.show()
        self.raise_()
        if save_error:
            self.status_label.setText("Settings applied, but could not be saved")
            self.status_label.setToolTip(save_error)
        else:
            self.status_label.setText("Settings saved")
            self.status_label.setToolTip(str(self.settings.path))

    def reset_widget_position(self):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.top() + 40)
        self.settings.setValue("x", self.x())
        self.settings.setValue("y", self.y())
        try:
            self.settings.sync()
            self.status_label.setText("Position reset")
        except OSError as error:
            self.status_label.setText("Position reset, but could not be saved")
            self.status_label.setToolTip(str(error))
        self.raise_()

    def toggle_compact_mode(self):
        if not self.compact_mode:
            self.precompact_size = (self.width(), self.height())
            if not self.price_only:
                self.full_size = self.precompact_size
            self.compact_mode = True
            self.title_label.hide()
            self.pair_label.hide()
            self.change_label.hide()
            self.chart.hide()
            self.status_label.hide()
            self.attribution_label.hide()
            self.settings_button.hide()
            self.refresh_button.hide()
            self.compact_button.hide()
            self.size_grip.hide()
            self.hide_button.hide()
            self.expand_button.show()
            self.setMinimumSize(250, 70)
            self.resize(300, 82)
            if self.last_price is None:
                self.price_label.setText("BTC  Loading…")
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 16, area.bottom() - self.height() + 8)
        else:
            self.compact_mode = False
            self.title_label.show()
            self.pair_label.show()
            self.change_label.setVisible(self.show_change)
            self.chart.show()
            self.status_label.show()
            self.attribution_label.show()
            self.settings_button.show()
            self.refresh_button.show()
            self.compact_button.show()
            self.size_grip.show()
            self.hide_button.show()
            self.expand_button.hide()
            if self.price_only:
                self.setMinimumSize(200, 80)
                self.resize(*getattr(self, "precompact_size", (300, 100)))
            else:
                self.setMinimumSize(320, 235)
                self.resize(*self.full_size)
        if self.last_price is not None:
            self.display_price(self.last_price)

    def refresh_price(self):
        if self.request_in_flight:
            return
        request = QNetworkRequest(QUrl(PRICE_URL))
        request.setRawHeader(QByteArray(b"Accept"), QByteArray(b"application/json"))
        request.setRawHeader(
            QByteArray(b"User-Agent"), QByteArray(b"BitcoinTicker/1.0")
        )
        if hasattr(request, "setTransferTimeout"):
            request.setTransferTimeout(15_000)
        reply = self.network.get(request)
        self.request_in_flight = True
        reply.finished.connect(lambda: self.handle_price_reply(reply))

    def handle_price_reply(self, reply):
        self.request_in_flight = False
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError(reply.errorString())
            payload = json.loads(bytes(reply.readAll()).decode("utf-8"))
            points = payload.get("prices") if isinstance(payload, dict) else None
            if not isinstance(points, list) or len(points) < 2:
                raise ValueError(f"{PROVIDER_NAME} did not return enough Bitcoin price history.")
            points = sorted(
                (point for point in points if isinstance(point, list) and len(point) == 2
                 and isinstance(point[0], (int, float)) and isinstance(point[1], (int, float))
                 and point[1] > 0),
                key=lambda point: point[0],
            )
            if len(points) < 2:
                raise ValueError("Bitcoin price history was missing or invalid.")
            latest_timestamp, price = points[-1]
            changes = {}
            for hours in CHANGE_PERIODS:
                target_timestamp = latest_timestamp - hours * 60 * 60 * 1000
                earlier = [point for point in points if point[0] <= target_timestamp]
                if earlier:
                    old_price = earlier[-1][1]
                    changes[hours] = {
                        "percentage": (price / old_price - 1) * 100,
                        "price": price - old_price,
                    }

            self.last_price = {
                "usd": price,
                "changes": changes,
                "updated_at": latest_timestamp,
                "history": [point[1] for point in points],
                "history_points": points,
            }
            self.last_success_at = time.time()
            self.display_price(self.last_price)
            updated = datetime.now().strftime("%I:%M:%S %p")
            market_time = datetime.fromtimestamp(latest_timestamp / 1000).strftime("%I:%M:%S %p")
            self.status_label.setText(f"LIVE  •  Refreshed {updated}")
            self.status_label.setToolTip(f"Latest {PROVIDER_NAME} market sample: {market_time}")
            self.status_label.setStyleSheet("font-size: 10px; color: #55dfa0;")
            if self.tray_icon is not None:
                self.tray_icon.setToolTip(f"Bitcoin: {format_price(price)} USD")
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError, OSError) as error:
            self.show_error(str(error))
        finally:
            reply.deleteLater()

    def display_price(self, quote):
        if self.compact_mode:
            self.price_label.setText(f"BTC  {format_price(quote['usd'])}")
            self.price_label.setStyleSheet(
                f"font-size: 20px; font-weight: bold; color: {self.colors['text'].name()};"
            )
            return
        self.price_label.setText(format_price(quote["usd"]))
        price_size = 20 if self.width() < 250 else 26 if self.width() < 310 else 34
        self.price_label.setStyleSheet(
            f"font-size: {price_size}px; font-weight: bold; color: {self.colors['text'].name()};"
        )
        if self.price_only:
            self.change_label.hide()
            self.chart.hide()
            return
        period = normalize_change_hours(
            self.settings.value("change_period", DEFAULTS["change_period"])
        )
        change_values = quote.get("changes", {}).get(period)
        if not isinstance(change_values, dict):
            self.update_chart(quote, None, period)
            if self.show_change:
                self.change_label.show()
                self.change_period_button.setText(f"{period}H")
                self.change_value_button.setText("Unavailable")
                self.change_value_button.setStyleSheet(
                    f"font-size: 12px; color: {self.colors['text'].name()}; "
                    "background: transparent; border: none; padding: 3px 4px;"
                )
            else:
                self.change_label.hide()
            return
        percentage_change = change_values["percentage"]
        self.update_chart(quote, percentage_change, period)
        if not self.show_change:
            self.change_label.hide()
            return
        self.change_label.show()
        change_type = self.settings.value("change_type", DEFAULTS["change_type"])
        if change_type == "price":
            change = change_values["price"]
            display_change = f"{'+' if change >= 0 else '-'}${abs(change):,.2f}"
        else:
            change = change_values["percentage"]
            decimals = int(
                self.settings.value("change_decimals", DEFAULTS["change_decimals"])
            )
            display_change = f"{change:+.{decimals}f}%"
        arrow = "▲" if change >= 0 else "▼"
        color_key = "up_color" if change >= 0 else "down_color"
        color = self.colors[color_key].name()
        self.change_period_button.setText(f"{period}H")
        self.change_value_button.setText(f"{arrow}  {display_change}")
        self.change_value_button.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {color}; "
            "background: transparent; border: none; padding: 3px 4px;"
        )

    def update_chart(self, quote, percentage_change, period):
        points = quote.get("history_points", [])
        if not points:
            points = [[index, value] for index, value in enumerate(quote.get("history", []))]
        start_time = points[-1][0] - period * 60 * 60 * 1000 if points else 0
        before_period = [point for point in points if point[0] < start_time]
        selected = [point for point in points if point[0] >= start_time]
        if before_period and selected:
            selected.insert(0, before_period[-1])
        if len(selected) < 2:
            selected = points[-2:]
        if percentage_change is None:
            color = self.colors["accent"]
        else:
            color_key = "up_color" if percentage_change >= 0 else "down_color"
            color = self.colors[color_key]
        self.chart.set_values([point[1] for point in selected], color)

    def show_error(self, message):
        if self.last_price is None:
            self.price_label.setText("BTC  —" if self.compact_mode else "Price unavailable")
            self.change_value_button.setText("Waiting for data")
        else:
            age = int(time.time() - self.last_success_at)
            self.status_label.setText(f"STALE  •  Last good quote {self.format_age(age)} ago")
            self.status_label.setToolTip(message)
            self.status_label.setStyleSheet("font-size: 10px; color: #ffb454;")
            return
        self.status_label.setText("OFFLINE  •  Will retry automatically")
        self.status_label.setToolTip(message)
        self.status_label.setStyleSheet("font-size: 10px; color: #ffb454;")

    @staticmethod
    def format_age(seconds):
        if seconds < 60:
            return f"{seconds}s"
        minutes, seconds = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"

    def event(self, event):
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and not self.settings_dialog_active
        ):
            QTimer.singleShot(0, self.lower)
        return super().event(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.raise_()
            self.activateWindow()
            self.drag_position = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = None
            event.accept()

    def closeEvent(self, event):
        saved_width, saved_height = (
            self.full_size if self.compact_mode or self.price_only else (self.width(), self.height())
        )
        if self.tray_icon is not None and self.tray_icon.isVisible() and not self.quitting:
            self.settings.setValue("width", saved_width)
            self.settings.setValue("height", saved_height)
            self.settings.setValue("x", self.x())
            self.settings.setValue("y", self.y())
            try:
                self.settings.sync()
            except OSError:
                pass
            self.hide()
            self.tray_icon.showMessage(
                "Bitcoin ticker is still running",
                "Double-click its tray icon to show it again.",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
            event.ignore()
            return

        self.settings.setValue("width", saved_width)
        self.settings.setValue("height", saved_height)
        self.settings.setValue("x", self.x())
        self.settings.setValue("y", self.y())
        try:
            self.settings.sync()
        except OSError:
            pass
        event.accept()


if __name__ == "__main__":
    if handle_command(sys.argv[1:]):
        sys.exit(0)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(not QSystemTrayIcon.isSystemTrayAvailable())
    ticker = BitcoinTicker()
    ticker.show()
    sys.exit(app.exec())
