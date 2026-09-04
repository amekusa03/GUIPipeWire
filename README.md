# GUIPipeWire 🎵

**GUIPipeWire** は、Linux の次世代オーディオサーバー **PipeWire** の各種設定（クロック・サンプリングレート・リサンプル品質・5.1chアップミックス・マイクAGC/ノイズ抑制）を視覚的にカスタマイズ・リアルタイム監視できる、モダンで高機能な PyQt5 製 GUI アプリケーションです。

![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=flat&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/GUI-PyQt5-41CD52?style=flat&logo=qt&logoColor=white)
![PipeWire](https://img.shields.io/badge/Audio-PipeWire-00ADD8?style=flat)
![Theme](https://img.shields.io/badge/Theme-Catppuccin_Mocha-89b4fa?style=flat)
![License](https://img.shields.io/badge/License-MIT-blue.svg?style=flat)

---

## ✨ 主な機能

### 1. ⚡ ワンクリック・プリセット機能
- 📻 **標準バランス**: 48 kHz, Quantum 1024 (一般利用推奨)
- 🎵 **ハイレゾオーディオ**: 〜192/384 kHz, 品質 14 (高音質音楽鑑賞)
- ⚡ **低遅延 DTM / ゲーミング**: 48/96 kHz, Quantum 128 (録音・ゲーム・配信)

### 2. 🔊 5.1ch アップミックス & 🎯 音響自動キャリブレーション
スクロール不要な **左右2カラム構成** で、空間音響と音量バランスを1画面で直感的に調整できます。

- **空間音響アップミックス (左カラム)**:
  - 方式 (`psd` / `simple` / `none`)
  - サブウーファー LFE カットオフ周波数 (40Hz 〜 200Hz)
  - リアスピーカー ディレイ (0.0ms 〜 50.0ms)
  - センター FC カットオフ周波数 (0Hz 〜 20000Hz)
  - ステレオ広がり感 (`stereo-widen`: 0.00 〜 1.00)
  - 6ch スピーカーテスト (`speaker-test`) 内蔵
- **スピーカー音量バランス & マイク自動測定 (右カラム)**:
  - **起動時自動同期**: 現在システムで設定されている 5.1ch 各チャンネル音量を自動取得してスライダーに即時反映。
  - **マイク自動キャリブレーション**: 各チャンネルから順次テスト音を鳴らし、マイク測定により全スピーカーの音量をリスニング位置で自動均一化。
  - **夜間・静音測定**: 爆音を出さずに小音量（15%）テスト音と環境ノイズ事前測定で安全・高精度に測定。
  - **6ch 個別音量スライダー**: 測定値の確認、手動微調整、100%リセット、PipeWireへの即時適用。

### 3. 🎙️ マイク & AGC (自動音量均一化) / ノイズ抑制
スクロール不要な **左右2カラム構成** で、配信・通話・ゲームに最適なクリアなマイク入力を実現します。

- **マイク入力 & モニター (左カラム)**:
  - リアルタイム・レベルメーター (RMS / Peak dBFS)
  - 音割れ / 適正 / 小声 の自動判定バッジ
  - マイク手動音量 (Gain) スライダー
- **PipeWire WebRTC DSP 仮想マイク (左カラム)**:
  - OSネイティブの超低遅延 WebRTC DSP 仮想マイクを作成・有効化。
  - 自動ゲイン調整 (AGC)、AIノイズ抑制、ハイパスフィルター、音声検知 (VAD) をワンクリック適用。
  - Discord、OBS、ブラウザ等の入力に「マイク (AGC・ノイズ抑制適用済み)」として指定可能。
- **動的ゲイン自動追従 (右カラム)**:
  - 目標音量（Target Level）に合わせて Python がマイクゲインをリアルタイムに自動微調整。
  - 環境ノイズを拾わない無音閾値（Noise Floor）設定。
  - 動作ログエリア（コンパクト表示）。
- **夜間・静音テスト支援モード (右カラム)**:
  - 囁き声の増幅テストや、極小音量テストトーン（-30dBFS）の注入シミュレーション。

### 4. ⏱️ クロック & サンプリングレート設定
- **標準動作周波数 (`default.clock.rate`)**: 44.1 kHz 〜 384 kHz から選択（デフォルト: 48 kHz 推奨）。
- **許可サンプリングレート (`default.clock.allowed-rates`)**: 音源に合わせて自動切り替えを許可する周波数をチェックボックスで一括指定。
- **バッファサイズ & レイテンシ (`Quantum`)**:
  - デフォルト Quantum (標準推奨: 1024 / 低遅延: 512 / 超低遅延 DTM: 128)
  - 最小・最大 Quantum の範囲指定。

### 5. 🎛️ クライアント・リサンプル設定
- **リサンプル品質 (`resample.quality`)**: 0 (超軽量) 〜 14 (マスタリング級最高音質) をスライダーで調整。
- **チャンネルミックス制御**: リサンプル無効化、音量正規化、基本アップミックスの切り替え。

### 6. 📊 リアルタイム監視 & ログ
- `pw-top` による再生・録音ストリームのリアルタイム監視。
- `pw-metadata settings` による現在の動作設定確認。
- `journalctl` による PipeWire システムログのリアルタイム表示。

### 7. 📝 設定ファイル直接編集
- 生成されたドロップイン設定ファイルを内蔵エディタで直接確認・編集・保存可能。

### 8. 🔄 全設定「デフォルトにリセット」機能
- 画面下部のボタン1つで、クロック・リサンプル・5.1chアップミックス・マイク仮想マイク・チャンネル音量・動的ゲインの**全設定を一括でシステム初期状態へ完全復元**。

---

## 🛠️ 動作要件 & インストール

### 必要環境
- **OS**: Linux (PipeWire が動作している環境)
- **Python**: 3.8 以上

### 依存パッケージのインストール

#### Ubuntu / Debian / Pop!_OS
```bash
sudo apt update
sudo apt install python3-pyqt5 python3-numpy python3-pulsectl alsa-utils pipewire-bin
```

#### Arch Linux / Manjaro
```bash
sudo pacman -S python-pyqt5 python-numpy alsa-utils pipewire
pip install pulsectl  # AUR 経由でも可
```

#### Fedora
```bash
sudo dnf install python3-qt5 python3-numpy alsa-utils pipewire-utils
pip install pulsectl
```

---

## 🚀 起動方法

```bash
# 実行スクリプトから起動
./run.sh

# または直接起動
python3 main.py
```

### デスクトップメニューへの追加 (オプション)
```bash
cp gui-pipewire.desktop ~/.local/share/applications/
```

---

## 📂 プロジェクト構成

```text
GUIPipeWire/
├── main.py                 # アプリケーション起動エントリーポイント
├── gui_window.py           # メインウィンドウ・UIレイアウト・各種タブ実装
├── pipewire_config.py      # 設定ファイル (~/.config/pipewire/) の読込・生成・リセット
├── pipewire_service.py     # PipeWire サービス制御 (systemctl, pw-top, journalctl)
├── pipewire_calibrator.py  # 5.1ch 音響自動キャリブレーション & 音量取得/適用
├── pipewire_agc.py         # マイク入力監視・WebRTC DSP 仮想マイク・動的ゲイン追従
├── gui-pipewire.desktop    # デスクトップ環境用ショートカット定義
├── run.sh                  # 起動用シェルスクリプト
└── README.md               # ドキュメント (本書)
```

---

## 📁 生成される設定ファイル

GUIPipeWire はシステムのルート領域を変更せず、ユーザー領域のドロップイン設定ファイルのみを操作します。

| 設定ファイル | 内容 |
|-------------|------|
| `~/.config/pipewire/pipewire.conf.d/10-clock.conf` | クロックレート・サンプリングレート・Quantum 設定 |
| `~/.config/pipewire/client.conf.d/10-resample.conf` | クライアント・リサンプル品質・ミキシング設定 |
| `~/.config/pipewire/pipewire-pulse.conf.d/20-upmix.conf` | 5.1ch アップミックス設定 (PulseAudio クライアント用) |
| `~/.config/pipewire/client.conf.d/20-upmix.conf` | 5.1ch アップミックス設定 (PipeWire ネイティブ用) |
| `~/.config/pipewire/pipewire.conf.d/99-input-dsp.conf` | WebRTC DSP マイク AGC / ノイズ抑制 仮想マイク設定 |

---

## ❓ よくある質問 (FAQ)

**Q. 設定を保存後に音が全く出なくなった**  
A. 画面下部の「デフォルトにリセット」ボタンを押してください。全設定が初期化されて PipeWire が再起動されます。それでも復旧しない場合:
```bash
systemctl --user restart pipewire pipewire-pulse
```

**Q. マイク & AGC タブで「pulsectl が見つかりません」エラーが出る**  
A. `pulsectl` ライブラリをインストールしてください。
```bash
pip install pulsectl
```

**Q. `pw-top` の情報が「取得エラー」になる**  
A. `pw-top` コマンドが未インストールの可能性があります。
```bash
# Ubuntu の場合
sudo apt install pipewire-bin
```

**Q. `./run.sh` で「Permission denied」と表示される**  
A. 実行権限を付与してください。
```bash
chmod +x run.sh
```

**Q. 5.1ch アップミックスを有効にしたが音が出ない**  
A. `speaker-test` をインストールして 6ch テストを試してください。
```bash
# Ubuntu の場合
sudo apt install alsa-utils
```

---

## 📄 ライセンス

MIT License © 2026 GUIPipeWire Contributors
