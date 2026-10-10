#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
niimbot_k3w.py — 从电脑直接打印到精臣 K3_W 等网口标签机（TCP 9100，精臣私有协议）。
依赖: Python 3.9+，python -m pip install -U pillow qrcode（qrcode 只在打二维码时用到）

打印机地址：用 --host，或设环境变量 NIIMBOT_HOST；端口默认 9100。

用法（选项写在文字前面；PowerShell 里文字用单引号）:
  python niimbot_k3w.py --host 192.168.1.50 --dry-run '第一行' '第二行'   只生成预览 preview.png，不打印
  python niimbot_k3w.py --host 192.168.1.50 '第一行' '第二行'             打印文字（自动选字号、居中，支持中文）
  python niimbot_k3w.py --host 192.168.1.50 --qr 'https://…' 'A-1024'    左边二维码，右边文字（文字可省略）
  python niimbot_k3w.py --host 192.168.1.50 --image 图片.png              打印图片（等比缩放后居中）
  python niimbot_k3w.py --host 192.168.1.50 --copies 3 '文字'             打 3 张
  python niimbot_k3w.py --host 192.168.1.50 -- '-18℃'                    文字以 - 开头时，前面加 --
结果: 退出码 0 且最后一行是 [OK] = 全部打完；[FAILED] = 失败，按提示处理，不要直接重跑。
在 Python 里: from niimbot_k3w import print_label
             print_label(img, host="192.168.1.50")  # img: PIL 图片或图片路径，黑色 = 打印，自动缩放到标签大小
