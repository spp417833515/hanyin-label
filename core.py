# -*- coding: utf-8 -*-
"""标签打印服务的数据与业务: 设置 / 尺寸预设 / 模板与变量 / API 密钥 / 打印.

数据都存在 data/ 下的 JSON 文件里:
    settings.json   端口、局域网开关、默认通道、尺寸预设
    templates.json  标签模板 (尺寸 + 元素列表, 文字里可写变量)
    keys.json       API 密钥 (只存 SHA-256, 明文只在生成时显示一次)

模板里的元素 (坐标、尺寸单位都是 mm, 原点在标签左上角):
    文本   {"type": "text", "x", "y", "w", "text", "pt", "bold", "align", "shrink"}
           高度由字号决定 (pt × 0.3528 × 1.25); shrink=true 时内容超过宽度自动缩小
    二维码 {"type": "qr", "x", "y", "size", "data"}
    线条   {"type": "rect", "x", "y", "w", "h"}   实心矩形, 细的就是线
    方框   {"type": "box", "x", "y", "w", "h", "t"}  空心矩形, t 为线宽
"""
import copy
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from PIL import Image, ImageDraw

import label
import printer

import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, "frozen", False)  # 打包成 exe 后安装目录只读, 数据放到用户目录
DATA = (os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "HanyinLabel", "data") if FROZEN
        else os.path.join(HERE, "data"))
DPI = 203
HEAD_DOTS = 384      # HM-T260L 打印头 48mm
PT_MM = 25.4 / 72    # 1pt = 0.3528mm
LINE = 1.25          # 文本框高度 = 字号 × 1.25

_lock = threading.RLock()

DEFAULT_SETTINGS = {
    "port": 8765,
    "lan": False,
    "channel": "auto",
    "sizes": [[25, 15], [30, 20], [40, 30], [50, 30]],
    "offset": [0, 0],   # 打印位置校准: 整体右移 / 下移 mm (负数往左 / 往上), 所有打印都会套用
}
MAX_OFFSET = 10

# 示例模板用"每行文字"写, 首次运行时自动排成元素
SAMPLE_TEMPLATES = [
    {"id": "prod", "name": "生产日期标签", "size": [25, 15], "lines": ["{品名}", "生产 {日期}", "批号 {流水号}"],
     "fields": {"品名": "不锈钢螺丝"}},
    {"id": "price", "name": "商品价签", "size": [25, 15], "lines": ["{品名}", "¥ {价格}"],
     "fields": {"品名": "螺丝 M6×20", "价格": "0.35"}},
    {"id": "loc", "name": "库位标签", "size": [30, 20], "lines": ["{库位}", "{区域}"], "qr": "{库位}",
     "fields": {"库位": "A-03-02", "区域": "一号仓"}},
    {"id": "asset", "name": "资产标签", "size": [40, 30],
     "lines": ["固定资产", "{资产名称}", "编号 ZC-{流水号}", "启用 {日期}"], "fields": {"资产名称": "激光打印机"}},
    {"id": "ship", "name": "快递分拣", "size": [50, 30], "lines": ["{格口}", "{快递公司}", "{日期} {时间}"],
     "fields": {"格口": "B-12", "快递公司": "顺丰"}},
]


class PrinterError(Exception):
    pass


# ---------------------------------------------------------------- JSON 存储

def _path(name):
    return os.path.join(DATA, name)


def _load(name, default):
    try:
        with open(_path(name), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return copy.deepcopy(default)


def _save(name, obj):
    os.makedirs(DATA, exist_ok=True)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, _path(name))


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def num(v, digits=1):
    v = round(float(v), digits)
    return int(v) if v.is_integer() else v


def mm2dot(mm):
    return int(round(mm * DPI / 25.4))


# ---------------------------------------------------------------- 设置 / 尺寸预设

def settings():
    with _lock:
        return {**DEFAULT_SETTINGS, **_load("settings.json", {})}


