#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mock_k3w.py — 在本机模拟一台精臣 K3_W，用来测试打印程序（不连真机、不出纸）。

模拟设备信息（型号/固件/序列号/RFID 等）是合成的占位值，不是任何真实设备的数据，
只用来让打印程序跑通流程；按真机观察到的时序和忙标志行为回应。
同时检查协议用法：帧格式和校验、命令顺序、图像行是否完整覆盖、黑点计数、
是否等打印机报告打完才发 printEnd、有没有发未知命令或 TSPL/ZPL 之类不是精臣帧的数据。

每个连接结束后写到 <输出文件夹>/<端口>/（默认 ./mock_sessions，启动时清空）：
  NNN_report.json  NNN_frames.txt  NNN_jobK_pageM.png   该连接的检查结果、收到的帧、"打出来"的标签
  last_report.json  last_label.png                       最近一个连接的结果
  summary.json                                           该端口所有连接的汇总
用法: python tools/mock_k3w.py                      监听 127.0.0.1:9100
      python tools/mock_k3w.py --ports 9100 9101 --out 某个文件夹
"""
import argparse
import json
import os
import shutil
import socketserver
import struct
import sys
import threading
import time

from PIL import Image, ImageOps

ROOT = os.path.abspath("mock_sessions")  # 输出文件夹，--out 可改；测试里直接改这个变量
PRINTHEAD_DOTS = 640
PAGE_SECONDS = 1.0    # 模拟每页打印用时：页结束后约 1 秒已打印页数加 1
FINISH_SECONDS = 0.4  # 页数加 1 之后还要走纸约 0.4 秒，这段时间状态里的忙标志仍是 1（真机实测）
LOCK = threading.Lock()
COUNTERS = {}
DEVICES = {}  # 端口 -> 打印机状态，同一端口的所有连接共用：{"busy_until": 忙到什么时候, "last_pages": 上个任务页数}

# 合成的模拟回包（占位值，非真实设备数据）；格式照真机，内容随便填
INFO = {  # get_info(0x40) 查询项 -> (回包命令, 数据)
    0x08: (0x48, bytes.fromhex("13 01")),  # 型号码 0x1301 = K3_W
    0x09: (0x49, bytes.fromhex("05 07")),  # 固件版本
    0x0C: (0x4C, bytes.fromhex("05 01")),  # 硬件版本
    0x0B: (0x4B, b"MOCK0000001"),         # 序列号（占位）
}
HEARTBEAT = bytes.fromhex("00 00 00 39 00 39 00 00 4e 00 04 00 01")
# RFID：UUID(8) + 条码 '000000000' + 序列 'MOCK000000000000' + 总数 828 + 已用 3 + 类型 1 ...（全是占位值）
RFID = (bytes.fromhex("00 00 00 00 00 00 00 00")
        + bytes([9]) + b"000000000"
        + bytes([16]) + b"MOCK000000000000"
        + bytes.fromhex("03 3c 00 03 01 02 b2"))
STATUS_DATA = bytes.fromhex("30 30 10 68 00 c8 00 00 00 14 01 03 02 08 4d 0b 04 0a 02 2a c0 02 23 8c 05 b4 14")
LABEL_TYPES = (1, 2, 5)  # K3_W 支持：1 间隙纸、2 黑标纸、5 透明纸


def build(cmd, data=b"\x01"):
    data = bytes(data)
    cs = cmd ^ len(data)
    for b in data:
        cs ^= b
    return b"\x55\x55" + bytes([cmd, len(data)]) + data + bytes([cs]) + b"\xaa\xaa"


def expected_counts(row):
    """niimbluelib 的黑点计数算法（真机验证过）。"""
    chunk = PRINTHEAD_DOTS // 8 // 3
    if len(row) <= chunk * 3:
        parts = [0, 0, 0]
        for i, b in enumerate(row):
            parts[i // chunk] += bin(b).count("1")
        return parts
    total = sum(bin(b).count("1") for b in row)
    return [0, total & 0xFF, total >> 8]


def ranges(nums):
    """[1, 2, 3, 7] -> '1-3, 7'"""
    out, start, prev = [], None, None
    for n in nums + [None]:
        if start is not None and n == prev + 1:
            prev = n
            continue
        if start is not None:
            out.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    return ", ".join(out[:10]) + (" ..." if len(out) > 10 else "")


class Session:
    def __init__(self, port, index):
        self.port, self.index = port, index
        self.dir = os.path.join(ROOT, str(port))
        self.errors, self.warnings, self.frames, self.jobs = [], [], [], []
        self.job = None
        self.label_type = self.density = None
        with LOCK:
            self.dev = DEVICES.setdefault(port, {"busy_until": 0.0, "last_pages": 1})  # 真机空闲时报上个任务的页数
        self.garbage = b""
        self.noted = {}

    def err(self, msg):
        if msg not in self.errors:
            self.errors.append(msg)

    def warn(self, msg, key=None):
        key = key or msg
        self.noted[key] = self.noted.get(key, 0) + 1
        if self.noted[key] <= 3:
            self.warnings.append(msg)
        elif self.noted[key] == 4:
            self.warnings.append(f"（同类警告「{key}」不再重复列出）")

    # ---------- 收帧 ----------
    def parse(self, buf):
        """切出完整帧；不是帧的字节记为垃圾。返回 (帧列表, 剩余缓冲)。"""
        frames = []
        while True:
            i = buf.find(b"\x55\x55")
            if i < 0:
                keep = 1 if buf.endswith(b"\x55") else 0
                self.garbage += buf[:len(buf) - keep]
                return frames, buf[len(buf) - keep:]
            self.garbage += buf[:i]
            buf = buf[i:]
            if len(buf) < 4 or len(buf) < buf[3] + 7:
                return frames, buf
            cmd, n = buf[2], buf[3]
            if buf[5 + n:7 + n] != b"\xaa\xaa":
                self.garbage += buf[:1]
                buf = buf[1:]
                continue
            data = buf[4:4 + n]
            cs = cmd ^ n
            for b in data:
                cs ^= b
            if buf[4 + n] != cs:
                self.err(f"命令 0x{cmd:02x} 的校验字节错误：收到 0x{buf[4 + n]:02x}，应为 0x{cs:02x}")
            else:
                frames.append((cmd, data))
            buf = buf[7 + n:]

    def on_frame(self, cmd, data):
        self.frames.append(f"{cmd:02x} {data.hex(' ')}")
        handler = getattr(self, f"cmd_{cmd:02x}", None)
        if handler is None:
            self.err(f"发送了未知命令 0x{cmd:02x}（真机回 0x00 不支持；协议里有写 RFID、升级固件等命令，不要盲发）")
            return [(0x00, b"\x01")]
        return handler(data) or []

    # ---------- 设置与查询 ----------
    def busy(self):
        return time.time() < self.dev["busy_until"]

    def rejected(self, name, reply):
        """打印机在忙时，真机对开始新任务的命令回 00 拒绝。"""
        if not self.busy():
            return None
        self.err(f"打印机还在忙（上一张没走完纸或别人正在打）时发了 {name}，真机回 00 拒绝；"
                 "应先轮询 printStatus，等回包第 8 字节（忙标志）为 0 再开始，并检查回包是否为 01")
        return [(reply, b"\x00")]

    def cmd_23(self, d):  # setLabelType
        r = self.rejected("setLabelType(0x23)", 0x33)
        if r:
            return r
        if len(d) != 1 or d[0] not in LABEL_TYPES:
            self.err(f"setLabelType(0x23) 数据应为 1 字节标签类型，收到 {d.hex(' ')}")
        elif d[0] != 1:
            self.warn(f"setLabelType={d[0]}，现在装的是间隙纸，应为 1")
        self.label_type = d[0] if d else None
        return [(0x33, b"\x01")]

    def cmd_21(self, d):  # setDensity
        r = self.rejected("setDensity(0x21)", 0x31)
        if r:
            return r
        if len(d) != 1 or not 1 <= d[0] <= 5:
            self.err(f"setDensity(0x21) 数据应为 1 字节 1-5，收到 {d.hex(' ')}")
        self.density = d[0] if d else None
        return [(0x31, b"\x01")]

    def cmd_40(self, d):  # get_info
        return [INFO[d[0]]] if d and d[0] in INFO else [(0x00, b"\x01")]

    def cmd_dc(self, d):  # heartbeat
        return [(0xDD, HEARTBEAT)]

    def cmd_1a(self, d):  # RFID 耗材信息
        return [(0x1B, RFID)]

    def cmd_a5(self, d):  # 协议版本等
        return [(0xB5, STATUS_DATA)]

    # ---------- 打印流程 ----------
    def cmd_01(self, d):  # printStart
        r = self.rejected("printStart(0x01)", 0x02)
        if r:
            self.dev["last_pages"] = 0  # 真机这时页数计数变成 0
            return r
        if self.job and not self.job["ended"]:
            self.err("上一个打印任务还没 printEnd 就又发了 printStart")
            self.finish_job(completed=False)
        if len(d) == 1:
            self.warn("printStart 用了 1 字节老格式（niimprint 的 B21/D11 用法），K3_W 实测用 9 字节")
        elif len(d) not in (7, 9):
            self.err(f"printStart(0x01) 数据应为 9 字节（K3_W 实测）或 7 字节，收到 {len(d)} 字节")
        if self.label_type is None or self.density is None:
            self.warn("printStart 之前没有 setLabelType / setDensity")
        total = struct.unpack(">H", d[:2])[0] if len(d) >= 2 else 1
        if total < 1:
            self.err("printStart 里的总页数为 0")
        self.job = {"total": max(total, 1), "label_type": self.label_type, "density": self.density,
                    "page": None, "pages": [], "queue": [], "done_reported": False, "ended": False}
        return [(0x02, b"\x01")]

    def cmd_03(self, d):  # pageStart（B1 流程才用）
        if not self.job or self.job["ended"]:
            self.err("pageStart(0x03) 之前没有 printStart")
        return [(0x04, b"\x01")]

    def cmd_13(self, d):  # setPageSize
        own_job = self.job and not self.job["ended"] and self.job["pages"]  # 同一任务里的下一页不算别人在忙
        r = None if own_job else self.rejected("setPageSize(0x13)", 0x14)
        if r:
            return r
        if not self.job or self.job["ended"]:
            self.err("setPageSize(0x13) 之前没有 printStart")
            return [(0xDB, b"\x01")]
        if len(d) < 4:
            self.err(f"setPageSize(0x13) 数据太短：{d.hex(' ')}")
            return [(0x14, b"\x01\x00")]
        if len(d) == 4:
            self.warn("setPageSize 用了 4 字节老格式，K3_W 实测用 13 字节")
        elif len(d) not in (6, 9, 13):
            self.err(f"setPageSize(0x13) 数据应为 13 字节（K3_W 实测），收到 {len(d)} 字节")
        rows, cols = struct.unpack(">HH", d[:4])
        copies = struct.unpack(">H", d[4:6])[0] if len(d) >= 6 else 1
        if not rows or not cols or cols % 8 or cols > PRINTHEAD_DOTS:
            self.err(f"setPageSize 尺寸不对：{rows} 行 x {cols} 列（列数须为 8 的倍数且不超过 {PRINTHEAD_DOTS}）")
        pg = self.job["page"]
        if pg and not pg["ended"]:
            self.err("上一页还没 pageEnd 就又发了 setPageSize")
        nb = max(cols // 8, 1)
        self.job["page"] = {"rows": rows, "cols": cols, "copies": max(copies, 1), "nb": nb,
                            "bitmap": bytearray(rows * nb), "cover": [0] * rows, "next": 0, "ended": False}
        return [(0x14, b"\x01\x00")]

    def _page(self, what):
        j = self.job
        if not j or j["ended"] or not j["page"] or j["page"]["ended"]:
            self.err(f"{what} 发在了 setPageSize 之前或 pageEnd 之后")
            return None
        return j["page"]

    def _fill(self, pg, y, rep, row):
        if rep == 0:
            self.err(f"第 {y} 行的重复数为 0")
        if y != pg["next"]:
            self.warn(f"行号不连续：期望第 {pg['next']} 行，收到第 {y} 行", key="行号不连续")
        replies = []
        for r in range(y, y + rep):
            if r >= pg["rows"]:
                self.err(f"行号超出页面：第 {r} 行，页面只有 {pg['rows']} 行")
                break
            pg["cover"][r] += 1
            pg["bitmap"][r * pg["nb"]:(r + 1) * pg["nb"]] = row
            if r % 200 == 199 or r == pg["rows"] - 1:  # 真机收到这些行时回 0xD3
                replies.append((0xD3, struct.pack(">HB", r, 1)))
        pg["next"] = y + rep
        return replies

    def cmd_85(self, d):  # 位图行：行号(2) 黑点计数(3) 重复数(1) 行数据
        pg = self._page("0x85 图像行")
        if pg is None:
            return []
        if len(d) < 6:
            self.err(f"0x85 图像行数据太短：{d.hex(' ')}")
            return []
        y, rep, row = struct.unpack(">H", d[:2])[0], d[5], d[6:]
        if len(row) != pg["nb"]:
            self.err(f"0x85 第 {y} 行有 {len(row)} 字节行数据，应为 列数/8 = {pg['nb']}")
        exp = expected_counts(row)
        if list(d[2:5]) != exp:
            self.warn(f"0x85 第 {y} 行黑点计数为 {list(d[2:5])}，按 niimbluelib 算法应为 {exp}", key="黑点计数")
        return self._fill(pg, y, rep, (row + bytes(pg["nb"]))[:pg["nb"]])

    def cmd_84(self, d):  # 空白行：行号(2) 重复数(1)
        pg = self._page("0x84 空白行")
        if pg is None:
            return []
        if len(d) != 3:
            self.err(f"0x84 空白行数据应为 3 字节（行号 2 + 重复数 1），收到 {d.hex(' ')}")
            return []
        return self._fill(pg, struct.unpack(">H", d[:2])[0], d[2], bytes(pg["nb"]))

    def cmd_83(self, d):  # 稀疏行：行号(2) 黑点计数(3) 重复数(1) 每个黑点的列号(2)
        pg = self._page("0x83 稀疏行")
        if pg is None:
            return []
        if len(d) < 6 or (len(d) - 6) % 2:
            self.err(f"0x83 稀疏行数据格式不对：{d.hex(' ')}")
            return []
        if (len(d) - 6) // 2 > 6:
            self.err("0x83 稀疏行超过 6 个黑点（niimbluelib 注明真机会关机）")
        self.warn("用了 0x83 稀疏行：本机真机未验证过，建议只用 0x84 / 0x85", key="0x83")
        row = bytearray(pg["nb"])
        for k in range(6, len(d), 2):
            x = struct.unpack(">H", d[k:k + 2])[0]
            if x < pg["nb"] * 8:
                row[x // 8] |= 0x80 >> (x % 8)
        return self._fill(pg, struct.unpack(">H", d[:2])[0], d[5], bytes(row))

    def cmd_e3(self, d):  # pageEnd
        pg = self._page("pageEnd(0xE3)")
        if pg is None:
            return [(0xE4, b"\x01")]
        missing = [r for r, c in enumerate(pg["cover"]) if c == 0]
        dup = [r for r, c in enumerate(pg["cover"]) if c > 1]
        if missing:
            self.err(f"pageEnd 时有 {len(missing)} 行没发：第 {ranges(missing)} 行")
        if dup:
            self.err(f"有 {len(dup)} 行发了不止一次：第 {ranges(dup)} 行")
        pg["ended"] = True
        j = self.job
        j["pages"].append(pg)
        t = max([time.time()] + j["queue"][-1:])
        for _ in range(pg["copies"]):
            t += PAGE_SECONDS
            j["queue"].append(t)  # 每页打完的时刻
        self.dev["busy_until"] = max(self.dev["busy_until"], t + FINISH_SECONDS)
        return [(0xE4, b"\x01")]

    def cmd_a3(self, d):  # printStatus：已打印页数(2) 打印进度(1) 走纸进度(1) + 3 字节 + 忙标志(1)
        j = self.job
        tail = bytes([0x10, 0x67, 0x00, 1 if self.busy() else 0])
        if not j or j["ended"]:
            return [(0xB3, struct.pack(">HBB", self.dev["last_pages"], 100, 100) + tail)]
        now = time.time()
        done = sum(1 for t in j["queue"] if t <= now)
        if j["queue"] and done >= j["total"]:
            j["done_reported"] = True
            page, prog, feed = done, 100, 100
        elif done < len(j["queue"]):
            frac = 1 - (j["queue"][done] - now) / PAGE_SECONDS
            if done == 0 and frac < 0.6:  # 真机刚开始打时仍报 100/100，不能拿进度判断完成
                page, prog, feed = 0, 100, 100
            else:
                page, prog, feed = done, max(2, int(frac * 100)), 0
        else:
            page, prog, feed = done, 100, 100
        return [(0xB3, struct.pack(">HBB", page, prog, feed) + tail)]

    def cmd_f3(self, d):  # printEnd
        j = self.job
        if not j or j["ended"]:
            self.warn("没有进行中的打印任务时发了 printEnd")
            return [(0xF4, b"\x01")]
        done = sum(1 for t in j["queue"] if t <= time.time())
        if not j["queue"] or done < j["total"]:
            self.err("还没打完（已打印页数 < 总页数）就发了 printEnd，真机上可能截断标签；"
                     "应轮询 printStatus(0xA3) 直到页数够了再发")
            return [(0xF4, b"\x00")]
        if not j["done_reported"]:
            self.warn("没有用 printStatus 确认打完就发了 printEnd（这次碰巧已经打完）")
        if self.busy():
            self.warn("打印机还在走纸（忙标志 1）时发了 printEnd：真机会接受，但紧接着开始的下一张会被拒绝")
        self.finish_job(completed=True)
        return [(0xF4, b"\x01")]

    def cmd_da(self, d):  # 取消打印
        if self.job and not self.job["ended"]:
            self.warn("发送了取消打印 0xDA")
            self.finish_job(completed=False)
        return [(0xD0, b"\x01")]

    def finish_job(self, completed):
        j = self.job
        j["ended"] = True
        j["completed"] = completed and bool(j["pages"])
        if j["completed"]:
            self.dev["last_pages"] = j["total"]
        self.jobs.append(j)

    # ---------- 收尾：写报告 ----------
    def close(self, leftover):
        if self.garbage:
            sample = self.garbage[:60]
            self.err(f"收到 {len(self.garbage)} 字节不是精臣协议帧的数据（如 TSPL/ZPL 文本或帧格式错误）："
                     f"{sample.hex(' ')} | {sample.decode('latin-1')!r}")
        if leftover:
            self.err(f"连接结束时还有 {len(leftover)} 字节凑不成完整的帧：{leftover[:40].hex(' ')}")
        if self.job and not self.job["ended"]:
            self.err("连接断开时打印任务还没有成功 printEnd")
            self.finish_job(completed=False)

        jobs, last_png = [], None
        with LOCK:
            os.makedirs(self.dir, exist_ok=True)
            for k, j in enumerate(self.jobs, 1):
                pages = []
                for m, pg in enumerate(j["pages"], 1):
                    img = Image.frombytes("1", (pg["nb"] * 8, pg["rows"]), bytes(pg["bitmap"]))
                    name = f"{self.index:03d}_job{k}_page{m}.png"
                    ImageOps.invert(img.convert("L")).save(os.path.join(self.dir, name))
                    pages.append({"rows": pg["rows"], "cols": pg["cols"], "copies": pg["copies"], "png": name})
                    if j["completed"]:
                        last_png = name
                jobs.append({"completed": j["completed"], "total_pages": j["total"],
                             "label_type": j["label_type"], "density": j["density"], "pages": pages})
            labels = sum(j["total"] for j in self.jobs if j["completed"])
            report = {"port": self.port, "connection": self.index, "ok": not self.errors,
                      "labels_printed": labels, "errors": self.errors, "warnings": self.warnings,
                      "jobs": jobs, "frames_received": len(self.frames)}
            text = json.dumps(report, ensure_ascii=False, indent=2)
            stem = os.path.join(self.dir, f"{self.index:03d}")
            for path in (stem + "_report.json", os.path.join(self.dir, "last_report.json")):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
            with open(stem + "_frames.txt", "w", encoding="utf-8") as f:
                f.write("\n".join(self.frames) + "\n")
            last = os.path.join(self.dir, "last_label.png")
            if last_png:
                shutil.copyfile(os.path.join(self.dir, last_png), last)
            elif os.path.exists(last):
                os.remove(last)
            path = os.path.join(self.dir, "summary.json")
            try:
                with open(path, encoding="utf-8") as f:
                    summary = json.load(f)
            except (OSError, ValueError):
                summary = {"connections": []}
            summary["connections"].append({"connection": self.index, "ok": report["ok"], "labels_printed": labels,
                                           "errors": len(self.errors), "warnings": len(self.warnings)})
            summary["total_labels_printed"] = sum(c["labels_printed"] for c in summary["connections"])
            summary["connections_with_errors"] = sum(1 for c in summary["connections"] if not c["ok"])
            with open(path, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        return report


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        port = self.server.server_address[1]
        with LOCK:
            COUNTERS[port] = COUNTERS.get(port, 0) + 1
            index = COUNTERS[port]
        s = Session(port, index)
        buf = b""
        self.request.settimeout(120)
        try:
            while True:
                chunk = self.request.recv(65536)
                if not chunk:
                    break
                frames, buf = s.parse(buf + chunk)
                for cmd, data in frames:
                    for t, d in s.on_frame(cmd, data):
                        self.request.sendall(build(t, d))
        except OSError as e:
            s.warn(f"连接异常结束：{e}")
        finally:
            r = s.close(buf)
            print(f"[{port} #{index}] ok={r['ok']} 打印 {r['labels_printed']} 张 "
                  f"错误 {len(r['errors'])} 警告 {len(r['warnings'])}", flush=True)


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True


def main():
    global ROOT
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="本机模拟精臣 K3_W（测试用，不连真机）")
    ap.add_argument("--ports", type=int, nargs="+", default=[9100])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--out", default=ROOT, help="结果输出文件夹，默认 ./mock_sessions")
    args = ap.parse_args()
    ROOT = os.path.abspath(args.out)
    for port in args.ports:
        shutil.rmtree(os.path.join(ROOT, str(port)), ignore_errors=True)
        srv = Server((args.host, port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        print(f"模拟 K3_W 监听 {args.host}:{port}", flush=True)
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
