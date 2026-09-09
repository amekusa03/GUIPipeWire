import os
import math
import time
import struct
import subprocess
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

CHANNEL_NAMES = [
    ("front-left", "Front Left (FL)"),
    ("front-right", "Front Right (FR)"),
    ("front-center", "Center (FC)"),
    ("lfe", "Subwoofer (LFE)"),
    ("rear-left", "Rear Left (RL)"),
    ("rear-right", "Rear Right (RR)")
]

def generate_channel_test_tone(channel_idx, num_channels=6, sample_rate=48000, duration_sec=0.8, volume=0.15):
    """
    Generate a test tone (5.1ch 16-bit PCM) for a specific channel.
    LFE channel uses 70Hz sine wave; other channels use 1000Hz. Includes 50ms fade in/out.
    """
    total_samples = int(sample_rate * duration_sec)
    t = np.linspace(0, duration_sec, total_samples, False)

    # Frequency: 70Hz for LFE, 1000Hz for regular channels
    freq = 70.0 if channel_idx == 3 else 1000.0
    tone = volume * np.sin(2 * np.pi * freq * t)

    # 50ms fade-in & fade-out
    fade_len = int(sample_rate * 0.05)
    fade_in = np.linspace(0, 1, fade_len)
    fade_out = np.linspace(1, 0, fade_len)
    tone[:fade_len] *= fade_in
    tone[-fade_len:] *= fade_out

    # 6-channel array initialized to 0
    multi_ch = np.zeros((total_samples, num_channels), dtype=np.int16)
    
    # Place tone into the target channel (-32767 to 32767)
    multi_ch[:, channel_idx] = (tone * 32767).astype(np.int16)

    return multi_ch.tobytes()


