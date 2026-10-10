"""Command-line tests against the mock printer (no hardware needed)."""
import os
import sys
import threading

import pytest
from PIL import Image, ImageChops

import niimbot_k3w as k


def host_args(m):
    return ["--host", m.host, "--port", str(m.port)]


def same_image(a, b):
    a, b = Image.open(a).convert("L"), Image.open(b).convert("L")
    return a.size == b.size and ImageChops.difference(a, b).getbbox() is None


def test_dry_run_writes_preview_and_does_not_connect(mock_printer, run_cli, tmp_path):
    rc, out, err = run_cli(*host_args(mock_printer), "--dry-run", "设备 A-1024", "责任人 张三")
    assert rc == 0, err
    assert "[DRY-RUN]" in out
    assert (tmp_path / "preview.png").exists()
    assert mock_printer.reports() == []


def test_dry_run_needs_no_host(run_cli, tmp_path):
    rc, out, err = run_cli("--dry-run", "--preview", "p.png", "A-1024")
    assert rc == 0, err
    assert (tmp_path / "p.png").exists()


def test_printing_without_host_is_a_usage_error(run_cli):
    rc, out, err = run_cli("A-1024")
    assert rc == 2 and "NIIMBOT_HOST" in err


def test_host_from_environment(mock_printer, run_cli):
    rc, out, err = run_cli("--port", str(mock_printer.port), "A-1024", env={"NIIMBOT_HOST": mock_printer.host})
    assert rc == 0, err
    assert mock_printer.wait(1)[0]["ok"]


def test_text_label_prints_exactly_the_preview(mock_printer, run_cli, tmp_path):
    rc, out, err = run_cli(*host_args(mock_printer), "仓库 B-03", "负责人 李四")
    assert rc == 0, err
    assert out.splitlines()[-1] == "[OK] printed 1/1"
    (report,) = mock_printer.wait(1)
    assert report["ok"] and not report["warnings"], report
    assert same_image(mock_printer.label(1), tmp_path / "preview.png")


def test_copies_wait_for_the_printer_to_be_idle(mock_printer, run_cli):
    """The mock rejects a new job while the previous label is still feeding, like the real printer."""
    rc, out, err = run_cli(*host_args(mock_printer), "--copies", "3", "样品 C 区")
    assert rc == 0, err
    reports = mock_printer.wait(3)
    assert [r["labels_printed"] for r in reports] == [1, 1, 1]
    assert all(r["ok"] and not r["warnings"] for r in reports), reports


def test_waits_while_someone_else_is_printing(mock_printer, run_cli):
    other = threading.Thread(target=k.print_label,
                             args=(Image.new("L", (480, 320), 0), mock_printer.host, mock_printer.port))
    other.start()
    rc, out, err = run_cli(*host_args(mock_printer), "后来的人")
    other.join()
    assert rc == 0, err
    reports = mock_printer.wait(2)
    assert all(r["ok"] for r in reports), [r["errors"] for r in reports]


def test_qr_label_matches_the_qr_matrix(mock_printer, run_cli, tmp_path):
    qrcode = pytest.importorskip("qrcode")
    data = "https://example.com/asset/A-1024"
    rc, out, err = run_cli(*host_args(mock_printer), "--qr", data, "A-1024")
    assert rc == 0, err
    mock_printer.wait(1)
    # sample the printed QR module by module and compare with what qrcode generates
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    q.add_data(data)
    q.make(fit=True)
    matrix = q.get_matrix()
    side = min(320, 480 * 11 // 20)
    box = side // (len(matrix) + 8)
    off = (side - box * len(matrix)) // 2
    top = (320 - side) // 2
    label = Image.open(mock_printer.label(1)).convert("L")
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            px = label.getpixel((off + x * box + box // 2, top + off + y * box + box // 2))
            assert (px < 128) == dark
    cv2 = pytest.importorskip("cv2")
    decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(mock_printer.label(1), cv2.IMREAD_GRAYSCALE))
    assert decoded == data


def test_options_after_double_dash_are_rejected(mock_printer, run_cli):
    rc, out, err = run_cli(*host_args(mock_printer), "--", "-18℃", "--dry-run")
    assert rc == 2 and "--dry-run" in err
    assert mock_printer.reports() == []


def test_text_starting_with_dash(run_cli):
    rc, out, err = run_cli("--dry-run", "--", "-18℃ 冷冻")
    assert rc == 0, err


@pytest.mark.parametrize("args", [
    ["--w-mm", "0", "x"], ["--w-mm", "81", "x"], ["--h-mm", "9000", "x"], ["--w-mm", "nan", "x"],
    ["--copies", "0", "x"], ["--density", "6", "x"], ["--image", "a.png", "x"], ["--qr", "a", "--image", "a.png"],
])
def test_bad_arguments_are_usage_errors(run_cli, args):
    rc, out, err = run_cli("--dry-run", *args)
    assert rc == 2 and "Traceback" not in err


@pytest.mark.parametrize("args, message", [
    (["  "], "空"),
    (["本设备为公司固定资产请勿私自挪用" * 10], "放不下"),
    (["--image", "no_such.png"], "打不开图片"),
    (["--font", "no_such_font.ttf", "x"], "打不开字体"),
])
def test_bad_input_fails_without_printing(mock_printer, run_cli, args, message):
    rc, out, err = run_cli(*host_args(mock_printer), *args)
    assert rc == 1 and err.startswith("[FAILED]") and message in err
    assert mock_printer.reports() == []


def test_missing_glyphs_are_reported(run_cli):
    rc, out, err = run_cli("--dry-run", "͸ 已检验")  # U+0378 is unassigned, so no font has it
    assert rc == 0 and "[WARN]" in out and "͸" in out


def test_connection_refused_is_reported(run_cli):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens on this port now
    rc, out, err = run_cli("--host", "127.0.0.1", "--port", str(port), "A-1024")
    assert rc == 1 and "[FAILED] printed 0/1" in err and "没有打出来" in err


def test_failure_in_the_middle_reports_what_was_printed(monkeypatch, capsys, tmp_path):
    calls = []

    def fake(img, *a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise k.PrintFailed("等打印完成超时", True)

    monkeypatch.setattr(k, "print_label", fake)
    monkeypatch.setattr(sys, "argv", ["niimbot_k3w.py", "--host", "127.0.0.1", "--copies", "3", "测试"])
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as e:
        k.main()
    assert e.value.code == 1
    out = capsys.readouterr()
    assert "已打印 1/3" in out.out
    assert "[FAILED] printed 1/3" in out.err and "可能已经出纸" in out.err


def test_version(run_cli):
    rc, out, err = run_cli("--version")
    assert rc == 0 and k.__version__ in out
