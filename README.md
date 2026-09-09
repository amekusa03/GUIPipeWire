**English** | [日本語](README.JP.md)

---

# GUIPipeWire 🎵

**GUIPipeWire** is a modern, feature-rich PyQt5 GUI application designed for Linux to visually configure and monitor the next-generation **PipeWire** audio server in real time (clock rates, quantum/latency, resampling quality, 5.1ch surround upmixing, and WebRTC DSP microphone AGC / noise suppression).

![Python](https://img.shields.io/badge/Python-3.8+-3776AB?style=flat&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/GUI-PyQt5-41CD52?style=flat&logo=qt&logoColor=white)
![PipeWire](https://img.shields.io/badge/Audio-PipeWire-00ADD8?style=flat)
![Theme](https://img.shields.io/badge/Theme-Catppuccin_Mocha-89b4fa?style=flat)
![License](https://img.shields.io/badge/License-MIT-blue.svg?style=flat)

---

## ✨ Key Features

### 1. ⚡ One-Click Audio Presets
- 📻 **Standard Balance**: 48 kHz, Quantum 1024 (Recommended for daily desktop use & video playback)
- 🎵 **Hi-Res Audio**: Up to 192/384 kHz, Quality 14 (Audiophile music listening)
- ⚡ **Low-Latency DAW / Gaming**: 48/96 kHz, Quantum 128 (Recording, competitive gaming, live streaming)

### 2. 🔊 5.1ch Surround Upmix & 🎯 Automatic Room Calibration
Designed with an intuitive, **no-scroll 2-column layout** to configure spatial audio upmixing and channel balance in a single view.

- **Spatial Audio Upmixing (Left Column)**:
  - Upmix Algorithms (`psd` / `simple` / `none`)
  - Subwoofer LFE Crossover Frequency (40 Hz – 200 Hz)
  - Rear Speaker Delay (0.0 ms – 50.0 ms)
  - Center Speaker (FC) Cutoff Frequency (0 Hz – 20,000 Hz)
  - Stereo Widening (`stereo-widen`: 0.00 – 1.00)
  - Built-in 6ch Speaker Test (`speaker-test`)
- **Speaker Balance & Microphone Auto-Calibration (Right Column)**:
  - **Auto-Sync on Launch**: Automatically fetches current system 5.1ch channel volumes and updates sliders in real time.
  - **Mic-Based Auto-Calibration**: Plays sequential test tones across all 6 channels and automatically equalizes speaker sound levels at the listening position using microphone feedback.
  - **Quiet / Safe Measurement**: Uses low-volume (15%) test tones with ambient noise baseline compensation for safe, accurate calibration without loud blasts.
  - **Individual 6ch Sliders**: Visual confirmation, manual fine-tuning, 100% reset, and immediate application to PipeWire.

### 3. 🎙️ Microphone & WebRTC DSP AGC / Noise Suppression
Compact **2-column layout** for crystal-clear microphone audio tailored for streaming, voice chat, and gaming.

- **Microphone Input & Monitoring (Left Column)**:
  - Real-time Audio Level Meters (RMS / Peak dBFS)
  - Live status badges: Clipping / Optimal / Low / Silent
  - Manual Mic Input Gain slider
- **PipeWire WebRTC DSP Virtual Microphone (Left Column)**:
  - Creates and enables an ultra-low-latency OS-native WebRTC DSP virtual microphone.
  - One-click toggle for Automatic Gain Control (AGC), AI noise suppression, high-pass filter, and Voice Activity Detection (VAD).
  - Selectable as an input device in Discord, OBS, web browsers, and games (`Microphone (with AGC & Noise Suppression)`).
- **Dynamic Gain Auto-Tracking (Right Column)**:
  - Real-time software gain tracking in Python to maintain a consistent target voice level.
  - Configurable Noise Floor threshold to ignore background room noise.
  - Compact real-time action log.
- **Night / Quiet Test Simulation Mode (Right Column)**:
  - Test whisper amplification or simulate with an ultra-quiet test tone (-30 dBFS).

### 4. ⏱️ Clock & Sample Rate Configuration
- **Default Clock Rate (`default.clock.rate`)**: Select from 44.1 kHz up to 384 kHz (48 kHz recommended).
- **Allowed Sample Rates (`default.clock.allowed-rates`)**: Multi-select allowed rates for seamless dynamic switching based on audio sources.
- **Buffer Size & Latency (`Quantum`)**:
  - Default Quantum (Standard: 1024 / Low-Latency: 512 / Ultra-low Latency: 128)
  - Min/Max Quantum boundary configuration.

### 5. 🎛️ Client Resampling & Mixing
- **Resample Quality (`resample.quality`)**: Slider adjustment from 0 (Ultra-lightweight) to 14 (Mastering grade).
- **Channel Mix Control**: Disable resampling, volume normalization, and basic channel mixing.

### 6. 📊 Real-Time Monitoring & System Logs
- Real-time active audio stream monitoring via `pw-top`.
- Inspect active runtime metadata via `pw-metadata settings`.
- Live PipeWire system log viewer using `journalctl`.

### 7. 📝 Integrated Configuration Editor
- View, edit, and save generated user drop-in config files directly inside the app.

### 8. 🔄 One-Click "Reset to Default"
- Instantly reset all clock, resampling, 5.1ch upmix, microphone DSP, channel volume, and AGC configurations back to standard system defaults.

---

## 🛠️ Requirements & Installation

### Prerequisites
- **OS**: Linux with **PipeWire** running
- **Python**: 3.8+

### Dependency Installation

#### Ubuntu / Debian / Pop!_OS
```bash
sudo apt update
sudo apt install python3-pyqt5 python3-numpy python3-pulsectl alsa-utils pipewire-bin
```

#### Arch Linux / Manjaro
```bash
sudo pacman -S python-pyqt5 python-numpy alsa-utils pipewire
pip install pulsectl  # or via AUR
```

#### Fedora
```bash
sudo dnf install python3-qt5 python3-numpy alsa-utils pipewire-utils
pip install pulsectl
```

---

## 🚀 Getting Started

```bash
# Run using the launcher script
./run.sh

# Or start directly with Python
python3 main.py
```

### Add to Desktop Menu (Optional)
```bash
cp gui-pipewire.desktop ~/.local/share/applications/
```

---

## 📂 Project Structure

```text
GUIPipeWire/
├── main.py                 # Application entry point
├── gui_window.py           # Main window UI layout & tab implementations
├── pipewire_config.py      # Config manager (~/.config/pipewire/) for load, generate, reset
├── pipewire_service.py     # Service manager (systemctl, pw-top, journalctl)
├── pipewire_calibrator.py  # 5.1ch auto-calibration & channel volume manager
├── pipewire_agc.py         # Mic monitor, WebRTC DSP virtual mic & dynamic AGC
├── gui-pipewire.desktop    # Desktop environment application shortcut
├── run.sh                  # Launcher shell script
├── README.md               # Documentation (English)
└── README.JP.md            # Documentation (Japanese)
```

---

## 📁 Generated Configuration Files

GUIPipeWire does not modify root system files. It safely operates purely within user-level drop-in configuration directories:

| Config File | Description |
|-------------|-------------|
| `~/.config/pipewire/pipewire.conf.d/10-clock.conf` | Clock rate, allowed sample rates, and Quantum settings |
| `~/.config/pipewire/client.conf.d/10-resample.conf` | Client resampling quality and mixing options |
| `~/.config/pipewire/pipewire-pulse.conf.d/20-upmix.conf` | 5.1ch upmix configuration (PulseAudio clients) |
| `~/.config/pipewire/client.conf.d/20-upmix.conf` | 5.1ch upmix configuration (Native PipeWire clients) |
| `~/.config/pipewire/pipewire.conf.d/99-input-dsp.conf` | WebRTC DSP microphone AGC / Noise suppression virtual source |

---

## ❓ FAQ & Troubleshooting

**Q. No audio output after applying settings**  
A. Click the **"Reset to Default"** button at the bottom of the window. This clears custom configurations and restarts PipeWire. If issues persist, run:
```bash
systemctl --user restart pipewire pipewire-pulse
```

**Q. Error "pulsectl not found" in the Mic & AGC tab**  
A. Install the `pulsectl` Python package:
```bash
pip install pulsectl
```

**Q. `pw-top` shows "Error retrieving status"**  
A. Ensure `pipewire-bin` / `pipewire-utils` is installed:
```bash
# Ubuntu / Debian
sudo apt install pipewire-bin
```

**Q. Permission denied when running `./run.sh`**  
A. Grant execute permissions:
```bash
chmod +x run.sh
```

**Q. No sound when testing 5.1ch upmix**  
A. Install `alsa-utils` for the 6-channel speaker test tool:
```bash
# Ubuntu / Debian
sudo apt install alsa-utils
```

---

## 📄 License

MIT License © 2026 GUIPipeWire Contributors
