"""Protocol-level tests: framing, row encoding, and byte-for-byte equality with the flow verified on hardware."""
import os
import random
import struct

from PIL import Image

import niimbot_k3w as k

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def xor(*parts):
    cs = 0
    for part in parts:
        for b in part:
            cs ^= b
    return cs


def test_frame_matches_bytes_seen_on_hardware():
    assert k.frame(0x23, [1]) == bytes.fromhex("55 55 23 01 01 23 aa aa")
    assert k.frame(0xA3) == bytes.fromhex("55 55 a3 01 01 a3 aa aa")
    page_size = struct.pack(">HHHHBBBH", 320, 480, 1, 0, 0, 0, 0, 0)
    assert k.frame(0x13, page_size) == bytes.fromhex(
        "55 55 13 0d 01 40 01 e0 00 01 00 00 00 00 00 00 00 bf aa aa")


def test_full_black_row_packet():
    bits = k.to_bits(Image.new("L", (480, 1), 0))
    (pkt,) = list(k.row_packets(bits))
    body = bytes([0x00, 0x00, 208, 208, 64, 1]) + b"\xff" * 60  # row 0, counts 26+26+8 bytes of dots, repeat 1
    assert pkt == b"\x55\x55" + bytes([0x85, len(body)]) + body + bytes([xor([0x85, len(body)], body)]) + b"\xaa\xaa"


def decode_rows(packets, width, height):
    """Rebuild the bitmap from 0x84 / 0x85 packets, checking row numbers and repeat limits on the way."""
    nb = (width + 7) // 8
    rows = [None] * height
    expect = 0
    for pkt in packets:
        assert pkt[:2] == b"\x55\x55" and pkt[-2:] == b"\xaa\xaa"
        cmd, n, data = pkt[2], pkt[3], pkt[4:-3]
        assert len(data) == n and pkt[-3] == xor([cmd, n], data)
        y = struct.unpack(">H", data[:2])[0]
        assert y == expect, "row numbers must be contiguous, starting at 0"
        if cmd == 0x84:
            rep, row = data[2], bytes(nb)
        else:
            assert cmd == 0x85
            rep, row = data[5], data[6:]
            assert len(row) == nb
            total = sum(bin(b).count("1") for b in row)
            if nb <= 78:
                chunks = [sum(bin(b).count("1") for b in row[i * 26:(i + 1) * 26]) for i in range(3)]
                assert list(data[2:5]) == chunks
            else:
                assert list(data[2:5]) == [0, total & 0xFF, total >> 8]  # little-endian total
        assert 1 <= rep <= 255
        assert all((y + r) % 200 != 0 for r in range(1, rep)), "a run must not cross row 200, 400, ..."
        for r in range(y, y + rep):
            rows[r] = row
        expect = y + rep
    assert expect == height
    return b"".join(rows)


def test_row_packets_roundtrip_random_images():
    rnd = random.Random(1)
    for width in (480, 632, 640):
        for _ in range(4):
            img = Image.new("L", (width, 450), 255)
            for _ in range(rnd.randint(0, 40)):
                x0, y0 = rnd.randrange(width), rnd.randrange(450)
                img.paste(0, (x0, y0, min(width, x0 + rnd.randint(1, 200)), min(450, y0 + rnd.randint(1, 120))))
            bits = k.to_bits(img)
            assert decode_rows(k.row_packets(bits), width, 450) == bits.tobytes()


def test_blank_page_uses_blank_row_packets():
    pkts = list(k.row_packets(k.to_bits(Image.new("L", (480, 320), 255))))
    assert [p[2] for p in pkts] == [0x84, 0x84]  # rows 0-199 and 200-319


def test_same_frames_as_hardware_verified_flow(mock_printer):
    """golden_frames.txt was recorded from the implementation that printed correctly on a real K3_W.
    Status polls (0xA3) are left out: the current code adds busy-flag waits around the job."""
    with open(os.path.join(DATA, "golden_frames.txt"), encoding="utf-8") as f:
        golden = f.read().splitlines()
    k.print_label(os.path.join(DATA, "golden.png"), mock_printer.host, mock_printer.port)
    (report,) = mock_printer.wait(1)
    assert report["ok"], report["errors"]
    assert [f for f in mock_printer.frames(1) if not f.startswith("a3 ")] == golden
    printed = Image.open(mock_printer.label(1)).convert("1")
    expected = Image.open(os.path.join(DATA, "golden.png")).convert("1")
    assert printed.tobytes() == expected.tobytes()
