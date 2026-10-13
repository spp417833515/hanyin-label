# -*- coding: utf-8 -*-
"""汉印标签打印服务: 界面 (web/) + HTTP API, 只用 Python 标准库起服务.

  pythonw server.py --open      启动服务并打开程序窗口 (已在运行则只打开窗口)
  python  server.py             前台启动, 日志打到控制台

鉴权规则:
  本机界面 (127.0.0.1 / localhost 同源访问) 不需要密钥, 可以管理模板、密钥、设置.
  其他调用方 (局域网设备、本机脚本、别的网页) 都要带密钥:
      Authorization: Bearer hm_xxx   或   X-API-Key: hm_xxx
  局域网访问默认关闭; 关闭时服务只监听 127.0.0.1.
"""
import argparse
import json
import logging
import logging.handlers
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import core

VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
RES = getattr(sys, "_MEIPASS", HERE)  # 打包成 exe 后资源在解包目录里
WEB = os.path.join(RES, "web")
ICON = os.path.join(WEB, "favicon.ico")
MAX_BODY = 2 * 1024 * 1024
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8", ".png": "image/png", ".svg": "image/svg+xml",
                 ".ico": "image/x-icon"}

log = logging.getLogger("label")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ---------------------------------------------------------------- 路由表

ROUTES = []


def route(method, pattern, local=False, public=False):
    """local: 只允许本机界面; public: 不需要密钥."""
    def deco(fn):
        ROUTES.append((method, re.compile(pattern), fn, local, public))
        return fn
    return deco


def lan_addresses():
    """默认网卡的地址排第一, 其后是其他网卡 (虚拟机 / WSL 网卡也会列出来)."""
    primary, ips = None, set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # 不发包, 只为拿到默认网卡地址
        primary = s.getsockname()[0]
        s.close()
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    ok = lambda ip: ip and not ip.startswith(("127.", "169.254.", "0."))
    return ([primary] if ok(primary) else []) + sorted(ip for ip in ips if ok(ip) and ip != primary)


def int_arg(v, default, lo, hi, what):
    try:
        v = int(v if v is not None else default)
    except (TypeError, ValueError):
        raise ApiError(400, f"{what}必须是整数")
    if not lo <= v <= hi:
        raise ApiError(400, f"{what}范围 {lo}~{hi}")
    return v


def get_template(ref):
    try:
        return core.find_template(ref)
    except KeyError as e:
        raise ApiError(404, str(e.args[0]))


@route("GET", r"/api/ping", public=True)
def api_ping(h, body, query):
    return {"app": "hanyin-label", "version": VERSION}


@route("GET", r"/api/status")
def api_status(h, body, query):
    s, pc = core.settings(), core.printer_config()
    mon = core.start_monitor()
    bt = dict(core.bt_state)
    can = bt["state"] != "fail" if s["channel"] == "bt" else mon.online
    return {
        "printer": {"model": "HPRT HM-T260L", "online": mon.online, "via": "bt" if s["channel"] == "bt" else "usb",
                    "can_print": can, "bt_port": pc["bt_port"], "bt": bt, "checked_at": mon.checked_at},
        "server": {"version": VERSION, "port": manager.port, "lan": s["lan"], "listen": manager.host,
                   "addresses": lan_addresses(), "channel": s["channel"]},
        "time": core.now_iso(),
    }


@route("GET", r"/api/presets")
def api_presets(h, body, query):
    return {"sizes": core.settings()["sizes"]}


@route("POST", r"/api/presets", local=True)
def api_presets_add(h, body, query):
    return {"sizes": core.add_size(body.get("size"))}


@route("DELETE", r"/api/presets/(?P<w>[\d.]+)x(?P<h>[\d.]+)", local=True)
def api_presets_del(h, body, query, w, h_):
    return {"sizes": core.remove_size([w, h_])}


def _with_vars(t):
    return {**t, "variables": core.template_vars(t)}


@route("GET", r"/api/templates")
def api_templates(h, body, query):
    return {"templates": [_with_vars(t) for t in core.list_templates()]}


