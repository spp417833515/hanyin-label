# -*- coding: utf-8 -*-
"""汉印 HPRT HM-T260L 标签打印工具

整张标签在电脑上渲染成位图再发给打印机, 预览图 = 实际打印效果, 中文不依赖打印机字库.
打印协议和通道见 printer.py.

  python label.py status                          检测打印机 (USB / 蓝牙)
  python label.py presets                         列出预设
  python label.py preview "第一行" "第二行"        只生成预览图 preview.png
  python label.py print "第一行" "第二行" -n 3     打印 3 张 (默认预设 25x15)
  python label.py print -p 25x15-二维码 --qr https://example.com "扫码查看"
  python label.py batch 列表.csv                   批量: 每行一张; 列名 qr/二维码, copies/份数, 其余列依次为文字行
  python label.py test                            打印自检标签 (边框 + 尺寸, 用来校准位置/方向)
  python label.py save-preset 30x20 --size 30x20  新建/修改预设 (写入 presets.json)

通道 (--via): auto 有 USB 走驱动否则蓝牙 / driver 驱动(同汉码) / raw USB 直发 / bt 蓝牙
"""
import argparse
import csv
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

import printer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "presets.json")
FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")

# 预设里没写的字段都用这里的值
BASE_PRESET = {
    "size": [25, 15],      # 宽 x 高 mm (宽 = 横跨打印头方向, 高 = 出纸方向)
    "layout": "text",      # text 纯文字 / qr 左二维码 + 右文字
    "margin": 1.0,         # 内边距 mm
    "align": "center",     # center / left
    "offset": [0, 0],      # 整体右移 / 下移 mm, 打印位置偏了用它校准
    "rotate": 0,           # 0 / 90 / 180 / 270 (顺时针), 方向反了用 180
    "title_ratio": 1.25,   # 多行时第 1 行相对放大倍数
    "bold": "first",       # first 第 1 行加粗 / all / none
    "qr_ec": "M",          # 二维码纠错等级 L/M/Q/H
    "threshold": 150,      # 灰度转黑白阈值, 调大笔画更粗
}

MIN_FONT = 10          # px; 203dpi 下更小的中文基本认不出
REF_GLYPHS = "国Ag"    # 用固定参考字形算行高, 各行基线才对齐


# ---------------------------------------------------------------- 配置 / 预设

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def parse_pair(s):
    a, b = s.lower().replace("*", "x").replace(",", "x").split("x")
    return [float(a), float(b)]


def preset_overrides(args):
    """命令行上显式给出的预设字段."""
    o = {}
    if getattr(args, "size", None):
        o["size"] = parse_pair(args.size)
    if getattr(args, "offset", None):
        o["offset"] = parse_pair(args.offset)
    for k in ("layout", "margin", "align", "rotate"):
        v = getattr(args, k, None)
        if v is not None:
            o[k] = v
    return o


def resolve_preset(cfg, args):
    name = getattr(args, "preset", None) or cfg["default_preset"]
    if name not in cfg["presets"]:
        sys.exit(f"没有预设「{name}」, 可用: {', '.join(cfg['presets'])}")
    p = {**BASE_PRESET, **cfg["presets"][name], **preset_overrides(args)}
    p["name"] = name
    return p


def physical_size(p):
    """打印方向上的尺寸 (旋转 90/270 时宽高互换)."""
    return p["size"] if int(p["rotate"]) in (0, 180) else p["size"][::-1]


# ---------------------------------------------------------------- 渲染

def mm2dot(mm, dpi):
    return int(round(mm * dpi / 25.4))


_fonts = {}


def get_font(bold, size):
    key = (bold, size)
    if key not in _fonts:
        name = "msyhbd.ttc" if bold else "msyh.ttc"
        path = os.path.join(FONT_DIR, name)
        if not os.path.exists(path):
            path = os.path.join(FONT_DIR, "simhei.ttf")
        _fonts[key] = ImageFont.truetype(path, size)
    return _fonts[key]