def update_settings(**changes):
    with _lock:
        s = settings()
        if "lan" in changes:
            s["lan"] = bool(changes["lan"])
        if "channel" in changes:
            if changes["channel"] not in ("auto", "usb", "driver", "bt"):
                raise ValueError("通道只能是 auto / usb / driver / bt")
            s["channel"] = changes["channel"]
        if "offset" in changes:
            s["offset"] = check_offset(changes["offset"])
        if "bt_port" in changes:
            port = str(changes["bt_port"] or "").strip().upper()
            if not re.fullmatch(r"COM\d{1,3}", port):
                raise ValueError("蓝牙串口格式应为 COM 加数字, 如 COM5")
            s["bt_port"] = port
            bt_state.update(state="unknown", message="", checked_at=None)
        _save("settings.json", s)
        return s


def check_offset(offset):
    try:
        x, y = num(offset[0], 2), num(offset[1], 2)
    except (TypeError, ValueError, IndexError):
        raise ValueError("偏移格式应为 [x, y], 单位 mm")
    if abs(x) > MAX_OFFSET or abs(y) > MAX_OFFSET:
        raise ValueError(f"偏移范围 -{MAX_OFFSET}~{MAX_OFFSET}mm")
    return [x, y]


def check_size(size):
    try:
        w, h = num(size[0]), num(size[1])
    except (TypeError, ValueError, IndexError):
        raise ValueError("尺寸格式应为 [宽, 高], 单位 mm")
    if not (10 <= w <= 50 and 8 <= h <= 200):
        raise ValueError("尺寸超出范围: 宽 10~50mm, 高 8~200mm")
    return [w, h]


def add_size(size):
    with _lock:
        s = settings()
        size = check_size(size)
        if size not in s["sizes"]:
            s["sizes"].append(size)
            _save("settings.json", s)
        return s["sizes"]


def remove_size(size):
    with _lock:
        s = settings()
        size = check_size(size)
        s["sizes"] = [x for x in s["sizes"] if x != size]
        _save("settings.json", s)
        return s["sizes"]


# ---------------------------------------------------------------- 变量

VAR_RE = re.compile(r"\{([^{}]+)\}")
AUTO_RE = re.compile(r"日期([+-]\d+天)?|时间|流水号")


def parse_var(body):
    i = body.find(":")
    return (body.strip(), "") if i < 0 else (body[:i].strip(), body[i + 1:])


def fmt_dt(d, f):
    return (f.replace("yyyy", f"{d.year:04d}").replace("MM", f"{d.month:02d}").replace("dd", f"{d.day:02d}")
            .replace("HH", f"{d.hour:02d}").replace("mm", f"{d.minute:02d}").replace("ss", f"{d.second:02d}"))


def expand(text, fields, serial, now):
    """模板文字 → 打印时的实际文字."""
    def rep(m):
        h, f = parse_var(m.group(1))
        mo = re.fullmatch(r"日期([+-]\d+)天", h)
        if h == "日期" or mo:
            return fmt_dt(now + timedelta(days=int(mo.group(1))) if mo else now, f or "yyyy-MM-dd")
        if h == "时间":
            return fmt_dt(now, f or "HH:mm")
        if h == "流水号":
            return str(serial).zfill(len(f) if f else 4)
        if h == "输入":
            h = f.strip()
        v = fields.get(h)
        return m.group(0) if v is None or str(v) == "" else str(v)
    return VAR_RE.sub(rep, text)


def texts_of(t):
    return [e["text"] if e["type"] == "text" else e["data"] for e in t["elements"] if e["type"] in ("text", "qr")]


def template_vars(t):
    """模板里出现的变量: auto=True 为日期/时间/流水号, 其余要在打印时填写."""
    out, seen = [], set()
    for text in texts_of(t):
        for m in VAR_RE.finditer(text):
            h, f = parse_var(m.group(1))
            if h == "输入":
                h = f.strip()
            if not h:
                continue
            if AUTO_RE.fullmatch(h):
                key, item = ("auto", m.group(0)), {"name": h, "auto": True, "raw": m.group(0)}
            else:
                key, item = ("field", h), {"name": h, "auto": False}
            if key not in seen:
                seen.add(key)
                out.append(item)
    return out