@route("GET", r"/api/templates/(?P<ref>[^/]+)")
def api_template(h, body, query, ref):
    return {"template": _with_vars(get_template(ref))}


@route("POST", r"/api/templates", local=True)
def api_template_create(h, body, query):
    t = dict(body)
    t.pop("id", None)
    return {"template": _with_vars(core.save_template(t))}


@route("PUT", r"/api/templates/(?P<ref>[^/]+)", local=True)
def api_template_update(h, body, query, ref):
    try:
        return {"template": _with_vars(core.save_template(body, tid=ref))}
    except KeyError as e:
        raise ApiError(404, str(e.args[0]))


@route("DELETE", r"/api/templates/(?P<ref>[^/]+)", local=True)
def api_template_delete(h, body, query, ref):
    t = get_template(ref)
    core.delete_template(t["id"])
    return {"deleted": t["id"]}


def _template_from(body):
    if isinstance(body.get("template_obj"), dict):
        t = core.normalize_template(body["template_obj"], tid=body["template_obj"].get("id") or "preview")
        return t
    if not body.get("template"):
        raise ApiError(400, "缺少 template (模板 id 或名称)")
    return get_template(str(body["template"]))


@route("POST", r"/api/preview")
def api_preview(h, body, query):
    t = _template_from(body)
    fields = body.get("fields") or {}
    if "serial" in body:
        t = {**t, "serial": int_arg(body["serial"], 1, 0, 10 ** 9, "serial ")}
    return "image/png", core.render_png(t, fields, int_arg(body.get("scale"), 1, 1, 8, "scale "))


@route("GET", r"/api/preview")
def api_preview_get(h, body, query):
    q = {k: v[0] for k, v in query.items()}
    ref, scale = q.pop("template", None), q.pop("scale", None)
    return api_preview(h, {"template": ref, "fields": q, "scale": scale}, {})


@route("POST", r"/api/print")
def api_print(h, body, query):
    t = _template_from(body) if not body.get("template_obj") else None
    if t is None:
        raise ApiError(400, "打印请用已保存的模板 (template)")
    if isinstance(body.get("rows"), list):
        if not body["rows"]:
            raise ApiError(400, "rows 是空的")
        rows = [((r or {}).get("fields") or {}, int_arg((r or {}).get("copies"), 1, 1, core.MAX_LABELS, "copies "))
                for r in body["rows"]]
    else:
        rows = [(body.get("fields") or {}, int_arg(body.get("copies"), 1, 1, core.MAX_LABELS, "copies "))]
    return core.print_template(t, rows, body.get("channel"))


@route("POST", r"/api/print/text")
def api_print_text(h, body, query):
    lines = body.get("lines")
    if isinstance(lines, str):
        lines = lines.splitlines()
    if not isinstance(lines, list):
        raise ApiError(400, "lines 应为字符串数组")
    return core.print_text(lines, body.get("size") or [25, 15], body.get("qr"),
                           int_arg(body.get("copies"), 1, 1, core.MAX_LABELS, "copies "),
                           body.get("align", "center"), body.get("fields"), body.get("channel"))


@route("GET", r"/api/keys", local=True)
def api_keys(h, body, query):
    return {"keys": core.list_keys()}


@route("POST", r"/api/keys", local=True)
def api_key_create(h, body, query):
    return {"key": core.create_key(body.get("name"))}


@route("DELETE", r"/api/keys/(?P<kid>[0-9a-f]+)", local=True)
def api_key_delete(h, body, query, kid):
    try:
        core.delete_key(kid)
    except KeyError as e:
        raise ApiError(404, str(e.args[0]))
    return {"deleted": kid}


def _settings_view(s=None):
    s = s or core.settings()
    pc = core.printer_config()
    return {**s, "autostart": core.get_autostart(), "bt_port": pc["bt_port"], "queue": pc["queue"],
            "data_dir": core.DATA, "app_dir": os.path.dirname(sys.executable) if core.FROZEN else HERE,
            "version": VERSION, "installed": core.FROZEN}


@route("GET", r"/api/settings", local=True)
def api_settings(h, body, query):
    return {"settings": _settings_view()}


