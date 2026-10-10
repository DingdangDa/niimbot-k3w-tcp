#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diag.py — 真机诊断：按 niimbot_k3w.py 的流程打印，记下每一帧收发的时间；
打完后继续轮询状态，看打印机打完之后多久才真正空闲。日志写到 diag_logs/。
用来排查连续打印、忙标志之类的时序问题。

  python tools/diag.py --host IP --tag A --text '诊断 A' '单张'              一个任务打 1 张
  python tools/diag.py --host IP --tag B --copies 2 --text '诊断 B' '2 份'  一个任务里打 2 份
  python tools/diag.py --host IP --status                                  只读：查一次状态，不打印
"""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import niimbot_k3w as k

T0 = time.time()
LOG = []


def log(msg):
    line = f"{(time.time() - T0) * 1000:8.0f} ms  {msg}"
    LOG.append(line)
    print(line, flush=True)


class LogPrinter(k.Printer):
    def send(self, cmd, data=b"\x01"):
        log(f"→ {cmd:02x} {bytes(data).hex(' ')}")
        super().send(cmd, data)

    def _next_frame(self):
        f = super()._next_frame()
        if f:
            log(f"← {f[0]:02x} {f[1].hex(' ')}")
        return f


def status(p):
    d = p.request(0xA3, [1], 0xB3)
    return int.from_bytes(d[:2], "big"), d


def snapshot(p, what):
    hb = p.request(0xDC, [1], 0xDD)
    page, d = status(p)
    rfid = p.request(0x1A, [1], 0x1B)
    i = 8 + 1 + rfid[8]
    i += 1 + rfid[i]
    used = int.from_bytes(rfid[i + 2:i + 4], "big")
    log(f"[{what}] 心跳 {hb.hex(' ')}；状态 page={page} {d.hex(' ')}；标签卷已用 {used}")
    return used


def observe(p, seconds):
    """打完后继续轮询状态、心跳和标签卷用量，记下变化。"""
    end = time.time() + seconds
    last = None
    while time.time() < end:
        page, d = status(p)
        hb = p.request(0xDC, [1], 0xDD)
        r = p.request(0x1A, [1], 0x1B)
        cur = (d, hb, r)
        if cur != last:
            log(f"  打完后 page={page} 状态 {d.hex(' ')}；心跳 {hb.hex(' ')}；RFID 尾 {r[-7:].hex(' ')}")
            last = cur
        time.sleep(0.2)


def run_job(p, bits, copies, timeout):
    rows, cols = bits.height, bits.width
    p.request(0x23, [1], 0x33)
    p.request(0x21, [3], 0x31)
    p.request(0x01, [0, copies, 0, 0, 0, 0, 0, 0, 0], 0x02)  # 总页数 = copies
    p.send(0xA3)
    p.request(0x13, struct.pack(">HHHHBBBH", rows, cols, copies, 0, 0, 0, 0, 0), 0x14)  # 份数 = copies
    n = 0
    for pkt in k.row_packets(bits):
        p.sock.sendall(pkt)
        n += 1
    log(f"→ 图像行 {n} 包")
    p.request(0xE3, [1], 0xE4, timeout=10)
    deadline = time.time() + timeout
    last = None
    while True:
        page, d = status(p)
        if d != last:
            log(f"  状态变化 page={page} 打印进度={d[2]} 走纸进度={d[3]} 其余={d[4:].hex(' ')}")
            last = d
        if page >= copies:
            break
        if time.time() > deadline:
            log(f"!! 等 {timeout}s 仍未打完")
            break
        time.sleep(0.3)
    ok = p.request(0xF3, [1], 0xF4)
    log(f"printEnd 回包 {ok.hex(' ')}")
    return page >= copies


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="X")
    ap.add_argument("--text", nargs="+", default=["诊断"])
    ap.add_argument("--copies", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=40)
    ap.add_argument("--after", type=float, default=8, help="打完后继续轮询状态的秒数")
    ap.add_argument("--jobs", type=int, default=1, help="连续几个任务，每个任务新开一个连接")
    ap.add_argument("--gap", type=float, default=0, help="任务之间间隔几秒")
    ap.add_argument("--status", action="store_true", help="只读：查一次状态，不打印")
    ap.add_argument("--qr", help="二维码内容；给了就打二维码 + 文字（和 niimbot_k3w.py --qr 一样）")
    ap.add_argument("--stamp", action="store_true", help="在文字后面加一行任务号和时间")
    ap.add_argument("--host", default=k.HOST, help="打印机 IP；默认取环境变量 NIIMBOT_HOST")
    ap.add_argument("--port", type=int, default=k.PORT, help="端口，默认 9100")
    args = ap.parse_args()
    if not args.host:
        ap.error("没有指定打印机地址：用 --host IP，或设环境变量 NIIMBOT_HOST")

    used = []
    try:
        for j in range(args.jobs):
            p = LogPrinter(args.host, args.port)
            try:
                if j == 0:
                    used.append(snapshot(p, "打印前"))
                    if args.status:
                        return
                text = args.text + ([f"任务 {j + 1}/{args.jobs} " + time.strftime("%H:%M:%S")] if args.stamp else [])
                if args.qr:
                    img, _ = k.qr_label(args.qr, text, k.label_size(), k.find_font())
                else:
                    img, _ = k.text_block(text, k.label_size(), k.find_font())
                log(f"===== 任务 {j + 1}/{args.jobs}（每个任务 {args.copies} 份）=====")
                done = run_job(p, k.to_bits(img), args.copies, args.timeout)
                log(f"任务 {j + 1} 结果：{'完成' if done else '未完成'}")
                observe(p, args.after)
                if j == args.jobs - 1:
                    used.append(snapshot(p, "打印后"))
            finally:
                p.close()
            if j < args.jobs - 1 and args.gap:
                log(f"间隔 {args.gap}s")
                time.sleep(args.gap)
        log(f"标签卷用量 {used[0]} -> {used[-1]}（实际出纸 {used[-1] - used[0]} 张）")
    finally:
        os.makedirs("diag_logs", exist_ok=True)
        path = os.path.join("diag_logs", f"{time.strftime('%H%M%S')}_{args.tag}.log")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(LOG) + "\n")
        print(f"日志: {os.path.abspath(path)}")


if __name__ == "__main__":
    main()
