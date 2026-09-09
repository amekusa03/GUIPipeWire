import os
import re
import sys
from pathlib import Path

import subprocess
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QIcon, QColor, QPalette
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QLabel, QComboBox, QCheckBox, QSlider, QSpinBox,
    QPushButton, QTextEdit, QGroupBox, QGridLayout, QMessageBox,
    QSplitter, QStatusBar, QFrame, QScrollArea, QStyleFactory, QProgressBar
)

from pipewire_config import PipeWireConfigManager, STANDARD_RATES, STANDARD_QUANTUMS
from pipewire_service import PipeWireServiceManager
from pipewire_agc import PipeWireAGCManager, MicrophoneMonitorThread
from pipewire_calibrator import CalibrationWorkerThread, apply_51ch_channel_volumes, get_51ch_channel_volumes, CHANNEL_NAMES



# ============================================================
# 5.1ch Upmix constants & helper functions
# ============================================================

UPMIX_CONF_PATHS = [
    os.path.expanduser("~/.config/pipewire/pipewire-pulse.conf.d/20-upmix.conf"),
    os.path.expanduser("~/.config/pipewire/client.conf.d/20-upmix.conf"),
]

UPMIX_PRESETS = {
    "Standard (PSD)": {
        "method": "psd",
        "lfe_cutoff": 150,
        "rear_delay": 0.0,
        "fc_cutoff": 12000,
        "stereo_widen": 0.0,
    },
    "Movies / Surround Focus": {
        "method": "psd",
        "lfe_cutoff": 120,
        "rear_delay": 15.0,
        "fc_cutoff": 10000,
        "stereo_widen": 0.1,
    },
    "Music / Natural Ambience": {
        "method": "psd",
        "lfe_cutoff": 80,
        "rear_delay": 5.0,
        "fc_cutoff": 0,
        "stereo_widen": 0.2,
    },
    "Simple Mode": {
        "method": "simple",
        "lfe_cutoff": 120,
        "rear_delay": 0.0,
        "fc_cutoff": 0,
        "stereo_widen": 0.0,
    },
}
UPMIX_CUSTOM_PRESET_NAME = "Custom (Manual Setup)"


def upmix_is_enabled() -> bool:
    """Return True if both config files exist."""
    return all(os.path.exists(p) for p in UPMIX_CONF_PATHS)


def upmix_parse_config(filepath: str) -> dict:
    """Read config file and return parameter dictionary."""
    params = {
        "method": "psd",
        "lfe_cutoff": 150,
        "rear_delay": 0.0,
        "fc_cutoff": 0,
        "stereo_widen": 0.0,
    }
    if not os.path.exists(filepath):
        return params
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r"channelmix\.upmix-method\s*=\s*(\w+)", content)
        if m:
            params["method"] = m.group(1)
        m = re.search(r"channelmix\.lfe-cutoff\s*=\s*(\d+)", content)
        if m:
            params["lfe_cutoff"] = int(m.group(1))
        m = re.search(r"channelmix\.rear-delay\s*=\s*([\d.]+)", content)
        if m:
            params["rear_delay"] = float(m.group(1))
        m = re.search(r"channelmix\.fc-cutoff\s*=\s*(\d+)", content)
        if m:
            params["fc_cutoff"] = int(m.group(1))
        m = re.search(r"channelmix\.stereo-widen\s*=\s*([\d.]+)", content)
        if m:
            params["stereo_widen"] = float(m.group(1))
    except Exception:
        pass
    return params


def upmix_generate_config(params: dict) -> str:
    """Generate PipeWire config file string from parameter dictionary."""
    lines = [
        "stream.properties = {",
        "    channelmix.upmix = true",
        f"    channelmix.upmix-method = {params.get('method', 'psd')}",
        f"    channelmix.lfe-cutoff = {int(params.get('lfe_cutoff', 150))}",
    ]
    rear_delay = float(params.get("rear_delay", 0.0))
    if rear_delay > 0:
        lines.append(f"    channelmix.rear-delay = {rear_delay:.1f}")
    fc_cutoff = int(params.get("fc_cutoff", 0))
    if fc_cutoff > 0:
        lines.append(f"    channelmix.fc-cutoff = {fc_cutoff}")
    stereo_widen = float(params.get("stereo_widen", 0.0))
    if stereo_widen > 0:
        lines.append(f"    channelmix.stereo-widen = {stereo_widen:.2f}")
    lines.append("}")
    return "\n".join(lines) + "\n"


class PwTopWorker(QThread):
    """Worker thread running pw-top in background and emitting results."""
    result_ready = pyqtSignal(str)

    def run(self):
        try:
            p = subprocess.run(
                ["pw-top", "-b", "-n", "2"],
                capture_output=True, text=True, timeout=6
            )
            raw = p.stdout
            blocks = raw.split("S   ID  QUANT")
            if len(blocks) > 1:
                self.result_ready.emit(("S   ID  QUANT" + blocks[-1]).strip())
            else:
                self.result_ready.emit(raw.strip() or p.stderr.strip())
        except Exception as e:
            self._mic_log(f"Error playing test tone: {e}")
            self.result_ready.emit(f"Error running pw-top: {e}")


