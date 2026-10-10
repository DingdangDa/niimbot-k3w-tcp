#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
info.py — 读取打印机信息（只读，不打印、不走纸）。

发送查询类命令，读出型号码、固件 / 硬件版本、协议版本和当前状态，用来确认打印机支持这套协议。
序列号默认不显示（方便把输出贴到 issue 里），加 --serial 显示。

  python tools/info.py --host 192.168.1.50
  python tools/info.py --host 192.168.1.50 --raw     同时显示每条回包的原始字节
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import niimbot_k3w as k  # noqa: E402

MODELS = {0x1301: "K3_W"}  # 型号码 -> 型号，来自 niimbluelib 的型号表；表里没有的只显示型号码


def version(d):
    return f"{d[0]}.{d[1]:02d}" if len(d) == 2 else d.hex(" ")


def protocol_version(d):
    """0xB5 回包第 11、12 字节（从 0 数），按 niimbluelib 的规则换算成协议版本。"""
    if len(d) < 13:
        return "未知"
    n = d[11] * 100 + d[12]
    return "5" if n >= 302 else "4" if n >= 300 else "3" if n >= 204 else f"未知（{n}）"


QUERIES = [  # (名字, 命令, 数据, 期望回包, 怎么显示)
    ("型号", 0x40, [0x08], 0x48,
     lambda d: f"0x{int.from_bytes(d, 'big'):04x}（{MODELS.get(int.from_bytes(d, 'big'), '型号表里没有')}）"),
    ("固件版本", 0x40, [0x09], 0x49, version),
    ("硬件版本", 0x40, [0x0C], 0x4C, version),
    ("序列号", 0x40, [0x0B], 0x4B, lambda d: d.decode("ascii", "replace")),
    ("协议版本", 0xA5, [0x01], 0xB5, protocol_version),
    ("状态", 0xA3, [0x01], 0xB3,
     lambda d: f"已打印页数 {int.from_bytes(d[:2], 'big')}，{'忙（正在打印或走纸）' if len(d) >= 8 and d[7] else '空闲'}"),
    ("心跳", 0xDC, [0x01], 0xDD, lambda d: f"{len(d)} 字节"),
]


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="读取打印机信息（只读，不打印、不走纸）")
    ap.add_argument("--host", default=k.HOST, help="打印机 IP；默认取环境变量 NIIMBOT_HOST")
    ap.add_argument("--port", type=int, default=k.PORT, help="端口，默认 9100")
    ap.add_argument("--serial", action="store_true", help="显示序列号")
    ap.add_argument("--raw", action="store_true", help="同时显示回包的原始字节")
    args = ap.parse_args()
    if not args.host:
        ap.error("没有指定打印机地址：用 --host IP，或设环境变量 NIIMBOT_HOST")

    try:
        p = k.Printer(args.host, args.port)
    except OSError as e:
        print(f"[FAILED] 连不上 {args.host}:{args.port}：{e}", file=sys.stderr)
        sys.exit(1)
    answered = 0
    try:
        for name, cmd, data, reply, show in QUERIES:
            try:
                d = p.request(cmd, data, reply, timeout=3)
            except RuntimeError as e:
                print(f"{name}：没有回答（{e}）")
                continue
            answered += 1
            text = show(d) if name != "序列号" or args.serial else "（加 --serial 显示）"
            print(f"{name}：{text}")
            if args.raw:
                print(f"    0x{reply:02x} {d.hex(' ')}")
    finally:
        p.close()
    if not answered:
        print("[FAILED] 打印机对所有查询都没有回答：它可能不支持这套协议，或者端口不对", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
