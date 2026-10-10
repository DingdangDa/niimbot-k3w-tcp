"""The examples run as documented, against the mock printer."""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(ROOT, "examples")


def run(cwd, *args):
    env = dict(os.environ)
    env.pop("NIIMBOT_HOST", None)
    r = subprocess.run([sys.executable, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(cwd), env=env)
    return r.returncode, r.stdout + r.stderr


def test_custom_layout(mock_printer, tmp_path):
    rc, out = run(tmp_path, os.path.join(EXAMPLES, "custom_layout.py"))
    assert rc == 0, out
    assert (tmp_path / "label.png").exists() and mock_printer.reports() == []
    rc, out = run(tmp_path, os.path.join(EXAMPLES, "custom_layout.py"),
                  "--host", mock_printer.host, "--port", str(mock_printer.port))
    assert rc == 0, out
    assert mock_printer.wait(1)[0]["ok"]


def test_batch_csv(mock_printer, tmp_path):
    pytest.importorskip("qrcode")
    script, csv = os.path.join(EXAMPLES, "batch_csv.py"), os.path.join(EXAMPLES, "labels.csv")
    rc, out = run(tmp_path, script, csv, "--dry-run")
    assert rc == 0, out
    assert sorted(os.listdir(tmp_path / "previews")) == ["001.png", "002.png", "003.png"]
    assert mock_printer.reports() == []
    rc, out = run(tmp_path, script, csv, "--host", mock_printer.host, "--port", str(mock_printer.port), "--start", "2")
    assert rc == 0, out
    reports = mock_printer.wait(2)
    assert [r["labels_printed"] for r in reports] == [1, 1] and all(r["ok"] for r in reports)


def test_batch_csv_stops_before_printing_on_a_bad_row(mock_printer, tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("code,location\nA-1,B-1\n,\n", encoding="utf-8")
    rc, out = run(tmp_path, os.path.join(EXAMPLES, "batch_csv.py"), str(bad),
                  "--host", mock_printer.host, "--port", str(mock_printer.port))
    assert rc == 1 and "row 2" in out
    assert mock_printer.reports() == []
