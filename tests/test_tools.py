"""tools/info.py and tools/diag.py against the mock printer."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cwd, *args):
    env = dict(os.environ)
    env.pop("NIIMBOT_HOST", None)
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(cwd), env=env)
    return r.returncode, r.stdout + r.stderr


def test_info_reads_model_and_hides_serial(mock_printer, tmp_path):
    info = os.path.join(ROOT, "tools", "info.py")
    rc, out = run(tmp_path, info, "--host", mock_printer.host, "--port", str(mock_printer.port))
    assert rc == 0, out
    assert "0x1301（K3_W）" in out and "5.07" in out and "协议版本：5" in out
    assert "MOCK" not in out  # serial number hidden by default
    rc, out = run(tmp_path, info, "--host", mock_printer.host, "--port", str(mock_printer.port), "--serial")
    assert rc == 0 and "MOCK0000001" in out
    assert all(r["labels_printed"] == 0 for r in mock_printer.wait(2))


def test_info_needs_a_host(tmp_path):
    rc, out = run(tmp_path, os.path.join(ROOT, "tools", "info.py"))
    assert rc == 2 and "NIIMBOT_HOST" in out


def test_diag_status_is_read_only_and_print_logs_frames(mock_printer, tmp_path):
    diag = os.path.join(ROOT, "tools", "diag.py")
    host = ["--host", mock_printer.host, "--port", str(mock_printer.port)]
    rc, out = run(tmp_path, diag, *host, "--status")
    assert rc == 0, out
    rc, out = run(tmp_path, diag, *host, "--tag", "t", "--after", "0", "--text", "诊断")
    assert rc == 0, out
    reports = mock_printer.wait(2)
    assert [r["labels_printed"] for r in reports] == [0, 1] and all(r["ok"] for r in reports)
    logs = os.listdir(tmp_path / "diag_logs")
    assert len(logs) == 2 and any(name.endswith("_t.log") for name in logs)
