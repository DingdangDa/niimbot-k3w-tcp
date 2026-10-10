"""Print one label per row of a CSV file.

Every column becomes one line of text, except a column named "qr", which becomes a QR code.

    python examples/batch_csv.py examples/labels.csv --dry-run              # previews/001.png, 002.png, ...
    python examples/batch_csv.py examples/labels.csv --host 192.168.1.50

All labels are built before anything is printed, so a bad row stops the run before any paper is used.
Labels are printed one job at a time. If one fails, the script stops and says how many were printed;
continue with --start.
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import niimbot_k3w as k  # noqa: E402


def make_label(row, size, font):
    qr = (row.pop("qr", None) or "").strip()
    lines = [v.strip() for v in row.values() if v and v.strip()]
    if qr:
        return k.qr_label(qr, lines, size, font)[0]
    if not lines:
        raise ValueError("empty row")
    return k.text_block(lines, size, font)[0]


def main():
    ap = argparse.ArgumentParser(description="Print one label per CSV row")
    ap.add_argument("csv", help="UTF-8 CSV file with a header row")
    ap.add_argument("--host", default=k.HOST, help="printer IP (default: NIIMBOT_HOST)")
    ap.add_argument("--port", type=int, default=k.PORT)
    ap.add_argument("--density", type=int, default=3, choices=range(1, 6))
    ap.add_argument("--start", type=int, default=1, help="first row to print, 1 = first data row")
    ap.add_argument("--dry-run", action="store_true", help="only write previews to ./previews/")
    args = ap.parse_args()
    if not args.dry_run and not args.host:
        ap.error("--host (or NIIMBOT_HOST) is required unless --dry-run")

    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    size, font = k.label_size(), k.find_font()
    labels = []
    for n, row in enumerate(rows, 1):
        try:
            labels.append(make_label(dict(row), size, font))
        except ValueError as e:
            sys.exit(f"row {n}: {e}; nothing was printed")

    todo = list(enumerate(labels, 1))[args.start - 1:]
    if args.dry_run:
        os.makedirs("previews", exist_ok=True)
        for n, img in todo:
            k.save_preview(k.to_bits(img), os.path.join("previews", f"{n:03d}.png"))
        print(f"wrote {len(todo)} previews to previews/")
        return
    for n, img in todo:
        try:
            k.print_label(img, args.host, args.port, args.density)
        except k.PrintFailed as e:
            state = "may have printed" if e.maybe_printed else "was not printed"
            sys.exit(f"row {n} failed: {e}\nrows {args.start}-{n - 1} were printed, row {n} {state}. "
                     f"Check the printer, then continue with --start {n} or --start {n + 1}.")
        print(f"printed row {n}")
    print(f"done: {len(todo)} labels")


if __name__ == "__main__":
    main()
