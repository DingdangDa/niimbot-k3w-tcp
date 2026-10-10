# NIIMBOT packet protocol over TCP port 9100 (K3_W notes)

This page documents what `niimbot_k3w.py` sends and what the printer answers. It was worked out on one
**NIIMBOT K3_W** (Wi‑Fi model, model id `0x1301`, firmware 5.07, protocol version 5 according to `0xA5`)
printing 60 × 40 mm gap labels at 203 dpi.

The packet format and command set are the same ones the Bluetooth and USB models use, documented by
[niimbluelib](https://github.com/MultiMote/niimbluelib) and [niimprint](https://github.com/kjy00302/niimprint).
This page concentrates on what is specific to the TCP connection and to this printer — mainly the
[busy flag](#the-busy-flag-and-rejected-jobs), which has to be respected between jobs.

Anything not listed here was not used or not tested.

## Transport

- Plain TCP to port 9100. No handshake or login; the first packet can be a command.
- Replies come back on the same connection. Several replies can arrive in one `recv()`, and one reply can be
  split across several, so buffer the stream and cut frames by header, length and trailer.
- One connection per print job works. A new connection is accepted right after the previous one closes.
- Text printer languages are not understood on this port: TSPL and ZPL jobs produced no label and no reply.
- The official desktop app did not find a printer on another subnet, while direct TCP to its IP worked
  (probably because the app discovers printers with broadcasts).

## Frame format

```
55 55 | cmd | len | data (len bytes) | checksum | AA AA
```

- `cmd`, `len` and `checksum` are one byte each; `len` is the number of data bytes.
- `checksum = cmd XOR len XOR every data byte`.
- Replies use the same format. Multi-byte numbers are big-endian (one exception, see the `0x85` pixel count).

Example: `55 55 23 01 01 23 AA AA` is command `0x23` with one data byte `01`.

## Commands used for printing

| Command | Data sent | Reply | Meaning |
|---|---|---|---|
| `0x23` | `[01]` | `0x33 [01]` | label type 1 = labels with gaps |
| `0x21` | `[density 1–5]` | `0x31 [01]` | print density |
| `0x01` | `[00 01 00 00 00 00 00 00 00]` | `0x02 [01]` | start a print job of 1 page (9-byte form) |
| `0xA3` | `[01]` | `0xB3`, 8 bytes | print status, see below |
| `0x13` | 13 bytes, see below | `0x14 [01 00]` | page size |
| `0x84` | `[row (2), repeat]` | none | blank rows |
| `0x85` | `[row (2), pixel count (3), repeat, row data]` | none | bitmap rows |
| `0xE3` | `[01]` | `0xE4 [01]` | end of page |
| `0xF3` | `[01]` | `0xF4 [01]` | end of the print job |

Error replies: `0x00` = command not supported, `0xDB` = print error (niimbluelib lists `01` = cover open,
`02` = no paper).

### Print one label

1. Poll `0xA3` every 0.3 s until the busy flag is 0 (see below).
2. `0x23 [01]` → `0x33 [01]`
3. `0x21 [density]` → `0x31 [01]`
4. `0x01 [00 01 00 00 00 00 00 00 00]` → `0x02 [01]`
5. `0xA3 [01]` once, without waiting for the reply (niimbluelib does this, following the official app; the
   `0xB3` reply arrives while you wait for the next one).
6. `0x13 [rows (2), columns (2), 00 01, 00 00, 00, 00, 00, 00 00]` → `0x14 [01 00]`.
   Rows run along the paper feed (40 mm = 320 rows); columns run across the print head
   (60 mm = 480 columns, a multiple of 8). For 60 × 40 mm:
   `55 55 13 0D 01 40 01 E0 00 01 00 00 00 00 00 00 00 BF AA AA`.
7. Send all image rows (`0x84` / `0x85`, below). They get no reply, except `0xD3 [row (2), 01]` after
   rows 199, 399, … and after the last row, which can be ignored.
8. `0xE3 [01]` → `0xE4 [01]`.
9. Poll `0xA3` every 0.3 s until the printed-page count is at least 1, then until the busy flag is 0.
   The first replies after `0xE3` can still say "0 pages, 100 % done" from before, so do not use the
   progress bytes to decide that the label is finished.
10. `0xF3 [01]` → `0xF4 [01]`.

If the first data byte of the replies in steps 2, 3, 4 or 6 is `00`, the printer has refused the job:
stop and do not send the image. If anything fails after step 4, still send `0xF3`.

To print N copies, repeat the whole sequence N times (one job per label). Copy counts in `0x01` / `0x13`
other than 1 were not tested.

For a 40 mm label, steps 2–10 took 1.2–1.6 s in the measured runs; most of it is the printer printing and feeding.

### Image rows

Each row of the label is `columns / 8` bytes. The most significant bit of each byte is the leftmost dot;
`1` = black.

- A row with no black dots is sent as `0x84 [row (2), repeat]`.
- Other rows: `0x85 [row (2), pixel count (3), repeat, row data]`.
- Row numbers start at 0. Consecutive identical rows are sent once with `repeat` 1–255, but a run must not
  cross row 200, 400, 600, … (a row whose number is a multiple of 200 always starts a new packet).
- Pixel count, when the row data is at most 78 bytes: split the row into three 26-byte chunks and count the
  `1` bits in each chunk. For longer rows: `[00, total & 0xFF, total >> 8]` — this is the one little-endian
  number in the protocol.

Example, an all-black row 0 of a 480-dot label:
`55 55 85 42 00 00 D0 D0 40 01 FF×60 86 AA AA` (counts 208 + 208 + 64).

## Print status (`0xB3`) and the busy flag

`0xA3 [01]` is answered with `0xB3` and 8 data bytes:

```
[printed pages (2), print progress %, feed progress %, ?, ?, ?, busy]
```

`busy` is `01` while the printer is printing or feeding paper and `00` when it is idle. Bytes 4–6 change
during printing but their meaning is unknown.

### The busy flag and rejected jobs

The printed-page count can reach 1 while the label is still feeding out. `0xF3` is accepted at that point,
but a job started right after it is refused:

| Time (ms) | Sent | Received |
|---:|---|---|
| 1293 | | `0xB3`: 1 page printed, busy = **01** |
| 1293 | `0xF3` (end job 1) | `0xF4 [01]` |
| 1320 | `0x23 [01]` (job 2) | `0x33` **`[00]`** |
| 1439 | `0x01 [00 01 …]` | `0x02` **`[00]`** |
| 1447 | `0x13 …` | `0x14` **`[00]`** |
| 1668 | image rows, `0xE3` | `0xE4 [01]` |
| … 42075 | `0xA3` polls | always 0 pages, busy = 00 — nothing was printed |

The refusal is silent apart from the `00` bytes: the image and `0xE3` are still acknowledged, and the label is
never printed. This only happened when the next job started within a few tens of milliseconds; in the runs
where the busy flag was already 0 when the page count reached 1, back-to-back jobs printed fine.

`niimbot_k3w.py` therefore waits for `busy = 00` before starting a job and again before sending `0xF3`, and
checks the first reply byte of steps 2, 3, 4 and 6. With these waits, two QR labels printed back to back
correctly on the real printer, and three back-to-back jobs pass against the mock printer, which reproduces
the refusal.

## Read-only queries

`tools/info.py` sends these. None of them print or move paper.

| Command | Reply | Content on the K3_W |
|---|---|---|
| `0x40 [08]` | `0x48 [13 01]` | model id `0x1301` (K3_W in niimbluelib's model table) |
| `0x40 [09]` | `0x49 [05 07]` | firmware version 5.07 |
| `0x40 [0C]` | `0x4C [05 01]` | hardware version 5.01 |
| `0x40 [0B]` | `0x4B [ASCII]` | serial number |
| `0x40 [0A]` | `0x00 [01]` | battery level: not supported (mains powered) |
| `0xDC [01]` | `0xDD`, 13 bytes | heartbeat |
| `0xA5 [01]` | `0xB5`, 27 bytes | status data; data bytes 11–12 (counting from 0) = `03 02` → protocol version 5 per niimbluelib |
| `0x1A [01]` | `0x1B`, 42 bytes | information about the label roll, including a used-label counter |

`tools/diag.py` reads the used-label counter before and after a run to count how many labels actually came
out.

## Differences from niimbluelib's D110M_V4 flow

This flow follows niimbluelib's `D110M_V4` print task (used for protocol version ≥ 4), with these
differences, all tested on the K3_W:

- the speed byte in `0x01` is `00`;
- `0x83` (indexed rows for rows with ≤ 6 black dots) is not used — every non-blank row is `0x85`;
- no heartbeat is sent after `0xF3`;
- the busy-flag waits described above.

## Not used

The protocol has further commands (for example calibration and firmware updates). This project does not send
them; sending unknown commands to a printer can leave it in a bad state.
