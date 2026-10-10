import glob
import json
import os
import subprocess
import sys
import threading
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import mock_k3w  # noqa: E402

SCRIPT = os.path.join(ROOT, "niimbot_k3w.py")


class MockPrinter:
    """A mock K3_W running in this process. Each connection writes NNN_report.json / NNN_frames.txt."""

    def __init__(self, port, folder):
        self.host, self.port, self.folder = "127.0.0.1", port, folder

    def reports(self):
        return sorted(glob.glob(os.path.join(self.folder, "[0-9][0-9][0-9]_report.json")))

    def wait(self, n, timeout=10):
        """Wait until at least n connections have finished and been written, then return their reports."""
        deadline = time.time() + timeout
        while len(self.reports()) < n:
            if time.time() > deadline:
                raise AssertionError(f"mock saw {len(self.reports())} connections, expected {n}")
            time.sleep(0.05)
        time.sleep(0.1)
        return [self.report(p) for p in self.reports()]

    @staticmethod
    def report(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def frames(self, index):
        with open(os.path.join(self.folder, f"{index:03d}_frames.txt"), encoding="utf-8") as f:
            return f.read().splitlines()

    def label(self, index, job=1, page=1):
        return os.path.join(self.folder, f"{index:03d}_job{job}_page{page}.png")


@pytest.fixture
def mock_printer(tmp_path, monkeypatch):
    monkeypatch.setattr(mock_k3w, "ROOT", str(tmp_path / "sessions"))
    monkeypatch.setattr(mock_k3w, "DEVICES", {})
    monkeypatch.setattr(mock_k3w, "COUNTERS", {})
    srv = mock_k3w.Server(("127.0.0.1", 0), mock_k3w.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield MockPrinter(port, str(tmp_path / "sessions" / str(port)))
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def run_cli(tmp_path):
    """Run niimbot_k3w.py as a subprocess in tmp_path; returns (exit code, stdout, stderr)."""

    def run(*args, env=None):
        e = dict(os.environ)
        e.pop("NIIMBOT_HOST", None)
        e.update(env or {})
        r = subprocess.run([sys.executable, SCRIPT, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(tmp_path), env=e)
        return r.returncode, r.stdout.strip(), r.stderr.strip()

    return run
