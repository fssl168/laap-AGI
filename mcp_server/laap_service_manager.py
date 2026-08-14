"""
LAAP Backend Service Manager
Starts/stops the LAAP Brain API server (laap_brain_api.py) on port 11546.
"""
import subprocess
import sys
import os
import time
import requests
from pathlib import Path

LAAP_ROOT = Path(r"D:\laap-AGI")
LAAP_API_SCRIPT = LAAP_ROOT / "aris_brain" / "laap_brain_api.py"
LAAP_PORT = 11546
LAAP_API_BASE = f"http://localhost:{LAAP_PORT}"


def is_running() -> bool:
    """Check if LAAP API is already running."""
    try:
        resp = requests.get(f"{LAAP_API_BASE}/health", timeout=2)
        return resp.status_code == 200
    except:
        return False


def start(timeout: int = 30) -> bool:
    """Start LAAP API server in background."""
    if is_running():
        print(f"[LAAP] Already running on port {LAAP_PORT}", file=sys.stderr)
        return True

    print(f"[LAAP] Starting LAAP Brain API on port {LAAP_PORT}...", file=sys.stderr)

    # Use Python from laap-AGI venv
    python_exe = LAAP_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python_exe.exists():
        python_exe = Path(sys.executable)

    # Set environment to include LAAP_ROOT
    env = os.environ.copy()
    env["LAAP_ROOT"] = str(LAAP_ROOT)
    env["PYTHONPATH"] = str(LAAP_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    # 本机服务默认只绑 127.0.0.1 (无认证 API 不应暴露到局域网; 需要远程访问时在 .env 设 LAAP_HOST=0.0.0.0)
    host = os.environ.get("LAAP_HOST", "127.0.0.1")
    proc = subprocess.Popen(
        [str(python_exe), str(LAAP_API_SCRIPT), "--port", str(LAAP_PORT), "--host", host],
        cwd=str(LAAP_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    # Wait for startup
    for i in range(timeout):
        time.sleep(1)
        if is_running():
            print(f"[LAAP] Started successfully (PID {proc.pid})", file=sys.stderr)
            return True
        print(f"[LAAP] Waiting... ({i+1}/{timeout}s)", file=sys.stderr)

    print(f"[LAAP] Timeout waiting for startup", file=sys.stderr)
    proc.terminate()
    return False


def stop() -> bool:
    """Stop LAAP API server."""
    if not is_running():
        print(f"[LAAP] Not running", file=sys.stderr)
        return True

    print(f"[LAAP] Stopping LAAP Brain API...", file=sys.stderr)

    # Kill by port on Windows
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["netstat", "-ano"],
                capture_output=True, text=True, encoding='utf-8', errors='ignore'
            )
            for line in result.stdout.splitlines():
                if f":{LAAP_PORT}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1] if parts else None
                    if pid:
                        try:
                            subprocess.run(["taskkill", "/PID", pid, "/F"], 
                                         check=False, capture_output=True)
                            print(f"[LAAP] Killed PID {pid}", file=sys.stderr)
                        except Exception as e:
                            print(f"[LAAP] Failed to kill {pid}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[LAAP] Error checking ports: {e}", file=sys.stderr)
    else:
        subprocess.run(["pkill", "-f", "laap_brain_api.py"], check=False)

    # Verify
    for _ in range(10):
        time.sleep(1)
        if not is_running():
            print(f"[LAAP] Stopped successfully", file=sys.stderr)
            return True

    print(f"[LAAP] Failed to stop", file=sys.stderr)
    return False


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "start":
            sys.exit(0 if start() else 1)
        elif cmd == "stop":
            sys.exit(0 if stop() else 1)
        elif cmd == "status":
            print("running" if is_running() else "stopped")
            sys.exit(0 if is_running() else 1)
        else:
            print(f"Unknown command: {cmd}")
            sys.exit(1)
    else:
        print("Usage: laap_service_manager.py [start|stop|status]")
        sys.exit(1)