def measure(text, font):
    l, _, r, _ = font.getbbox(text)
    _, rt, _, rb = font.getbbox(REF_GLYPHS)
    return r - l, rb - rt, l, rt


def fit_lines(lines, bw, bh, p):
    """找最大字号让所有行塞进 bw x bh; 过宽的单行单独缩小."""
    n = len(lines)

    def attempt(s):
        rows, total = [], 0
        for i, text in enumerate(lines):
            bold = p["bold"] == "all" or (p["bold"] == "first" and i == 0)
            size = max(MIN_FONT, int(s * (p["title_ratio"] if i == 0 and n > 1 else 1)))
            f = get_font(bold, size)
            w, h, l, top = measure(text, f)
            while w > bw and size > MIN_FONT:
                size = max(MIN_FONT, min(size - 1, int(size * bw / w)))
                f = get_font(bold, size)
                w, h, l, top = measure(text, f)
            rows.append((text, f, w, h, l, top, size))
            total += h
        spacing = max(1, round(min(r[6] for r in rows) * 0.15)) if n > 1 else 0
        return rows, total + spacing * (n - 1), spacing

    lo, hi, best = MIN_FONT, max(MIN_FONT, bh * 2), None
    while lo <= hi:
        mid = (lo + hi) // 2
        rows, total, spacing = attempt(mid)
        if total <= bh and all(r[2] <= bw for r in rows):
            best, lo = (rows, total, spacing), mid + 1
        else:
            hi = mid - 1
    if best is None:
        best = attempt(MIN_FONT)
        print("警告: 文字太多, 最小字号也放不下, 会被截断", file=sys.stderr)
    return best


def draw_text(draw, lines, box, p):
    bx, by, bw, bh = box
    rows, total, spacing = fit_lines(lines, bw, bh, p)
    y = by + (bh - total) // 2
    for text, f, w, h, l, top, size in rows:
        x = bx + ((bw - w) // 2 if p["align"] == "center" else 0)
        draw.text((x - l, y - top), text, font=f, fill=0)
        y += h + spacing
    return [r[6] for r in rows]


def draw_qr(draw, data, x, y, side, ec):
    import qrcode
    levels = {"L": qrcode.constants.ERROR_CORRECT_L, "M": qrcode.constants.ERROR_CORRECT_M,
              "Q": qrcode.constants.ERROR_CORRECT_Q, "H": qrcode.constants.ERROR_CORRECT_H}
    q = qrcode.QRCode(border=0, error_correction=levels[ec.upper()])
    q.add_data(data)
    q.make(fit=True)
    matrix = q.get_matrix()
    n = len(matrix)
    scale = side // n
    if scale < 1:
        raise ValueError(f"二维码内容太长 ({n}x{n} 模块), {side} 点的空间放不下, 请缩短内容或用更大的标签")
    if scale < 2:
        print(f"警告: 二维码每个模块只有 {scale} 点 (0.125mm), 可能扫不出来, 建议缩短内容或换大标签",
              file=sys.stderr)
    size = n * scale
    ox, oy = x + (side - size) // 2, y + (side - size) // 2
    for r, row in enumerate(matrix):
        for c, v in enumerate(row):
            if v:
                draw.rectangle([ox + c * scale, oy + r * scale,
                                ox + (c + 1) * scale - 1, oy + (r + 1) * scale - 1], fill=0)
    return n, scale


def render(p, lines, qr=None, dpi=203, test=False):
    """按预设画一张标签, 返回 '1' 模式位图 (已按 rotate 旋转到打印方向)."""
    w, h = mm2dot(p["size"][0], dpi), mm2dot(p["size"][1], dpi)
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    m = mm2dot(p["margin"], dpi)
    bx, by, bw, bh = m, m, w - 2 * m, h - 2 * m

    if qr and (p["layout"] == "qr" or not lines):
        side = min(bh, bw)
        if lines:
            draw_qr(draw, qr, bx, by, side, p["qr_ec"])
            gap = mm2dot(1, dpi)
            bx, bw = bx + side + gap, bw - side - gap
        else:
            draw_qr(draw, qr, bx + (bw - side) // 2, by, side, p["qr_ec"])
    elif qr:
        print(f"提示: 预设「{p['name']}」的 layout 不是 qr, 二维码被忽略 (加 --layout qr)", file=sys.stderr)

    sizes = draw_text(draw, lines, (bx, by, bw, bh), p) if lines else []
    if sizes and min(sizes) < 14:
        print(f"提示: 最小字号 {min(sizes)}px (约 {min(sizes) * 25.4 / dpi:.1f}mm), 字多时可能偏小",
              file=sys.stderr)

    img = img.point(lambda v: 0 if v < p["threshold"] else 255).convert("1", dither=Image.Dither.NONE)
    if test:
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, w - 1, h - 1], outline=0, width=2)
        t = mm2dot(3, dpi)
        d.polygon([(0, 0), (t, 0), (0, t)], fill=0)  # 左上角实心三角: 看方向 / 偏移

    rot = {0: None, 90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
           270: Image.Transpose.ROTATE_90}[int(p["rotate"])]
    return img.transpose(rot) if rot is not None else img