def uses_serial(t):
    return any("{流水号" in s for s in texts_of(t))


def missing_fields(t, fields):
    return [v["name"] for v in template_vars(t) if not v["auto"] and not str(fields.get(v["name"], "")).strip()]


# ---------------------------------------------------------------- 元素

def text_height(pt):
    return pt * PT_MM * LINE


def layout_lines(size, lines, qr=None, align="center"):
    """把"每行文字 (+ 左侧二维码)"排成元素: 旧模板迁移和 /api/print/text 用."""
    w, h = size
    m = 1.0
    x0, bw, bh = m, w - 2 * m, h - 2 * m
    els = []
    lines = [s for s in lines if str(s).strip()]
    if qr:
        side = round(min(bh, bw), 1)
        if lines:
            els.append({"type": "qr", "x": m, "y": round((h - side) / 2, 2), "size": side, "data": qr})
            x0 = m + side + 1
            bw = w - x0 - m
        else:
            els.append({"type": "qr", "x": round((w - side) / 2, 2), "y": m, "size": side, "data": qr})
    if lines:
        ratios = [1.25 if i == 0 and len(lines) > 1 else 1 for i in range(len(lines))]
        unit = bh / sum(ratios)
        y = m
        for i, s in enumerate(lines):
            box = unit * ratios[i]
            pt = max(4.0, min(36.0, round(box / LINE / PT_MM * 2) / 2))
            els.append({"type": "text", "x": round(x0, 2), "y": round(y + (box - text_height(pt)) / 2, 2),
                        "w": round(max(bw, 2), 2), "text": str(s), "pt": pt, "bold": i == 0, "align": align,
                        "shrink": True})
            y += box
    return els


def _numf(v, lo, hi, what):
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what}必须是数字")
    return num(min(max(v, lo), hi), 2)


def normalize_element(e, size):
    if not isinstance(e, dict):
        raise ValueError("元素格式不对")
    W, H = size
    typ = e.get("type")
    out = {"id": re.sub(r"[^0-9A-Za-z_-]", "", str(e.get("id") or ""))[:16] or "e" + secrets.token_hex(3),
           "type": typ,
           "x": _numf(e.get("x", 0), -W, 2 * W, "x "),
           "y": _numf(e.get("y", 0), -H, 2 * H, "y ")}
    if typ == "text":
        out.update(w=_numf(e.get("w", W - 2), 0.5, 200, "宽度"), text=str(e.get("text") or "")[:200],
                   pt=_numf(e.get("pt", 9), 3, 72, "字号"), bold=bool(e.get("bold")),
                   align=e.get("align") if e.get("align") in ("left", "center", "right") else "left",
                   shrink=e.get("shrink", True) is not False)
    elif typ == "qr":
        out.update(size=_numf(e.get("size", min(W, H) - 2), 3, 100, "二维码大小"), data=str(e.get("data") or "")[:500])
    elif typ == "rect":
        out.update(w=_numf(e.get("w", 10), 0.1, 200, "宽度"), h=_numf(e.get("h", 0.3), 0.1, 200, "高度"))
    elif typ == "box":
        out.update(w=_numf(e.get("w", 10), 0.5, 200, "宽度"), h=_numf(e.get("h", 5), 0.5, 200, "高度"),
                   t=_numf(e.get("t", 0.3), 0.1, 5, "线宽"))
    else:
        raise ValueError(f"不支持的元素类型: {typ}")
    return out


# ---------------------------------------------------------------- 模板

