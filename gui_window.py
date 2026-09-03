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
    QSplitter, QStatusBar, QFrame, QScrollArea, QStyleFactory
)

from pipewire_config import PipeWireConfigManager, STANDARD_RATES, STANDARD_QUANTUMS
from pipewire_service import PipeWireServiceManager



# ============================================================
# 5.1ch アップミックス関連定数 & ヘルパー関数
# ============================================================

UPMIX_CONF_PATHS = [
    os.path.expanduser("~/.config/pipewire/pipewire-pulse.conf.d/20-upmix.conf"),
    os.path.expanduser("~/.config/pipewire/client.conf.d/20-upmix.conf"),
]

UPMIX_PRESETS = {
    "標準 (PSD標準)": {
        "method": "psd",
        "lfe_cutoff": 150,
        "rear_delay": 0.0,
        "fc_cutoff": 12000,
        "stereo_widen": 0.0,
    },
    "映画・サラウンド重視": {
        "method": "psd",
        "lfe_cutoff": 120,
        "rear_delay": 15.0,
        "fc_cutoff": 10000,
        "stereo_widen": 0.1,
    },
    "音楽・自然な広がり": {
        "method": "psd",
        "lfe_cutoff": 80,
        "rear_delay": 5.0,
        "fc_cutoff": 0,
        "stereo_widen": 0.2,
    },
    "シンプル (Simple方式)": {
        "method": "simple",
        "lfe_cutoff": 120,
        "rear_delay": 0.0,
        "fc_cutoff": 0,
        "stereo_widen": 0.0,
    },
}
UPMIX_CUSTOM_PRESET_NAME = "カスタム (手動設定)"


def upmix_is_enabled() -> bool:
    """両方の設定ファイルが存在するとき True を返す。"""
    return all(os.path.exists(p) for p in UPMIX_CONF_PATHS)


def upmix_parse_config(filepath: str) -> dict:
    """設定ファイルを読み込んでパラメータ辞書を返す。"""
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
    """パラメータ辞書から PipeWire 設定ファイル文字列を生成する。"""
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
    """pw-top をバックグラウンドスレッドで実行し、結果をシグナルで通知するワーカー"""
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
            self.result_ready.emit(f"pw-top 取得エラー: {e}")