def save_preview(images, path, scale=4):
    gap = 12
    w = max(i.width for i in images) * scale + 2 * gap
    h = sum(i.height * scale + gap for i in images) + gap
    sheet = Image.new("RGB", (w, h), (200, 200, 200))
    y = gap
    for img in images:
        big = img.convert("RGB").resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
        sheet.paste(big, (gap, y))
        y += big.height + gap
    sheet.save(path)


# ---------------------------------------------------------------- 发送

def resolve_via(cfg, args):
    pcfg = cfg["printer"]
    via = getattr(args, "via", None) or pcfg.get("via", "auto")
    if via == "auto":
        via = "driver" if printer.usb_present(pcfg["usb_id"]) else "bt"
    if via in ("driver", "raw") and not printer.usb_present(pcfg["usb_id"]):
        sys.exit("USB 上没检测到打印机 (线没插 / 没开机 / 休眠了). 可以改用 --via bt")
    return via


def send(cfg, via, p, labels, title):
    pcfg = cfg["printer"]
    dpi = pcfg["dpi"]
    offset = (mm2dot(p["offset"][0], dpi), mm2dot(p["offset"][1], dpi))
    total = sum(n for _, n in labels)
    if via == "driver":
        printer.send_driver(pcfg["queue"], labels, physical_size(p), offset, title)
        print(f"已经驱动「{pcfg['queue']}」打印 {total} 张")
        return
    data = printer.encode_job(labels, offset)
    if via == "raw":
        printer.send_raw_spooler(pcfg["queue"], data, title)
        print(f"已经 USB 直发 {total} 张 ({len(data)} 字节)")
    else:
        print(f"通过蓝牙 {pcfg['bt_port']} 连接打印机...")
        try:
            printer.send_bt(pcfg["bt_port"], data)
        except OSError as e:
            sys.exit(f"蓝牙失败: {e.strerror}")
        print(f"已经蓝牙发送 {total} 张 ({len(data)} 字节)")


def output(cfg, args, p, labels, title):
    preview = args.output or os.path.join(HERE, "preview.png")
    save_preview([img for img, _ in labels], preview)
    print(f"预览: {preview}")
    if args.command == "preview":
        return
    if args.dry_run:
        dpi = cfg["printer"]["dpi"]
        data = printer.encode_job(labels, (mm2dot(p["offset"][0], dpi), mm2dot(p["offset"][1], dpi)))
        prn = os.path.splitext(preview)[0] + ".prn"
        with open(prn, "wb") as f:
            f.write(data)
        print(f"未发送 (--dry-run), 打印机字节流已写入: {prn}")
        return
    send(cfg, resolve_via(cfg, args), p, labels, title)


# ---------------------------------------------------------------- 命令

