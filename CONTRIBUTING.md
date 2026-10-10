# Contributing

Thanks for helping. Bug reports, results from other printer models and pull requests are all welcome.
Issues and pull requests can be in English or Chinese.

## Reporting a printer model

Whether it worked or not, please open an issue with:

- the output of `python tools/info.py --host <printer IP>` (the serial number is hidden unless you add
  `--serial`; leave it out);
- the label size, the command you ran and its full output;
- what came out of the printer.

For timing problems (labels missing when printing several in a row, timeouts), also attach the log from
`python tools/diag.py --host <printer IP> --jobs 2 --text test`. It prints two labels and records every
packet with timestamps.

## Development

```sh
pip install -e ".[test]"
pytest
```

The tests start their own mock printers and never touch real hardware. Please run them before opening a pull
request, and add a test for new behaviour.

- **Try changes against the mock first** (`python tools/mock_k3w.py`). It checks the packets it receives and
  saves what would have been printed as a PNG, so most mistakes show up without wasting labels.
- **On real hardware, start with `--dry-run`**, then print a single label.
- **Protocol changes need hardware evidence.** `tests/data/golden_frames.txt` records the packets of a flow
  that printed correctly on a real K3_W. If a change makes that test fail, explain in the pull request why
  and which printer you verified the new packets on.
- **Only use commands documented in [docs/protocol.md](docs/protocol.md).** If you find out something new
  about the protocol, add it there together with how you observed it.
- Program messages are in Chinese; keep new ones in the same style. Code follows the existing style (no
  extra dependencies beyond Pillow, and qrcode for QR codes).