class GUIPipeWireWindow(QMainWindow):
    """PipeWire GUI 設定ツールのメインウィンドウ"""

    BASE_TITLE = "GUIPipeWire"

    def __init__(self):
        super().__init__()
        self.config_mgr = PipeWireConfigManager()
        self.service_mgr = PipeWireServiceManager()
        self._unsaved_changes = False

        self.setWindowTitle(self.BASE_TITLE)
        self.resize(880, 680)

        # 全体スタイルの適用
        self._apply_dark_theme()

        # UI コンポーネント構築
        self._init_ui()

        # 起動時に必須コマンドを確認
        self._check_required_commands()

        # 設定の読み込み
        self.load_all_settings()
        # 読み込み直後はまだ未保存フラグを立てない
        self._unsaved_changes = False
        self._update_title()

        # タイマー1: pw-metadata を 3 秒ごとに更新 (軽量)
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(3000)
        self.status_timer.timeout.connect(self.update_live_status)
        self.status_timer.start()

        # タイマー2: pw-top を 6 秒ごとに更新 (バックグラウンドスレッド)
        self.pwtop_timer = QTimer(self)
        self.pwtop_timer.setInterval(6000)
        self.pwtop_timer.timeout.connect(self.trigger_pw_top_update)
        self.pwtop_timer.start()

        # pw-top ワーカー
        self._pwtop_worker = None

        # 初回更新
        self.update_live_status()
        self.trigger_pw_top_update()

    def _apply_dark_theme(self):
        """洗練されたダークモードスタイルの設定"""
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

    def _init_ui(self):
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # トップヘッダー
        header_layout = QHBoxLayout()
        title_label = QLabel("PipeWire オーディオ GUI 設定")
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        title_label.setFont(title_font)
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        # サービス状態バッジ
        self.lbl_status_badge = QLabel(" 状態確認中... ")
        self.lbl_status_badge.setObjectName("status_badge")
        self.lbl_status_badge.setStyleSheet("background-color: #45475a; color: #cdd6f4;")
        header_layout.addWidget(self.lbl_status_badge)

        main_layout.addLayout(header_layout)

        # タブウィジェット
        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_clock_tab(), "⏱️ クロック・サンプリングレート")
        self.tabs.addTab(self._create_resample_tab(), "🎛️ クライアント・リサンプル")
        self.tabs.addTab(self._create_upmix_tab(), "🔊 5.1ch アップミックス")
        self.tabs.addTab(self._create_preset_tab(), "⚡ プリセット")
        self.tabs.addTab(self._create_raw_editor_tab(), "📝 設定ファイル直接編集")
        self.tabs.addTab(self._create_status_tab(), "📊 リアルタイム監視 & ログ")
        main_layout.addWidget(self.tabs)

        # 全設定ウィジェットの変更シグナルを _mark_unsaved に接続
        self._connect_change_signals()

        # アクションフッター (「設定保存 ＆ PipeWire 再起動」)
        footer_layout = QHBoxLayout()

        self.btn_reset = QPushButton("デフォルトにリセット")
        self.btn_reset.setObjectName("btn_danger")
        self.btn_reset.clicked.connect(self.on_reset_defaults)
        footer_layout.addWidget(self.btn_reset)

        footer_layout.addStretch()

        self.btn_restart_service = QPushButton("🔄 PipeWire のみ再起動 (systemctl)")
        self.btn_restart_service.clicked.connect(self.on_restart_service_only)
        footer_layout.addWidget(self.btn_restart_service)

        self.btn_save_apply = QPushButton("💾 設定を保存して PipeWire を再起動")
        self.btn_save_apply.setObjectName("btn_primary")
        self.btn_save_apply.clicked.connect(self.on_save_and_apply)
        footer_layout.addWidget(self.btn_save_apply)

        main_layout.addLayout(footer_layout)

        self.setCentralWidget(main_widget)

    # -------------------------------------------------------------
    # タブ1: クロック & サンプリングレート
    # -------------------------------------------------------------
    def _create_clock_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        # デフォルトクロックレート
        group_rate = QGroupBox("デフォルト・クロックレート (default.clock.rate)")
        layout_rate = QVBoxLayout(group_rate)

        hbox_rate = QHBoxLayout()
        lbl_rate = QLabel("標準動作周波数 (Hz):")
        self.cmb_default_rate = QComboBox()
        for rate in STANDARD_RATES:
            label = f"{rate} Hz ({rate/1000:.1f} kHz)"
            if rate == 48000:
                label += " ★ [デフォルト / 推奨]"
            elif rate == 44100:
                label += " (CD標準)"
            elif rate == 96000:
                label += " (ハイレゾ)"
            self.cmb_default_rate.addItem(label, rate)

        idx_48k = self.cmb_default_rate.findData(48000)
        if idx_48k >= 0:
            self.cmb_default_rate.setCurrentIndex(idx_48k)

        hbox_rate.addWidget(lbl_rate)
        hbox_rate.addWidget(self.cmb_default_rate)
        hbox_rate.addStretch()
        layout_rate.addLayout(hbox_rate)

        lbl_rate_desc = QLabel("💡 PipeWireのデフォルトかつ最もオススメの設定は 「48000 Hz (48 kHz)」 です。\n   動画再生・ゲーム・配信・一般的なオーディオ機器と最も親和性が高くトラブルが少なくなります。")
        lbl_rate_desc.setStyleSheet("color: #a6adc8; font-size: 9pt;")
        layout_rate.addWidget(lbl_rate_desc)

        layout.addWidget(group_rate)

        # 許可サンプリングレート (default.clock.allowed-rates)
        group_allowed = QGroupBox("許可サンプリングレート (default.clock.allowed-rates)")
        layout_allowed = QVBoxLayout(group_allowed)
        lbl_allowed_info = QLabel("音源のサンプリングレートに応じてPipeWireが自動切り替えを許可する周波数群:")
        lbl_allowed_info.setStyleSheet("color: #a6adc8;")
        layout_allowed.addWidget(lbl_allowed_info)

        grid_rates = QGridLayout()
        self.rate_checkboxes = {}
        for idx, rate in enumerate(STANDARD_RATES):
            cb = QCheckBox(f"{rate} Hz ({rate/1000:.1f} kHz)")
            grid_rates.addWidget(cb, idx // 4, idx % 4)
            self.rate_checkboxes[rate] = cb

        layout_allowed.addLayout(grid_rates)

        # 選択ヘルパーボタン
        btn_layout = QHBoxLayout()
        btn_all = QPushButton("全選択")
        btn_all.clicked.connect(lambda: self._set_rate_checkboxes(STANDARD_RATES))
        btn_std = QPushButton("標準 (44.1k / 48k)")
        btn_std.clicked.connect(lambda: self._set_rate_checkboxes([44100, 48000]))
        btn_hires = QPushButton("ハイレゾ重視 (44.1k~192k)")
        btn_hires.clicked.connect(lambda: self._set_rate_checkboxes([44100, 48000, 88200, 96000, 176400, 192000]))

        btn_layout.addWidget(btn_all)
        btn_layout.addWidget(btn_std)
        btn_layout.addWidget(btn_hires)
        btn_layout.addStretch()
        layout_allowed.addLayout(btn_layout)

        layout.addWidget(group_allowed)

        # バッファ & レイテンシ (Quantum)
        group_quantum = QGroupBox("バッファサイズ & レイテンシ設定 (Quantum)")
        grid_q = QGridLayout(group_quantum)

        grid_q.addWidget(QLabel("デフォルト Quantum (default.clock.quantum):"), 0, 0)
        self.cmb_quantum = QComboBox()
        for q in STANDARD_QUANTUMS:
            label = f"{q} samples ({q/48:.1f}ms @ 48k)"
            if q == 1024:
                label += " ★ [デフォルト / 標準推奨]"
            elif q == 512:
                label += " (低遅延バランス)"
            elif q == 128:
                label += " (超低遅延 DTM)"
            self.cmb_quantum.addItem(label, q)
        grid_q.addWidget(self.cmb_quantum, 0, 1)

        grid_q.addWidget(QLabel("最小 Quantum (default.clock.min-quantum):"), 1, 0)
        self.cmb_min_quantum = QComboBox()
        for q in [16, 32, 64, 128, 256, 512]:
            self.cmb_min_quantum.addItem(f"{q} samples", q)
        grid_q.addWidget(self.cmb_min_quantum, 1, 1)

        grid_q.addWidget(QLabel("最大 Quantum (default.clock.max-quantum):"), 2, 0)
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
        """設定ウィジェットが変更されたら未保存フラグを立てる"""
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
    # タブ2: クライアント & リサンプル設定
    # -------------------------------------------------------------
    def _create_resample_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        # リサンプル品質
        group_quality = QGroupBox("リサンプル品質 (resample.quality)")
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

        hbox_slider.addWidget(QLabel("品質レベル (0~15):"))
        hbox_slider.addWidget(self.slider_quality)
        hbox_slider.addWidget(self.spin_quality)
        vbox_q.addLayout(hbox_slider)

        self.lbl_quality_desc = QLabel("4: デフォルト (バランス良好)")
        self.lbl_quality_desc.setStyleSheet("color: #89b4fa; font-weight: bold;")
        vbox_q.addWidget(self.lbl_quality_desc)

        layout.addWidget(group_quality)

        # チャンネルミックス & その他
        group_mix = QGroupBox("チャンネルミックス & リサンプル制御")
        vbox_mix = QVBoxLayout(group_mix)

        self.cb_disable_resample = QCheckBox("resample.disable (リサンプル処理を完全に禁止する)")
        self.cb_normalize = QCheckBox("channelmix.normalize (チャンネルミキシング時の音量を正規化する)")
        self.cb_upmix = QCheckBox("channelmix.upmix (ステレオ音源をマルチチャンネルにアップミックスする)")

        vbox_mix.addWidget(self.cb_disable_resample)
        vbox_mix.addWidget(self.cb_normalize)
        vbox_mix.addWidget(self.cb_upmix)

        hbox_upmix_method = QHBoxLayout()
        hbox_upmix_method.addWidget(QLabel("アップミックス方式 (channelmix.upmix-method):"))
        self.cmb_upmix_method = QComboBox()
        self.cmb_upmix_method.addItems(["psd", "simple", "none"])
        hbox_upmix_method.addWidget(self.cmb_upmix_method)
        hbox_upmix_method.addStretch()
        vbox_mix.addLayout(hbox_upmix_method)

        layout.addWidget(group_mix)

        # 保存先インフォ
        lbl_info = QLabel("※ この設定は ~/.config/pipewire/client.conf.d/10-resample.conf に保存されます。")
        lbl_info.setStyleSheet("color: #a6adc8; font-style: italic;")
        layout.addWidget(lbl_info)

        layout.addStretch()
        return tab

    def _update_quality_label(self, val):
        descriptions = {
            0: "0: 最低品質 (極めて軽量)",
            4: "4: デフォルト (標準CPU使用量 / 良好な音質)",
            10: "10: 高品位 (Hi-Fiオーディオ推奨)",
            14: "14: 最高精度 (マスタリング級 / 高精度のリサンプリング)",
            15: "15: 最大負荷 (実験的)",
        }
        text = descriptions.get(val, f"{val}: カスタム品質レベル")
        self.lbl_quality_desc.setText(text)

    # -------------------------------------------------------------
    # タブ3: プリセット
    # -------------------------------------------------------------
    def _create_preset_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        lbl_desc = QLabel("用途に合った最適なプリセットをワンクリックで選択できます:")
        layout.addWidget(lbl_desc)

        # プリセット1: Hi-Fi Audiophile
        btn_hifi = QPushButton("🎵 ハイレゾオーディオ (Hi-Fi Audiophile)")
        btn_hifi.setStyleSheet("text-align: left; padding: 14px; font-size: 11pt;")
        btn_hifi.clicked.connect(self.apply_hifi_preset)
        lbl_hifi_info = QLabel("  ・allowed-rates: [44.1k, 48k, 88.2k, 96k, 176.4k, 192k, 352.8k, 384k]\n  ・resample.quality: 14 (最高精度)")
        lbl_hifi_info.setStyleSheet("color: #a6adc8; margin-bottom: 10px;")

        # プリセット2: DTM / Low Latency
        btn_dtm = QPushButton("⚡ 低遅延 DTM / ゲーミング (Low Latency Pro)")
        btn_dtm.setStyleSheet("text-align: left; padding: 14px; font-size: 11pt;")
        btn_dtm.clicked.connect(self.apply_low_latency_preset)
        lbl_dtm_info = QLabel("  ・allowed-rates: [48k, 96k]\n  ・min-quantum: 64, quantum: 128 (低バッファ・低レイテンシ)")
        lbl_dtm_info.setStyleSheet("color: #a6adc8; margin-bottom: 10px;")

        # プリセット3: 標準
        btn_std = QPushButton("📻 標準バランス (Standard 44.1k / 48k)")
        btn_std.setStyleSheet("text-align: left; padding: 14px; font-size: 11pt;")
        btn_std.clicked.connect(self.apply_standard_preset)
        lbl_std_info = QLabel("  ・allowed-rates: [44.1k, 48k]\n  ・quantum: 1024, resample.quality: 4")
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
        QMessageBox.information(self, "プリセット適用", "「ハイレゾオーディオ」プリセットを画面上に読み込みました。\n「設定を保存して PipeWire を再起動」を押して反映してください。")

    def apply_low_latency_preset(self):
        self.cmb_default_rate.setCurrentIndex(self.cmb_default_rate.findData(96000))
        self._set_rate_checkboxes([48000, 96000])
        self.cmb_quantum.setCurrentIndex(self.cmb_quantum.findData(128))
        self.cmb_min_quantum.setCurrentIndex(self.cmb_min_quantum.findData(64))
        self.spin_quality.setValue(4)
        QMessageBox.information(self, "プリセット適用", "「低遅延 DTM / ゲーミング」プリセットを画面上に読み込みました。\n「設定を保存して PipeWire を再起動」を押して反映してください。")

    def apply_standard_preset(self):
        self.cmb_default_rate.setCurrentIndex(self.cmb_default_rate.findData(48000))
        self._set_rate_checkboxes([44100, 48000])
        self.cmb_quantum.setCurrentIndex(self.cmb_quantum.findData(1024))
        self.spin_quality.setValue(4)
        QMessageBox.information(self, "プリセット適用", "「標準バランス」プリセットを画面上に読み込みました。\n「設定を保存して PipeWire を再起動」を押して反映してください。")

    # -------------------------------------------------------------
    # タブ4: 設定ファイル直接編集
    # -------------------------------------------------------------
    def _create_raw_editor_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        hbox_select = QHBoxLayout()
        hbox_select.addWidget(QLabel("編集対象ファイル:"))
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

        # 既存の他ファイルも検索して追加
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

        btn_reload_raw = QPushButton("再読み込み")
        btn_reload_raw.clicked.connect(self._load_raw_file_to_editor)
        hbox_select.addWidget(btn_reload_raw)

        layout.addLayout(hbox_select)

        self.txt_raw_editor = QTextEdit()
        layout.addWidget(self.txt_raw_editor)

        btn_save_raw = QPushButton("💾 このテキストファイルの内容を保存")
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
                self.txt_raw_editor.setText(f"# ファイル読込エラー: {e}")
        else:
            self.txt_raw_editor.setText("# (このファイルは未作成です。設定保存時に自動生成されます)")

    def _save_raw_file_from_editor(self):
        file_path = self.cmb_editor_file.currentData()
        if not file_path:
            return
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self.txt_raw_editor.toPlainText())
            QMessageBox.information(self, "保存完了", f"{os.path.basename(file_path)} を保存しました。")
            self.load_all_settings()
            if hasattr(self, "_upmix_load_from_file"):
                self._upmix_load_from_file()
                self._upmix_refresh_status_badge()
        except Exception as e:
            QMessageBox.critical(self, "保存エラー", f"保存失敗: {e}")

    # -------------------------------------------------------------
    # タブ5: リアルタイム監視 & ログ
    # -------------------------------------------------------------
    def _create_status_tab(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # リアルタイム設定表示
        group_live = QGroupBox("現在の PipeWire 動作ステータス (pw-metadata settings)")
        vbox_live = QVBoxLayout(group_live)
        self.lbl_live_metadata = QLabel("取得中...")
        self.lbl_live_metadata.setStyleSheet("font-family: monospace; color: #a6e3a1;")
        vbox_live.addWidget(self.lbl_live_metadata)
        layout.addWidget(group_live)

        # pw-top ノード再生状態
        group_pwtop = QGroupBox("現在の再生・ノード状態 (pw-top リアルタイム情報)")
        vbox_pwtop = QVBoxLayout(group_pwtop)
        self.txt_pwtop = QTextEdit()
        self.txt_pwtop.setReadOnly(True)
        self.txt_pwtop.setStyleSheet("font-family: monospace; background-color: #181825; color: #cdd6f4;")
        self.txt_pwtop.setMaximumHeight(180)
        vbox_pwtop.addWidget(self.txt_pwtop)

        btn_refresh_pwtop = QPushButton("🔄 pw-top 情報を更新")
        btn_refresh_pwtop.clicked.connect(self.update_pw_top)
        vbox_pwtop.addWidget(btn_refresh_pwtop)
        layout.addWidget(group_pwtop)

        # journalctl ログ
        group_logs = QGroupBox("PipeWire システムログ (journalctl --user -u pipewire)")
        vbox_logs = QVBoxLayout(group_logs)
        self.txt_logs = QTextEdit()
        self.txt_logs.setReadOnly(True)
        vbox_logs.addWidget(self.txt_logs)

        btn_refresh_logs = QPushButton("🔄 ログを更新")
        btn_refresh_logs.clicked.connect(self.update_logs)
        vbox_logs.addWidget(btn_refresh_logs)

        layout.addWidget(group_logs)
        return tab

    # -------------------------------------------------------------
    # ロジック
    # -------------------------------------------------------------
    def load_all_settings(self):
        """設定ファイルから現在の値をUIへ反映"""
        clock_settings = self.config_mgr.load_clock_settings()

        # デフォルトクロックレート
        def_rate = clock_settings.get("default.clock.rate", 48000)
        idx = self.cmb_default_rate.findData(def_rate)
        if idx >= 0:
            self.cmb_default_rate.setCurrentIndex(idx)

        # 許可サンプリングレート
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

        # リサンプル設定
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

        # 5.1ch アップミックス設定の読み込み
        if hasattr(self, "_upmix_load_from_file"):
            self._upmix_load_from_file()
            self._upmix_refresh_status_badge()

    def update_live_status(self):
        """サービス状態と pw-metadata の定期更新"""
        is_active, status_str = self.service_mgr.get_service_status()

        if is_active:
            self.lbl_status_badge.setText(" ● PipeWire 動作中 ")
            self.lbl_status_badge.setStyleSheet("background-color: #a6e3a1; color: #11111b;")
        else:
            self.lbl_status_badge.setText(" ✖ 停止中 ")
            self.lbl_status_badge.setStyleSheet("background-color: #f38ba8; color: #11111b;")

        meta = self.service_mgr.get_live_metadata()
        meta_text = (
            f"現在の動的サンプリングレート: {meta.get('clock.rate')}\n"
            f"許可されたサンプリングレート : {meta.get('clock.allowed-rates')}\n"
            f"現在の Quantum (バッファ)   : {meta.get('clock.quantum')}\n"
            f"Quantum 範囲 (min ~ max)  : {meta.get('clock.min-quantum')} ~ {meta.get('clock.max-quantum')}"
        )
        self.lbl_live_metadata.setText(meta_text)

    def update_logs(self):
        logs = self.service_mgr.get_recent_logs(80)
        self.txt_logs.setText(logs)
        # 最下部へスクロール
        sb = self.txt_logs.verticalScrollBar()
        sb.setValue(sb.maximum())

    def on_save_and_apply(self):
        """設定を保存し systemctl --user restart pipewire を実行"""
        # クロック設定
        default_rate = self.cmb_default_rate.currentData()
        selected_rates = [rate for rate, cb in self.rate_checkboxes.items() if cb.isChecked()]

        if not selected_rates:
            QMessageBox.warning(self, "警告", "許可サンプリングレートが1つも選択されていません。少なくとも1つ選択してください。")
            return

        if default_rate not in selected_rates:
            selected_rates.append(default_rate)
            QMessageBox.information(
                self, "自動修正",
                f"デフォルト動作周波数 ({default_rate} Hz) が許可サンプリングレートに含まれていなかったため、自動的に追加しました。"
            )

        quantum = self.cmb_quantum.currentData()
        min_quantum = self.cmb_min_quantum.currentData()
        max_quantum = self.cmb_max_quantum.currentData()

        # 保存
        f1 = self.config_mgr.save_clock_settings(
            rate=default_rate,
            allowed_rates=selected_rates,
            min_quantum=min_quantum,
            max_quantum=max_quantum,
            quantum=quantum
        )

        # リサンプル設定
        f2 = self.config_mgr.save_resample_settings(
            quality=self.spin_quality.value(),
            disable=self.cb_disable_resample.isChecked(),
            normalize=self.cb_normalize.isChecked(),
            upmix=self.cb_upmix.isChecked(),
            upmix_method=self.cmb_upmix_method.currentText()
        )

        # PipeWire 再起動 (systemctl --user restart pipewire)
        results = self.service_mgr.restart_pipewire()

        success = all(res[1] for res in results)
        if success:
            self._unsaved_changes = False
            self._update_title()
            QMessageBox.information(
                self,
                "保存 & 再起動完了",
                f"設定ファイルを正常に更新しました:\n・{f1}\n・{f2}\n\n`systemctl --user restart pipewire` を実行しました！"
            )
        else:
            err_msg = "\n".join([f"{res[0]}: {res[2]}" for res in results if not res[1]])
            QMessageBox.critical(self, "再起動エラー", f"設定ファイルは保存されましたが、PipeWireの再起動でエラーが発生しました:\n{err_msg}")

        self.update_live_status()
        self.update_logs()

    def on_restart_service_only(self):
        results = self.service_mgr.restart_pipewire()
        success = all(res[1] for res in results)
        if success:
            QMessageBox.information(self, "再起動完了", "systemctl --user restart pipewire を実行しました。")
        else:
            QMessageBox.critical(self, "再起動エラー", "PipeWireの再起動に失敗しました。")
        self.update_live_status()
        self.update_logs()

    def on_reset_defaults(self):
        reply = QMessageBox.question(
            self,
            "初期化の確認",
            "GUIPipeWireで生成したカスタム設定ファイルを削除し、システムのデフォルト設定に戻しますか？\n(PipeWireも再起動されます)",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            removed = self.config_mgr.reset_custom_configs()
            self.service_mgr.restart_pipewire()
            self.load_all_settings()
            self.update_live_status()
            self.update_logs()
            self._unsaved_changes = False
            self._update_title()
            QMessageBox.information(self, "リセット完了", f"以下のカスタム設定ファイルを削除しデフォルトに復元しました:\n" + "\n".join(removed))


    # -------------------------------------------------------------
    # 未保存変更インジケーター & ユーティリティ
    # -------------------------------------------------------------
    def _update_title(self):
        """タイトルバーに未保存変更マークを反映"""
        if self._unsaved_changes:
            self.setWindowTitle(f"* {self.BASE_TITLE}  [未保存の変更あり]")
            self.btn_save_apply.setStyleSheet(
                "background-color: #f9e2af; color: #11111b; font-size: 11pt; "
                "padding: 10px 24px; border-radius: 6px; font-weight: bold;"
            )
        else:
            self.setWindowTitle(self.BASE_TITLE)
            self.btn_save_apply.setStyleSheet("")
            self.btn_save_apply.setObjectName("btn_primary")

    def _mark_unsaved(self):
        """UI の変更を検知して未保存フラグを立てる"""
        if not self._unsaved_changes:
            self._unsaved_changes = True
            self._update_title()

    def _check_required_commands(self):
        """起動時に必須コマンドの存在を確認して警告"""
        import shutil
        missing = [cmd for cmd in ["pw-top", "pw-metadata", "systemctl"] if shutil.which(cmd) is None]
        if missing:
            QMessageBox.warning(
                self,
                "必須コマンド未検出",
                "以下のコマンドが見つかりません。一部機能が利用できない場合があります:\n\n"
                + "\n".join(f"  ・{cmd}" for cmd in missing)
                + "\n\npipewire-bin / pipewire パッケージをインストールしてください。"
            )

    def trigger_pw_top_update(self):
        """pw-top をバックグラウンドスレッドで非同期実行"""
        # 前のワーカーがまだ動いている場合はスキップ
        if self._pwtop_worker is not None and self._pwtop_worker.isRunning():
            return
        self.txt_pwtop.setPlaceholderText("pw-top 取得中...")
        self._pwtop_worker = PwTopWorker()
        self._pwtop_worker.result_ready.connect(self._on_pwtop_result)
        self._pwtop_worker.start()

    def _on_pwtop_result(self, text: str):
        """バックグラウンドスレッドから pw-top 結果を受け取って表示"""
        self.txt_pwtop.setText(text)
        self.txt_pwtop.setPlaceholderText("")

    def update_pw_top(self):
        """後方互換のため残す（trigger_pw_top_update に委譲）"""
        self.trigger_pw_top_update()


    # ============================================================
    # タブ: 🔊 5.1ch アップミックス
    # ============================================================

    def _create_upmix_tab(self):
        """5.1ch アップミックス設定タブを生成して返す。"""
        self._upmix_updating = False
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(16)

        # --- 状態バッジ ---
        status_layout = QHBoxLayout()
        status_layout.addWidget(QLabel("現在の状態:"))
        self.lbl_upmix_status = QLabel()
        self.lbl_upmix_status.setObjectName("status_badge")
        self._upmix_refresh_status_badge()
        status_layout.addWidget(self.lbl_upmix_status)
        status_layout.addStretch()
        layout.addLayout(status_layout)

        # --- プリセット選択 ---
        group_preset = QGroupBox("プリセット選択")
        vbox_preset = QVBoxLayout(group_preset)
        preset_names = list(UPMIX_PRESETS.keys()) + [UPMIX_CUSTOM_PRESET_NAME]
        self.cmb_upmix_preset = QComboBox()
        self.cmb_upmix_preset.addItems(preset_names)
        self.cmb_upmix_preset.currentTextChanged.connect(self._upmix_on_preset_selected)
        vbox_preset.addWidget(self.cmb_upmix_preset)
        layout.addWidget(group_preset)

        # --- 詳細パラメータ ---
        group_params = QGroupBox("詳細パラメータ調整")
        params_layout = QVBoxLayout(group_params)
        params_layout.setSpacing(12)

        # アップミックス方式
        row_method = QHBoxLayout()
        row_method.addWidget(QLabel("アップミックス方式 (channelmix.upmix-method):"))
        self.cmb_upmix_method2 = QComboBox()
        self.cmb_upmix_method2.addItems(["psd", "simple", "none"])
        self.cmb_upmix_method2.currentTextChanged.connect(self._upmix_on_param_changed)
        row_method.addWidget(self.cmb_upmix_method2)
        row_method.addStretch()
        params_layout.addLayout(row_method)

        # LFE カットオフ
        params_layout.addWidget(QLabel("サブウーファー LFE カットオフ (channelmix.lfe-cutoff):"))
        row_lfe = QHBoxLayout()
        self.slider_upmix_lfe = QSlider(Qt.Horizontal)
        self.slider_upmix_lfe.setRange(40, 200)
        self.slider_upmix_lfe.setSingleStep(10)
        self.slider_upmix_lfe.setPageStep(10)
        self.slider_upmix_lfe.setValue(150)
        self.lbl_upmix_lfe = QLabel("150 Hz")
        self.lbl_upmix_lfe.setMinimumWidth(70)
        self.slider_upmix_lfe.valueChanged.connect(
            lambda v: (self.lbl_upmix_lfe.setText(f"{v} Hz"), self._upmix_on_param_changed())
        )
        row_lfe.addWidget(self.slider_upmix_lfe)
        row_lfe.addWidget(self.lbl_upmix_lfe)
        params_layout.addLayout(row_lfe)

        # リアスピーカーディレイ
        params_layout.addWidget(QLabel("リアスピーカー ディレイ (channelmix.rear-delay, ms / 0=なし):"))
        row_delay = QHBoxLayout()
        self.slider_upmix_delay = QSlider(Qt.Horizontal)
        self.slider_upmix_delay.setRange(0, 500)   # 0.0～50.0ms を 10倍スケールで管理
        self.slider_upmix_delay.setSingleStep(5)
        self.slider_upmix_delay.setPageStep(50)
        self.slider_upmix_delay.setValue(0)
        self.lbl_upmix_delay = QLabel("0.0 ms")
        self.lbl_upmix_delay.setMinimumWidth(70)
        self.slider_upmix_delay.valueChanged.connect(
            lambda v: (self.lbl_upmix_delay.setText(f"{v / 10:.1f} ms"), self._upmix_on_param_changed())
        )
        row_delay.addWidget(self.slider_upmix_delay)
        row_delay.addWidget(self.lbl_upmix_delay)
        params_layout.addLayout(row_delay)

        # センター FC カットオフ
        params_layout.addWidget(QLabel("センター FC カットオフ (channelmix.fc-cutoff, Hz / 0=無効):"))
        row_fc = QHBoxLayout()
        self.slider_upmix_fc = QSlider(Qt.Horizontal)
        self.slider_upmix_fc.setRange(0, 40)       # 0～20000Hz を 500Hz刻みで管理
        self.slider_upmix_fc.setSingleStep(1)
        self.slider_upmix_fc.setPageStep(4)
        self.slider_upmix_fc.setValue(0)
        self.lbl_upmix_fc = QLabel("0 Hz (無効)")
        self.lbl_upmix_fc.setMinimumWidth(110)
        self.slider_upmix_fc.valueChanged.connect(
            lambda v: (
                self.lbl_upmix_fc.setText(f"{v * 500} Hz" if v > 0 else "0 Hz (無効)"),
                self._upmix_on_param_changed()
            )
        )
        row_fc.addWidget(self.slider_upmix_fc)
        row_fc.addWidget(self.lbl_upmix_fc)
        params_layout.addLayout(row_fc)

        # ステレオワイド
        params_layout.addWidget(QLabel("ステレオ背景の広がり感 (channelmix.stereo-widen, 0.00～1.00):"))
        row_widen = QHBoxLayout()
        self.slider_upmix_widen = QSlider(Qt.Horizontal)
        self.slider_upmix_widen.setRange(0, 20)    # 0.00～1.00 を 0.05刻みで管理
        self.slider_upmix_widen.setSingleStep(1)
        self.slider_upmix_widen.setPageStep(2)
        self.slider_upmix_widen.setValue(0)
        self.lbl_upmix_widen = QLabel("0.00")
        self.lbl_upmix_widen.setMinimumWidth(40)
        self.slider_upmix_widen.valueChanged.connect(
            lambda v: (self.lbl_upmix_widen.setText(f"{v * 0.05:.2f}"), self._upmix_on_param_changed())
        )
        row_widen.addWidget(self.slider_upmix_widen)
        row_widen.addWidget(self.lbl_upmix_widen)
        params_layout.addLayout(row_widen)

        layout.addWidget(group_params)

        # --- ボタン群 ---
        group_actions = QGroupBox("操作")
        btn_layout = QHBoxLayout(group_actions)
        btn_layout.setSpacing(10)

        btn_enable = QPushButton("✅ 有効化 (再起動)")
        btn_enable.setObjectName("btn_primary")
        btn_enable.clicked.connect(self._upmix_enable)
        btn_layout.addWidget(btn_enable)

        btn_disable = QPushButton("❌ 無効化 (2ch復帰)")
        btn_disable.setObjectName("btn_danger")
        btn_disable.clicked.connect(self._upmix_disable)
        btn_layout.addWidget(btn_disable)

        btn_test = QPushButton("🔉 スピーカーテスト (6ch)")
        btn_test.clicked.connect(self._upmix_speaker_test)
        btn_layout.addWidget(btn_test)

        layout.addWidget(group_actions)

        # 設定ファイルパス案内
        lbl_paths = QLabel(
            "※ 設定ファイル: " + UPMIX_CONF_PATHS[0] + " / " + UPMIX_CONF_PATHS[1]
        )
        lbl_paths.setStyleSheet("color: #a6adc8; font-style: italic; font-size: 9pt;")
        layout.addWidget(lbl_paths)

        layout.addStretch()

        # 現在の設定ファイルの値で UI を初期化
        self._upmix_load_from_file()

        return tab

    def _upmix_get_params(self) -> dict:
        """UI の現在値をパラメータ辞書として返す。"""
        return {
            "method": self.cmb_upmix_method2.currentText(),
            "lfe_cutoff": self.slider_upmix_lfe.value(),
            "rear_delay": self.slider_upmix_delay.value() / 10.0,
            "fc_cutoff": self.slider_upmix_fc.value() * 500,
            "stereo_widen": round(self.slider_upmix_widen.value() * 0.05, 2),
        }

    def _upmix_set_params(self, p: dict):
        """パラメータ辞書を UI に反映する（シグナルを一時的にブロック）。"""
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
        # ラベルを手動更新
        self.lbl_upmix_lfe.setText(f"{self.slider_upmix_lfe.value()} Hz")
        self.lbl_upmix_delay.setText(f"{self.slider_upmix_delay.value() / 10:.1f} ms")
        fc = self.slider_upmix_fc.value() * 500
        self.lbl_upmix_fc.setText(f"{fc} Hz" if fc > 0 else "0 Hz (無効)")
        self.lbl_upmix_widen.setText(f"{self.slider_upmix_widen.value() * 0.05:.2f}")

    def _upmix_load_from_file(self):
        """既存の設定ファイルを読み込んで UI を初期化する。"""
        params = upmix_parse_config(UPMIX_CONF_PATHS[0])
        self._upmix_set_params(params)
        self._upmix_detect_preset()

    def _upmix_detect_preset(self):
        """現在のパラメータに合致するプリセットを検索してコンボボックスに反映する。"""
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

    def _upmix_refresh_status_badge(self):
        """有効/無効状態バッジを更新する。"""
        if upmix_is_enabled():
            self.lbl_upmix_status.setText(" ● 有効 (5.1ch Upmix ON) ")
            self.lbl_upmix_status.setStyleSheet(
                "background-color: #a6e3a1; color: #11111b; "
                "padding: 4px 10px; border-radius: 12px; font-weight: bold;"
            )
        else:
            self.lbl_upmix_status.setText(" ✖ 無効 (標準 2ch Direct) ")
            self.lbl_upmix_status.setStyleSheet(
                "background-color: #f38ba8; color: #11111b; "
                "padding: 4px 10px; border-radius: 12px; font-weight: bold;"
            )

    # ------ シグナルハンドラ ------

    def _upmix_on_preset_selected(self, preset_name: str):
        """プリセット選択時: パラメータを一括反映する。"""
        if self._upmix_updating:
            return
        if preset_name in UPMIX_PRESETS:
            self._upmix_set_params(UPMIX_PRESETS[preset_name])

    def _upmix_on_param_changed(self):
        """スライダー/コンボ変更時: プリセット表示を更新する。"""
        if self._upmix_updating:
            return
        self._upmix_detect_preset()

    # ------ ボタン操作 ------

    def _upmix_enable(self):
        """現在の UI パラメータで設定ファイルを書き込み、PipeWire を再起動する。"""
        params = self._upmix_get_params()
        conf_content = upmix_generate_config(params)
        try:
            for path in UPMIX_CONF_PATHS:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(conf_content)
            self.service_mgr.restart_pipewire()
            self._upmix_refresh_status_badge()
            self.update_live_status()
            QMessageBox.information(
                self,
                "有効化 完了",
                f"5.1ch アップミックス設定を適用しました。\nPipeWire を再起動しました。\n\n"
                f"[設定値]\n"
                f"方式            : {params['method']}\n"
                f"LFE カットオフ  : {params['lfe_cutoff']} Hz\n"
                f"リアディレイ    : {params['rear_delay']:.1f} ms\n"
                f"FC カットオフ   : {params['fc_cutoff']} Hz\n"
                f"ステレオワイド  : {params['stereo_widen']:.2f}",
            )
        except Exception as e:
            QMessageBox.critical(self, "エラー", str(e))

    def _upmix_disable(self):
        """設定ファイルを削除し PipeWire を再起動してアップミックスを無効化する。"""
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
                "無効化 完了",
                "5.1ch アップミックスを無効化し、標準 2ch Direct に戻しました。\nPipeWire を再起動しました。",
            )
        except Exception as e:
            # 削除済みファイルをロールバック
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
                self, "エラー",
                f"無効化に失敗しました（変更を元に戻しました）:\n{e}"
            )

    def _upmix_speaker_test(self):
        """6ch スピーカーテストを非同期で起動する。"""
        try:
            subprocess.Popen(
                ["speaker-test", "-D", "pulse", "-c", "6", "-t", "wav", "-l", "1"]
            )
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "コマンド未検出",
                "speaker-test コマンドが見つかりません。\nalsa-utils をインストールしてください。",
            )
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"テストの実行に失敗しました: {e}")
