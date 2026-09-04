import os
import math
import time
import struct
import subprocess
import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

CHANNEL_NAMES = [
    ("front-left", "フロント左 (FL)"),
    ("front-right", "フロント右 (FR)"),
    ("front-center", "センター (FC)"),
    ("lfe", "サブウーファー (LFE)"),
    ("rear-left", "リア左 (RL)"),
    ("rear-right", "リア右 (RR)")
]

def generate_channel_test_tone(channel_idx, num_channels=6, sample_rate=48000, duration_sec=0.8, volume=0.15):
    """
    指定したチャンネルのみにテストトーン（5.1ch 16bit PCM）を生成。
    LFEチャンネルは70Hz、通常チャンネルは1000Hz。フェードイン・アウト付き。
    """
    total_samples = int(sample_rate * duration_sec)
    t = np.linspace(0, duration_sec, total_samples, False)

    # 周波数設定 (LFE は 70Hz、通常は 1000Hz)
    freq = 70.0 if channel_idx == 3 else 1000.0
    tone = volume * np.sin(2 * np.pi * freq * t)

    # 50ms フェードイン & フェードアウト
    fade_len = int(sample_rate * 0.05)
    fade_in = np.linspace(0, 1, fade_len)
    fade_out = np.linspace(1, 0, fade_len)
    tone[:fade_len] *= fade_in
    tone[-fade_len:] *= fade_out

    # 6チャンネル配列の作成 (全て0で初期化)
    multi_ch = np.zeros((total_samples, num_channels), dtype=np.int16)
    
    # 対象チャンネルのみに配置 (-32767 〜 32767)
    multi_ch[:, channel_idx] = (tone * 32767).astype(np.int16)

    return multi_ch.tobytes()


class CalibrationWorkerThread(QThread):
    """
    5.1ch 各スピーカーから順次音を出してマイクで音量を測定し、
    全チャンネル均一化のための補正ゲインを自動計算するスレッド。
    """
    progress_updated = pyqtSignal(int, str, str)  # step_idx (0..6), ch_name, status_msg
    step_result = pyqtSignal(int, float)          # ch_idx (0..5), measured_db
    finished_calibration = pyqtSignal(dict)       # 全結果データ dict
    error_occurred = pyqtSignal(str)              # エラーメッセージ

    def __init__(self, sink_name=None, mic_source_name=None, test_volume=0.15, parent=None):
        super().__init__(parent)
        self.sink_name = sink_name
        self.mic_source_name = mic_source_name
        self.test_volume = test_volume  # 0.05 〜 0.5 (夜間は 0.10 〜 0.15 推奨)
        self.running = False

    def stop(self):
        self.running = False

    def run(self):
        self.running = True
        sample_rate = 48000
        duration = 0.8
        
        try:
            # ------------------------------------------------------------
            # Step 0: 暗騒音（部屋の環境ノイズ）の測定 (1秒間)
            # ------------------------------------------------------------
            self.progress_updated.emit(0, "環境ノイズ", "部屋の暗騒音（環境音）を測定中...")
            noise_floor_db = self._record_mic_rms(duration_sec=0.8, sample_rate=sample_rate)
            if not self.running:
                return

            measured_dbs = []

            # ------------------------------------------------------------
            # Step 1〜6: 各チャンネルの順次テスト & マイク計測
            # ------------------------------------------------------------
            for ch_idx, (ch_id, ch_label) in enumerate(CHANNEL_NAMES):
                if not self.running:
                    return

                self.progress_updated.emit(ch_idx + 1, ch_label, f"{ch_label} を再生＆マイク測定中...")

                # テスト音 PCM データ生成
                raw_audio = generate_channel_test_tone(
                    channel_idx=ch_idx,
                    num_channels=6,
                    sample_rate=sample_rate,
                    duration_sec=duration,
                    volume=self.test_volume
                )

                # 再生と録音を並行実行
                measured_db = self._play_and_record(raw_audio, duration_sec=duration, sample_rate=sample_rate)
                if not self.running:
                    return

                measured_dbs.append(measured_db)
                self.step_result.emit(ch_idx, measured_db)

                # チャンネル間の短い待機 (0.3秒)
                time.sleep(0.3)

            # ------------------------------------------------------------
            # 補正値（キャリブレーションゲイン）の計算
            # ------------------------------------------------------------
            # フロント左 (FL) を基準、または有効な平均値を基準ターゲットとする
            valid_dbs = [db for db in measured_dbs if db > -80.0]
            if not valid_dbs:
                target_db = -30.0
            else:
                # フロント左をメイン基準（FLが極端に低くなければ）
                target_db = measured_dbs[0] if measured_dbs[0] > -70.0 else np.mean(valid_dbs)

            recommended_volumes = []
            gain_diffs_db = []

            for db in measured_dbs:
                # 目標との差分 (dB)
                diff_db = target_db - db
                # 補正倍率 (安全のため ±9dB にリミット)
                diff_db_clamped = max(-9.0, min(9.0, diff_db))
                multiplier = 10.0 ** (diff_db_clamped / 20.0)
                
                # 基準100%に対する推奨音量 (0.2〜1.8 = 20%〜180%)
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

            self.progress_updated.emit(7, "完了", "全チャンネルの測定が完了しました！")
            self.finished_calibration.emit(result_data)

        except Exception as e:
            self.error_occurred.emit(f"キャリブレーション失敗: {e}")

    def _record_mic_rms(self, duration_sec=0.8, sample_rate=16000):
        """マイクから音声を指定秒数録音して RMS (dBFS) を計算"""
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
        """テスト音を 6ch 再生しながら、マイク入力を同時に録音して RMS を計算"""
        # 1. 録音プロセスを先に起動 (16kHz mono)
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

        # 2. 再生プロセスを起動 (48kHz 6ch)
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
        
        # 音声データを流し込んで再生
        play_proc.stdin.write(raw_audio)
        play_proc.stdin.close()

        # 録音データを読み込み (再生時間分)
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
        
        # 立ち上がりレイテンシをスキップするため、中盤 60% のサンプルで RMS を評価
        start_idx = int(count * 0.2)
        end_idx = int(count * 0.8)
        steady_samples = samples[start_idx:end_idx] if end_idx > start_idx else samples

        sum_sq = sum(s * s for s in steady_samples)
        rms = math.sqrt(sum_sq / max(1, len(steady_samples)))
        return 20.0 * math.log10(max(1.0, rms) / 32767.0)


def get_51ch_channel_volumes(sink_name="default"):
    """
    現在の PipeWire / PulseAudio デフォルト Sink から各チャンネルの音量 (0.0 〜 1.5 等) を取得。
    戻り値: [v_fl, v_fr, v_fc, v_lfe, v_rl, v_rr] (6要素の浮動小数点数リスト)
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
    PipeWire / PulseAudio の 5.1ch Sink に対してチャンネル別の音量を即時適用。
    volumes_list: [v_fl, v_fr, v_fc, v_lfe, v_rl, v_rr] (各 0.0 〜 1.5 などの浮動小数点数)
    """
    import pulsectl
    with pulsectl.Pulse('guipipewire-51-calib') as pulse:
        if not sink_name or sink_name == "default":
            sink_name = pulse.server_info().default_sink_name
        sink = pulse.get_sink_by_name(sink_name)
        if not sink:
            raise ValueError(f"Sink '{sink_name}' が見つかりません")
        
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