class GUIPipeWireWindow(QMainWindow):
    """Main Window for PipeWire GUI Configuration Tool."""

    BASE_TITLE = "GUIPipeWire"

    def __init__(self):
        super().__init__()
        self.config_mgr = PipeWireConfigManager()
        self.service_mgr = PipeWireServiceManager()
        self.agc_mgr = PipeWireAGCManager()
        self.mic_monitor_thread = None
        self._unsaved_changes = False

        self.setWindowTitle(self.BASE_TITLE)
        self.resize(880, 680)

        # Apply dark mode style
        self._apply_dark_theme()

        # Build UI components
        self._init_ui()

        # Check required CLI commands on startup
        self._check_required_commands()

        # Load configurations
        self.load_all_settings()
        # Unsaved changes flag is clear on initial load
        self._unsaved_changes = False
        self._update_title()

        # Timer 1: Update pw-metadata every 3 seconds (lightweight)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(3000)
        self.status_timer.timeout.connect(self.update_live_status)
        self.status_timer.start()

        # Timer 2: Update pw-top every 6 seconds (background thread)
        self.pwtop_timer = QTimer(self)
        self.pwtop_timer.setInterval(6000)
        self.pwtop_timer.timeout.connect(self.trigger_pw_top_update)
        self.pwtop_timer.start()

        # pw-top worker
        self._pwtop_worker = None

        # Initial updates
        self.update_live_status()
        self.trigger_pw_top_update()

    def _apply_dark_theme(self):
        """Configure Catppuccin Mocha-inspired dark mode theme."""
        self.setStyleSheet("""
            QMainWindow {
                background-color: #1e1e2e;
                color: #cdd6f4;
            }
            QWidget {
                font-family: 'Segoe UI', 'Noto Sans JP', sans-serif;
                font-size: 10pt;
                color: #cdd6f4;
            }
            QTabWidget::pane {
                border: 1px solid #45475a;
                border-radius: 8px;
                background-color: #1e1e2e;
                top: -1px;
            }
            QTabBar::tab {
                background: #181825;
                color: #a6adc8;
                border: 1px solid #313244;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 10px 18px;
                margin-right: 2px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background: #313244;
                color: #89b4fa;
                border-color: #89b4fa;
            }
            QTabBar::tab:hover:!selected {
                background: #28293d;
                color: #cdd6f4;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #45475a;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 14px;
                background-color: #181825;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
                color: #89b4fa;
            }
            QPushButton {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #45475a;
                border-color: #89b4fa;
            }
            QPushButton:pressed {
                background-color: #585b70;
            }
            
            QProgressBar {
                border: 1px solid #45475a;
                border-radius: 6px;
                text-align: center;
                color: #ffffff;
                background-color: #11111b;
                height: 20px;
                font-size: 9pt;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #a6e3a1, stop:0.7 #f9e2af, stop:0.9 #fab387, stop:1.0 #f38ba8);
                border-radius: 5px;
            }
            QPushButton#btn_primary {
                background-color: #89b4fa;
                color: #11111b;
                font-size: 11pt;
                padding: 10px 24px;
                border-radius: 6px;
            }
            QPushButton#btn_primary:hover {
                background-color: #b4befe;
            }
            QPushButton#btn_danger {
                background-color: #f38ba8;
                color: #11111b;
            }
            QPushButton#btn_danger:hover {
                background-color: #f5e0dc;
            }
            QComboBox, QSpinBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QComboBox:hover, QSpinBox:hover {
                border-color: #89b4fa;
            }
            QComboBox::drop-down {
                border: none;
            }
            QCheckBox {
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 4px;
                border: 1px solid #45475a;
                background-color: #313244;
            }
            QCheckBox::indicator:checked {
                background-color: #89b4fa;
                border-color: #89b4fa;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #313244;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #89b4fa;
                width: 18px;
                height: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider::handle:horizontal:hover {
                background: #b4befe;
            }
            QTextEdit {
                background-color: #11111b;
                color: #a6e3a1;
                font-family: 'Consolas', 'Courier New', monospace;
                border: 1px solid #313244;
                border-radius: 6px;
                padding: 8px;
            }
            QLabel#status_badge {
                padding: 4px 10px;
                border-radius: 12px;
                font-weight: bold;
            }
        """)


    def closeEvent(self, event):
        if hasattr(self, 'mic_monitor_thread') and self.mic_monitor_thread:
            self.mic_monitor_thread.stop()
        event.accept()

    def _init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # Top header
        header_layout = QHBoxLayout()
        title_label = QLabel("PipeWire Audio GUI Configuration")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # Service status badge
        self.lbl_status_badge = QLabel(" Checking status... ")
        self.lbl_status_badge.setObjectName("status_badge")
        self.lbl_status_badge.setStyleSheet("background-color: #45475a; color: #cdd6f4;")
        header_layout.addWidget(self.lbl_status_badge)

        main_layout.addLayout(header_layout)

        # Tab widget
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_preset_tab(), "⚡ Presets")
        self.tabs.addTab(self._create_upmix_tab(), "🔊 5.1ch Upmix & Calibration")
        self.tabs.addTab(self._create_mic_agc_tab(), "🎙️ Mic & AGC")
        self.tabs.addTab(self._create_clock_tab(), "⏱️ Clock & Rates")
        self.tabs.addTab(self._create_resample_tab(), "🎛️ Client Resample")
        self.tabs.addTab(self._create_status_tab(), "📊 Monitor & Logs")
        self.tabs.addTab(self._create_raw_editor_tab(), "📝 Config Editor")
        main_layout.addWidget(self.tabs)

        # Connect change signals to _mark_unsaved
        self._connect_change_signals()

        # Action footer (Save & Restart)
        footer_layout = QHBoxLayout()

        self.btn_reset = QPushButton("Reset to Default")
        self.btn_reset.setObjectName("btn_danger")
        self.btn_reset.clicked.connect(self.on_reset_defaults)
        footer_layout.addWidget(self.btn_reset)

        footer_layout.addStretch()

        self.btn_restart_service = QPushButton("🔄 Restart PipeWire Only (systemctl)")
        self.btn_restart_service.clicked.connect(self.on_restart_service_only)
        footer_layout.addWidget(self.btn_restart_service)

        self.btn_save_apply = QPushButton("💾 Save Settings & Restart PipeWire")
        self.btn_save_apply.setObjectName("btn_primary")
        self.btn_save_apply.clicked.connect(self.on_save_and_apply)
        footer_layout.addWidget(self.btn_save_apply)

        main_layout.addLayout(footer_layout)

        self.setCentralWidget(main_widget)

    # -------------------------------------------------------------
    # Tab 1: Clock & Sample Rates
    # -------------------------------------------------------------
    def _create_clock_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        # Default clock rate
        group_rate = QGroupBox("Default Clock Rate (default.clock.rate)")
        layout_rate = QVBoxLayout(group_rate)

        hbox_rate = QHBoxLayout()
        lbl_rate = QLabel("Standard Clock Rate (Hz):")
        self.cmb_default_rate = QComboBox()
        for rate in STANDARD_RATES:
            label = f"{rate} Hz ({rate/1000:.1f} kHz)"
            if rate == 48000:
                label += " ★ [Default / Recommended]"
            elif rate == 44100:
                label += " (CD Standard)"
            elif rate == 96000:
                label += " (Hi-Res)"
            self.cmb_default_rate.addItem(label, rate)

        idx_48k = self.cmb_default_rate.findData(48000)
        if idx_48k >= 0:
            self.cmb_default_rate.setCurrentIndex(idx_48k)

        hbox_rate.addWidget(lbl_rate)
        hbox_rate.addWidget(self.cmb_default_rate)
        hbox_rate.addStretch()
        layout_rate.addLayout(hbox_rate)

        lbl_rate_desc = QLabel("💡 PipeWire default and recommended setting is 48000 Hz (48 kHz).\nOffers optimal compatibility and fewest issues with video playback, gaming, and DAC hardware.")
        lbl_rate_desc.setStyleSheet("color: #a6adc8; font-size: 9pt;")
        layout_rate.addWidget(lbl_rate_desc)

        layout.addWidget(group_rate)

        # Allowed sample rates (default.clock.allowed-rates)
        group_allowed = QGroupBox("Allowed Sample Rates (default.clock.allowed-rates)")
        layout_allowed = QVBoxLayout(group_allowed)
        lbl_allowed_info = QLabel("Allowed sample rates for dynamic automatic switching based on active audio streams:")
        lbl_allowed_info.setStyleSheet("color: #a6adc8;")
        layout_allowed.addWidget(lbl_allowed_info)

        grid_rates = QGridLayout()
        self.rate_checkboxes = {}
        for idx, rate in enumerate(STANDARD_RATES):
            cb = QCheckBox(f"{rate} Hz ({rate/1000:.1f} kHz)")
            grid_rates.addWidget(cb, idx // 4, idx % 4)
            self.rate_checkboxes[rate] = cb

        layout_allowed.addLayout(grid_rates)

        # Helper selection buttons
        btn_layout = QHBoxLayout()
        btn_all = QPushButton("Select All")
        btn_all.clicked.connect(lambda: self._set_rate_checkboxes(STANDARD_RATES))
        btn_std = QPushButton("Standard (44.1k / 48k)")
        btn_std.clicked.connect(lambda: self._set_rate_checkboxes([44100, 48000]))
        btn_hires = QPushButton("Hi-Res Focus (44.1k - 192k)")
        btn_hires.clicked.connect(lambda: self._set_rate_checkboxes([44100, 48000, 88200, 96000, 176400, 192000]))

        btn_layout.addWidget(btn_all)
        btn_layout.addWidget(btn_std)
        btn_layout.addWidget(btn_hires)
        btn_layout.addStretch()
        layout_allowed.addLayout(btn_layout)

        layout.addWidget(group_allowed)

        # Buffer & Latency (Quantum)
        group_quantum = QGroupBox("Buffer Size & Latency Configuration (Quantum)")
        grid_q = QGridLayout(group_quantum)

        grid_q.addWidget(QLabel("Default Quantum (default.clock.quantum):"), 0, 0)
        self.cmb_quantum = QComboBox()
        for q in STANDARD_QUANTUMS:
            label = f"{q} samples ({q/48:.1f}ms @ 48k)"
            if q == 1024:
                label += " ★ [Default / Recommended]"
            elif q == 512:
                label += " (Low Latency / Gaming)"
            elif q == 128:
                label += " (Ultra-low Latency / DAW)"
            self.cmb_quantum.addItem(label, q)
        grid_q.addWidget(self.cmb_quantum, 0, 1)

        grid_q.addWidget(QLabel("Min Quantum (default.clock.min-quantum):"), 1, 0)
        self.cmb_min_quantum = QComboBox()
        for q in [16, 32, 64, 128, 256, 512]:
            self.cmb_min_quantum.addItem(f"{q} samples", q)
        grid_q.addWidget(self.cmb_min_quantum, 1, 1)

        grid_q.addWidget(QLabel("Max Quantum (default.clock.max-quantum):"), 2, 0)
        self.cmb_max_quantum = QComboBox()
        for q in [1024, 2048, 4096, 8192, 16384]:
            self.cmb_max_quantum.addItem(f"{q} samples", q)
        grid_q.addWidget(self.cmb_max_quantum, 2, 1)

        layout.addWidget(group_quantum)
        layout.addStretch()
        return tab

    def _set_rate_checkboxes(self, target_rates):
        for rate, cb in self.rate_checkboxes.items():
            cb.setChecked(rate in target_rates)

    def _connect_change_signals(self):
        """Mark unsaved changes on widget modification"""
        self.cmb_default_rate.currentIndexChanged.connect(self._mark_unsaved)
        self.cmb_quantum.currentIndexChanged.connect(self._mark_unsaved)
        self.cmb_min_quantum.currentIndexChanged.connect(self._mark_unsaved)
        self.cmb_max_quantum.currentIndexChanged.connect(self._mark_unsaved)
        self.spin_quality.valueChanged.connect(self._mark_unsaved)
        self.cb_disable_resample.stateChanged.connect(self._mark_unsaved)
        self.cb_normalize.stateChanged.connect(self._mark_unsaved)
        self.cb_upmix.stateChanged.connect(self._mark_unsaved)
        self.cmb_upmix_method.currentIndexChanged.connect(self._mark_unsaved)
        for cb in self.rate_checkboxes.values():
            cb.stateChanged.connect(self._mark_unsaved)

    # -------------------------------------------------------------
    # Tab 2: Client & Resample Configuration
    # -------------------------------------------------------------
    def _create_resample_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        # Resample quality
        group_quality = QGroupBox("Resample quality (resample.quality)")
        vbox_q = QVBoxLayout(group_quality)

        hbox_slider = QHBoxLayout()
        self.slider_quality = QSlider(Qt.Horizontal)
        self.slider_quality.setRange(0, 15)
        self.slider_quality.setSingleStep(1)
        self.slider_quality.setPageStep(1)

        self.spin_quality = QSpinBox()
        self.spin_quality.setRange(0, 15)

        self.slider_quality.valueChanged.connect(self.spin_quality.setValue)
        self.spin_quality.valueChanged.connect(self.slider_quality.setValue)
        self.spin_quality.valueChanged.connect(self._update_quality_label)

        hbox_slider.addWidget(QLabel("Quality Level (0-15):"))
        hbox_slider.addWidget(self.slider_quality)
        hbox_slider.addWidget(self.spin_quality)
        vbox_q.addLayout(hbox_slider)

        self.lbl_quality_desc = QLabel("4: Default (Balanced / Good Quality)")
        self.lbl_quality_desc.setStyleSheet("color: #89b4fa; font-weight: bold;")
        vbox_q.addWidget(self.lbl_quality_desc)

        layout.addWidget(group_quality)

        # Channel mixing & misc options
        group_mix = QGroupBox("Channel Mixing & Resample Behavior")
        vbox_mix = QVBoxLayout(group_mix)

        self.cb_disable_resample = QCheckBox("resample.disable (Disable resampling)")
        self.cb_normalize = QCheckBox("channelmix.normalize (Normalize volume during mixing)")
        self.cb_upmix = QCheckBox("channelmix.upmix (Upmix stereo sources to multi-channel)")

        vbox_mix.addWidget(self.cb_disable_resample)
        vbox_mix.addWidget(self.cb_normalize)
        vbox_mix.addWidget(self.cb_upmix)

        hbox_upmix_method = QHBoxLayout()
        hbox_upmix_method.addWidget(QLabel("Upmix Method (channelmix.upmix-method):"))
        self.cmb_upmix_method = QComboBox()
        self.cmb_upmix_method.addItems(["psd", "simple", "none"])
        hbox_upmix_method.addWidget(self.cmb_upmix_method)
        hbox_upmix_method.addStretch()
        vbox_mix.addLayout(hbox_upmix_method)

        layout.addWidget(group_mix)

        # Save destination info
        lbl_info = QLabel("※ Saved to ~/.config/pipewire/client.conf.d/10-resample.conf")
        lbl_info.setStyleSheet("color: #a6adc8; font-style: italic;")
        layout.addWidget(lbl_info)

        layout.addStretch()
        return tab

    def _update_quality_label(self, val):
        descriptions = {
            0: "0: Minimum (Ultra-lightweight / Low CPU)",
            4: "4: Default (Standard CPU / Good Audio Quality)",
            10: "10: High Quality (Audiophile Recommended)",
            14: "14: Mastering Grade (Ultra-high Precision Resampling)",
            15: "15: Maximum (Experimental)",
        }
        text = descriptions.get(val, f"{val}: Custom Quality Level")
        self.lbl_quality_desc.setText(text)

    # -------------------------------------------------------------
    # Tab 3: Presets
    # -------------------------------------------------------------
    def _create_preset_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        lbl_desc = QLabel("Select an optimized audio profile preset with a single click:")
        layout.addWidget(lbl_desc)

        # Preset 1: Hi-Fi Audiophile
        btn_hifi = QPushButton("🎵 Hi-Res Audiophile (192kHz / Quality 14)")
        btn_hifi.setStyleSheet("text-align: left; padding: 14px; font-size: 11pt;")
        btn_hifi.clicked.connect(self.apply_hifi_preset)
        lbl_hifi_info = QLabel("  - allowed-rates: [44.1k, 48k, 88.2k, 96k, 176.4k, 192k, 352.8k, 384k]\n- resample.quality: 14 (Mastering Grade)")
        lbl_hifi_info.setStyleSheet("color: #a6adc8; margin-bottom: 10px;")

        # Preset 2: DTM / Low Latency
        btn_dtm = QPushButton("⚡ Low-Latency DAW / Gaming (Quantum 128)")
        btn_dtm.setStyleSheet("text-align: left; padding: 14px; font-size: 11pt;")
        btn_dtm.clicked.connect(self.apply_low_latency_preset)
        lbl_dtm_info = QLabel("  - allowed-rates: [48k, 96k]\n- min-quantum: 64, quantum: 128 (Ultra-low Latency)")
        lbl_dtm_info.setStyleSheet("color: #a6adc8; margin-bottom: 10px;")

        # Preset 3: Standard
        btn_std = QPushButton("📻 Standard Balance (48kHz / Quantum 1024)")
        btn_std.setStyleSheet("text-align: left; padding: 14px; font-size: 11pt;")
        btn_std.clicked.connect(self.apply_standard_preset)
        lbl_std_info = QLabel("  - allowed-rates: [44.1k, 48k]\n- quantum: 1024, resample.quality: 4 (Default)")
        lbl_std_info.setStyleSheet("color: #a6adc8;")

        layout.addWidget(btn_hifi)
        layout.addWidget(lbl_hifi_info)
        layout.addWidget(btn_dtm)
        layout.addWidget(lbl_dtm_info)
        layout.addWidget(btn_std)
        layout.addWidget(lbl_std_info)

        layout.addStretch()
        return tab

    def apply_hifi_preset(self):
        self.cmb_default_rate.setCurrentIndex(self.cmb_default_rate.findData(192000))
        self._set_rate_checkboxes(STANDARD_RATES)
        self.spin_quality.setValue(14)
        QMessageBox.information(self, "Preset Applied", "Loaded 'Hi-Res Audiophile' preset.\nClick 'Save Settings & Restart PipeWire' to apply.")

    def apply_low_latency_preset(self):
        self.cmb_default_rate.setCurrentIndex(self.cmb_default_rate.findData(96000))
        self._set_rate_checkboxes([48000, 96000])
        self.cmb_quantum.setCurrentIndex(self.cmb_quantum.findData(128))
        self.cmb_min_quantum.setCurrentIndex(self.cmb_min_quantum.findData(64))
        self.spin_quality.setValue(4)
        QMessageBox.information(self, "Preset Applied", "Loaded 'Low-Latency DAW / Gaming' preset.\nClick 'Save Settings & Restart PipeWire' to apply.")

    def apply_standard_preset(self):
        self.cmb_default_rate.setCurrentIndex(self.cmb_default_rate.findData(48000))
        self._set_rate_checkboxes([44100, 48000])
        self.cmb_quantum.setCurrentIndex(self.cmb_quantum.findData(1024))
        self.spin_quality.setValue(4)
        QMessageBox.information(self, "Preset Applied", "Loaded 'Standard Balance' preset.\nClick 'Save Settings & Restart PipeWire' to apply.")

    # -------------------------------------------------------------
    # Tab 4: Config File Editor
    # -------------------------------------------------------------
    def _create_raw_editor_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        hbox_select = QHBoxLayout()
        hbox_select.addWidget(QLabel("Select Configuration File:"))
        self.cmb_editor_file = QComboBox()
        self.cmb_editor_file.addItem("10-clock.conf (~/.config/pipewire/pipewire.conf.d/10-clock.conf)", str(self.config_mgr.clock_conf_file))
        self.cmb_editor_file.addItem("10-resample.conf (~/.config/pipewire/client.conf.d/10-resample.conf)", str(self.config_mgr.resample_conf_file))
        self.cmb_editor_file.addItem("20-upmix.conf [Pulse] (~/.config/pipewire/pipewire-pulse.conf.d/20-upmix.conf)", UPMIX_CONF_PATHS[0])
        self.cmb_editor_file.addItem("20-upmix.conf [Client] (~/.config/pipewire/client.conf.d/20-upmix.conf)", UPMIX_CONF_PATHS[1])

        added_paths = {
            str(self.config_mgr.clock_conf_file),
            str(self.config_mgr.resample_conf_file),
            UPMIX_CONF_PATHS[0],
            UPMIX_CONF_PATHS[1],
        }

        # Search and add other existing configuration files
        scan_dirs = [
            self.config_mgr.pw_conf_d,
            self.config_mgr.client_conf_d,
            Path(os.path.expanduser("~/.config/pipewire/pipewire-pulse.conf.d")),
        ]
        for d in scan_dirs:
            if d.exists():
                for f in d.glob("*.conf"):
                    if str(f) not in added_paths:
                        self.cmb_editor_file.addItem(f"{f.name} ({f})", str(f))
                        added_paths.add(str(f))

        self.cmb_editor_file.currentIndexChanged.connect(self._load_raw_file_to_editor)
        hbox_select.addWidget(self.cmb_editor_file)

        btn_reload_raw = QPushButton("Reload")
        btn_reload_raw.clicked.connect(self._load_raw_file_to_editor)
        hbox_select.addWidget(btn_reload_raw)

        layout.addLayout(hbox_select)

        self.txt_raw_editor = QTextEdit()
        layout.addWidget(self.txt_raw_editor)

        btn_save_raw = QPushButton("💾 Save Configuration File")
        btn_save_raw.clicked.connect(self._save_raw_file_from_editor)
        layout.addWidget(btn_save_raw)

        self._load_raw_file_to_editor()
        return tab

    def _load_raw_file_to_editor(self):
        file_path = self.cmb_editor_file.currentData()
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    self.txt_raw_editor.setText(f.read())
            except Exception as e:
                self.txt_raw_editor.setText(f"# Error loading file: {e}")
        else:
            self.txt_raw_editor.setText("# (This file does not exist yet. It will be generated upon saving settings.)")

    def _save_raw_file_from_editor(self):
        file_path = self.cmb_editor_file.currentData()
        if not file_path:
            return
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.txt_raw_editor.toPlainText())
            QMessageBox.information(self, "Save Complete", f"{os.path.basename(file_path)} saved successfully.")
            self.load_all_settings()
            if hasattr(self, "_upmix_load_from_file"):
                self._upmix_load_from_file()
                self._upmix_refresh_status_badge()
        except Exception as e:
            self._mic_log(f"Error playing test tone: {e}")
            QMessageBox.critical(self, "Save Error", str(e))

    # -------------------------------------------------------------
    # Tab 5: Real-time Monitoring & Logs
    # -------------------------------------------------------------
    def _create_status_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # Runtime settings display
        group_live = QGroupBox("Active PipeWire Runtime Status (pw-metadata settings)")
        vbox_live = QVBoxLayout(group_live)
        self.lbl_live_metadata = QLabel("Retrieving status...")
        self.lbl_live_metadata.setStyleSheet("font-family: monospace; color: #a6e3a1;")
        vbox_live.addWidget(self.lbl_live_metadata)
        layout.addWidget(group_live)

        # pw-top active node stream status
        group_pwtop = QGroupBox("Active Audio Streams & Nodes (pw-top real-time)")
        vbox_pwtop = QVBoxLayout(group_pwtop)
        self.txt_pwtop = QTextEdit()
        self.txt_pwtop.setReadOnly(True)
        self.txt_pwtop.setStyleSheet("font-family: monospace; background-color: #181825; color: #cdd6f4;")
        self.txt_pwtop.setMaximumHeight(180)
        vbox_pwtop.addWidget(self.txt_pwtop)

        btn_refresh_pwtop = QPushButton("🔄 Refresh Streams (pw-top)")
        btn_refresh_pwtop.clicked.connect(self.update_pw_top)
        vbox_pwtop.addWidget(btn_refresh_pwtop)
        layout.addWidget(group_pwtop)

        # journalctl system logs
        group_logs = QGroupBox("PipeWire System Logs (journalctl --user -u pipewire)")
        vbox_logs = QVBoxLayout(group_logs)
        self.txt_logs = QTextEdit()
        self.txt_logs.setReadOnly(True)
        vbox_logs.addWidget(self.txt_logs)

        btn_refresh_logs = QPushButton("🔄 Refresh Logs")
        btn_refresh_logs.clicked.connect(self.update_logs)
        vbox_logs.addWidget(btn_refresh_logs)

        layout.addWidget(group_logs)
        return tab

    # -------------------------------------------------------------
    # Logic Handlers
    # -------------------------------------------------------------
    def load_all_settings(self):
        """Load current values from config files into UI"""
        clock_settings = self.config_mgr.load_clock_settings()

        # Default clock rate
        def_rate = clock_settings.get("default.clock.rate", 48000)
        idx = self.cmb_default_rate.findData(def_rate)
        if idx >= 0:
            self.cmb_default_rate.setCurrentIndex(idx)

        # Allowed sample rates
        allowed = clock_settings.get("default.clock.allowed-rates", [44100, 48000, 88200, 96000])
        self._set_rate_checkboxes(allowed)

        # Quantums
        q = clock_settings.get("default.clock.quantum", 1024)
        q_idx = self.cmb_quantum.findData(q)
        if q_idx >= 0:
            self.cmb_quantum.setCurrentIndex(q_idx)

        min_q = clock_settings.get("default.clock.min-quantum", 32)
        min_q_idx = self.cmb_min_quantum.findData(min_q)
        if min_q_idx >= 0:
            self.cmb_min_quantum.setCurrentIndex(min_q_idx)

        max_q = clock_settings.get("default.clock.max-quantum", 2048)
        max_q_idx = self.cmb_max_quantum.findData(max_q)
        if max_q_idx >= 0:
            self.cmb_max_quantum.setCurrentIndex(max_q_idx)

        # Resampling settings
        resample_settings = self.config_mgr.load_resample_settings()
        q_val = resample_settings.get("resample.quality", 4)
        self.spin_quality.setValue(q_val)
        self._update_quality_label(q_val)
        self.cb_disable_resample.setChecked(resample_settings.get("resample.disable", False))
        self.cb_normalize.setChecked(resample_settings.get("channelmix.normalize", True))
        self.cb_upmix.setChecked(resample_settings.get("channelmix.upmix", True))

        method = resample_settings.get("channelmix.upmix-method", "psd")
        m_idx = self.cmb_upmix_method.findText(method)
        if m_idx >= 0:
            self.cmb_upmix_method.setCurrentIndex(m_idx)

        # Load 5.1ch upmix settings
        if hasattr(self, "_upmix_load_from_file"):
            self._upmix_load_from_file()
            self._upmix_refresh_status_badge()

    def update_live_status(self):
        """Periodically update service status and pw-metadata"""
        is_active, status_str = self.service_mgr.get_service_status()

        if is_active:
            self.lbl_status_badge.setText(" ● PipeWire Running ")
            self.lbl_status_badge.setStyleSheet("background-color: #a6e3a1; color: #11111b;")
        else:
            self.lbl_status_badge.setText(" ✖ Stopped ")
            self.lbl_status_badge.setStyleSheet("background-color: #f38ba8; color: #11111b;")

        meta = self.service_mgr.get_live_metadata()
        meta_text = (
            f"Active Sampling Rate : {meta.get('clock.rate')}\n"
            f"Allowed Sample Rates : {meta.get('clock.allowed-rates')}\n"
            f"Active Quantum (Buffer): {meta.get('clock.quantum')}\n"
            f"Quantum Range (min~max): {meta.get('clock.min-quantum')} ~ {meta.get('clock.max-quantum')}"
        )
        self.lbl_live_metadata.setText(meta_text)

    def update_logs(self):
        logs = self.service_mgr.get_recent_logs(80)
        self.txt_logs.setText(logs)
        # Scroll to bottom
        sb = self.txt_logs.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_save_and_apply(self):
        """Save settings and restart pipewire services"""
        # Clock settings
        default_rate = self.cmb_default_rate.currentData()
        selected_rates = [rate for rate, cb in self.rate_checkboxes.items() if cb.isChecked()]

        if not selected_rates:
            QMessageBox.warning(self, "Warning", "No allowed sample rates selected. Please select at least one.")
            return

        if default_rate not in selected_rates:
            selected_rates.append(default_rate)
            QMessageBox.information(
                self, "Auto-Correct",
                f"Default rate ({default_rate} Hz) was not in allowed rates, so it was automatically added."
            )

        quantum = self.cmb_quantum.currentData()
        min_quantum = self.cmb_min_quantum.currentData()
        max_quantum = self.cmb_max_quantum.currentData()

        # Save
        f1 = self.config_mgr.save_clock_settings(
            rate=default_rate,
            allowed_rates=selected_rates,
            min_quantum=min_quantum,
            max_quantum=max_quantum,
            quantum=quantum
        )

        # Resampling settings
        f2 = self.config_mgr.save_resample_settings(
            quality=self.spin_quality.value(),
            disable=self.cb_disable_resample.isChecked(),
            normalize=self.cb_normalize.isChecked(),
            upmix=self.cb_upmix.isChecked(),
            upmix_method=self.cmb_upmix_method.currentText()
        )

        # Restart PipeWire (systemctl --user restart pipewire)
        results = self.service_mgr.restart_pipewire()

        success = all(res[1] for res in results)
        if success:
            self._unsaved_changes = False
            self._update_title()
            QMessageBox.information(
                self,
                "Saved & Restarted",
                f"Configuration updated successfully:\n- {f1}\n- {f2}\n\nExecuted !"
)
        else:
            err_msg = "\n".join([f"{res[0]}: {res[2]}" for res in results if not res[1]])
            QMessageBox.critical(self, "Restart Error", f"Configuration saved, but error restarting PipeWire:\n{err_msg}")

        self.update_live_status()
        self.update_logs()

    def on_restart_service_only(self):
        results = self.service_mgr.restart_pipewire()
        success = all(res[1] for res in results)
        if success:
            QMessageBox.information(self, "Restart Completed", "Executed systemctl --user restart pipewire.")
        else:
            QMessageBox.critical(self, "Restart Error", "Failed to restart PipeWire.")
        self.update_live_status()
        self.update_logs()

    def on_reset_defaults(self):
        reply = QMessageBox.question(
            self,
            "Confirm Reset to Default",
            "Are you sure you want to remove all custom settings (clock, resample, 5.1ch upmix, virtual DSP mic) and reset channel volumes to flat default?\n\n(PipeWire service will be restarted automatically)",
QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            try:
                # 1. Clean up all custom configuration files
                removed = self.config_mgr.reset_custom_configs()
                
                # 2. Disable WebRTC DSP virtual mic
                self.agc_mgr.disable_hardware_agc()
                self.chk_soft_agc_enable.setChecked(False)
                self.slider_mic_gain.setValue(100)
                
                # 3. Reset 5.1ch channel volumes to 100%
                try:
                    apply_51ch_channel_volumes("default", [1.0] * 6)
                except Exception:
                    pass
                
                # 4. Restart PipeWire services
                self.service_mgr.restart_pipewire()
                
                # 5. Reload UI state and sync
                self.load_all_settings()
                self.update_live_status()
                self.update_logs()
                self._upmix_load_from_file()
                self._upmix_refresh_status_badge()
                self._calib_reset_flat()
                self._mic_refresh_hw_status()
                self._mic_refresh_sources()
                
                self._unsaved_changes = False
                self._update_title()
                
                msg = (
                    "All settings restored to system defaults!\n\n"
"[Reset Items]\n"
"- ⏱️ Clock & Sample Rates\n"
"- 🎛️ Client Resample Quality\n"
"- 🔊 5.1ch Surround Upmix (Reverted to 2ch Direct)\n"
"- ⚡ WebRTC DSP Virtual Mic (Disabled)\n"
"- 🎯 5.1ch Speaker Volumes (Reset to 100% Flat)\n"
"- 🔄 PipeWire Service Restarted\n"
)
                if removed:
                    msg += "\n[Deleted Configuration Files]\n" + "\n".join(removed)

                QMessageBox.information(self, "Reset Complete", msg)
            except Exception as e:
                QMessageBox.critical(self, "Reset Error", f"Error restoring defaults: {e}")


    # -------------------------------------------------------------
    # Unsaved changes indicator & utilities
    # -------------------------------------------------------------
    def _update_title(self):
        """Update title bar unsaved indicator"""
        if self._unsaved_changes:
            self.setWindowTitle(f"* {self.BASE_TITLE}  [Unsaved Changes]")
            self.btn_save_apply.setStyleSheet(
                "background-color: #f9e2af; color: #11111b; font-size: 11pt; "
                "padding: 10px 24px; border-radius: 6px; font-weight: bold;"
            )
        else:
            self.setWindowTitle(self.BASE_TITLE)
            self.btn_save_apply.setStyleSheet("")
            self.btn_save_apply.setObjectName("btn_primary")

    def _mark_unsaved(self):
        """Mark unsaved changes on UI modification"""
        if not self._unsaved_changes:
            self._unsaved_changes = True
            self._update_title()

    def _check_required_commands(self):
        """Check presence of required CLI commands"""
        import shutil
        missing = [cmd for cmd in ["pw-top", "pw-metadata", "systemctl"] if shutil.which(cmd) is None]
        if missing:
            QMessageBox.warning(
                self,
                "Required Command Missing",
                "The following commands were not found. Some features may be unavailable:\n\n"
+ "\n".join(f"  - {cmd}" for cmd in missing)
                + "\n\nPlease install pipewire-bin / pipewire packages."
)

    def trigger_pw_top_update(self):
        """Run pw-top asynchronously in background thread"""
        # Skip if previous worker is still running
        if self._pwtop_worker is not None and self._pwtop_worker.isRunning():
            return
        self.txt_pwtop.setPlaceholderText("Retrieving pw-top...")
        self._pwtop_worker = PwTopWorker()
        self._pwtop_worker.result_ready.connect(self._on_pwtop_result)
        self._pwtop_worker.start()

    def _on_pwtop_result(self, text: str):
        """Receive and display pw-top result from background thread"""
        self.txt_pwtop.setText(text)
        self.txt_pwtop.setPlaceholderText("")

    def update_pw_top(self):
        """Retained for backwards compatibility (delegates to trigger_pw_top_update)"""
        self.trigger_pw_top_update()


# ============================================================
    # Tab: 🔊 5.1ch Surround Upmix
# ============================================================
    # Tab: 🔊 5.1ch Surround Upmix
    # ============================================================

    def _create_upmix_tab(self):
        """Create 5.1ch upmix & auto-calibration tab (2-column layout)"""
        self._upmix_updating = False
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # ========================================================
        # Left column: 5.1ch Surround Upmixing
        # ========================================================
        col_left = QVBoxLayout()
        col_left.setSpacing(10)

        group_upmix = QGroupBox("🔊 5.1ch Spatial Audio Upmixing")
        vbox_upmix = QVBoxLayout(group_upmix)
        vbox_upmix.setContentsMargins(12, 14, 12, 12)
        vbox_upmix.setSpacing(10)

        # Status badge & presets
        row_top = QHBoxLayout()
        row_top.setSpacing(8)
        row_top.addWidget(QLabel("Status:"))
        self.lbl_upmix_status = QLabel()
        self.lbl_upmix_status.setObjectName("status_badge")
        self._upmix_refresh_status_badge()
        row_top.addWidget(self.lbl_upmix_status)
        row_top.addSpacing(10)
        row_top.addWidget(QLabel("Preset:"))
        preset_names = list(UPMIX_PRESETS.keys()) + [UPMIX_CUSTOM_PRESET_NAME]
        self.cmb_upmix_preset = QComboBox()
        self.cmb_upmix_preset.addItems(preset_names)
        self.cmb_upmix_preset.currentTextChanged.connect(self._upmix_on_preset_selected)
        row_top.addWidget(self.cmb_upmix_preset, 1)
        vbox_upmix.addLayout(row_top)

        # Parameter configuration grid
        grid_p = QGridLayout()
        grid_p.setHorizontalSpacing(10)
        grid_p.setVerticalSpacing(8)

        # 1. Upmix method
        grid_p.addWidget(QLabel("Method (method):"), 0, 0)
        self.cmb_upmix_method2 = QComboBox()
        self.cmb_upmix_method2.addItems(["psd", "simple", "none"])
        self.cmb_upmix_method2.currentTextChanged.connect(self._upmix_on_param_changed)
        grid_p.addWidget(self.cmb_upmix_method2, 0, 1, 1, 2)

        # 2. LFE Cutoff
        grid_p.addWidget(QLabel("Subwoofer LFE Cutoff:"), 1, 0)
        self.slider_upmix_lfe = QSlider(Qt.Horizontal)
        self.slider_upmix_lfe.setRange(40, 200)
        self.slider_upmix_lfe.setSingleStep(10)
        self.slider_upmix_lfe.setPageStep(10)
        self.slider_upmix_lfe.setValue(150)
        self.lbl_upmix_lfe = QLabel("150 Hz")
        self.lbl_upmix_lfe.setMinimumWidth(60)
        self.slider_upmix_lfe.valueChanged.connect(
            lambda v: (self.lbl_upmix_lfe.setText(f"{v} Hz"), self._upmix_on_param_changed())
        )
        grid_p.addWidget(self.slider_upmix_lfe, 1, 1)
        grid_p.addWidget(self.lbl_upmix_lfe, 1, 2)

        # 3. Rear Delay
        grid_p.addWidget(QLabel("Rear Speaker Delay:"), 2, 0)
        self.slider_upmix_delay = QSlider(Qt.Horizontal)
        self.slider_upmix_delay.setRange(0, 500)
        self.slider_upmix_delay.setSingleStep(5)
        self.slider_upmix_delay.setPageStep(50)
        self.slider_upmix_delay.setValue(0)
        self.lbl_upmix_delay = QLabel("0.0 ms")
        self.lbl_upmix_delay.setMinimumWidth(60)
        self.slider_upmix_delay.valueChanged.connect(
            lambda v: (self.lbl_upmix_delay.setText(f"{v / 10:.1f} ms"), self._upmix_on_param_changed())
        )
        grid_p.addWidget(self.slider_upmix_delay, 2, 1)
        grid_p.addWidget(self.lbl_upmix_delay, 2, 2)

        # 4. Center FC Cutoff
        grid_p.addWidget(QLabel("Center Cutoff (FC):"), 3, 0)
        self.slider_upmix_fc = QSlider(Qt.Horizontal)
        self.slider_upmix_fc.setRange(0, 40)
        self.slider_upmix_fc.setSingleStep(1)
        self.slider_upmix_fc.setPageStep(4)
        self.slider_upmix_fc.setValue(0)
        self.lbl_upmix_fc = QLabel("0 Hz (Disabled)")
        self.lbl_upmix_fc.setMinimumWidth(80)
        self.slider_upmix_fc.valueChanged.connect(
            lambda v: (
                self.lbl_upmix_fc.setText(f"{v * 500} Hz" if v > 0 else "0 Hz (Disabled)"),
                self._upmix_on_param_changed()
            )
        )
        grid_p.addWidget(self.slider_upmix_fc, 3, 1)
        grid_p.addWidget(self.lbl_upmix_fc, 3, 2)

        # 5. Stereo Widening
        grid_p.addWidget(QLabel("Stereo Widening:"), 4, 0)
        self.slider_upmix_widen = QSlider(Qt.Horizontal)
        self.slider_upmix_widen.setRange(0, 20)
        self.slider_upmix_widen.setSingleStep(1)
        self.slider_upmix_widen.setPageStep(2)
        self.slider_upmix_widen.setValue(0)
        self.lbl_upmix_widen = QLabel("0.00")
        self.lbl_upmix_widen.setMinimumWidth(60)
        self.slider_upmix_widen.valueChanged.connect(
            lambda v: (self.lbl_upmix_widen.setText(f"{v * 0.05:.2f}"), self._upmix_on_param_changed())
        )
        grid_p.addWidget(self.slider_upmix_widen, 4, 1)
        grid_p.addWidget(self.lbl_upmix_widen, 4, 2)

        vbox_upmix.addLayout(grid_p)
        vbox_upmix.addStretch()

        # Upmix action buttons
        row_upmix_btn = QHBoxLayout()
        row_upmix_btn.setSpacing(6)

        self.btn_upmix_disable = QPushButton("❌ Disable Upmix")
        self.btn_upmix_disable.setObjectName("btn_danger")
        self.btn_upmix_disable.clicked.connect(self._upmix_disable)
        row_upmix_btn.addWidget(self.btn_upmix_disable)

        self.btn_upmix_test = QPushButton("🎵 6ch Test")
        self.btn_upmix_test.clicked.connect(self._upmix_speaker_test)
        row_upmix_btn.addWidget(self.btn_upmix_test)

        self.btn_upmix_enable = QPushButton("🔊 Enable / Apply")
        self.btn_upmix_enable.setObjectName("btn_primary")
        self.btn_upmix_enable.clicked.connect(self._upmix_enable)
        row_upmix_btn.addWidget(self.btn_upmix_enable, 1)

        vbox_upmix.addLayout(row_upmix_btn)
        col_left.addWidget(group_upmix)
        layout.addLayout(col_left, 45)

        # ========================================================
        # Right column: 5.1ch Speaker Balance & Calibration
        # ========================================================
        col_right = QVBoxLayout()
        col_right.setSpacing(10)

        group_calib = QGroupBox("🎯 5.1ch Room Calibration & Volume Balance")
        vbox_calib = QVBoxLayout(group_calib)
        vbox_calib.setContentsMargins(12, 14, 12, 12)
        vbox_calib.setSpacing(8)

        desc_calib = QLabel("Plays sequential test tones to equalize 5.1ch speaker volumes at your listening position via mic.")
        desc_calib.setStyleSheet("color: #a6adc8; font-size: 8.5pt;")
        vbox_calib.addWidget(desc_calib)

        # Calibration mic & test volume
        row_c_conf = QHBoxLayout()
        row_c_conf.setSpacing(6)
        row_c_conf.addWidget(QLabel("Mic:"))
        self.cmb_calib_mic = QComboBox()
        self.cmb_calib_mic.setMinimumWidth(130)
        self.btn_calib_mic_refresh = QPushButton("🔄")
        self.btn_calib_mic_refresh.setFixedWidth(32)
        self.btn_calib_mic_refresh.clicked.connect(self._calib_refresh_mics)
        row_c_conf.addWidget(self.cmb_calib_mic, 1)
        row_c_conf.addWidget(self.btn_calib_mic_refresh)

        row_c_conf.addSpacing(6)
        row_c_conf.addWidget(QLabel("Volume:"))
        self.slider_calib_vol = QSlider(Qt.Horizontal)
        self.slider_calib_vol.setRange(5, 50)
        self.slider_calib_vol.setValue(15)
        self.lbl_calib_vol = QLabel("15%")
        self.lbl_calib_vol.setMinimumWidth(40)
        self.slider_calib_vol.valueChanged.connect(
            lambda v: self.lbl_calib_vol.setText(f"{v}%")
        )
        row_c_conf.addWidget(self.slider_calib_vol)
        row_c_conf.addWidget(self.lbl_calib_vol)
        vbox_calib.addLayout(row_c_conf)

        # Measurement status & control buttons
        row_c_act = QHBoxLayout()
        row_c_act.setSpacing(6)
        self.lbl_calib_status = QLabel(" Ready ")
        self.lbl_calib_status.setObjectName("status_badge")
        self.lbl_calib_status.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 4px 8px;")
        row_c_act.addWidget(self.lbl_calib_status, 1)

        self.btn_calib_stop = QPushButton("⏹️ Stop")
        self.btn_calib_stop.setObjectName("btn_danger")
        self.btn_calib_stop.setEnabled(False)
        self.btn_calib_stop.clicked.connect(self._calib_stop)
        row_c_act.addWidget(self.btn_calib_stop)

        self.btn_calib_start = QPushButton("🎙️ Start Auto-Calib")
        self.btn_calib_start.setObjectName("btn_primary")
        self.btn_calib_start.clicked.connect(self._calib_start)
        row_c_act.addWidget(self.btn_calib_start)
        vbox_calib.addLayout(row_c_act)

        # 6-channel individual volume sliders (2x3 grid)
        grid_channels = QGridLayout()
        grid_channels.setHorizontalSpacing(12)
        grid_channels.setVerticalSpacing(4)
        
        self.calib_sliders = []
        self.lbl_calib_values = []
        self.lbl_calib_measures = []

        for idx, (ch_id, ch_label) in enumerate(CHANNEL_NAMES):
            col_offset = (idx % 2) * 4
            row = idx // 2

            lbl_title = QLabel(f"<b>{ch_label}</b>")
            lbl_meas = QLabel("Meas: --")
            lbl_meas.setStyleSheet("color: #a6adc8; font-size: 8pt;")
            
            slider = QSlider(Qt.Horizontal)
            slider.setRange(20, 180)
            slider.setValue(100)
            
            lbl_val = QLabel("100%")
            lbl_val.setMinimumWidth(38)
            lbl_val.setStyleSheet("font-size: 9pt;")
            slider.valueChanged.connect(
                (lambda l_val: lambda v: l_val.setText(f"{v}%"))(lbl_val)
            )

            self.calib_sliders.append(slider)
            self.lbl_calib_values.append(lbl_val)
            self.lbl_calib_measures.append(lbl_meas)

            grid_channels.addWidget(lbl_title, row * 2, col_offset)
            grid_channels.addWidget(lbl_meas, row * 2, col_offset + 1, 1, 2, Qt.AlignRight)
            grid_channels.addWidget(slider, row * 2 + 1, col_offset, 1, 2)
            grid_channels.addWidget(lbl_val, row * 2 + 1, col_offset + 2)

        vbox_calib.addLayout(grid_channels)
        vbox_calib.addStretch()

        # Volume apply buttons
        row_apply = QHBoxLayout()
        row_apply.setSpacing(6)
        self.btn_calib_reset = QPushButton("🔄 100% Reset")
        self.btn_calib_reset.clicked.connect(self._calib_reset_flat)
        row_apply.addWidget(self.btn_calib_reset)

        self.btn_calib_apply = QPushButton("💾 Apply Volumes to PipeWire")
        self.btn_calib_apply.setObjectName("btn_primary")
        self.btn_calib_apply.clicked.connect(self._calib_apply_volumes)
        row_apply.addWidget(self.btn_calib_apply, 1)
        vbox_calib.addLayout(row_apply)

        col_right.addWidget(group_calib)
        layout.addLayout(col_right, 55)

        # Initialization
        self._calib_worker = None
        self._calib_refresh_mics()
        self._upmix_load_from_file()
        self._calib_load_current_volumes()

        return tab

    def _upmix_get_params(self) -> dict:
        """Get current parameter dictionary from UI inputs."""
        return {
            "method": self.cmb_upmix_method2.currentText(),
            "lfe_cutoff": self.slider_upmix_lfe.value(),
            "rear_delay": self.slider_upmix_delay.value() / 10.0,
            "fc_cutoff": self.slider_upmix_fc.value() * 500,
            "stereo_widen": round(self.slider_upmix_widen.value() * 0.05, 2),
        }

    def _upmix_set_params(self, p: dict):
        """Set parameter dictionary to UI (temporarily blocking signals)."""
        self._upmix_updating = True
        try:
            idx = self.cmb_upmix_method2.findText(p.get("method", "psd"))
            if idx >= 0:
                self.cmb_upmix_method2.setCurrentIndex(idx)
            self.slider_upmix_lfe.setValue(int(p.get("lfe_cutoff", 150)))
            self.slider_upmix_delay.setValue(int(round(p.get("rear_delay", 0.0) * 10)))
            self.slider_upmix_fc.setValue(int(p.get("fc_cutoff", 0)) // 500)
            self.slider_upmix_widen.setValue(int(round(p.get("stereo_widen", 0.0) / 0.05)))
        finally:
            self._upmix_updating = False
        # Manually update labels
        self.lbl_upmix_lfe.setText(f"{self.slider_upmix_lfe.value()} Hz")
        self.lbl_upmix_delay.setText(f"{self.slider_upmix_delay.value() / 10:.1f} ms")
        fc = self.slider_upmix_fc.value() * 500
        self.lbl_upmix_fc.setText(f"{fc} Hz" if fc > 0 else "0 Hz (Disabled)")
        self.lbl_upmix_widen.setText(f"{self.slider_upmix_widen.value() * 0.05:.2f}")

    def _upmix_load_from_file(self):
        """Load existing configuration files into UI."""
        params = upmix_parse_config(UPMIX_CONF_PATHS[0])
        self._upmix_set_params(params)
        self._upmix_detect_preset()

    def _upmix_detect_preset(self):
        """Detect matching preset for current parameters."""
        current = self._upmix_get_params()
        for name, preset in UPMIX_PRESETS.items():
            if all(current[k] == preset[k] for k in preset):
                self._upmix_updating = True
                self.cmb_upmix_preset.setCurrentText(name)
                self._upmix_updating = False
                return
        self._upmix_updating = True
        self.cmb_upmix_preset.setCurrentText(UPMIX_CUSTOM_PRESET_NAME)
        self._upmix_updating = False

    def _upmix_on_preset_selected(self, preset_name: str):
        """Apply preset parameters on selection."""
        if self._upmix_updating:
            return
        if preset_name in UPMIX_PRESETS:
            self._upmix_set_params(UPMIX_PRESETS[preset_name])

    def _upmix_on_param_changed(self):
        """Update preset selector when parameters change."""
        if self._upmix_updating:
            return
        self._upmix_detect_preset()

    def _upmix_refresh_status_badge(self):
        """Refresh upmix status badge."""
        if upmix_is_enabled():
            self.lbl_upmix_status.setText(" ● 5.1ch Upmix ON ")
            self.lbl_upmix_status.setStyleSheet(
                "background-color: #a6e3a1; color: #11111b; "
                "padding: 3px 8px; border-radius: 6px; font-weight: bold;"
            )
        else:
            self.lbl_upmix_status.setText(" ○ 2ch Direct (Disabled) ")
            self.lbl_upmix_status.setStyleSheet(
                "background-color: #313244; color: #a6adc8; "
                "padding: 3px 8px; border-radius: 6px;"
            )

    def _upmix_enable(self):
        """Apply 5.1ch upmixing settings and restart PipeWire."""
        try:
            params = self._upmix_get_params()
            conf_content = upmix_generate_config(params)
            for path in UPMIX_CONF_PATHS:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(conf_content)
            self.service_mgr.restart_pipewire()
            self._upmix_refresh_status_badge()
            self.update_live_status()
            QMessageBox.information(
                self,
                "Upmix Enabled",
                "5.1ch surround upmix configuration applied.\nPipeWire restarted.\n\n" +
f"[Settings]\nMethod: {params['method']}\nLFE: {params['lfe_cutoff']}Hz\nRear: {params['rear_delay']:.1f}ms\nFC: {params['fc_cutoff']}Hz\nWiden: {params['stereo_widen']:.2f}"
)
        except Exception as e:
            self._mic_log(f"Upmix apply error: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def _upmix_disable(self):
        """Remove 5.1ch upmixing configuration and restart PipeWire."""
        removed = []
        try:
            for path in UPMIX_CONF_PATHS:
                if os.path.exists(path):
                    os.remove(path)
                    removed.append(path)
            self.service_mgr.restart_pipewire()
            self._upmix_refresh_status_badge()
            self.update_live_status()
            QMessageBox.information(
                self,
                "Upmix Disabled",
                "5.1ch upmixing disabled, reverted to standard 2ch Direct.\nPipeWire restarted.",
            )
        except Exception as e:
            self._mic_log(f"Upmix disable error: {e}")
            params = self._upmix_get_params()
            conf_content = upmix_generate_config(params)
            for path in removed:
                try:
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(conf_content)
                except OSError:
                    pass
            QMessageBox.critical(
                self, "Error",
                f"Failed to disable upmixing (changes reverted): {e}"
            )

    def _upmix_speaker_test(self):
        """Launch 6ch speaker test asynchronously."""
        try:
            subprocess.Popen(
                ["speaker-test", "-D", "pulse", "-c", "6", "-t", "wav", "-l", "1"]
            )
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "Command Missing",
                "speaker-test command not found.\nPlease install alsa-utils.",
            )
        except Exception as e:
            self._mic_log(f"Error playing test tone: {e}")
            QMessageBox.critical(self, "Error", f"Failed to run test: {e}")

    # ------ 5.1ch Calibration Handlers ------

    def _calib_refresh_mics(self):
        """Populate calibration mic list"""
        self.cmb_calib_mic.blockSignals(True)
        self.cmb_calib_mic.clear()
        self.cmb_calib_mic.addItem("Default Microphone (Default)", "default")
        try:
            import pulsectl
            with pulsectl.Pulse('guipipewire-calib-list') as pulse:
                for src in pulse.source_list():
                    if not src.name.endswith(".monitor"):
                        desc = src.description or src.name
                        self.cmb_calib_mic.addItem(f"{desc}", src.name)
        except Exception:
            pass
        self.cmb_calib_mic.blockSignals(False)

    def _calib_start(self):
        """Start 5.1ch auto-calibration measurement"""
        if self._calib_worker and self._calib_worker.isRunning():
            return

        mic_name = self.cmb_calib_mic.currentData()
        vol = self.slider_calib_vol.value() / 100.0

        self.btn_calib_start.setEnabled(False)
        self.btn_calib_stop.setEnabled(True)
        self.lbl_calib_status.setText(" 🚀 Measuring: Please remain quiet... ")
        self.lbl_calib_status.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 6px; padding: 4px 8px;")

        for lbl in self.lbl_calib_measures:
            lbl.setText("Measurement: Waiting...")

        self._calib_worker = CalibrationWorkerThread(
            sink_name="default",
            mic_source_name=mic_name,
            test_volume=vol,
            parent=self
        )
        self._calib_worker.progress_updated.connect(self._calib_on_progress)
        self._calib_worker.step_result.connect(self._calib_on_step_result)
        self._calib_worker.finished_calibration.connect(self._calib_on_finished)
        self._calib_worker.error_occurred.connect(self._calib_on_error)
        self._calib_worker.start()

    def _calib_stop(self):
        """Abort measurement"""
        if self._calib_worker:
            self._calib_worker.stop()
            self._calib_worker.wait(1000)
        self.btn_calib_start.setEnabled(True)
        self.btn_calib_stop.setEnabled(False)
        self.lbl_calib_status.setText(" ⏹️ Calibration Aborted ")
        self.lbl_calib_status.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 4px 8px;")

    def _calib_on_progress(self, step_idx, ch_name, status_msg):
        if step_idx == 0:
            self.lbl_calib_status.setText(f" 🔇 {status_msg} ")
        elif 1 <= step_idx <= 6:
            self.lbl_calib_status.setText(f" 🔊 [{step_idx}/6] {status_msg} ")
        else:
            self.lbl_calib_status.setText(f" 🎉 {status_msg} ")

    def _calib_on_step_result(self, ch_idx, measured_db):
        if 0 <= ch_idx < len(self.lbl_calib_measures):
            self.lbl_calib_measures[ch_idx].setText(f"Measured: {measured_db:.1f} dBFS")

    def _calib_on_finished(self, results):
        self.btn_calib_start.setEnabled(True)
        self.btn_calib_stop.setEnabled(False)
        self.lbl_calib_status.setText(" 🎉 Complete! Recommended volumes applied ")
        self.lbl_calib_status.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 6px; padding: 4px 8px;")

        rec_vols = results.get("recommended_volumes", [1.0] * 6)
        gain_diffs = results.get("gain_diffs_db", [0.0] * 6)

        for idx, vol in enumerate(rec_vols):
            if idx < len(self.calib_sliders):
                val_int = int(round(vol * 100))
                self.calib_sliders[idx].setValue(val_int)
                diff = gain_diffs[idx] if idx < len(gain_diffs) else 0.0
                sign = "+" if diff >= 0 else ""
                meas_db = results["measured_dbs"][idx] if idx < len(results["measured_dbs"]) else -90.0
                self.lbl_calib_measures[idx].setText(
                    f"{meas_db:.1f} dBFS ({sign}{diff:.1f} dB)"
                )

        QMessageBox.information(
            self,
            "Calibration Complete",
            "All 6 channels measured successfully!\n\n"
"Calculated volume adjustments have been applied to sliders to equalize levels at listening position.\n\n"
"Recommended volume adjustments have been applied to sliders.\n\n"
"Click '💾 Apply Volumes to PipeWire' to activate."
        )

    def _calib_on_error(self, err_msg):
        self.btn_calib_start.setEnabled(True)
        self.btn_calib_stop.setEnabled(False)
        self.lbl_calib_status.setText(f" ⚠️ Error: {err_msg} ")
        self.lbl_calib_status.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; border-radius: 6px; padding: 4px 8px;")
        QMessageBox.critical(self, "Error", err_msg)

    def _calib_apply_volumes(self):
        """Apply current 6ch slider volumes to PipeWire"""
        volumes = [s.value() / 100.0 for s in self.calib_sliders]
        try:
            apply_51ch_channel_volumes("default", volumes)
            vol_str = ", ".join([f"{CHANNEL_NAMES[i][0]}: {int(volumes[i]*100)}%" for i in range(len(volumes))])
            QMessageBox.information(
                self,
                "Volumes Applied",
                f"5.1ch channel volumes applied to PipeWire!\n\n{vol_str}"
)
        except Exception as e:
            self._mic_log(f"Volume apply error: {e}")
            QMessageBox.critical(self, "Error", f"Failed to apply volumes: {e}")

    def _calib_load_current_volumes(self):
        """Get current 5.1ch volumes from PipeWire and set sliders"""
        try:
            vols = get_51ch_channel_volumes("default")
            for idx, vol in enumerate(vols):
                if idx < len(self.calib_sliders):
                    val_int = int(round(vol * 100))
                    self.calib_sliders[idx].blockSignals(True)
                    self.calib_sliders[idx].setValue(val_int)
                    self.calib_sliders[idx].blockSignals(False)
                    if idx < len(self.lbl_calib_values):
                        self.lbl_calib_values[idx].setText(f"{val_int}%")
        except Exception:
            pass

    def _calib_reset_flat(self):
        """Reset all 6 channel volumes to 100% flat"""
        for slider in self.calib_sliders:
            slider.setValue(100)
        try:
            apply_51ch_channel_volumes("default", [1.0] * 6)
            QMessageBox.information(
                self,
                "Reset Complete",
                "All channel volumes have been reset to 100% (Flat)."
            )
        except Exception as e:
            self._mic_log(f"Volume reset error: {e}")
            QMessageBox.critical(self, "Error", f"Failed to reset volumes: {e}")


    # ============================================================
    # Tab: 🎙️ Mic & AGC
    # ============================================================

    def _create_mic_agc_tab(self):
        """Create Mic & AGC tab (2-column layout)"""
        tab = QWidget()
        layout = QHBoxLayout(tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # ========================================================
        # Left column: Mic Monitor & WebRTC DSP Virtual Mic
        # ========================================================
        col_left = QVBoxLayout()
        col_left.setSpacing(10)

        # 1. Mic Input & Monitor
        group_meter = QGroupBox("🎙️ Microphone Input & Level Monitor")
        vbox_meter = QVBoxLayout(group_meter)
        vbox_meter.setContentsMargins(12, 14, 12, 12)
        vbox_meter.setSpacing(8)

        # Device selection
        row_dev = QHBoxLayout()
        row_dev.setSpacing(6)
        row_dev.addWidget(QLabel("Input Device:"))
        self.cmb_mic_source = QComboBox()
        self.btn_refresh_mics = QPushButton("🔄")
        self.btn_refresh_mics.setFixedWidth(32)
        self.btn_refresh_mics.clicked.connect(self._mic_refresh_sources)
        row_dev.addWidget(self.cmb_mic_source, 1)
        row_dev.addWidget(self.btn_refresh_mics)
        vbox_meter.addLayout(row_dev)

        # Level meter & status badges
        row_progress = QHBoxLayout()
        row_progress.setSpacing(6)
        self.prog_mic_level = QProgressBar()
        self.prog_mic_level.setRange(0, 100)
        self.prog_mic_level.setValue(0)
        self.prog_mic_level.setTextVisible(True)
        self.prog_mic_level.setFormat("-∞ dBFS")
        self.prog_mic_level.setFixedHeight(18)
        row_progress.addWidget(self.prog_mic_level, 1)

        self.lbl_mic_clip = QLabel(" ● Optimal ")
        self.lbl_mic_clip.setObjectName("status_badge")
        self.lbl_mic_clip.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 2px 6px;")
        row_progress.addWidget(self.lbl_mic_clip)
        vbox_meter.addLayout(row_progress)

        # Numerical level details
        self.lbl_mic_db_detail = QLabel("RMS: -∞ dBFS | Peak: -∞ dBFS | Gain: 100%")
        self.lbl_mic_db_detail.setStyleSheet("color: #a6adc8; font-size: 8.5pt;")
        vbox_meter.addWidget(self.lbl_mic_db_detail)

        # Manual Mic Gain
        row_gain = QHBoxLayout()
        row_gain.setSpacing(6)
        row_gain.addWidget(QLabel("Manual Gain:"))
        self.slider_mic_gain = QSlider(Qt.Horizontal)
        self.slider_mic_gain.setRange(0, 150)
        self.slider_mic_gain.setValue(100)
        self.lbl_mic_gain_val = QLabel("100%")
        self.lbl_mic_gain_val.setMinimumWidth(40)
        self.slider_mic_gain.valueChanged.connect(self._mic_on_gain_slider_changed)
        row_gain.addWidget(self.slider_mic_gain, 1)
        row_gain.addWidget(self.lbl_mic_gain_val)
        vbox_meter.addLayout(row_gain)

        col_left.addWidget(group_meter)

        # 2. PipeWire WebRTC DSP Virtual Mic
        group_hw_agc = QGroupBox("⚡ PipeWire WebRTC DSP Virtual Microphone (OS-Native)")
        vbox_hw = QVBoxLayout(group_hw_agc)
        vbox_hw.setContentsMargins(12, 14, 12, 12)
        vbox_hw.setSpacing(8)

        desc_hw = QLabel("Applies AI noise suppression, AGC, and high-pass filtering via an OS virtual microphone.")
        desc_hw.setStyleSheet("color: #a6adc8; font-size: 8.5pt;")
        vbox_hw.addWidget(desc_hw)

        grid_opts = QGridLayout()
        grid_opts.setHorizontalSpacing(10)
        grid_opts.setVerticalSpacing(4)
        self.chk_webrtc_agc = QCheckBox("Auto Gain Control (AGC)")
        self.chk_webrtc_agc.setChecked(True)
        self.chk_webrtc_noise = QCheckBox("AI Noise Suppression")
        self.chk_webrtc_noise.setChecked(True)
        self.chk_webrtc_highpass = QCheckBox("High-Pass Filter")
        self.chk_webrtc_highpass.setChecked(True)
        self.chk_webrtc_vad = QCheckBox("Voice Activity Detection (VAD)")
        self.chk_webrtc_vad.setChecked(True)

        grid_opts.addWidget(self.chk_webrtc_agc, 0, 0)
        grid_opts.addWidget(self.chk_webrtc_noise, 0, 1)
        grid_opts.addWidget(self.chk_webrtc_highpass, 1, 0)
        grid_opts.addWidget(self.chk_webrtc_vad, 1, 1)
        vbox_hw.addLayout(grid_opts)

        row_hw_btn = QHBoxLayout()
        row_hw_btn.setSpacing(6)
        self.lbl_hw_status = QLabel(" Checking status... ")
        self.lbl_hw_status.setObjectName("status_badge")
        row_hw_btn.addWidget(self.lbl_hw_status, 1)

        self.btn_hw_disable = QPushButton("❌ Disable Virtual Mic")
        self.btn_hw_disable.setObjectName("btn_danger")
        self.btn_hw_disable.clicked.connect(self._mic_disable_hardware_agc)
        row_hw_btn.addWidget(self.btn_hw_disable)

        self.btn_hw_enable = QPushButton("🎙️ Enable Virtual Mic")
        self.btn_hw_enable.setObjectName("btn_primary")
        self.btn_hw_enable.clicked.connect(self._mic_enable_hardware_agc)
        row_hw_btn.addWidget(self.btn_hw_enable)
        vbox_hw.addLayout(row_hw_btn)

        col_left.addWidget(group_hw_agc)
        col_left.addStretch()
        layout.addLayout(col_left, 50)

        # ========================================================
        # Right column: Real-time Dynamic Gain & Quiet Test
        # ========================================================
        col_right = QVBoxLayout()
        col_right.setSpacing(10)

        # 3. Real-time Dynamic Gain Auto-Tracking
        group_soft = QGroupBox("🎛️ Dynamic Gain Auto-Tracking (Software Control)")
        vbox_soft = QVBoxLayout(group_soft)
        vbox_soft.setContentsMargins(12, 14, 12, 12)
        vbox_soft.setSpacing(8)

        self.chk_soft_agc_enable = QCheckBox("Enable Real-Time Dynamic Gain Tracking")
        self.chk_soft_agc_enable.toggled.connect(self._mic_on_soft_agc_toggled)
        vbox_soft.addWidget(self.chk_soft_agc_enable)

        # Target Volume dBFS
        row_target = QHBoxLayout()
        row_target.setSpacing(6)
        row_target.addWidget(QLabel("Target Volume:"))
        self.slider_target_db = QSlider(Qt.Horizontal)
        self.slider_target_db.setRange(-30, -10)
        self.slider_target_db.setValue(-18)
        self.lbl_target_db = QLabel("-18 dBFS")
        self.lbl_target_db.setMinimumWidth(55)
        self.slider_target_db.valueChanged.connect(
            lambda v: (self.lbl_target_db.setText(f"{v} dBFS"), self._mic_update_soft_params())
        )
        row_target.addWidget(self.slider_target_db, 1)
        row_target.addWidget(self.lbl_target_db)
        vbox_soft.addLayout(row_target)

        # Noise Floor threshold
        row_floor = QHBoxLayout()
        row_floor.setSpacing(6)
        row_floor.addWidget(QLabel("Noise Floor:"))
        self.slider_noise_floor = QSlider(Qt.Horizontal)
        self.slider_noise_floor.setRange(-60, -30)
        self.slider_noise_floor.setValue(-48)
        self.lbl_noise_floor = QLabel("-48 dBFS")
        self.lbl_noise_floor.setMinimumWidth(55)
        self.slider_noise_floor.valueChanged.connect(
            lambda v: (self.lbl_noise_floor.setText(f"{v} dBFS"), self._mic_update_soft_params())
        )
        row_floor.addWidget(self.slider_noise_floor, 1)
        row_floor.addWidget(self.lbl_noise_floor)
        vbox_soft.addLayout(row_floor)

        # AGC Action Log
        self.txt_agc_log = QTextEdit()
        self.txt_agc_log.setReadOnly(True)
        self.txt_agc_log.setFixedHeight(50)
        self.txt_agc_log.setStyleSheet("background-color: #11111b; color: #a6e3a1; font-family: monospace; font-size: 8pt; border: 1px solid #313244; border-radius: 4px; padding: 4px;")
        vbox_soft.addWidget(self.txt_agc_log)

        col_right.addWidget(group_soft)

        # 4. Quiet / Night Test Simulation Mode
        group_night = QGroupBox("🌙 Quiet / Night Test Simulation Mode")
        vbox_night = QVBoxLayout(group_night)
        vbox_night.setContentsMargins(12, 14, 12, 12)
        vbox_night.setSpacing(8)

        lbl_night_desc = QLabel("Simulate whisper gain tracking or inject an ultra-quiet test tone (-30 dBFS).")
        lbl_night_desc.setStyleSheet("color: #a6adc8; font-size: 8.5pt;")
        vbox_night.addWidget(lbl_night_desc)

        row_night_act = QHBoxLayout()
        row_night_act.setSpacing(6)
        self.lbl_quiet_detect = QLabel(" 🌙 Quiet Test: Idle ")
        self.lbl_quiet_detect.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 4px 8px; font-size: 8.5pt;")
        row_night_act.addWidget(self.lbl_quiet_detect, 1)

        self.btn_night_sim = QPushButton("🧪 Inject Test Tone (-30dBFS)")
        self.btn_night_sim.clicked.connect(self._mic_play_night_test_tone)
        row_night_act.addWidget(self.btn_night_sim)
        vbox_night.addLayout(row_night_act)

        col_right.addWidget(group_night)
        col_right.addStretch()
        layout.addLayout(col_right, 50)

        # Initialization
        self._mic_refresh_sources()
        self._mic_refresh_hw_status()
        self._mic_start_monitor()

        self.cmb_mic_source.currentIndexChanged.connect(self._mic_on_source_selected)

        return tab

    def _mic_refresh_hw_status(self):
        """Update Hardware AGC status badge"""
        if self.agc_mgr.is_hardware_agc_enabled():
            self.lbl_hw_status.setText(" ● Virtual Mic Active ")
            self.lbl_hw_status.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 6px; padding: 3px 8px;")
        else:
            self.lbl_hw_status.setText(" ○ Virtual Mic Inactive ")
            self.lbl_hw_status.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 3px 8px;")

    def _mic_refresh_sources(self):
        """Populate microphone source list"""
        self.cmb_mic_source.blockSignals(True)
        self.cmb_mic_source.clear()
        self.cmb_mic_source.addItem("Default Microphone (Default)", "default")
        try:
            import pulsectl
            with pulsectl.Pulse('guipipewire-mic-list') as pulse:
                for src in pulse.source_list():
                    if not src.name.endswith(".monitor"):
                        desc = src.description or src.name
                        self.cmb_mic_source.addItem(f"{desc}", src.name)
        except Exception:
            pass
        self.cmb_mic_source.blockSignals(False)

    def _mic_start_monitor(self):
        """Start microphone level monitor thread"""
        if hasattr(self, 'mic_monitor_thread') and self.mic_monitor_thread:
            self.mic_monitor_thread.stop()
            self.mic_monitor_thread.wait(500)

        source_name = self.cmb_mic_source.currentData() or "default"
        self.mic_monitor_thread = MicrophoneMonitorThread(source_name=source_name, parent=self)
        self.mic_monitor_thread.level_updated.connect(self._mic_on_level_updated)
        self.mic_monitor_thread.agc_action.connect(self._mic_log)
        self._mic_update_soft_params()
        self.mic_monitor_thread.start()

    def _mic_on_source_selected(self):
        """Handle microphone selection change"""
        self._mic_start_monitor()

    def _mic_update_soft_params(self):
        """Apply software AGC parameters to worker thread"""
        if hasattr(self, 'mic_monitor_thread') and self.mic_monitor_thread:
            enabled = self.chk_soft_agc_enable.isChecked()
            target = float(self.slider_target_db.value())
            noise_floor = float(self.slider_noise_floor.value())
            self.mic_monitor_thread.set_agc_params(
                enabled=enabled,
                target_db=target,
                noise_floor_db=noise_floor,
                max_gain=1.5
            )

    def _mic_on_soft_agc_toggled(self, checked):
        self._mic_update_soft_params()
        if checked:
            self._mic_log("[AGC] Real-time dynamic gain auto-tracking started")
        else:
            self._mic_log("[AGC] Real-time dynamic gain auto-tracking stopped")

    def _mic_on_gain_slider_changed(self, val):
        """Handle manual mic gain slider change"""
        self.lbl_mic_gain_val.setText(f"{val}%")
        if not self.chk_soft_agc_enable.isChecked():
            try:
                import pulsectl
                with pulsectl.Pulse('guipipewire-gain-set') as pulse:
                    source_name = self.cmb_mic_source.currentData()
                    if source_name == "default":
                        source_name = pulse.server_info().default_source_name
                    src = pulse.get_source_by_name(source_name)
                    if src:
                        pulse.volume_set_all_chans(src, val / 100.0)
            except Exception:
                pass

    def _mic_on_level_updated(self, rms_db, peak_db, current_vol, is_clipping):
        """Handle level updates from monitor thread"""
        progress_val = max(0, min(100, int((rms_db + 60.0) * (100.0 / 60.0))))
        self.prog_mic_level.setValue(progress_val)
        self.prog_mic_level.setFormat(f"{rms_db:.1f} dBFS")

        self.lbl_mic_db_detail.setText(
            f"RMS: {rms_db:.1f} dBFS | Peak: {peak_db:.1f} dBFS | Gain: {int(current_vol * 100)}%"
        )

        # Clipping / status badge updates
        if is_clipping or peak_db > -2.0:
            self.lbl_mic_clip.setText(" ⚠️ Clipping ")
            self.lbl_mic_clip.setStyleSheet("background-color: #f38ba8; color: #11111b; font-weight: bold; border-radius: 6px; padding: 2px 6px;")
        elif rms_db > -18.0:
            self.lbl_mic_clip.setText(" 🔊 Loud ")
            self.lbl_mic_clip.setStyleSheet("background-color: #fab387; color: #11111b; font-weight: bold; border-radius: 6px; padding: 2px 6px;")
        elif rms_db > -35.0:
            self.lbl_mic_clip.setText(" ● Optimal ")
            self.lbl_mic_clip.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 6px; padding: 2px 6px;")
        elif rms_db > -50.0:
            self.lbl_mic_clip.setText(" 🔈 Quiet ")
            self.lbl_mic_clip.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 6px; padding: 2px 6px;")
        else:
            self.lbl_mic_clip.setText(" Silent ")
            self.lbl_mic_clip.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 2px 6px;")

        # Quiet test status indicator
        if -45.0 < rms_db < -25.0:
            self.lbl_quiet_detect.setText(f" 🌙 Whisper detected ({rms_db:.1f} dBFS): Boosting Gain ")
            self.lbl_quiet_detect.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 6px; padding: 4px 8px; font-size: 8.5pt;")
        elif rms_db >= -25.0:
            self.lbl_quiet_detect.setText(f" 🔊 Normal Input ({rms_db:.1f} dBFS) ")
            self.lbl_quiet_detect.setStyleSheet("background-color: #313244; color: #cdd6f4; border-radius: 6px; padding: 4px 8px; font-size: 8.5pt;")
        else:
            self.lbl_quiet_detect.setText(" 🌙 Quiet Test: Idle ")
            self.lbl_quiet_detect.setStyleSheet("background-color: #313244; color: #a6adc8; border-radius: 6px; padding: 4px 8px; font-size: 8.5pt;")

        if self.chk_soft_agc_enable.isChecked():
            self.slider_mic_gain.blockSignals(True)
            self.slider_mic_gain.setValue(int(current_vol * 100))
            self.lbl_mic_gain_val.setText(f"{int(current_vol * 100)}%")
            self.slider_mic_gain.blockSignals(False)

    def _mic_enable_hardware_agc(self):
        """Enable PipeWire WebRTC AGC virtual microphone"""
        try:
            conf_path = self.agc_mgr.enable_hardware_agc(
                gain_control=self.chk_webrtc_agc.isChecked(),
                noise_suppression=self.chk_webrtc_noise.isChecked(),
                high_pass=self.chk_webrtc_highpass.isChecked(),
                voice_detection=self.chk_webrtc_vad.isChecked()
            )
            self.service_mgr.restart_pipewire()
            self._mic_refresh_hw_status()
            self._mic_refresh_sources()
            self.update_live_status()
            self._mic_log(f"WebRTC DSP virtual microphone enabled: {conf_path}")
            QMessageBox.information(
                self,
                "Virtual Mic Enabled",
                "PipeWire WebRTC DSP (AGC & Noise Suppression) virtual microphone created and activated.\n"
                "PipeWire restarted.\n\n"
"In Discord, OBS, or browser input device settings,\n"
"or browser input device settings."
            )
        except Exception as e:
            self._mic_log(f"AGC enable error: {e}")
            QMessageBox.critical(self, "Error", f"Failed to enable AGC: {e}")

    def _mic_disable_hardware_agc(self):
        """Disable PipeWire WebRTC AGC virtual microphone"""
        try:
            self.agc_mgr.disable_hardware_agc()
            self.service_mgr.restart_pipewire()
            self._mic_refresh_hw_status()
            self._mic_refresh_sources()
            self.update_live_status()
            self._mic_log("WebRTC DSP virtual microphone disabled")
            QMessageBox.information(
                self,
                "Virtual Mic Disabled",
                "PipeWire restarted.\n\n"
)
        except Exception as e:
            self._mic_log(f"AGC disable error: {e}")
            QMessageBox.critical(self, "Error", f"Failed to disable AGC: {e}")

    def _mic_log(self, msg):
        """Append to AGC log"""
        self.txt_agc_log.append(msg)

    def _mic_play_night_test_tone(self):
        """Play ultra-quiet test tone (-30dBFS) asynchronously"""
        self._mic_log("[Quiet Test] Playing ultra-quiet test tone (-30dBFS)...")
        script = """
import numpy as np
sr = 44100
t = np.linspace(0, 3, int(sr * 3), False)
tone = 0.03 * np.sin(2 * np.pi * 1000 * t)
import subprocess
raw = (tone * 32767).astype(np.int16).tobytes()
p = subprocess.Popen(['pw-play', '--rate=44100', '--channels=1', '--format=s16', '-'], stdin=subprocess.PIPE)
p.communicate(input=raw)
"""
        try:
            subprocess.Popen([sys.executable, "-c", script])
        except Exception as e:
            self._mic_log(f"Error playing test tone: {e}")
