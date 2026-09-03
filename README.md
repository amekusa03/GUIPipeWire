# GUIPipeWire

> **PipeWire の設定を、コマンドなしで。**

PipeWire のサンプリングレート、バッファサイズ（Quantum）、リサンプル品質、リアルタイム再生状態をグラフィカルに設定・監視できる PyQt5 ベースの GUI ツールです。

![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=flat&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-GUI-41CD52?style=flat&logo=qt&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-yellow?style=flat)

---

## 💡 PipeWire って何？ / このツールはなぜ必要？

**PipeWire** は、現代の Linux における標準のオーディオ・ビデオサーバーです。
Ubuntu 22.04 以降、Fedora、Arch Linux など多くのディストリビューションで標準採用されており、音楽・動画・ゲーム・配信など、あらゆる音声処理の中心に位置しています。

PipeWire の設定は本来 `~/.config/pipewire/` 配下のテキストファイルを手で書き換える必要があり、初心者には敷居が高い作業でした。  
**GUIPipeWire** はその作業を GUI から直感的に行えるようにするツールです。

```
こんな時に役立ちます:

🔊 音が途切れる・カクつく         → バッファサイズ (Quantum) を増やす
🎵 ハイレゾ音源を正しく再生したい  → 許可サンプリングレートを拡張する
🎛️ DTM・録音の遅延を減らしたい   → Quantum を 128 等に下げる
🐛 音声周りのトラブルを調べたい    → pw-top でリアルタイム状態を確認する
```

---

## 🌟 主な機能

### 1. ⏱️ クロック & サンプリングレート設定

| 設定項目 | 概要 | 推奨値 |
|---------|------|-------|
| **デフォルト動作周波数** | 音声処理の基本周波数 | **48000 Hz (48 kHz)** ★ |
| **許可サンプリングレート** | 音源の周波数に合わせて自動追従する周波数の範囲 | 44100, 48000 (標準) |

> 🔰 **初心者向け補足**: 「サンプリングレート」とは「1秒間に音声データを何回サンプリングするか」の値です。  
> 48 kHz が現代の標準で、ゲーム・動画・配信のほぼすべてがこの値を使用しています。  
> 迷ったら **48000 Hz** を選べばまず問題ありません。

### 2. 🎛️ バッファサイズ (Quantum) & リサンプル設定

| 設定項目 | 値の目安 | 推奨場面 |
|---------|---------|---------|
| **Quantum (バッファ)** | 1024 (21.3 ms) | 一般利用・安定性重視 ★ |
| | 512 (10.7 ms) | バランス重視 |
| | 128 (2.7 ms) | DTM・録音・超低遅延 |
| **リサンプル品質** | 4 (デフォルト) | 一般利用 |
| | 14 (最高) | ハイレゾ・高音質重視 |

> 🔰 **初心者向け補足**: 「Quantum (バッファ)」は「音声データをまとめて処理するブロックの大きさ」です。  
> 大きいほど安定しますが遅延が増え、小さいほど遅延は減りますが CPU への負荷が増えます。  
> 音が途切れる場合は大きくし、DTM・録音の遅延が気になる場合は小さくしてみてください。

### 3. 🔊 5.1ch サラウンド・アップミックス

2ch ステレオ音源を 5.1ch サラウンドへアップミックスする各種設定を細かく調整・切り替え可能です:

- **プリセット**: 標準 (PSD) / 映画・サラウンド重視 / 音楽・自然な広がり / シンプル (Simple方式) / カスタム
- **詳細パラメータ**:
  - アップミックス方式 (`psd` / `simple` / `none`)
  - サブウーファー LFE カットオフ周波数 (40Hz 〜 200Hz)
  - リアスピーカー ディレイ (0.0ms 〜 50.0ms)
  - センター FC カットオフ周波数 (0Hz 〜 20000Hz)
  - ステレオ広がり感 (`stereo-widen`: 0.00 〜 1.00)
- **6ch スピーカーテスト (`speaker-test`) 内蔵**

### 4. ⚡ ワンクリック・プリセット機能

手動設定が不要な用途別プリセットを用意しています:

| プリセット | 用途 | 主な設定 |
|-----------|------|---------|
| 📻 **標準バランス** | 一般利用・迷ったらこれ | 48 kHz, Quantum 1024 |
| 🎵 **ハイレゾオーディオ** | 高音質音楽鑑賞 | 〜384 kHz 対応, リサンプル品質 14 |
| ⚡ **低遅延 DTM / ゲーミング** | 録音・配信・ゲーム | 48/96 kHz, Quantum 128 |

### 4. 📊 リアルタイム再生監視 & ログ

- **`pw-top` 連携**: 現在オーディオを再生・録音しているアプリ（Rhythmbox, Firefox, Spotify 等）の状態を表示。
  ```
  S   ID  QUANT   RATE  WAIT  BUSY  ERR  FORMAT         NAME
  R   83   2048  44100  69us  22us    0  S32LE 2 44100  alsa_output...
  R  127   3969  44100  24us  37us    0  S16LE 2 44100  Rhythmbox
  ```