def normalize_template(t, tid=None):
    if not isinstance(t, dict):
        raise ValueError("模板格式不对")
    size = check_size(t.get("size") or [25, 15])
    if t.get("elements") is not None:
        if not isinstance(t["elements"], list):
            raise ValueError("elements 应为数组")
        raw = t["elements"][:40]
    else:  # 旧格式: lines + qr
        raw = layout_lines(size, [str(x)[:120] for x in (t.get("lines") or [])][:10],
                           str(t["qr"])[:500] if t.get("qr") else None,
                           "left" if t.get("align") == "left" else "center")
    elements = [normalize_element(e, size) for e in raw]
    if not elements:
        raise ValueError("标签上至少要有一个元素 (文本 / 二维码 / 线条)")
    seen = set()
    for e in elements:  # 元素 id 去重
        while e["id"] in seen:
            e["id"] = "e" + secrets.token_hex(3)
        seen.add(e["id"])
    try:
        serial = max(0, int(t.get("serial") or 1))
        step = max(1, int(t.get("serial_step") or 1))
    except (TypeError, ValueError):
        raise ValueError("流水号必须是整数")
    fields = {str(k)[:40]: str(v)[:200] for k, v in (t.get("fields") or {}).items()}
    out = {
        "id": tid or t.get("id") or secrets.token_hex(3),
        "name": (str(t.get("name") or "").strip() or "未命名模板")[:40],
        "size": size,
        "elements": elements,
        "fields": {},
        "serial": serial,
        "serial_step": step,
        "updated": now_iso(),
    }
    out["fields"] = {v["name"]: fields.get(v["name"], "") for v in template_vars(out) if not v["auto"]}
    return out


def _templates():
    data = _load("templates.json", None)
    if data is None:
        data = [normalize_template(t) for t in SAMPLE_TEMPLATES]
        _save("templates.json", data)
    elif any("elements" not in t for t in data):  # 旧版 (每行文字) 模板 → 元素
        data = [t if "elements" in t else {**normalize_template(t), "updated": t.get("updated", now_iso())}
                for t in data]
        _save("templates.json", data)
    return data


def list_templates():
    with _lock:
        return _templates()


def find_template(ref):
    with _lock:
        ts = _templates()
        for t in ts:
            if t["id"] == ref:
                return t
        for t in ts:
            if t["name"] == ref:
                return t
    raise KeyError(f"没有找到模板「{ref}」")


def save_template(t, tid=None):
    with _lock:
        ts = _templates()
        if tid is not None and not any(x["id"] == tid for x in ts):
            raise KeyError(f"没有找到模板「{tid}」")
        new = normalize_template(t, tid)
        if any(x["name"] == new["name"] and x["id"] != new["id"] for x in ts):
            raise ValueError(f"已经有叫「{new['name']}」的模板了, 换个名字")
        ts = [new if x["id"] == new["id"] else x for x in ts]
        if not any(x["id"] == new["id"] for x in ts):
            ts.append(new)
        _save("templates.json", ts)
        return new


def delete_template(ref):
    with _lock:
        t = find_template(ref)
        _save("templates.json", [x for x in _templates() if x["id"] != t["id"]])
        return t


def _set_serial(tid, serial):
    with _lock:
        ts = _templates()
        for x in ts:
            if x["id"] == tid:
                x["serial"] = serial
        _save("templates.json", ts)


# ---------------------------------------------------------------- 渲染