@route("POST", r"/api/settings", local=True)
def api_settings_update(h, body, query):
    old = core.settings()
    s = core.update_settings(**{k: body[k] for k in ("lan", "channel", "offset", "bt_port") if k in body})
    if "autostart" in body:
        core.set_autostart(bool(body["autostart"]))
    if s["lan"] != old["lan"]:
        manager.rebind(s["lan"])
    return {"settings": _settings_view(s), "restarting": s["lan"] != old["lan"]}


@route("GET", r"/api/printer/ports", local=True)
def api_ports(h, body, query):
    return {"ports": core.list_com_ports(), "current": core.printer_config()["bt_port"]}


@route("POST", r"/api/admin/open-data", local=True)
def api_open_data(h, body, query):
    os.makedirs(core.DATA, exist_ok=True)
    os.startfile(core.DATA)
    return {"opened": core.DATA}


@route("POST", r"/api/printer/bt-check", local=True)
def api_bt_check(h, body, query):
    return {"bt": core.check_bluetooth()}


def _float_arg(v, default, what):
    try:
        return float(v if v not in (None, "") else default)
    except (TypeError, ValueError):
        raise ApiError(400, f"{what}必须是数字")


@route("GET", r"/api/calibrate/preview", local=True)
def api_calibrate_preview(h, body, query):
    q = {k: v[0] for k, v in query.items()}
    size = [_float_arg(q.get("w"), 25, "w "), _float_arg(q.get("h"), 15, "h ")]
    offset = [_float_arg(q.get("x"), 0, "x "), _float_arg(q.get("y"), 0, "y ")]
    return "image/png", core.calibration_png(size, offset, int_arg(q.get("scale"), 3, 1, 8, "scale "))


@route("POST", r"/api/calibrate/print", local=True)
def api_calibrate_print(h, body, query):
    return core.print_calibration(body.get("size") or [25, 15], body.get("offset") or [0, 0],
                                  int_arg(body.get("copies"), 1, 1, 5, "copies "), body.get("channel"))


@route("POST", r"/api/printer/feed", local=True)
def api_printer_feed(h, body, query):
    return core.feed_label(body.get("channel"))


@route("GET", r"/api/printer/params", local=True)
def api_printer_params(h, body, query):
    return core.read_printer_params()


@route("POST", r"/api/printer/params", local=True)
def api_printer_params_set(h, body, query):
    return core.write_printer_params(body)


@route("POST", r"/api/printer/learn", local=True)
def api_printer_learn(h, body, query):
    return core.paper_learn()


@route("GET", r"/api/log", local=True)
def api_log(h, body, query):
    return {"calls": list(core.call_log)}


@route("POST", r"/api/admin/shutdown", local=True)
def api_shutdown(h, body, query):
    threading.Timer(0.3, manager.stop).start()
    return {"stopping": True}


# ---------------------------------------------------------------- 请求处理

def is_loopback(ip):
    return ip == "::1" or ip.startswith("127.") or ip == "::ffff:127.0.0.1"