- **`pw-metadata settings`**: 現在システムに適用されている動的設定をリアルタイム確認。
- **`journalctl` ログ**: PipeWire サービスのシステムログを確認。

### 5. 📝 設定ファイル直接編集

生成した設定ファイルをアプリ内テキストエディタで直接閲覧・編集・保存することもできます。

---

## 🛠️ 動作要件

- **OS**: Linux (PipeWire が動作しているディストリビューション)
- **Python**: 3.8 以上
- **必要なコマンド**: `pw-top`, `pw-metadata`, `systemctl`（通常 PipeWire に同梱）

### Python の確認

```bash
python3 --version   # 3.8 以上であれば OK
```

### PyQt5 のインストール

#### Ubuntu / Debian / Pop!_OS
```bash
sudo apt update
sudo apt install python3-pyqt5 pipewire-bin
```

#### Arch Linux / Manjaro
```bash
sudo pacman -S python-pyqt5 pipewire
```

#### Fedora
```bash
sudo dnf install python3-qt5 pipewire-utils
```

#### pip を使う場合（仮想環境不要）
```bash
pip install PyQt5
```

---

## 🚀 インストール & 起動

### ステップ 1: リポジトリを取得

```bash
git clone https://github.com/yourusername/GUIPipeWire.git
cd GUIPipeWire
```

### ステップ 2: PyQt5 をインストール（まだの場合）

```bash
# Ubuntu / Debian の場合
sudo apt install python3-pyqt5
```

### ステップ 3: 起動

```bash
./run.sh
```

または:

```bash
python3 main.py
```

### (オプション) デスクトップショートカットを追加

```bash
cp gui-pipewire.desktop ~/.local/share/applications/
```

アプリケーションランチャーから「GUIPipeWire」で起動できるようになります。

---

## 🔧 使い方

### 基本的な設定の流れ

```
1. アプリを起動する
2. 「⏱️ クロック・サンプリングレート」タブで設定値を選択
   (迷ったら「⚡ プリセット」タブから用途に合ったものをクリック)
3. 画面下部の「💾 設定を保存して PipeWire を再起動」をクリック
4. 「📊 リアルタイム監視」タブで設定が反映されたか確認
```

> ⚠️ **注意**: 設定を保存すると PipeWire が自動的に再起動されます。  
> 再起動中（1〜2秒程度）は一時的に音声出力が途切れますが、正常な動作です。

### 初期状態に戻したい場合

画面下部の **「デフォルトに戻す (設定ファイルを削除)」** ボタンを押すと、  
GUIPipeWire が生成した設定ファイルが削除され、PipeWire のシステムデフォルト設定に戻ります。

---

## 📁 生成される設定ファイル

GUIPipeWire はシステム本体の設定を変更せず、ユーザー領域のドロップイン設定ファイルのみを操作します。

| ファイル | 内容 |
|---------|------|
| `~/.config/pipewire/pipewire.conf.d/10-clock.conf` | クロック・サンプリングレート設定 |
| `~/.config/pipewire/client.conf.d/10-resample.conf` | リサンプル・チャンネルミキシング設定 |
| `~/.config/pipewire/pipewire-pulse.conf.d/20-upmix.conf` | 5.1ch アップミックス設定 (Pulseクライアント用) |
| `~/.config/pipewire/client.conf.d/20-upmix.conf` | 5.1ch アップミックス設定 (PipeWireネイティブ用) |

保存時には自動的にバックアップ (`.bak`) が作成されます。

---

## 📂 プロジェクト構成

```text
GUIPipeWire/
├── main.py              # アプリケーションのエントリーポイント
├── gui_window.py        # PyQt5 メインウィンドウ・UIレイアウト・各種タブ
├── pipewire_config.py   # 設定ファイル (~/.config/pipewire/) の読み書き・パース
├── pipewire_service.py  # PipeWire サービス制御 (systemctl, pw-top, pw-metadata, journalctl)
├── gui-pipewire.desktop # デスクトップ環境用アプリケーションショートカット
├── run.sh               # 起動用シェルスクリプト
└── README.md            # ドキュメント (本書)
```

---

## ❓ よくある質問 (FAQ)

**Q. 設定を保存後に音が全く出なくなった**  
A. 「デフォルトに戻す」ボタンを押してリセットした後、PipeWire を再起動してください。  
```bash
systemctl --user restart pipewire pipewire-pulse
```

**Q. `pw-top` が「取得エラー」になる**  
A. `pw-top` コマンドがインストールされていない可能性があります。  
```bash
# Ubuntu の場合
sudo apt install pipewire-bin

# Arch の場合
sudo pacman -S pipewire
```

**Q. `./run.sh` の実行時に「Permission denied」と表示される**  
A. 実行権限を付与してください。
```bash
chmod +x run.sh
```

---

## 📄 ライセンス

MIT License © 2026 GUIPipeWire Contributors