"""
import argparse
import math
import os
import socket
import struct
import sys
import tempfile
import time
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps

__version__ = "0.1.0"

HOST = os.environ.get("NIIMBOT_HOST")  # 打印机 IP，用 --host 或环境变量 NIIMBOT_HOST 指定
PORT = int(os.environ.get("NIIMBOT_PORT", "9100"))
DPI = 203                 # 8 点/mm
PRINTHEAD_DOTS = 640      # 打印头 80mm（K3_W；其他型号见 README）
LABEL_MM = (60, 40)       # 默认标签：宽 60mm（横跨打印头）x 高 40mm（走纸方向）
FONTS = [                 # 依次尝试的中文字体（Windows / macOS / Linux）
    "C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
    "/System/Library/Fonts/STHeiti Medium.ttc", "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
FONT_NAMES = {            # --font 可以直接写的字体名（Windows）
    "微软雅黑": "msyh.ttc", "Microsoft YaHei": "msyh.ttc", "黑体": "simhei.ttf", "SimHei": "simhei.ttf",
    "宋体": "simsun.ttc", "SimSun": "simsun.ttc", "楷体": "simkai.ttf", "KaiTi": "simkai.ttf",
}


# ---------- 协议帧：55 55 | 命令 | 长度 | 数据 | 校验 | AA AA，校验 = 命令 ^ 长度 ^ 每个数据字节 ----------
def frame(cmd, data=b"\x01"):
    data = bytes(data)
    cs = cmd ^ len(data)
    for b in data:
        cs ^= b
    return b"\x55\x55" + bytes([cmd, len(data)]) + data + bytes([cs]) + b"\xaa\xaa"


class Printer:
    def __init__(self, host=HOST, port=PORT, timeout=5):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""

    def close(self):
        self.sock.close()

    def send(self, cmd, data=b"\x01"):
        self.sock.sendall(frame(cmd, data))

    def _next_frame(self):
        """从接收缓冲取出一个校验正确的帧 (命令, 数据)；数据不够一帧时返回 None。"""
        while True:
            i = self.buf.find(b"\x55\x55")
            if i < 0:
                self.buf = self.buf[-1:]
                return None
            self.buf = self.buf[i:]
            if len(self.buf) < 4:
                return None
            cmd, n = self.buf[2], self.buf[3]
            if len(self.buf) < n + 7:
                return None
            data = self.buf[4:4 + n]
            cs = cmd ^ n
            for b in data:
                cs ^= b
            if self.buf[4 + n] == cs and self.buf[5 + n:7 + n] == b"\xaa\xaa":
                self.buf = self.buf[7 + n:]
                return cmd, data
            self.buf = self.buf[1:]  # 不是有效帧，跳一个字节重新找帧头

    def request(self, cmd, data, reply, timeout=5):
        """发送命令，等到指定类型的回包并返回其数据；其他回包忽略。"""
        self.send(cmd, data)
        deadline = time.time() + timeout
        while True:
            f = self._next_frame()
            if f:
                if f[0] == reply:
                    return f[1]
                if f[0] in (0x00, 0xDB):  # 0x00 = 不支持该命令，0xDB = 打印错误（01 上盖开，02 缺纸）
                    raise RuntimeError(f"打印机报错：命令 0x{cmd:02x} 收到 0x{f[0]:02x} {f[1].hex(' ')}")
                continue
            left = deadline - time.time()
            if left <= 0:
                raise RuntimeError(f"等待回包超时：命令 0x{cmd:02x}")
            self.sock.settimeout(left)
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                raise RuntimeError(f"等待回包超时：命令 0x{cmd:02x}")
            if not chunk:
                raise RuntimeError("打印机断开了连接")
            self.buf += chunk


# ---------- 图片 -> 打印数据 ----------
def label_size(w_mm=LABEL_MM[0], h_mm=LABEL_MM[1]):
    """标签的 (宽, 高) 点数；宽要凑成 8 的倍数。60x40mm -> (480, 320)。"""
    return (round(w_mm * DPI / 25.4) + 7) // 8 * 8, round(h_mm * DPI / 25.4)


def flatten(img):
    """转灰度：按 EXIF 方向摆正，透明部分按白色，16 位等灰度按亮度范围换算。"""
    img = ImageOps.exif_transpose(img)
    if img.mode in ("I", "F") or img.mode.startswith("I;16"):
        hi = img.getextrema()[1]
        scale = 255 / 65535 if hi > 255 else (255 if hi <= 1 else 1)
        return img.convert("I").convert("F").point(lambda v: v * scale).convert("L")
    if img.mode in ("RGBA", "LA", "P", "PA") or "transparency" in img.info:
        img = Image.alpha_composite(Image.new("RGBA", img.size, "white"), img.convert("RGBA"))
    return img.convert("L")


def to_bits(img):
    """转 1 位图：深色(<128) 为 1 = 打黑点。"""
    return flatten(img).point(lambda v: 255 if v < 128 else 0, mode="1")


def fit(img, size):
    """等比缩放到 size 并居中；放大用最近邻，二维码、条码边缘不发虚。"""
    img = flatten(img)
    if img.size == size:
        return img
    scale = min(size[0] / img.width, size[1] / img.height)
    new = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    canvas = Image.new("L", size, 255)
    canvas.paste(img.resize(new, Image.NEAREST if scale > 1 else Image.LANCZOS),
                 ((size[0] - new[0]) // 2, (size[1] - new[1]) // 2))
    return canvas


def prepare(img, size=None):
    """图片（或图片路径）-> 要打印的 1 位图，大小 = 标签大小。"""
    if isinstance(img, (str, os.PathLike)):
        img = Image.open(img)
    return to_bits(fit(img, size or label_size()))


def row_packets(bits):
    """逐行编码：全白行用 0x84，有黑点的行用 0x85；相同的相邻行合并成重复数（每 200 行断开一次）。"""
    w, h = bits.size
    nb = (w + 7) // 8
    raw = bits.tobytes()  # 每行 nb 字节，每字节最高位是最左边的点
    runs = []  # [起始行号, 重复数, 行数据]
    for y in range(h):
        row = raw[y * nb:(y + 1) * nb]
        if runs and y % 200 != 0 and runs[-1][2] == row:
            runs[-1][1] += 1
        else:
            runs.append([y, 1, row])
    chunk = PRINTHEAD_DOTS // 8 // 3  # 黑点数分 3 段统计，每段 26 字节
    for y, rep, row in runs:
        if not any(row):
            yield frame(0x84, struct.pack(">HB", y, rep))
            continue
        if len(row) <= chunk * 3:
            counts = [0, 0, 0]
            for i, b in enumerate(row):
                counts[i // chunk] += bin(b).count("1")
        else:
            total = sum(bin(b).count("1") for b in row)
            counts = [0, total & 0xFF, total >> 8]
        yield frame(0x85, struct.pack(">H", y) + bytes(counts) + bytes([rep]) + row)


class PrintFailed(RuntimeError):
    """打印失败。maybe_printed 为 True 时整页数据已发出，这张可能已经出纸。"""

    def __init__(self, msg, maybe_printed):
        super().__init__(msg)
        self.maybe_printed = maybe_printed


def status(p):
    """查打印状态，返回 (已打印页数, 是否在忙)。0xB3 回包第 8 字节：1 = 正在打印或走纸，0 = 空闲（实测）。"""
    d = p.request(0xA3, [1], 0xB3)
    return int.from_bytes(d[:2], "big"), len(d) >= 8 and d[7] != 0


def wait_until(p, done, timeout, error):
    """每 0.3 秒查一次状态，直到 done(页数, 在忙) 为真；超时抛 RuntimeError(error)。"""
    deadline = time.time() + timeout
    while not done(*status(p)):
        if time.time() > deadline:
            raise RuntimeError(error)
        time.sleep(0.3)


def print_label(img, host=HOST, port=PORT, density=3, size=None):
    """打印一张标签。img: PIL 图片或图片路径，黑色 = 打印，自动等比缩放到标签大小（默认 60x40mm）后居中。"""
    if not host:
        raise ValueError("没有指定打印机地址：传 host=，或设环境变量 NIIMBOT_HOST")
    if not isinstance(density, int) or not 1 <= density <= 5:
        raise ValueError("浓度要是 1-5 的整数")
    bits = prepare(img, size)
    if not bits.getbbox():
        raise ValueError("要打印的内容是空白")
    rows, cols = bits.height, bits.width
    p = None
    started = page_sent = False
    try:
        p = Printer(host, port)
        # 上一张还在走纸、或别人正在打时，打印机会拒绝新任务（回包 00），所以先等它空闲
        wait_until(p, lambda page, busy: not busy, 20, "打印机一直在忙（可能别人正在打印），稍后再试")
        setup = [(0x23, [1], 0x33),                          # 标签类型 1 = 间隙纸
                 (0x21, [density], 0x31),                    # 浓度 1-5
                 (0x01, [0, 1, 0, 0, 0, 0, 0, 0, 0], 0x02)]  # 开始打印，共 1 页
        for cmd, data, reply in setup:
            if p.request(cmd, data, reply)[:1] != b"\x01":
                raise RuntimeError(f"打印机拒绝了命令 0x{cmd:02x}（可能还在忙），稍后再试")
        started = True
        p.send(0xA3)                                        # 查一次状态，不等回包（照官方流程）
        size_cmd = struct.pack(">HHHHBBBH", rows, cols, 1, 0, 0, 0, 0, 0)  # 行数、列数、份数 1
        if p.request(0x13, size_cmd, 0x14)[:1] != b"\x01":
            raise RuntimeError("打印机拒绝了页面尺寸（命令 0x13）")
        for pkt in row_packets(bits):
            p.sock.sendall(pkt)
        page_sent = True
        p.request(0xE3, [1], 0xE4, timeout=10)              # 页结束
        wait_until(p, lambda page, busy: page >= 1, 30, "等打印完成超时")  # 已打印页数到 1
        try:  # 页数到了可能还在走纸，等它空闲再结束，否则紧接着的下一张会被拒绝
            wait_until(p, lambda page, busy: not busy, 5, "")
        except RuntimeError:
            pass
        ok = p.request(0xF3, [1], 0xF4)                     # 结束打印
        started = False
        if not ok or ok[0] != 1:
            raise RuntimeError(f"结束打印失败：{ok.hex(' ')}")
    except (OSError, RuntimeError) as e:
        raise PrintFailed(str(e), page_sent) from e
    finally:
        if started:  # 中途出错也结束打印，免得机器卡在打印状态
            try:
                p.request(0xF3, [1], 0xF4, timeout=2)
            except Exception:
                pass
        if p:
            p.close()


# ---------- 排版 ----------
def find_font(name=None):
    """--font 给了就只用它（文件路径或 FONT_NAMES 里的名字），否则依次试 FONTS。"""
    for p in [FONT_NAMES.get(name, name)] if name else FONTS:
        try:
            ImageFont.truetype(p, 10)
            return p
        except OSError:
            pass
    if name:
        raise ValueError(f"打不开字体 {name}：请给 .ttf / .ttc 文件的完整路径，或写 微软雅黑 / 黑体 / 宋体 / 楷体")
    raise ValueError("找不到中文字体：请用 --font 指定一个 .ttf / .ttc 字体文件")


def clean(text):
    """去掉零宽字符、变体选择符等看不见的字符（字体里没有，会打成方框），Tab 换成空格。"""
    return "".join(c for c in text.replace("\t", " ")
                   if unicodedata.category(c) not in ("Cc", "Cf") and not "\ufe00" <= c <= "\ufe0f")


def missing_chars(text, font_path):
    """字体里没有的字符（打出来是方框）：渲染结果和一个肯定没有的字符一样。"""
    font = ImageFont.truetype(font_path, 32)

    def shape(c):
        im = Image.new("1", (48, 48))
        ImageDraw.Draw(im).text((8, 8), c, font=font, fill=1)
        return im.tobytes()

    box = shape("\U0010fffd")
    return [c for c in dict.fromkeys(text) if not c.isspace() and shape(c) == box]


def text_block(lines, size, font_path, margin=16):
    """几行文字排进 size 大小的区域：用能放下的最大字号，水平垂直居中。返回 (图, 字号)。"""
    w, h = size
    img = Image.new("L", size, 255)
    d = ImageDraw.Draw(img)
    for fs in range(200, 7, -2):
        font = ImageFont.truetype(font_path, fs)
        line_h = fs * 1.25
        if (line_h * len(lines) <= h - 2 * margin
                and max(d.textlength(t, font=font) for t in lines) <= w - 2 * margin):
            break
    else:
        raise ValueError("文字太多，标签上放不下：请缩短或拆成几行")
    top = (h - line_h * len(lines)) / 2
    for i, t in enumerate(lines):
        d.text((w / 2, top + line_h * (i + 0.5)), t, font=font, fill=0, anchor="mm")
    return img, fs


def qr_image(data, side):
    """边长 side 的二维码：纠错等级 M，四周至少留 4 格白边，每格整数个点。"""
    try:
        import qrcode
    except ImportError:
        raise ValueError("打二维码要先安装 qrcode：python -m pip install qrcode")
    q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=0)
    q.add_data(data)
    q.make(fit=True)
    m = q.get_matrix()
    box = side // (len(m) + 8)
    if box < 3:
        raise ValueError("二维码内容太长，格子太小扫不出来：请缩短内容")
    img = Image.new("L", (side, side), 255)
    d = ImageDraw.Draw(img)
    off = (side - box * len(m)) // 2
    for y, row in enumerate(m):
        for x, v in enumerate(row):
            if v:
                d.rectangle([off + x * box, off + y * box, off + (x + 1) * box - 1, off + (y + 1) * box - 1], fill=0)
    return img


def qr_label(data, lines, size, font_path):
    """二维码在左（约占宽度 55%），文字在右；没有文字时二维码居中。返回 (图, 字号)。"""
    w, h = size
    img = Image.new("L", size, 255)
    if not any(lines):
        side = min(w, h)
        img.paste(qr_image(data, side), ((w - side) // 2, (h - side) // 2))
        return img, None
    side = min(h, w * 11 // 20)
    img.paste(qr_image(data, side), (0, (h - side) // 2))
    block, fs = text_block(lines, (w - side, h), font_path, margin=8)
    img.paste(block, (side, 0))
    return img, fs


def save_preview(bits, path=None):
    """保存预览图：给了 path 就存那里，否则存到当前文件夹的 preview.png，不能写就存到临时文件夹。返回路径。"""
    img = ImageOps.invert(bits.convert("L"))
    candidates = [path] if path else ["preview.png", os.path.join(tempfile.gettempdir(), "preview.png")]
    for p in candidates:
        try:
            img.save(p)
            return os.path.abspath(p)
        except (OSError, ValueError):
            pass
    return None


def fail(msg):
    print(f"[FAILED] {msg}", file=sys.stderr)
    sys.exit(1)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="打印到精臣 K3_W 网络标签机（TCP 9100）。选项写在文字前面。")
    ap.add_argument("--version", action="version", version=f"niimbot-k3w {__version__}")
    ap.add_argument("text", nargs="*", help="要打印的文字，每个参数一行；以 - 开头时前面加 --")
    ap.add_argument("--qr", help="二维码内容（网址、编号等）；二维码在左，文字在右")
    ap.add_argument("--image", help="要打印的图片（png / jpg / bmp 等）")
    ap.add_argument("--copies", type=int, default=1, help="打印张数，默认 1")
    ap.add_argument("--density", type=int, default=3, choices=range(1, 6), help="浓度 1-5，默认 3")
    ap.add_argument("--w-mm", type=float, default=LABEL_MM[0], help="标签宽 mm（横跨打印头），默认 60，最大 80")
    ap.add_argument("--h-mm", type=float, default=LABEL_MM[1], help="标签高 mm（走纸方向），默认 40")
    ap.add_argument("--font", help="字体文件路径，或 微软雅黑 / 黑体 / 宋体 / 楷体；默认自动找中文字体")
    ap.add_argument("--host", default=HOST, help="打印机 IP；默认取环境变量 NIIMBOT_HOST")
    ap.add_argument("--port", type=int, default=PORT, help="端口，默认 9100")
    ap.add_argument("--dry-run", action="store_true", help="只生成预览，不打印")
    ap.add_argument("--preview", help="预览图保存路径，默认当前文件夹的 preview.png")
    args = ap.parse_args()

    stray = [t for t in args.text if t.split("=")[0] in ap._option_string_actions]
    if stray:
        ap.error(f"「{stray[0]}」被当成了要打印的文字：选项要写在 -- 和文字的前面")
    if not args.dry_run and not args.host:
        ap.error("没有指定打印机地址：用 --host IP，或设环境变量 NIIMBOT_HOST（只预览可加 --dry-run）")
    if args.image and (args.text or args.qr):
        ap.error("--image 不能和文字、--qr 一起用：要组合就先画成一张图，再用 --image")
    if not (args.image or args.text or args.qr):
        ap.error("请给出要打印的文字，或用 --qr / --image")
    if args.copies < 1:
        ap.error("--copies 至少是 1")
    if not (math.isfinite(args.w_mm) and math.isfinite(args.h_mm) and args.w_mm > 0 and args.h_mm > 0):
        ap.error("--w-mm / --h-mm 要是正数")
    size = label_size(args.w_mm, args.h_mm)
    if not (8 <= size[0] <= PRINTHEAD_DOTS and 8 <= size[1] <= 8000):
        ap.error(f"标签尺寸不对：宽 1-{PRINTHEAD_DOTS // 8}mm，高 1-1000mm")

    warnings = []
    try:
        if args.image:
            try:
                src = Image.open(args.image)
                src.load()
            except Exception as e:  # 文件不存在、不是图片、SVG、文件损坏等
                raise ValueError(f"打不开图片 {args.image}：{e}（SVG 请先转成 PNG）")
            img = fit(src, size)
        else:
            lines = [clean(line).strip() for t in args.text for line in (t.splitlines() or [""])]
            if args.qr is not None and not args.qr.strip():
                raise ValueError("--qr 的内容是空的")
            if not args.qr and not any(lines):
                raise ValueError("要打印的文字是空的")
            font_path = find_font(args.font)
            img, fs = qr_label(args.qr, lines, size, font_path) if args.qr else text_block(lines, size, font_path)
            missing = missing_chars("".join(lines), font_path)
            if missing:
                warnings.append(f"字体里没有这些字符，会打成方框：{' '.join(missing)}（删掉或换成别的字，或用 --font 换字体）")
            if fs and fs < 24:
                warnings.append(f"字比较小（约 {fs / 8:.1f}mm），可以缩短内容或拆成几行")
        bits = to_bits(img)
        if not bits.getbbox():
            raise ValueError("要打印的内容是空白（图片颜色太浅，或者没有文字）")
    except (OSError, ValueError) as e:
        fail(f"{e}。没有打印。")

    path = save_preview(bits, args.preview)
    print(f"[PREVIEW] {path}（{bits.width}x{bits.height} 点，就是实际打出的样子）" if path else "[WARN] 预览图保存失败")
    for w in warnings:
        print(f"[WARN] {w}")
    if args.dry_run:
        print("[DRY-RUN] 只生成了预览，没有打印。确认后去掉 --dry-run 正式打印。")
        return
    for i in range(args.copies):
        try:
            print_label(img, args.host, args.port, args.density, size)
        except PrintFailed as e:
            this = "这张可能已经出纸" if e.maybe_printed else "这张没有打出来"
            fail(f"printed {i}/{args.copies}：第 {i + 1} 张打印失败：{e}\n"
                 f"前 {i} 张已打好，{this}。先看打印机实际出了几张，再用 --copies 只补缺的，不要直接重跑。\n"
                 f"请检查：打印机开机、有纸、上盖合好，没有别人正在打；电脑能连上 {args.host}:{args.port}"
                 f"（PowerShell: Test-NetConnection {args.host} -Port {args.port}）")
        print(f"已打印 {i + 1}/{args.copies}")
    print(f"[OK] printed {args.copies}/{args.copies}")


if __name__ == "__main__":
    main()
