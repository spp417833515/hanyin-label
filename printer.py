# -*- coding: utf-8 -*-
"""汉印 HPRT HM-T260L 打印协议与连接通道.

协议是从 Windows 驱动 (HPRT HM-T260L, hprtmobile.inf 2.7.4.1) 的实际输出逆向得到的,
这台机器不认 TSPL. 一个打印任务的字节流:

    00 × 260                      唤醒 (打印机休眠时前面的字节会丢)
    1D 's' 'e' 't' 'p' 01         任务头
    每张标签:
        若干块  1D 76 30 30 xL xH yL yH  len(4 字节小端)  LZO1X 压缩数据
                (x = 每行字节数, 按 4 字节对齐; y = 本块行数, 驱动每块 10 行; 位 1 = 黑)
        12 0C                     本张结束, 走到下一张标签的起点

三个通道:
    driver  走 Windows 驱动 GDI 打印, 和「汉码」软件一样 (只能 USB)
    raw     自己生成上面的字节流, 经驱动队列原样发给 USB
    bt      同样的字节流, 发到蓝牙虚拟串口 (COM5)
"""
import ctypes
import struct
import time
from ctypes import wintypes

from PIL import Image

WAKE = b"\x00" * 260
JOB_HEADER = b"\x1dsetp\x01"
PAGE_END = b"\x12\x0c"
BAND_ROWS = 10


# ---------------------------------------------------------------- 编码

def lzo1x_literal(raw):
    """只含一段字面量的合法 LZO1X 流: 不压缩, 但固件能按 LZO1X 正常解开."""
    n = len(raw)
    if n < 4:
        raise ValueError("LZO 字面量段至少 4 字节")
    if n <= 238:
        head = bytes([17 + n])
    else:  # 0, k 个 0, b  →  长度 = 255k + 15 + b + 3
        r = n - 18
        k = (r - 1) // 255
        head = b"\x00" * (k + 1) + bytes([r - 255 * k])
    return head + raw + b"\x11\x00\x00"  # 11 00 00 = 结束标记


HEAD_DOTS = 384  # 打印头 48mm


def encode_page(img, offset=(0, 0)):
    """'1' 模式标签图 → 一页光栅数据 (含结尾 12 0C).

    offset 为整体右移/下移的点数, 可以是负数 (往左/往上, 移出去的部分裁掉).
    页高始终等于标签高度, 这样偏移不会影响打印机的走纸定位.
    """
    ox, oy = int(offset[0]), int(offset[1])
    width = min(HEAD_DOTS, img.width + max(0, ox))
    wbytes = (width + 31) // 32 * 4
    canvas = Image.new("1", (wbytes * 8, img.height), 1)
    canvas.paste(img.convert("1"), (ox, oy))
    data = bytes(b ^ 0xFF for b in canvas.tobytes())  # PIL: 1 = 白; 打印机: 1 = 黑
    out = bytearray()
    for y in range(0, canvas.height, BAND_ROWS):
        rows = min(BAND_ROWS, canvas.height - y)
        comp = lzo1x_literal(data[y * wbytes:(y + rows) * wbytes])
        out += b"\x1dv00" + struct.pack("<HHI", wbytes, rows, len(comp)) + comp
    return bytes(out) + PAGE_END


def encode_job(labels, offset=(0, 0)):
    """labels: [(图, 份数), ...] → 完整任务字节流."""
    pages = [encode_page(img, offset) for img, n in labels for _ in range(n)]
    return WAKE + JOB_HEADER + b"".join(pages)


def feed_job():
    """不打印内容, 只让打印机走到下一张标签的开头."""
    return WAKE + JOB_HEADER + PAGE_END


# ---------------------------------------------------------------- 解码 (自检 / 分析驱动输出用)

