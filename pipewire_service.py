import subprocess
import re

class PipeWireServiceManager:
    """Class to manage PipeWire systemd user services, metadata querying, and log inspection."""

    @staticmethod
    def restart_pipewire():
        """
        Execute systemctl --user restart pipewire (and pipewire-pulse if active).
        """
        results = []
        # pipewire
        cmd1 = ["systemctl", "--user", "restart", "pipewire"]
        p1 = subprocess.run(cmd1, capture_output=True, text=True)
        results.append(("pipewire", p1.returncode == 0, p1.stdout or p1.stderr))

        # pipewire-pulse
        cmd2 = ["systemctl", "--user", "restart", "pipewire-pulse"]
        p2 = subprocess.run(cmd2, capture_output=True, text=True)
        if p2.returncode == 0:
            results.append(("pipewire-pulse", True, p2.stdout or p2.stderr))

        return results

    @staticmethod
    def get_service_status():
        """
        Get output of systemctl --user status pipewire
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
        Get active runtime metadata via pw-metadata -n settings
        """
        info = {
            "clock.rate": "Unknown",
            "clock.allowed-rates": "Unknown",
            "clock.quantum": "Unknown",
            "clock.min-quantum": "Unknown",
            "clock.max-quantum": "Unknown",
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
            info["raw"] = f"Error retrieving settings: {e}"

        return info

    @staticmethod
    def get_recent_logs(lines=50):
        """
        Get recent logs from journalctl --user -u pipewire
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
            return f"Error retrieving logs: {e}"

    @staticmethod
    def get_pw_top_snapshot():
        """
        Run pw-top -b -n 2 to get real-time stream status and node playback statistics.
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
            return f"Error running pw-top: {e}"
