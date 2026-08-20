import subprocess
import re

class PipeWireServiceManager:
    """PipeWire systemd サービス制御、メタデータ取得、ログ閲覧を管理するクラス"""

    @staticmethod
    def restart_pipewire():
        """
        systemctl --user restart pipewire pipewire-pulse (必要に応じて) を実行
        """
        results = []
        # pipewire
        cmd1 = ["systemctl", "--user", "restart", "pipewire"]
        p1 = subprocess.run(cmd1, capture_output=True, text=True)
        results.append(("pipewire", p1.returncode == 0, p1.stdout or p1.stderr))

        # pipewire-pulse が存在・有効な場合は再起動
        cmd2 = ["systemctl", "--user", "restart", "pipewire-pulse"]
        p2 = subprocess.run(cmd2, capture_output=True, text=True)
        if p2.returncode == 0:
            results.append(("pipewire-pulse", True, p2.stdout or p2.stderr))

        return results

    @staticmethod
    def get_service_status():
        """
        systemctl --user status pipewire の出力を取得
        """
        try:
            p = subprocess.run(
                ["systemctl", "--user", "status", "pipewire"],
                capture_output=True,
                text=True,
                timeout=5
            )
            is_active = p.returncode == 0
            return is_active, p.stdout or p.stderr
        except Exception as e:
            return False, str(e)

    @staticmethod
    def get_live_metadata():
        """
        pw-metadata -n settings から現在のリアルタイム設定情報を取得
        """
        info = {
            "clock.rate": "不明",
            "clock.allowed-rates": "不明",
            "clock.quantum": "不明",
            "clock.min-quantum": "不明",
            "clock.max-quantum": "不明",
            "raw": ""
        }
        try:
            p = subprocess.run(
                ["pw-metadata", "-n", "settings"],
                capture_output=True,
                text=True,
                timeout=5
            )
            raw = p.stdout
            info["raw"] = raw

            for line in raw.splitlines():
                if "key:'clock.rate'" in line:
                    m = re.search(r"value:'([^']+)'", line)
                    if m:
                        info["clock.rate"] = m.group(1)
                elif "key:'clock.allowed-rates'" in line:
                    m = re.search(r"value:'([^']+)'", line)
                    if m:
                        info["clock.allowed-rates"] = m.group(1)
                elif "key:'clock.quantum'" in line:
                    m = re.search(r"value:'([^']+)'", line)
                    if m:
                        info["clock.quantum"] = m.group(1)
                elif "key:'clock.min-quantum'" in line:
                    m = re.search(r"value:'([^']+)'", line)
                    if m:
                        info["clock.min-quantum"] = m.group(1)
                elif "key:'clock.max-quantum'" in line:
                    m = re.search(r"value:'([^']+)'", line)
                    if m:
                        info["clock.max-quantum"] = m.group(1)

        except Exception as e:
            info["raw"] = f"取得エラー: {e}"

        return info

    @staticmethod
    def get_recent_logs(lines=50):
        """
        journalctl --user -u pipewire の最新ログを取得
        """
        try:
            p = subprocess.run(
                ["journalctl", "--user", "-u", "pipewire", "-n", str(lines), "--no-pager"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return p.stdout or p.stderr
        except Exception as e:
            return f"ログ取得エラー: {e}"

    @staticmethod
    def get_pw_top_snapshot():
        """
        pw-top -b -n 2 を実行して最新のリアルタイムグラフ・ノード再生状態を取得
        (1回目サンプルは初期化直後のため0/---となり、2回目サンプルで正確なリアルタイム数値が得られる)
        """
        try:
            p = subprocess.run(
                ["pw-top", "-b", "-n", "2"],
                capture_output=True,
                text=True,
                timeout=5
            )
            raw = p.stdout
            blocks = raw.split("S   ID  QUANT")
            if len(blocks) > 1:
                return ("S   ID  QUANT" + blocks[-1]).strip()
            return raw.strip() or p.stderr
        except Exception as e:
            return f"pw-top 取得エラー: {e}"