def lzo1x_decompress(src):
    """标准 LZO1X 解压 (照 lzo1x_d.ch 的状态机), 返回 (数据, 消耗字节数)."""
    out, ip = bytearray(), 0

    def back(pos, n):
        for _ in range(n):
            out.append(out[pos])
            pos += 1

    def ext(t, base):
        nonlocal ip
        while src[ip] == 0:
            t += 255
            ip += 1
        t += base + src[ip]
        ip += 1
        return t

    state, t = "loop", 0
    if src[0] > 17:
        t, ip = src[0] - 17, 1
        if t < 4:
            state = "match_next"
        else:
            out += src[ip:ip + t]
            ip += t
            state = "first_literal_run"
    while True:
        if state == "loop":
            t = src[ip]
            ip += 1
            if t >= 16:
                state = "match"
            else:
                if t == 0:
                    t = ext(0, 15)
                out += src[ip:ip + t + 3]
                ip += t + 3
                state = "first_literal_run"
        elif state == "first_literal_run":
            t = src[ip]
            ip += 1
            if t >= 16:
                state = "match"
            else:
                back(len(out) - 0x801 - (t >> 2) - (src[ip] << 2), 3)
                ip += 1
                state = "match_done"
        elif state == "match":
            if t >= 64:
                m = len(out) - 1 - ((t >> 2) & 7) - (src[ip] << 3)
                ip += 1
                back(m, (t >> 5) + 1)
            elif t >= 32:
                t = t & 31 or ext(0, 31)
                m = len(out) - 1 - ((src[ip] >> 2) + (src[ip + 1] << 6))
                ip += 2
                back(m, t + 2)
            elif t >= 16:
                m = len(out) - ((t & 8) << 11)
                t = t & 7 or ext(0, 7)
                m -= (src[ip] >> 2) + (src[ip + 1] << 6)
                ip += 2
                if m == len(out):
                    return bytes(out), ip
                back(m - 0x4000, t + 2)
            else:
                back(len(out) - 1 - (t >> 2) - (src[ip] << 2), 2)
                ip += 1
            state = "match_done"
        elif state == "match_done":
            t = src[ip - 2] & 3
            state = "loop" if t == 0 else "match_next"
        elif state == "match_next":
            out += src[ip:ip + t]
            ip += t
            t = src[ip]
            ip += 1
            state = "match"