def cmd_status(cfg, args):
    import win32print
    pcfg = cfg["printer"]
    try:
        h = win32print.OpenPrinter(pcfg["queue"])
        info = win32print.GetPrinter(h, 2)
        jobs = win32print.EnumJobs(h, 0, 99, 1)
        win32print.ClosePrinter(h)
        print(f"驱动队列: {info['pPrinterName']}  驱动: {info['pDriverName']}  端口: {info['pPortName']}"
              f"  队列中任务: {len(jobs)}")
    except Exception as e:
        print(f"驱动队列「{pcfg['queue']}」打不开: {e}")
    print("USB 连接:", "已连接" if printer.usb_present(pcfg["usb_id"]) else "未检测到 (USB 线没插 / 没开机 / 休眠)")
    if args.no_bt:
        return
    print(f"蓝牙 {pcfg['bt_port']}: 连接中 (最多约 5 秒)...")
    try:
        printer.SerialPort(pcfg["bt_port"]).close()
        print(f"蓝牙 {pcfg['bt_port']}: 可以连接")
    except OSError as e:
        print(f"蓝牙 {pcfg['bt_port']}: 失败 - {e.strerror}")


def cmd_presets(cfg, args):
    for name, raw in cfg["presets"].items():
        p = {**BASE_PRESET, **raw}
        mark = " (默认)" if name == cfg["default_preset"] else ""
        extra = []
        if raw.get("lines"):
            extra.append("文字: " + " / ".join(raw["lines"]))
        if raw.get("qr"):
            extra.append("二维码: " + raw["qr"])
        print(f"{name}{mark}: {p['size'][0]:g}x{p['size'][1]:g}mm  布局 {p['layout']}  旋转 {p['rotate']}"
              f"  偏移 {p['offset']}" + ("  " + "  ".join(extra) if extra else ""))


def content_from(args, p):
    lines = args.lines or p.get("lines") or []
    qr = args.qr if args.qr is not None else p.get("qr")
    if not lines and not qr:
        sys.exit("没有内容: 请给出文字 (位置参数) 或 --qr")
    return lines, qr


def cmd_print(cfg, args):
    p = resolve_preset(cfg, args)
    lines, qr = content_from(args, p)
    copies = getattr(args, "copies", 1)
    img = render(p, lines, qr, cfg["printer"]["dpi"])
    print(f"预设 {p['name']}: {p['size'][0]:g}x{p['size'][1]:g}mm, {copies} 张")
    output(cfg, args, p, [(img, copies)], "Label")