class Handler(BaseHTTPRequestHandler):
    server_version = "HanyinLabel/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-API-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")

    def trusted_local(self):
        """本机界面: 回环地址 + Host 是 127.0.0.1/localhost + 没有跨站 Origin (防别的网页借浏览器调用)."""
        if not is_loopback(self.client_address[0]):
            return False
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in ("127.0.0.1", "localhost"):
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in (f"http://127.0.0.1:{manager.port}", f"http://localhost:{manager.port}")

    def api_key(self):
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return (self.headers.get("X-API-Key") or "").strip()

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ApiError(413, "请求太大")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(400, "请求体不是合法的 JSON (UTF-8)")
        if not isinstance(body, dict):
            raise ApiError(400, "请求体应为 JSON 对象")
        return body

    def send_body(self, status, ctype, data, api=True):
        self.send_response(status)
        if api:
            self.cors()
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, status, obj):
        self.send_body(status, "application/json; charset=utf-8", json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(WEB, name))
        if not full.startswith(WEB + os.sep) or not os.path.isfile(full):
            return self.send_body(404, "text/plain; charset=utf-8", "404".encode(), api=False)
        with open(full, "rb") as f:
            data = f.read()
        ctype = CONTENT_TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        self.send_body(200, ctype, data, api=False)

    def dispatch(self, method):
        u = urlparse(self.path)
        path = unquote(u.path)
        if not path.startswith("/api/"):
            if method != "GET":
                return self.send_body(405, "text/plain; charset=utf-8", b"405", api=False)
            return self.static(path)
        ip, status, who = self.client_address[0], 500, "本机界面"
        try:
            for m, rx, fn, local_only, public in ROUTES:
                mt = rx.fullmatch(path)
                if not mt or m != method:
                    continue
                trusted = self.trusted_local()
                if not trusted:
                    if local_only:
                        raise ApiError(403, "这个接口只允许在本机的打印程序界面里使用")
                    if not public:
                        if not is_loopback(ip) and not core.settings()["lan"]:
                            raise ApiError(403, "局域网访问未开启")
                        entry = core.verify_key(self.api_key())
                        if not entry:
                            who = "无效密钥"
                            raise ApiError(401, "缺少密钥或密钥无效 (Authorization: Bearer <密钥>)")
                        who = f"密钥「{entry['name']}」"
                body = self.read_json() if method in ("POST", "PUT") else {}
                kwargs = {(k + "_" if k == "h" else k): v for k, v in mt.groupdict().items()}
                result = fn(self, body, parse_qs(u.query), **kwargs)
                status = 200
                if isinstance(result, tuple):
                    self.send_body(200, *result)
                else:
                    self.send_json(200, {"ok": True, **result})
                return
            raise ApiError(404, f"没有这个接口: {method} {path}")
        except ApiError as e:
            status = e.status
            self.send_json(e.status, {"ok": False, "error": e.message})
        except KeyError as e:
            status = 404
            self.send_json(404, {"ok": False, "error": str(e.args[0])})
        except ValueError as e:
            status = 400
            self.send_json(400, {"ok": False, "error": str(e)})
        except core.PrinterError as e:
            status = 503
            self.send_json(503, {"ok": False, "error": str(e)})
        except Exception as e:
            log.error("处理 %s %s 出错:\n%s", method, path, traceback.format_exc())
            self.send_json(500, {"ok": False, "error": f"服务内部错误: {e}"})
        finally:
            if who != "本机界面" or path.startswith("/api/print"):
                core.log_call(ip, method, path, status, who)


# ---------------------------------------------------------------- 服务管理

class Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return  # 浏览器关掉了连接, 正常现象, 不记日志
        super().handle_error(request, client_address)