def decode_job(data):
    """任务字节流 → 每页一张 '1' 模式图 (白底黑字)."""
    p = 0
    while data[p] == 0:
        p += 1
    if data[p:p + len(JOB_HEADER)] != JOB_HEADER:
        raise ValueError(f"任务头不对: {data[p:p + 8].hex(' ')}")
    p += len(JOB_HEADER)
    pages, rows, wbytes = [], bytearray(), 0
    while p < len(data):
        if data[p:p + 2] == PAGE_END:
            pages.append(Image.frombytes("1", (wbytes * 8, len(rows) // wbytes),
                                         bytes(b ^ 0xFF for b in rows)))
            rows, p = bytearray(), p + 2
            continue
        if data[p:p + 4] != b"\x1dv00":
            raise ValueError(f"偏移 {p} 处不是光栅块: {data[p:p + 8].hex(' ')}")
        wbytes, nrows, ln = struct.unpack_from("<HHI", data, p + 4)
        raw, used = lzo1x_decompress(data[p + 12:p + 12 + ln])
        if used != ln or len(raw) != wbytes * nrows:
            raise ValueError(f"偏移 {p} 处块长度不符")
        rows += raw
        p += 12 + ln
    return pages


# ---------------------------------------------------------------- 通道

def usb_present(usb_id):
    import win32com.client
    wmi = win32com.client.GetObject("winmgmts:")
    for dev in wmi.ExecQuery("SELECT DeviceID FROM Win32_PnPEntity WHERE DeviceID LIKE 'USBPRINT%'"):
        if usb_id.upper() in dev.DeviceID.upper():
            return True
    return False


def send_driver(queue, labels, size_mm, offset=(0, 0), title="Label"):
    """经 Windows 驱动 GDI 打印 (纸张设成标签尺寸, 每份一页), 和「汉码」走的是同一条路."""
    import win32con
    import win32gui
    import win32print
    import win32ui
    from PIL import ImageWin

    h = win32print.OpenPrinter(queue)
    try:
        dm = win32print.GetPrinter(h, 2)["pDevMode"]
        dm.PaperSize = 256  # 自定义纸张, 单位 0.1mm
        dm.PaperWidth, dm.PaperLength = round(size_mm[0] * 10), round(size_mm[1] * 10)
        dm.Orientation = 1
        dm.Copies = 1
        dm.Fields |= (win32con.DM_PAPERSIZE | win32con.DM_PAPERWIDTH | win32con.DM_PAPERLENGTH
                      | win32con.DM_ORIENTATION | win32con.DM_COPIES)
        win32print.DocumentProperties(0, h, queue, dm, dm, win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
    finally:
        win32print.ClosePrinter(h)

    hdc = win32gui.CreateDC("WINSPOOL", queue, dm)
    dc = win32ui.CreateDCFromHandle(hdc)
    try:
        dc.StartDoc(title)
        for img, n in labels:
            dib = ImageWin.Dib(img.convert("L"))
            box = (offset[0], offset[1], offset[0] + img.width, offset[1] + img.height)
            for _ in range(n):
                dc.StartPage()
                dib.draw(hdc, box)
                dc.EndPage()
        dc.EndDoc()
    finally:
        dc.DeleteDC()


def send_raw_spooler(queue, data, title="Label"):
    """字节流原样经驱动队列发给 USB (RAW 数据类型绕过驱动渲染)."""
    import win32print
    h = win32print.OpenPrinter(queue)
    try:
        win32print.StartDocPrinter(h, 1, (title, None, "RAW"))
        win32print.StartPagePrinter(h)
        win32print.WritePrinter(h, data)
        win32print.EndPagePrinter(h)
        win32print.EndDocPrinter(h)
    finally:
        win32print.ClosePrinter(h)


class COMMTIMEOUTS(ctypes.Structure):
    _fields_ = [(n, wintypes.DWORD) for n in (
        "ReadIntervalTimeout", "ReadTotalTimeoutMultiplier", "ReadTotalTimeoutConstant",
        "WriteTotalTimeoutMultiplier", "WriteTotalTimeoutConstant")]


class SerialPort:
    """最小的 Win32 串口封装 (蓝牙 SPP 虚拟串口, 波特率无意义)."""
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    _k32.ReadFile.argtypes = _k32.WriteFile.argtypes
    _k32.SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(COMMTIMEOUTS)]
    _k32.PurgeComm.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    ERRORS = {2: "端口不存在 (蓝牙配对被删除?)", 5: "端口被其他程序占用 (比如汉码软件正连着蓝牙)",
              121: "连接超时: 打印机没开机 / 休眠 / 不在附近 / 正被手机等其他设备连着"}

    def __init__(self, port):
        self.h = self._k32.CreateFileW("\\\\.\\" + port, 0xC0000000, 0, None, 3, 0, None)
        if self.h in (None, wintypes.HANDLE(-1).value):
            err = ctypes.get_last_error()
            raise OSError(err, self.ERRORS.get(err, f"打开 {port} 失败 (错误码 {err})"))
        self._k32.SetCommTimeouts(self.h, ctypes.byref(COMMTIMEOUTS(50, 0, 1500, 0, 30000)))

    def write(self, data):
        done = wintypes.DWORD()
        view = memoryview(data)
        while view:
            chunk = bytes(view[:4096])
            if not self._k32.WriteFile(self.h, chunk, len(chunk), ctypes.byref(done), None) or not done.value:
                raise OSError(ctypes.get_last_error(), "蓝牙发送失败")
            view = view[done.value:]
        self._k32.FlushFileBuffers(self.h)

    def read(self, total=3.0, quiet=0.5):
        """读应答: 收到数据后 quiet 秒没有新数据就返回, 最多等 total 秒."""
        out, t0, last = b"", time.time(), time.time()
        buf, got = ctypes.create_string_buffer(512), wintypes.DWORD()
        while time.time() - t0 < total:
            self._k32.ReadFile(self.h, buf, 512, ctypes.byref(got), None)  # 每次最多阻塞 1.5 秒
            if got.value:
                out += buf.raw[:got.value]
                last = time.time()
            elif out and time.time() - last > quiet:
                break
        return out

    def purge(self):
        self._k32.PurgeComm(self.h, 0x0008)  # PURGE_RXCLEAR

    def close(self):
        time.sleep(0.3)  # 给蓝牙栈时间把尾巴发完
        self._k32.CloseHandle(self.h)


def send_bt(port, data):
    sp = SerialPort(port)
    try:
        sp.write(data)
    finally:
        sp.close()


# ---------------------------------------------------------------- 打印机参数 (HPRT "ESC FS & V1" 指令, 经蓝牙双向读写)
# 参数编号/长度来自汉码软件里 HM-T260L 的机型配置 (Config/DeviceKey.json), 已实测读写:
#   getkey: ESC FS "& V1 getkey\r\n" 01 <编号 2 字节小端> <长度 1 字节>          → 应答 <值>
#   setkey: ESC FS "& V1 setkey\r\n" 01 <编号 2 字节小端> <长度 1 字节> <值>      → 应答 <编号 2 字节> 00
#   do "save_param_zone" 把参数写进掉电保存区                                       → 应答 00

V1 = b"\x1b\x1c& V1 "


def v1_getkey(sp, key, size):
    sp.purge()
    sp.write(V1 + b"getkey\r\n\x01" + key.to_bytes(2, "little") + bytes([size]))
    r = sp.read()
    if len(r) < size:
        raise OSError(0, f"读取参数 {key} 没有应答")
    return r[:size]


def v1_setkey(sp, key, size, value):
    sp.purge()
    sp.write(V1 + b"setkey\r\n\x01" + key.to_bytes(2, "little") + bytes([size]) + value.to_bytes(size, "little"))
    r = sp.read(total=2.5)
    if r[:3] != key.to_bytes(2, "little") + b"\x00":
        raise OSError(0, f"写参数 {key} 失败 (应答 {r.hex(' ') or '无'})")


def v1_do(sp, action, wait=2.5):
    sp.purge()
    sp.write(V1 + f'do "{action}"\r\n'.encode("ascii"))
    return sp.read(total=wait)