def read_csv_rows(path):
    for enc in ("utf-8-sig", "gbk"):
        try:
            with open(path, encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    sys.exit(f"{path} 编码无法识别 (支持 UTF-8 / GBK)")


def cmd_batch(cfg, args):
    p = resolve_preset(cfg, args)
    labels = []
    for row in read_csv_rows(args.csv):
        qr, n, lines = None, args.copies, []
        for key, val in row.items():
            k, val = (key or "").strip().lower(), (val or "").strip()
            if k in ("qr", "二维码"):
                qr = val or None
            elif k in ("copies", "份数", "数量"):
                n = int(val) if val else n
            elif val:
                lines.append(val)
        if lines or qr:
            labels.append((render(p, lines, qr, cfg["printer"]["dpi"]), n))
    if not labels:
        sys.exit("CSV 里没有可打印的行")
    total = sum(n for _, n in labels)
    print(f"预设 {p['name']}: {len(labels)} 种标签, 共 {total} 张")
    output(cfg, args, p, labels, f"Labels x{total}")


def cmd_test(cfg, args):
    p = resolve_preset(cfg, args)
    p = {**p, "margin": max(p["margin"], 1.5)}
    via = "预览" if args.dry_run else resolve_via(cfg, args)
    args.via = via
    lines = [f"{p['size'][0]:g}x{p['size'][1]:g}mm", "HM-T260L 自检", f"通道 {via}"]
    img = render(p, lines, None, cfg["printer"]["dpi"], test=True)
    output(cfg, args, p, [(img, 1)], f"Label test ({via})")


def cmd_save_preset(cfg, args):
    base = cfg["presets"].get(args.base or args.name, {})
    new = {**base, **preset_overrides(args)}
    if args.lines:
        new["lines"] = args.lines
    if args.qr is not None:
        new["qr"] = args.qr
    if args.clear_content:
        new.pop("lines", None)
        new.pop("qr", None)
    new.setdefault("size", BASE_PRESET["size"])
    cfg["presets"][args.name] = new
    if args.default:
        cfg["default_preset"] = args.name
    save_config(cfg)
    print(f"已保存预设「{args.name}」: {json.dumps(new, ensure_ascii=False)}")
    if args.default:
        print("并设为默认预设")


def build_parser():
    ap = argparse.ArgumentParser(description="汉印 HM-T260L 标签打印",
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    layout = argparse.ArgumentParser(add_help=False)
    g = layout.add_argument_group("预设参数 (临时覆盖)")
    g.add_argument("-p", "--preset", help="预设名, 默认见 presets.json 的 default_preset")
    g.add_argument("--size", help="标签尺寸 宽x高 mm, 如 30x20")
    g.add_argument("--layout", choices=["text", "qr"], help="布局")
    g.add_argument("--margin", type=float, help="内边距 mm")
    g.add_argument("--align", choices=["center", "left"])
    g.add_argument("--offset", help="整体偏移 x,y mm (右移,下移)")
    g.add_argument("--rotate", type=int, choices=[0, 90, 180, 270])

    send_opts = argparse.ArgumentParser(add_help=False)
    s = send_opts.add_argument_group("输出")
    s.add_argument("--via", choices=["auto", "driver", "raw", "bt"],
                   help="通道, 默认 auto (有 USB 走驱动, 否则蓝牙)")
    s.add_argument("-o", "--output", help="预览图路径, 默认 preview.png")
    s.add_argument("--dry-run", action="store_true", help="只生成预览和 .prn 字节流文件, 不发送")

    content = argparse.ArgumentParser(add_help=False)
    content.add_argument("lines", nargs="*", help="文字, 每个参数一行")
    content.add_argument("--qr", help="二维码内容")

    st = sub.add_parser("status", help="检测打印机连接")
    st.add_argument("--no-bt", action="store_true", help="不尝试蓝牙")
    sub.add_parser("presets", help="列出预设")
    sub.add_parser("preview", parents=[content, layout, send_opts], help="只生成预览图")
    pr = sub.add_parser("print", parents=[content, layout, send_opts], help="打印")
    pr.add_argument("-n", "--copies", type=int, default=1, help="份数")
    ba = sub.add_parser("batch", parents=[layout, send_opts], help="按 CSV 批量打印")
    ba.add_argument("csv")
    ba.add_argument("-n", "--copies", type=int, default=1, help="CSV 没有份数列时每行的份数")
    sub.add_parser("test", parents=[layout, send_opts], help="打印自检标签")
    sp = sub.add_parser("save-preset", parents=[layout], help="新建/修改预设")
    sp.add_argument("name")
    sp.add_argument("lines", nargs="*", help="预设里保存的固定文字, 每个参数一行")
    sp.add_argument("--qr", help="预设里保存的固定二维码内容")
    sp.add_argument("--from", dest="base", help="以哪个预设为基础 (默认: 同名预设, 没有则用内置默认值)")
    sp.add_argument("--default", action="store_true", help="设为默认预设")
    sp.add_argument("--clear-content", action="store_true", help="清掉预设里保存的文字/二维码")
    return ap


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    args = build_parser().parse_args()
    cfg = load_config()
    try:
        {"status": cmd_status, "presets": cmd_presets, "preview": cmd_print, "print": cmd_print,
         "batch": cmd_batch, "test": cmd_test, "save-preset": cmd_save_preset}[args.command](cfg, args)
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