def render_label(t, fields, serial, now=None):
    """模板 + 变量值 → '1' 模式位图 (和打印出来的一模一样)."""
    now = now or datetime.now()
    w, h = t["size"]
    img = Image.new("L", (min(mm2dot(w), HEAD_DOTS), mm2dot(h)), 255)
    d = ImageDraw.Draw(img)
    for e in t["elements"]:
        x, y = e["x"] * DPI / 25.4, e["y"] * DPI / 25.4
        if e["type"] == "text":
            s = expand(e["text"], fields, serial, now)
            if not s.strip():
                continue
            px = e["pt"] * DPI / 72
            box_w, box_h = e["w"] * DPI / 25.4, px * LINE
            font = label.get_font(e["bold"], max(4, round(px)))
            tw = font.getlength(s)
            if e["shrink"] and tw > box_w > 0:
                font = label.get_font(e["bold"], max(4, int(px * box_w / tw)))
            ax = {"left": x, "center": x + box_w / 2, "right": x + box_w}[e["align"]]
            anchor = {"left": "lm", "center": "mm", "right": "rm"}[e["align"]]
            d.text((ax, y + box_h / 2), s, font=font, fill=0, anchor=anchor)
        elif e["type"] == "qr":
            data = expand(e["data"], fields, serial, now)
            if data.strip():
                label.draw_qr(d, data, round(x), round(y), mm2dot(e["size"]), "M")
        elif e["type"] == "rect":
            x0, y0 = round(x), round(y)
            d.rectangle([x0, y0, x0 + max(1, mm2dot(e["w"])) - 1, y0 + max(1, mm2dot(e["h"])) - 1], fill=0)
        elif e["type"] == "box":
            x0, y0 = round(x), round(y)
            d.rectangle([x0, y0, x0 + max(2, mm2dot(e["w"])) - 1, y0 + max(2, mm2dot(e["h"])) - 1],
                        outline=0, width=max(1, mm2dot(e["t"])))
    return img.point(lambda v: 0 if v < label.BASE_PRESET["threshold"] else 255).convert("1", dither=Image.Dither.NONE)


def render_png(t, fields, scale=1):
    img = render_label(t, {**t["fields"], **fields}, t.get("serial", 1))
    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- 打印机状态

class UsbMonitor(threading.Thread):
    """每 2 秒查一次 USB 上有没有打印机 (WMI 要在本线程初始化 COM)."""

    def __init__(self, usb_id, interval=2.0):
        super().__init__(daemon=True)
        self.usb_id, self.interval = usb_id, interval
        self.online, self.checked_at = False, None

    def run(self):
        import pythoncom
        pythoncom.CoInitialize()
        while True:
            try:
                self.online = printer.usb_present(self.usb_id)
                self.checked_at = now_iso()
            except Exception:
                pass
            time.sleep(self.interval)


_monitor = None


def printer_config():
    """打印机配置: presets.json 里的默认值, 蓝牙串口可在设置里改 (换电脑后 COM 口编号可能不同)."""
    cfg = dict(label.load_config()["printer"])
    port = settings().get("bt_port")
    if port:
        cfg["bt_port"] = port
    return cfg


