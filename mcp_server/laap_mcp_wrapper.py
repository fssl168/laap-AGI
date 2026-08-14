"""
LAAP MCP Server wrapper with automatic backend service management.
Starts LAAP API when MCP server initializes, stops it on exit.
"""
import sys
import os
import signal
import subprocess

# ── Path setup ──────────────────────────────────────────────
LAAP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAAP_VENV_SITE = os.path.join(LAAP_ROOT, '.venv', 'Lib', 'site-packages')

sys.path = [p for p in sys.path if 'hermes-agent' not in p]
if LAAP_VENV_SITE not in sys.path:
    sys.path.insert(0, LAAP_VENV_SITE)

print(f"LAAP MCP wrapper started", file=sys.stderr)
print(f"  Python: {sys.executable}", file=sys.stderr)
print(f"  LAAP_ROOT: {LAAP_ROOT}", file=sys.stderr)

# ── Start LAAP backend service ───────────────────────────────
LAAP_MANAGER = os.path.join(LAAP_ROOT, 'mcp_server', 'laap_service_manager.py')

def start_laap():
    """Start LAAP API server with proper environment."""
    env = os.environ.copy()
    env['LAAP_ROOT'] = LAAP_ROOT
    env['PYTHONPATH'] = LAAP_ROOT + os.pathsep + env.get('PYTHONPATH', '')
    
    proc = subprocess.Popen(
        [sys.executable, LAAP_MANAGER, 'start'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        cwd=LAAP_ROOT,
    )
    return proc

# Start LAAP on import
_laap_proc = start_laap()
print(f"[LAAP] Backend service starting...", file=sys.stderr)

# ── Cleanup on exit ──────────────────────────────────────────
def cleanup(signum=None, frame=None):
    """Stop LAAP backend on exit."""
    print(f"[LAAP] Stopping backend service...", file=sys.stderr)
    try:
        subprocess.run([sys.executable, LAAP_MANAGER, 'stop'], 
                     capture_output=True, timeout=10)
    except Exception as e:
        print(f"[LAAP] Cleanup error: {e}", file=sys.stderr)
    if signum is not None:
        sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

# ── Run MCP server ───────────────────────────────────────────
script_path = os.path.join(LAAP_ROOT, 'mcp_server', 'laap_mcp_server.py')

with open(script_path, 'r', encoding='utf-8') as f:
    code = compile(f.read(), script_path, 'exec')
    namespace = {'__name__': '__main__', '__file__': script_path}
    exec(code, namespace)
