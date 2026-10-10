# Changelog

## 0.1.0 — unreleased

First release.

- Print text (including Chinese), QR codes and images to a NIIMBOT K3_W over TCP port 9100.
- Waits for the printer's busy flag before starting a job and before ending it, so labels printed back to
  back are not silently refused.
- Preview with `--dry-run`; clear `[OK]` / `[FAILED]` result lines and exit codes.
- `tools/info.py` (read-only printer information), `tools/diag.py` (packet log with timestamps),
  `tools/mock_k3w.py` (mock printer for testing without hardware).
- Protocol notes in `docs/protocol.md`.