def list_com_ports():
    """本机所有串口, 蓝牙串口标出来 (配对打印机后系统会生成两个, 一般编号小的那个能用)."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    wmi = win32com.client.GetObject("winmgmts:")
    ports = []
    for dev in wmi.ExecQuery("SELECT Name, PNPDeviceID FROM Win32_PnPEntity WHERE Name LIKE '%(COM%'"):
        m = re.search(r"\((COM\d+)\)", dev.Name or "")
        if m:
            ports.append({"port": m.group(1), "name": dev.Name, "bluetooth": "BTHENUM" in (dev.PNPDeviceID or "").upper()})
    return sorted(ports, key=lambda p: int(p["port"][3:]))


def start_monitor():
    global _monitor
    if _monitor is None:
        _monitor = UsbMonitor(printer_config()["usb_id"])
        _monitor.start()
    return _monitor


def usb_online():
    """打印前现查一次, 不用缓存, 免得刚拔线还显示在线."""
    import pythoncom
    pythoncom.CoInitialize()
    return printer.usb_present(printer_config()["usb_id"])


# ---------------------------------------------------------------- 打印

_print_lock = threading.Lock()
MAX_LABELS = 500


def _send(labels, channel, size, title, offset=None):
    """auto / usb / driver 都要求 USB 上有打印机, 没有就直接拒绝; bt 只在明确指定时使用.

    offset: 打印位置校准 (mm), 不传就用设置里保存的.
    """
    pcfg = printer_config()
    ox, oy = check_offset(offset if offset is not None else settings()["offset"])
    dots = (mm2dot(ox), mm2dot(oy))
    if channel == "bt":
        via = "bt"
    else:
        if not usb_online():
            raise PrinterError("打印机未连接: USB 上没检测到打印机 (没插线、没开机或已自动关机)")
        via = "usb" if channel == "auto" else channel
    try:
        if via == "driver":
            printer.send_driver(pcfg["queue"], labels, size, dots, title=title)
        else:
            data = printer.encode_job(labels, dots)
            if via == "usb":
                printer.send_raw_spooler(pcfg["queue"], data, title)
            else:
                printer.send_bt(pcfg["bt_port"], data)
                bt_state.update(state="ok", message=f"蓝牙 {pcfg['bt_port']} 可以连接", checked_at=now_iso())
    except OSError as e:
        if via == "bt":
            bt_state.update(state="fail", message=f"蓝牙 {pcfg['bt_port']} 连不上: {e.strerror}", checked_at=now_iso())
            raise PrinterError(f"打印机未连接: 蓝牙{e.strerror}")
        raise PrinterError(f"发送失败: {e}")
    return via


def print_template(t, rows, channel=None):
    """rows: [(变量值, 份数)]. 用流水号的模板每张单独渲染, 打印成功后才把流水号写回."""
    channel = channel or settings()["channel"]
    if channel not in ("auto", "usb", "driver", "bt"):
        raise ValueError("channel 只能是 auto / usb / driver / bt")
    total = sum(n for _, n in rows)
    if total < 1:
        raise ValueError("份数至少 1")
    if total > MAX_LABELS:
        raise ValueError(f"一次最多打印 {MAX_LABELS} 张")
    now = datetime.now()
    serial, step = t.get("serial", 1), t.get("serial_step", 1)
    labels = []
    for i, (fields, copies) in enumerate(rows, 1):
        merged = {**t["fields"], **{k: str(v) for k, v in fields.items()}}
        miss = missing_fields(t, merged)
        if miss:
            where = f"第 {i} 行" if len(rows) > 1 else ""
            raise ValueError(f"{where}缺少变量: {'、'.join(miss)}")
        if uses_serial(t):
            for _ in range(copies):
                labels.append((render_label(t, merged, serial, now), 1))
                serial += step
        else:
            labels.append((render_label(t, merged, serial, now), copies))
    with _print_lock:
        via = _send(labels, channel, t["size"], f"{t['name']} x{total}")
        if uses_serial(t) and t.get("id"):
            _set_serial(t["id"], serial)
    return {"printed": total, "channel": via, "serial_next": serial}


def print_text(lines, size, qr=None, copies=1, align="center", fields=None, channel=None):
    t = normalize_template({"name": "临时标签", "size": size, "lines": lines, "qr": qr, "align": align,
                            "fields": fields or {}}, tid="")
    t["id"] = None
    return print_template(t, [(fields or {}, copies)], channel)


# ---------------------------------------------------------------- 校准 (调试模式)

def calibration_template(size, offset):
    """校准标签: 离四边 0.5mm 的细边框 + 中心十字 + 当前偏移值. 哪条边线看不到, 内容就偏向哪边."""
    w, h = check_size(size)
    x, y = check_offset(offset)
    t = 0.3
    pt = max(5.0, min(9.0, round(h / 3 / PT_MM / LINE * 2) / 2))
    els = [
        {"type": "rect", "x": 0.5, "y": 0.5, "w": w - 1, "h": t},
        {"type": "rect", "x": 0.5, "y": h - 0.5 - t, "w": w - 1, "h": t},
        {"type": "rect", "x": 0.5, "y": 0.5, "w": t, "h": h - 1},
        {"type": "rect", "x": w - 0.5 - t, "y": 0.5, "w": t, "h": h - 1},
        {"type": "rect", "x": w / 2 - 2, "y": h / 2 - t / 2, "w": 4, "h": t},
        {"type": "rect", "x": w / 2 - t / 2, "y": h / 2 - 2, "w": t, "h": 4},
        {"type": "text", "x": 1.5, "y": 1.2, "w": w - 3, "text": f"校准 {num(w)}×{num(h)}", "pt": pt, "bold": True,
         "align": "center"},
        {"type": "text", "x": 1.5, "y": h - 1.2 - text_height(pt * 0.85), "w": w - 3,
         "text": f"X {x:+.1f}  Y {y:+.1f}", "pt": round(pt * 0.85 * 2) / 2, "align": "center"},
    ]
    return normalize_template({"name": "校准", "size": [w, h], "elements": els}, tid="calibrate")


def calibration_png(size, offset, scale=3):
    """预览: 按偏移把内容挪过去, 标签外的部分裁掉 (和打印出来的位置一致)."""
    t = calibration_template(size, offset)
    img = render_label(t, {}, 1)
    ox, oy = mm2dot(offset[0]), mm2dot(offset[1])
    shifted = Image.new("1", img.size, 1)
    shifted.paste(img, (ox, oy))
    if scale > 1:
        shifted = shifted.resize((shifted.width * scale, shifted.height * scale), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    shifted.save(buf, "PNG")
    return buf.getvalue()


def print_calibration(size, offset, copies=1, channel=None):
    t = calibration_template(size, offset)
    img = render_label(t, {}, 1)
    with _print_lock:
        via = _send([(img, copies)], channel or settings()["channel"], t["size"], "校准标签", offset=offset)
    return {"printed": copies, "channel": via}


def feed_label(channel=None):
    """走纸到下一张标签开头 (不打印内容)."""
    channel = channel or settings()["channel"]
    pcfg = printer_config()
    with _print_lock:
        if channel == "bt":
            try:
                printer.send_bt(pcfg["bt_port"], printer.feed_job())
            except OSError as e:
                raise PrinterError(f"打印机未连接: 蓝牙{e.strerror}")
        else:
            if not usb_online():
                raise PrinterError("打印机未连接: USB 上没检测到打印机")
            printer.send_raw_spooler(pcfg["queue"], printer.feed_job(), "走纸")
    return {"fed": True}


# ---------------------------------------------------------------- 打印机自身参数 (经蓝牙读写)
# 编号、长度、取值范围都来自汉码软件里 HM-T260L 的机型配置

PRINTER_PARAMS = {
    "paper_type": {"key": 206, "size": 1, "name": "纸张类型", "choices": {2: "标签纸 (间隙)", 0: "连续纸", 1: "黑标纸"}},
    "density": {"key": 203, "size": 1, "name": "打印浓度", "choices": {35: "淡", 55: "偏淡", 75: "正常", 95: "偏浓"}},
    "sleep_sec": {"key": 400, "size": 4, "name": "休眠等待 (秒)", "min": 60, "max": 600},
    "poweroff_min": {"key": 401, "size": 4, "name": "自动关机 (分钟)", "min": 10, "max": 120},
}


def _bt_session(fn):
    port = printer_config()["bt_port"]
    with _print_lock:
        try:
            sp = printer.SerialPort(port)
        except OSError as e:
            raise PrinterError(f"蓝牙连不上打印机: {e.strerror}")
        try:
            sp.read(total=0.6)  # 清掉残留
            return fn(sp)
        except OSError as e:
            raise PrinterError(f"蓝牙通讯失败: {e.strerror or e}")
        finally:
            sp.close()


def read_printer_params():
    def run(sp):
        out = {"model": printer.v1_getkey(sp, 4, 16).rstrip(b"\x00").decode("ascii", "replace")}
        for k, p in PRINTER_PARAMS.items():
            out[k] = int.from_bytes(printer.v1_getkey(sp, p["key"], p["size"]), "little")
        return out
    return {"params": _bt_session(run), "spec": PRINTER_PARAMS}


def write_printer_params(changes):
    todo = []
    for k, v in changes.items():
        p = PRINTER_PARAMS.get(k)
        if p is None:
            raise ValueError(f"不支持的参数: {k}")
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{p['name']} 必须是整数")
        if "choices" in p and v not in p["choices"]:
            raise ValueError(f"{p['name']} 只能是 {list(p['choices'])}")
        if "min" in p and not p["min"] <= v <= p["max"]:
            raise ValueError(f"{p['name']} 范围 {p['min']}~{p['max']}")
        todo.append((p, v))
    if not todo:
        raise ValueError("没有要修改的参数")

    def run(sp):
        for p, v in todo:
            printer.v1_setkey(sp, p["key"], p["size"], v)
        r = printer.v1_do(sp, "save_param_zone")
        if r[:1] != b"\x00":
            raise OSError(0, "保存参数失败")
    _bt_session(run)
    return read_printer_params()


def paper_learn():
    """让打印机重新识别标签纸 (换新卷纸后用). 打印机会响一声."""
    _bt_session(lambda sp: printer.v1_do(sp, "paper_learn", wait=1.5))
    return {"sent": True}


# 蓝牙不能像 USB 那样一直轮询 (每次要连一下, 还会占住打印机的蓝牙), 只在选蓝牙 / 打印前后检测, 结果缓存
bt_state = {"state": "unknown", "checked_at": None, "message": ""}


def check_bluetooth():
    port = printer_config()["bt_port"]
    with _print_lock:
        try:
            printer.SerialPort(port).close()
            bt_state.update(state="ok", message=f"蓝牙 {port} 可以连接")
        except OSError as e:
            bt_state.update(state="fail", message=f"蓝牙 {port} 连不上: {e.strerror}")
    bt_state["checked_at"] = now_iso()
    return dict(bt_state)


# ---------------------------------------------------------------- 开机自动启动 (当前用户的注册表 Run 项)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "HanyinLabelPrint"


def autostart_command():
    if FROZEN:
        return f'"{sys.executable}" --tray'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw if os.path.exists(pyw) else sys.executable}" "{os.path.join(HERE, "server.py")}" --tray'


def get_autostart():
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_NAME)[0] == autostart_command()
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass
    return get_autostart()


# ---------------------------------------------------------------- API 密钥

def _hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _public_key(e):
    return {k: e.get(k) for k in ("id", "name", "prefix", "suffix", "created", "last_used", "calls")}


def list_keys():
    with _lock:
        return [_public_key(e) for e in _load("keys.json", [])]


def create_key(name):
    with _lock:
        keys = _load("keys.json", [])
        key = "hm_" + secrets.token_urlsafe(24)
        e = {"id": secrets.token_hex(4), "name": (str(name or "").strip() or "未命名")[:40], "hash": _hash(key),
             "prefix": key[:7], "suffix": key[-4:], "created": now_iso(), "last_used": None, "calls": 0}
        keys.append(e)
        _save("keys.json", keys)
        return {**_public_key(e), "key": key}


def delete_key(kid):
    with _lock:
        keys = _load("keys.json", [])
        left = [e for e in keys if e["id"] != kid]
        if len(left) == len(keys):
            raise KeyError("没有这个密钥")
        _save("keys.json", left)


def verify_key(key):
    if not key:
        return None
    h = _hash(key.strip())
    with _lock:
        keys = _load("keys.json", [])
        for e in keys:
            if hmac.compare_digest(e["hash"], h):
                e["last_used"] = now_iso()
                e["calls"] = e.get("calls", 0) + 1
                _save("keys.json", keys)
                return _public_key(e)
    return None


# ---------------------------------------------------------------- 调用记录

call_log = deque(maxlen=200)


def log_call(ip, method, path, status, who):
    call_log.appendleft({"time": now_iso(), "ip": ip, "method": method, "path": path, "status": status, "who": who})