class CalibrationWorkerThread(QThread):
    """
    Worker thread that sequentially plays test tones across 5.1ch speakers,
    records microphone input, and calculates optimal channel balance gain multipliers.
    """
    progress_updated = pyqtSignal(int, str, str)  # step_idx (0..7), ch_name, status_msg
    step_result = pyqtSignal(int, float)          # ch_idx, measured_db
    finished_calibration = pyqtSignal(dict)       # result_dict
    error_occurred = pyqtSignal(str)              # error_msg

    def __init__(self, sink_name=None, mic_source_name=None, test_volume=0.15):
        super().__init__()
        self.sink_name = sink_name
        self.mic_source_name = mic_source_name
        self.test_volume = test_volume
        self.running = True

    def stop(self):
        self.running = False

    def run(self):
        try:
            sample_rate = 48000
            duration = 0.8  # Test tone duration per channel

            # ------------------------------------------------------------
            # Step 0: Measure ambient noise baseline (0.8s)
            # ------------------------------------------------------------
            self.progress_updated.emit(0, "Ambient Noise", "Measuring background noise baseline (0.8s)...")
            noise_floor_db = self._record_mic_rms(duration_sec=duration, sample_rate=16000)
            if not self.running:
                return

            measured_dbs = []

            # ------------------------------------------------------------
            # Step 1-6: Sequentially test FL, FR, FC, LFE, RL, RR
            # ------------------------------------------------------------
            for ch_idx, (ch_id, ch_label) in enumerate(CHANNEL_NAMES):
                if not self.running:
                    return

                self.progress_updated.emit(ch_idx + 1, ch_label, f"Testing {ch_label}... (0.8s)")

                # Generate 6ch PCM test audio
                raw_audio = generate_channel_test_tone(
                    channel_idx=ch_idx,
                    num_channels=6,
                    sample_rate=sample_rate,
                    duration_sec=duration,
                    volume=self.test_volume
                )

                # Play sound and record microphone RMS simultaneously
                measured_db = self._play_and_record(raw_audio, duration_sec=duration, sample_rate=sample_rate)
                if not self.running:
                    return

                measured_dbs.append(measured_db)
                self.step_result.emit(ch_idx, measured_db)

                # Short delay between channels (0.3s)
                time.sleep(0.3)

            # ------------------------------------------------------------
            # Calculate correction gains
            # ------------------------------------------------------------
            # Use Front Left (FL) as reference or mean of valid channels
            valid_dbs = [db for db in measured_dbs if db > -80.0]
            if not valid_dbs:
                target_db = -30.0
            else:
                target_db = measured_dbs[0] if measured_dbs[0] > -70.0 else float(np.mean(valid_dbs))

            recommended_volumes = []
            gain_diffs_db = []

            for db in measured_dbs:
                diff_db = target_db - db
                # Clamp adjustment within +/- 9dB for safety
                diff_db_clamped = max(-9.0, min(9.0, diff_db))
                multiplier = 10.0 ** (diff_db_clamped / 20.0)
                
                # Recommended volume relative to 100% (0.2 to 1.8 = 20% to 180%)
                rec_vol = max(0.2, min(1.8, multiplier))
                
                gain_diffs_db.append(diff_db)
                recommended_volumes.append(round(rec_vol, 2))

            result_data = {
                "noise_floor_db": noise_floor_db,
                "target_db": target_db,
                "measured_dbs": measured_dbs,
                "gain_diffs_db": gain_diffs_db,
                "recommended_volumes": recommended_volumes
            }

            self.progress_updated.emit(7, "Complete", "All channel measurements completed successfully!")
            self.finished_calibration.emit(result_data)

        except Exception as e:
            self.error_occurred.emit(f"Calibration failed: {e}")

    def _record_mic_rms(self, duration_sec=0.8, sample_rate=16000):
        """Record microphone audio and compute RMS (dBFS)"""
        cmd = [
            "pw-record",
            "--channels=1",
            f"--rate={sample_rate}",
            "--format=s16",
            "-"
        ]
        if self.mic_source_name and self.mic_source_name != "default":
            cmd.extend(["--target", self.mic_source_name])

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        total_bytes = int(sample_rate * 2 * duration_sec)
        try:
            raw_data = proc.stdout.read(total_bytes)
        finally:
            proc.terminate()
            proc.wait()

        if not raw_data or len(raw_data) < 2:
            return -90.0

        count = len(raw_data) // 2
        samples = struct.unpack(f"<{count}h", raw_data[:count*2])
        sum_sq = sum(s * s for s in samples)
        rms = math.sqrt(sum_sq / max(1, count))
        return 20.0 * math.log10(max(1.0, rms) / 32767.0)

    def _play_and_record(self, raw_audio, duration_sec=0.8, sample_rate=48000):
        """Play 6ch audio and record mic input simultaneously to compute steady RMS"""
        rec_rate = 16000
        rec_cmd = [
            "pw-record",
            "--channels=1",
            f"--rate={rec_rate}",
            "--format=s16",
            "-"
        ]
        if self.mic_source_name and self.mic_source_name != "default":
            rec_cmd.extend(["--target", self.mic_source_name])

        rec_proc = subprocess.Popen(rec_cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

        play_cmd = [
            "pw-play",
            "--channels=6",
            f"--rate={sample_rate}",
            "--format=s16",
            "-"
        ]
        if self.sink_name and self.sink_name != "default":
            play_cmd.extend(["--target", self.sink_name])

        play_proc = subprocess.Popen(play_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
        play_proc.stdin.write(raw_audio)
        play_proc.stdin.close()

        total_rec_bytes = int(rec_rate * 2 * duration_sec)
        try:
            rec_data = rec_proc.stdout.read(total_rec_bytes)
        finally:
            play_proc.wait()
            rec_proc.terminate()
            rec_proc.wait()

        if not rec_data or len(rec_data) < 2:
            return -90.0

        count = len(rec_data) // 2
        samples = struct.unpack(f"<{count}h", rec_data[:count*2])
        
        # Evaluate steady samples (middle 60%) to skip latency transient
        start_idx = int(count * 0.2)
        end_idx = int(count * 0.8)
        steady_samples = samples[start_idx:end_idx] if end_idx > start_idx else samples

        sum_sq = sum(s * s for s in steady_samples)
        rms = math.sqrt(sum_sq / max(1, len(steady_samples)))
        return 20.0 * math.log10(max(1.0, rms) / 32767.0)


def get_51ch_channel_volumes(sink_name="default"):
    """
    Retrieve current channel volumes (0.0 to 1.5+) from PipeWire / PulseAudio sink.
    Returns: [v_fl, v_fr, v_fc, v_lfe, v_rl, v_rr] (6-element float list)
    """
    result = [1.0] * 6
    try:
        import pulsectl
        with pulsectl.Pulse('guipipewire-51-getvol') as pulse:
            if not sink_name or sink_name == "default":
                sink_name = pulse.server_info().default_sink_name
            sink = pulse.get_sink_by_name(sink_name)
            if not sink:
                return result
            
            ch_map = {ch: vol for ch, vol in zip(sink.channel_list, sink.volume.values)}
            for idx, (ch_id, _) in enumerate(CHANNEL_NAMES):
                if ch_id in ch_map:
                    result[idx] = float(ch_map[ch_id])
                elif ch_id == "rear-left" and "side-left" in ch_map:
                    result[idx] = float(ch_map["side-left"])
                elif ch_id == "rear-right" and "side-right" in ch_map:
                    result[idx] = float(ch_map["side-right"])
    except Exception:
        pass
    return result


def apply_51ch_channel_volumes(sink_name, volumes_list):
    """
    Apply per-channel volumes to a 5.1ch PipeWire / PulseAudio sink.
    volumes_list: [v_fl, v_fr, v_fc, v_lfe, v_rl, v_rr] (floats 0.0 to 1.5+)
    """
    import pulsectl
    with pulsectl.Pulse('guipipewire-51-calib') as pulse:
        if not sink_name or sink_name == "default":
            sink_name = pulse.server_info().default_sink_name
        sink = pulse.get_sink_by_name(sink_name)
        if not sink:
            raise ValueError(f"Sink '{sink_name}' not found")
        
        vol_dict = {ch_id: volumes_list[i] for i, (ch_id, _) in enumerate(CHANNEL_NAMES) if i < len(volumes_list)}
        
        new_vals = []
        for ch in sink.channel_list:
            if ch in vol_dict:
                new_vals.append(float(vol_dict[ch]))
            elif ch == "side-left" and "rear-left" in vol_dict:
                new_vals.append(float(vol_dict["rear-left"]))
            elif ch == "side-right" and "rear-right" in vol_dict:
                new_vals.append(float(vol_dict["rear-right"]))
            else:
                new_vals.append(1.0)
        
        sink.volume.values = new_vals
        pulse.volume_set(sink, sink.volume)
    return True
