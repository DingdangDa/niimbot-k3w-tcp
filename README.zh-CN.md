# niimbot-k3w-tcp

在 Windows、macOS 或 Linux 上，通过网络（Wi‑Fi，TCP 9100 端口）直接打印到**精臣 K3_W** 标签机：不用官方软件，
也不用蓝牙。支持文字（含中文）、二维码和图片。

[English](README.md)

![示例标签：两行文字、二维码加文字、自定义排版](docs/images/examples.png)

> 非官方项目，与精臣（NIIMBOT）无关。只在一个型号上测试过，见[测试情况](#测试情况)。

## 为什么做这个

现有的精臣开源项目（[niimbluelib](https://github.com/MultiMote/niimbluelib)、
[niimprint](https://github.com/kjy00302/niimprint) 等）都是走蓝牙或 USB。K3_W 可以连 Wi‑Fi，在 TCP 9100 端口上
用的是同一套数据包协议，所以网络上任何能连到它的电脑都能打印，包括另一个网段的电脑（官方电脑软件搜不到这种情况下的打印机）。

和蓝牙流程有一处不同：上一张标签还在走纸时，打印机会悄悄拒绝新的打印任务，所以每次开始前要等它的"忙"标志清零。
详见 [docs/protocol.md](docs/protocol.md)（英文）。

## 测试情况

| | |
|---|---|
| 真机测过 | 精臣 K3_W（固件 5.07），60 × 40 mm 间隙标签，浓度 3，Windows 11，Python 3.12：文字、中文、二维码、连打 2 张 |
| 只在模拟打印机上测过 | 其他标签尺寸和浓度、连打 3 张以上、两台电脑同时打印 |
| 没测过 | 其他型号的打印机、在 macOS / Linux 上接真机 |

欢迎反馈其他型号的测试结果，见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 安装

需要 Python 3.9 以上。下载或克隆本仓库，在仓库文件夹里运行：

```sh
pip install ".[qr]"        # 安装 niimbot-k3w 命令；[qr] 是二维码支持
```

也可以不安装，只用单个文件：`pip install pillow qrcode`，然后把下面例子里的 `niimbot-k3w` 换成
`python niimbot_k3w.py`。

## 快速开始

1. 让打印机连上网络，查到它的 IP（比如在路由器的已连接设备列表里），确认 9100 端口能连通：

   ```sh
   # Windows PowerShell
   Test-NetConnection 192.168.1.50 -Port 9100
   # macOS / Linux
   nc -vz 192.168.1.50 9100
   ```

2. 可选：确认打印机能应答。这一步只读信息，不会打印。

   ```sh
   python tools/info.py --host 192.168.1.50
   ```

3. 先预览，再打印。选项写在文字前面；每个文字参数占一行，字号自动取能放下的最大值。

   ```sh
   niimbot-k3w --dry-run "仓库 B-03" "资产 A-1024"         # 只生成 preview.png，不打印
   niimbot-k3w --host 192.168.1.50 "仓库 B-03" "资产 A-1024"
   ```

设置环境变量 `NIIMBOT_HOST=192.168.1.50` 后可以省略 `--host`。PowerShell 里建议用单引号，双引号里的 `$` 会被当成变量。

## 命令行参数

| 参数 | |
|---|---|
| `文字 …` | 每个参数一行；文字以 `-` 开头时在它前面加 `--`，例如 `-- "-18℃"` |
| `--qr 内容` | 左边二维码、右边文字；没有文字时二维码居中 |
| `--image 文件` | 打印图片（PNG、JPEG、BMP 等），等比缩放后居中；SVG、PDF 要先转换 |
| `--copies N` | 打 N 张，每张一个任务 |
| `--density 1-5` | 浓度，默认 3 |
| `--w-mm`、`--h-mm` | 标签尺寸（mm），默认 60 × 40；宽是横跨打印头的方向，最大 80 |
| `--font 路径` | 字体文件，或 Windows 字体名如 `微软雅黑`、`黑体`；默认自动找中文字体 |
| `--host`、`--port` | 打印机地址；默认取 `NIIMBOT_HOST`，端口 9100 |
| `--dry-run` | 只生成预览 |
| `--preview 文件` | 预览图保存位置，默认当前文件夹的 `preview.png` |

预览图就是实际打出来的样子。

**退出码**：`0` 且最后一行是 `[OK] printed N/N`：全部打完。`1` 且输出 `[FAILED] …`：会写明已经打好几张、失败的那张
有没有可能已经出纸，先看打印机再决定要不要重打。`2`：参数错误，什么都没发。

## 在 Python 里用

```python
from PIL import Image, ImageDraw
import niimbot_k3w as k

img = Image.new("L", k.label_size(), 255)        # 480 x 320 点 = 60 x 40 mm；深色像素会被打印
ImageDraw.Draw(img).rectangle([10, 10, 469, 309], outline=0, width=6)
k.print_label(img, host="192.168.1.50")          # 任意尺寸都会缩放到标签大小；也可以传图片路径
```

打印失败时 `print_label` 抛出 `k.PrintFailed`，它的 `maybe_printed` 表示这张有没有可能已经出纸。排版用的辅助函数：
`label_size`、`text_block`、`qr_label`、`find_font`、`to_bits`（实际要打的 1 位图）。

更多例子见 [examples/](examples)：自定义排版、按 CSV 每行打一张。

## 没有打印机也能测试

`tools/mock_k3w.py` 在本机模拟一台 K3_W：检查收到的每个数据包，像真机一样在"忙"的时候拒绝新任务，并把本来会打出来
的内容存成 PNG：

```sh
python tools/mock_k3w.py                          # 监听 127.0.0.1:9100
niimbot-k3w --host 127.0.0.1 "测试"               # 在另一个终端里运行
# 结果：mock_sessions/9100/last_report.json 和 last_label.png
```

运行测试（测试会自己启动模拟打印机）：

```sh
pip install ".[test]"
pytest
```

其中一个测试会发送一张固定的图，把数据包（状态查询除外）和真机打印成功那个版本的录制结果逐字节对比。

## 工具

| | |
|---|---|
| `tools/info.py` | 读取型号、固件、协议版本和状态，只读 |
| `tools/mock_k3w.py` | 模拟打印机，用于测试 |
| `tools/diag.py` | 打印时记录每个数据包和时间，并在前后读取标签卷的已用张数，用来排查时序问题（会真的打印） |

## 安全

只发送 [docs/protocol.md](docs/protocol.md) 里列出的命令。协议里还有别的命令（比如固件升级），本项目不用；
给打印机发未知命令可能让它出问题。除了每个任务都会发的标签类型和浓度，不改打印机的任何设置，也不往标签卷里写东西。

## 致谢

数据包格式、命令和打印流程来自 MultiMote 的 [niimbluelib](https://github.com/MultiMote/niimbluelib) 和 kjy00302
的 [niimprint](https://github.com/kjy00302/niimprint)（及 [AndBondStyle 的分支](https://github.com/AndBondStyle/niimprint)）
的逆向工作，它们都采用 MIT 许可证。本项目在 TCP 上重新实现了这套流程，并补充了在 K3_W 上发现的内容。

## 许可证

[MIT](LICENSE)。NIIMBOT 和精臣是其所有者的商标。
