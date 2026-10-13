# -*- coding: utf-8 -*-
"""任务栏右下角托盘图标 (pywin32 实现, 不需要额外依赖).

左键单击 / 双击: 打开主界面; 右键: 菜单. 提示文字每 3 秒刷新一次 (打印机是否在线).
"""
import os
import threading
import time

import win32api
import win32con
import win32gui

WM_TRAY = win32con.WM_USER + 20
WM_REFRESH = win32con.WM_USER + 21


class Tray:
    def __init__(self, icon_path, tip_fn, menu_fn, on_click):
        """tip_fn() -> 提示文字; menu_fn() -> [(文字, 动作, {"disabled"/"checked"/"default"}) 或 None(分隔线)]."""
        self.tip_fn, self.menu_fn, self.on_click = tip_fn, menu_fn, on_click
        self.actions = {}
        self.alive = True
        taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")  # 资源管理器重启后要重新加图标
        wc = win32gui.WNDCLASS()
        self.hinst = wc.hInstance = win32api.GetModuleHandle(None)
        wc.lpszClassName = "HanyinLabelTray"
        wc.lpfnWndProc = {taskbar_created: self._on_recreate, win32con.WM_DESTROY: self._on_destroy,
                          win32con.WM_COMMAND: self._on_command, WM_TRAY: self._on_notify, WM_REFRESH: self._on_refresh}
        atom = win32gui.RegisterClass(wc)
        self.hwnd = win32gui.CreateWindow(atom, "HanyinLabelTray", win32con.WS_OVERLAPPED, 0, 0, 0, 0, 0, 0,
                                          self.hinst, None)
        if icon_path and os.path.exists(icon_path):
            self.hicon = win32gui.LoadImage(self.hinst, icon_path, win32con.IMAGE_ICON, 0, 0,
                                            win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
        else:
            self.hicon = win32gui.LoadIcon(0, win32con.IDI_APPLICATION)
        self._add()

    def _nid(self, flags):
        return self.hwnd, 0, flags, WM_TRAY, self.hicon, self.tip_fn()[:127]

    def _add(self):
        flags = win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, self._nid(flags))
        except win32gui.error:
            pass  # 开机时任务栏可能还没起来, 等 TaskbarCreated 再加

    def _on_recreate(self, hwnd, msg, wparam, lparam):
        self._add()
        return 0

    def _on_refresh(self, hwnd, msg, wparam, lparam):
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, self._nid(win32gui.NIF_TIP))
        except win32gui.error:
            pass
        return 0

    def _on_notify(self, hwnd, msg, wparam, lparam):
        if lparam in (win32con.WM_LBUTTONUP, win32con.WM_LBUTTONDBLCLK):
            self.on_click()
        elif lparam == win32con.WM_RBUTTONUP:
            self._show_menu()
        return 0

    def build_menu(self, items=None):
        """按 menu_fn 的描述建菜单, 返回菜单句柄. 描述出错时退回只有"退出"的菜单, 保证总能退出."""
        try:
            items = self.menu_fn() if items is None else items
        except Exception:
            import traceback
            traceback.print_exc()
            items = [("退出", self.quit, set())]
        menu = win32gui.CreatePopupMenu()
        self.actions = {}
        default_id = None
        for i, item in enumerate(items, 1000):
            if item is None:
                win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")  # 分隔线也必须传字符串, 不能是 None
                continue
            text, action, flags = item
            f = win32con.MF_STRING
            if "disabled" in flags:
                f |= win32con.MF_GRAYED
            if "checked" in flags:
                f |= win32con.MF_CHECKED
            if "default" in flags:
                default_id = i
            win32gui.AppendMenu(menu, f, i, text)
            self.actions[i] = action
        if default_id is not None:
            win32gui.SetMenuDefaultItem(menu, default_id, 0)
        return menu

    def _show_menu(self):
        menu = self.build_menu()
        x, y = win32gui.GetCursorPos()
        try:
            win32gui.SetForegroundWindow(self.hwnd)  # 不加这句, 点菜单外面菜单不会消失
        except win32gui.error:
            pass  # 拿不到前台权限时菜单照样能弹出
        win32gui.TrackPopupMenu(menu, win32con.TPM_LEFTALIGN | win32con.TPM_BOTTOMALIGN, x, y, 0, self.hwnd, None)
        win32gui.PostMessage(self.hwnd, win32con.WM_NULL, 0, 0)
        win32gui.DestroyMenu(menu)

    def _on_command(self, hwnd, msg, wparam, lparam):
        action = self.actions.get(win32api.LOWORD(wparam))
        if action:
            action()
        return 0

    def _on_destroy(self, hwnd, msg, wparam, lparam):
        self.alive = False
        try:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        except win32gui.error:
            pass
        win32gui.PostQuitMessage(0)
        return 0

    def _refresher(self):
        while self.alive:
            time.sleep(3)
            if self.alive:
                win32gui.PostMessage(self.hwnd, WM_REFRESH, 0, 0)

    def run(self):
        """在主线程里跑消息循环, 直到 quit()."""
        threading.Thread(target=self._refresher, daemon=True).start()
        win32gui.PumpMessages()

    def quit(self):
        if self.alive:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