class Manager:
    def __init__(self, port):
        self.port, self.host, self.httpd = port, None, None
        self.stopped = threading.Event()
        self.on_stop = None  # 托盘注册的回调: 服务停了托盘也退出

    def start(self, lan):
        host = "0.0.0.0" if lan else "127.0.0.1"
        httpd = Server((host, self.port), Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.httpd, self.host = httpd, host
        log.info("监听 %s:%s", host, self.port)

    def rebind(self, lan):
        def work():
            time.sleep(0.4)  # 先让当前请求的响应发出去
            old = self.httpd
            old.shutdown()
            old.server_close()
            try:
                self.start(lan)
            except OSError as e:
                log.error("切换监听地址失败, 退回本机模式: %s", e)
                core.update_settings(lan=False)
                self.start(False)
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        if self.stopped.is_set():
            return
        self.stopped.set()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.on_stop:
            self.on_stop()


manager = None


def already_running(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "hanyin-label"
    except Exception:
        return False


def open_window(url):
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for exe in candidates:
        if os.path.isfile(exe):
            subprocess.Popen([exe, f"--app={url}", "--window-size=1400,900"])
            return
    webbrowser.open(url)


def message_box(text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "汉印标签打印", 0x10)
    except Exception:
        pass


def setup_logging(console):
    os.makedirs(core.DATA, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(os.path.join(core.DATA, "server.log"), maxBytes=1_000_000,
                                              backupCount=2, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(fh)
    log.setLevel(logging.INFO)
    if console:
        log.addHandler(logging.StreamHandler())
    if sys.stdout is None:  # pythonw 没有控制台
        sys.stdout = sys.stderr = open(os.path.join(core.DATA, "stdout.log"), "a", encoding="utf-8", buffering=1)


def tray_status_text():
    s = core.settings()
    if s["channel"] == "bt":
        return "蓝牙模式" + {"ok": " · 可以连接", "fail": " · 连不上"}.get(core.bt_state["state"], "")
    mon = core._monitor
    return "打印机已连接 (USB)" if mon and mon.online else "USB 未连接"


def run_tray(url):
    """主线程跑托盘消息循环; 关掉程序窗口后服务和 API 继续在后台工作."""
    import tray

    def toggle_autostart():
        on = core.set_autostart(not core.get_autostart())
        log.info("开机自动启动: %s", on)

    def quit_all():
        import ctypes
        if ctypes.windll.user32.MessageBoxW(None, "退出后，标签打印和局域网 API 都会停止。\n确定退出？",
                                            "汉印标签打印", 0x4 | 0x20 | 0x40000) == 6:  # 是/否 + 置顶
            manager.stop()

    def menu():
        s = core.settings()
        addrs = lan_addresses()
        api = f"API：局域网 {addrs[0]}:{manager.port}" if s["lan"] and addrs else "API：仅本机可用"
        return [("打开主界面", lambda: open_window(url), {"default"}),
                ("设置…", lambda: open_window(url + "#settings"), set()), None,
                (f"打印机：{tray_status_text()}", None, {"disabled"}),
                (api, None, {"disabled"}), None,
                ("开机自动启动", toggle_autostart, {"checked"} if core.get_autostart() else set()), None,
                ("退出", quit_all, set())]

    t = tray.Tray(ICON, lambda: f"汉印标签打印 · {tray_status_text()}", menu, lambda: open_window(url))
    manager.on_stop = t.quit
    t.run()


def main():
    global manager
    ap = argparse.ArgumentParser(description="汉印标签打印服务")
    ap.add_argument("--tray", action="store_true", help="只在托盘后台运行, 不打开窗口 (开机自动启动用)")
    ap.add_argument("--open", action="store_true", help="(旧参数, 现在默认就会打开窗口)")
    ap.add_argument("--no-tray", action="store_true", help="不显示托盘图标, 前台运行 (调试用)")
    ap.add_argument("--port", type=int, help="端口, 默认见 data/settings.json (8765)")
    args = ap.parse_args()

    if os.name == "nt" and os.environ.get("TZ"):
        # Windows 上残留的 TZ 环境变量会让 Python 算错本地时间 ({日期}/{时间} 会打错), 去掉后重新启动自己
        env = {k: v for k, v in os.environ.items() if k != "TZ"}
        cmd = [sys.executable] if core.FROZEN else [sys.executable, os.path.abspath(__file__)]
        subprocess.Popen([*cmd, *sys.argv[1:]], env=env, cwd=HERE)
        return

    setup_logging(console=sys.stdout is not None and args.no_tray)
    s = core.settings()
    port = args.port or s["port"]
    url = f"http://127.0.0.1:{port}/"
    if already_running(port):  # 已经在运行: 只打开窗口
        if not args.tray:
            open_window(url)
        return

    manager = Manager(port)
    try:
        manager.start(s["lan"])
    except OSError as e:
        msg = f"端口 {port} 被占用或无法监听: {e}"
        log.error(msg)
        message_box(msg)
        sys.exit(1)
    core.start_monitor()
    log.info("汉印标签打印服务 %s 已启动: %s (数据目录 %s)", VERSION, url, core.DATA)
    if not args.tray:
        open_window(url)
    if args.no_tray:
        print(f"汉印标签打印服务已启动: {url}  (Ctrl+C 停止)")
        try:
            while not manager.stopped.wait(1):
                pass
        except KeyboardInterrupt:
            manager.stop()
    else:
        run_tray(url)
        manager.stop()
    log.info("服务已停止")


if __name__ == "__main__":
    main()
