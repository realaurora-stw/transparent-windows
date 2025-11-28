# window_alpha_dropdown_with_cleanup.py
# Single-file Windows tool (Python 3.8+) to set top-level window opacity via dropdown.
# Feature: Press ` (backtick) to toggle persistent "pass-through" (click-through) mode
# for the currently selected window. Press ` again to toggle it off.
#
# Behavior:
#  - Applying opacity forces the affected window TOPMOST (best-effort).
#  - Hold CTRL (left or right) to enable temporary click-through (passthrough) on affected windows.
#  - Press ` to toggle persistent passthrough (per-selected-window).
#  - If the script exits (GUI close, Ctrl+C, console close, system shutdown), it will attempt to restore
#    all modified windows (remove layered/transparent flags and remove TOPMOST).
#
# Usage: Run on Windows 11/10 with Python 3.8+. Run from a console to be able to Ctrl+C.

import ctypes
import ctypes.wintypes as wt
import tkinter as tk
from tkinter import ttk, messagebox
import signal
import atexit
import sys
import os
from typing import Dict

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Constants
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x00000002

# SetWindowPos flags and HWNDs
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = ctypes.c_void_p(-1)  # (HWND)-1
HWND_NOTOPMOST = ctypes.c_void_p(-2)  # (HWND)-2

# Safe wintype fallbacks
HWND = getattr(wt, "HWND", ctypes.c_void_p)
DWORD = getattr(wt, "DWORD", ctypes.c_ulong)
LONG = getattr(wt, "LONG", ctypes.c_long)
UINT = getattr(wt, "UINT", ctypes.c_uint)
LPARAM = getattr(wt, "LPARAM", ctypes.c_void_p)
BOOL = getattr(wt, "BOOL", ctypes.c_int)

# Win32 functions
EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(BOOL, HWND, LPARAM)
IsWindowVisible = user32.IsWindowVisible
GetWindowTextLength = user32.GetWindowTextLengthW
GetWindowText = user32.GetWindowTextW
GetClassName = user32.GetClassNameW
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
SetWindowLongPtr = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
GetWindowLongPtr = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
SetLayeredWindowAttributes = user32.SetLayeredWindowAttributes
GetAncestor = user32.GetAncestor
SetWindowPos = user32.SetWindowPos
GetAsyncKeyState = user32.GetAsyncKeyState
GA_ROOT = 2

# VK codes
VK_CONTROL = 0x11
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_OEM_3 = 0xC0  # Backtick/tilde key on US keyboards; may vary on other layouts

# Storage for windows we modify so we can restore later
# hwnd (int) -> {"orig_ex": int, "alpha": int (0..255), "passthrough": bool, "is_topmost": bool, "passthrough_locked": bool}
modified_windows: Dict[int, Dict] = {}

def hwnd_to_int(hwnd):
    try:
        return int(hwnd)
    except Exception:
        try:
            return ctypes.cast(hwnd, ctypes.c_void_p).value
        except Exception:
            return 0

def get_window_text(hwnd):
    try:
        length = GetWindowTextLength(hwnd)
        if length == 0:
            return ""
        buff = ctypes.create_unicode_buffer(length + 1)
        GetWindowText(hwnd, buff, length + 1)
        return buff.value
    except Exception:
        return ""

def get_class_name(hwnd):
    try:
        buff = ctypes.create_unicode_buffer(256)
        GetClassName(hwnd, buff, 256)
        return buff.value
    except Exception:
        return ""

def enum_top_level_windows():
    """Return list of tuples (hwnd_int, title, class_name) for visible top-level windows."""
    windows = []

    @EnumWindowsProc
    def _enum_proc(hwnd, lParam):
        try:
            if not IsWindowVisible(hwnd):
                return 1  # continue
            try:
                top = GetAncestor(hwnd, GA_ROOT)
                if top:
                    hwnd_use = top
                else:
                    hwnd_use = hwnd
            except Exception:
                hwnd_use = hwnd

            hid = hwnd_to_int(hwnd_use)
            if hid == 0:
                return 1

            title = get_window_text(hwnd_use).strip()
            cls = get_class_name(hwnd_use)
            windows.append((hid, title, cls))
        except Exception:
            pass
        return 1  # continue enumeration

    EnumWindows(_enum_proc, 0)
    seen = set()
    unique = []
    for h, t, c in windows:
        if h not in seen:
            seen.add(h)
            unique.append((h, t, c))
    return unique

def safe_GetWindowLongPtr(hwnd_int, index=GWL_EXSTYLE):
    try:
        return GetWindowLongPtr(ctypes.c_void_p(hwnd_int), index)
    except Exception:
        try:
            return GetWindowLongPtr(hwnd_int, index)
        except Exception:
            return 0

def safe_SetWindowLongPtr(hwnd_int, index, new_value):
    try:
        return SetWindowLongPtr(ctypes.c_void_p(hwnd_int), index, new_value)
    except Exception:
        try:
            return SetWindowLongPtr(hwnd_int, index, new_value)
        except Exception:
            return 0

def set_window_alpha(hwnd_int, alpha_0_100):
    """Apply layered alpha to top-level window and make it topmost.
    alpha_0_100 is 0..100 (0 transparent, 100 opaque)."""
    if not hwnd_int:
        return False
    # store original exstyle if first time
    if hwnd_int not in modified_windows:
        try:
            orig_ex = safe_GetWindowLongPtr(hwnd_int, GWL_EXSTYLE)
        except Exception:
            orig_ex = 0
        modified_windows[hwnd_int] = {"orig_ex": orig_ex, "alpha": 255, "passthrough": False, "is_topmost": False, "passthrough_locked": False}

    # ensure layered style
    cur_ex = safe_GetWindowLongPtr(hwnd_int, GWL_EXSTYLE)
    if not (cur_ex & WS_EX_LAYERED):
        new_ex = cur_ex | WS_EX_LAYERED
        safe_SetWindowLongPtr(hwnd_int, GWL_EXSTYLE, new_ex)

    a_byte = int(max(0, min(100, int(alpha_0_100))) * 255 / 100)
    try:
        res = SetLayeredWindowAttributes(ctypes.c_void_p(hwnd_int), 0, a_byte, LWA_ALPHA)
    except Exception:
        res = 0
    modified_windows[hwnd_int]["alpha"] = a_byte

    # Make topmost (best-effort). Mark as topmost in our storage.
    try:
        SetWindowPos(ctypes.c_void_p(hwnd_int), HWND_TOPMOST,
                     0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        modified_windows[hwnd_int]["is_topmost"] = True
    except Exception:
        modified_windows[hwnd_int]["is_topmost"] = False

    return bool(res)

def set_passthrough_for_hwnd(hwnd_int, enable=True, mark_locked=False):
    """Temporarily add/remove WS_EX_TRANSPARENT to allow clicks through.
       If the window wasn't tracked before, create a modified_windows entry to allow later restoration.
       mark_locked is used when enabling 'persistent' passthrough - it sets passthrough_locked flag.
    """
    try:
        # ensure we have an entry
        if hwnd_int not in modified_windows:
            try:
                orig_ex = safe_GetWindowLongPtr(hwnd_int, GWL_EXSTYLE)
            except Exception:
                orig_ex = 0
            modified_windows[hwnd_int] = {"orig_ex": orig_ex, "alpha": 255, "passthrough": False, "is_topmost": False, "passthrough_locked": False}

        cur_ex = safe_GetWindowLongPtr(hwnd_int, GWL_EXSTYLE)
        if enable:
            if cur_ex & WS_EX_TRANSPARENT:
                # already enabled; still mark flags
                modified_windows[hwnd_int]["passthrough"] = True
                if mark_locked:
                    modified_windows[hwnd_int]["passthrough_locked"] = True
                return True
            new_ex = cur_ex | WS_EX_TRANSPARENT
            safe_SetWindowLongPtr(hwnd_int, GWL_EXSTYLE, new_ex)
            modified_windows[hwnd_int]["passthrough"] = True
            if mark_locked:
                modified_windows[hwnd_int]["passthrough_locked"] = True
            # Reapply layered alpha in case changing styles nuked it
            info = modified_windows.get(hwnd_int)
            if info:
                try:
                    SetLayeredWindowAttributes(ctypes.c_void_p(hwnd_int), 0, info.get("alpha", 255), LWA_ALPHA)
                except Exception:
                    pass
            return True
        else:
            if not (cur_ex & WS_EX_TRANSPARENT):
                # already disabled
                modified_windows[hwnd_int]["passthrough"] = False
                modified_windows[hwnd_int]["passthrough_locked"] = False
                return True
            new_ex = cur_ex & (~WS_EX_TRANSPARENT)
            safe_SetWindowLongPtr(hwnd_int, GWL_EXSTYLE, new_ex)
            modified_windows[hwnd_int]["passthrough"] = False
            modified_windows[hwnd_int]["passthrough_locked"] = False
            # Reapply layered alpha
            info = modified_windows.get(hwnd_int)
            if info:
                try:
                    SetLayeredWindowAttributes(ctypes.c_void_p(hwnd_int), 0, info.get("alpha", 255), LWA_ALPHA)
                except Exception:
                    pass
            return True
    except Exception:
        return False

def remove_topmost(hwnd_int):
    try:
        SetWindowPos(ctypes.c_void_p(hwnd_int), HWND_NOTOPMOST,
                     0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        return True
    except Exception:
        return False

def restore_window(hwnd_int):
    if hwnd_int in modified_windows:
        info = modified_windows[hwnd_int]
        try:
            # restore alpha to 100%
            SetLayeredWindowAttributes(ctypes.c_void_p(hwnd_int), 0, 255, LWA_ALPHA)
        except Exception:
            pass
        try:
            safe_SetWindowLongPtr(hwnd_int, GWL_EXSTYLE, info["orig_ex"])
        except Exception:
            pass
        # remove topmost (best-effort)
        try:
            remove_topmost(hwnd_int)
        except Exception:
            pass
        del modified_windows[hwnd_int]

def restore_all():
    # Best-effort restore of everything we've changed. Safe to call many times.
    for h in list(modified_windows.keys()):
        try:
            restore_window(h)
        except Exception:
            pass

# ---------- Cleanup / Signal handling ----------
# atexit fallback: always try to restore
atexit.register(restore_all)

# Signal handler for SIGINT / SIGTERM (Ctrl+C from console)
def _py_signal_handler(signum, frame):
    try:
        restore_all()
    except Exception:
        pass
    # be polite: exit
    try:
        sys.exit(0)
    except Exception:
        os._exit(0)

# Register Python-level signals
try:
    signal.signal(signal.SIGINT, _py_signal_handler)
except Exception:
    pass
try:
    signal.signal(signal.SIGTERM, _py_signal_handler)
except Exception:
    pass

# Windows console control handler (so console close / shutdown also triggers restore)
# Prototype: BOOL HandlerRoutine(DWORD dwCtrlType)
try:
    PHANDLER_ROUTINE = ctypes.WINFUNCTYPE(BOOL, DWORD)
    def _console_handler(dwCtrlType):
        # dwCtrlType: CTRL_C_EVENT=0, CTRL_BREAK_EVENT=1, CTRL_CLOSE_EVENT=2, CTRL_LOGOFF_EVENT=5, CTRL_SHUTDOWN_EVENT=6
        try:
            restore_all()
        except Exception:
            pass
        # Return True to indicate we've handled it (prevents default immediate termination so we can cleanup)
        return True
    # Keep a reference so it won't be GC'd
    console_handler_ref = PHANDLER_ROUTINE(_console_handler)
    try:
        kernel32.SetConsoleCtrlHandler(console_handler_ref, True)
    except Exception:
        # best-effort; not fatal if it fails
        console_handler_ref = None
except Exception:
    console_handler_ref = None

# ---------- GUI ----------
class App:
    POLL_MS = 60  # poll interval for CTRL state & window list refresh tasks

    def __init__(self, root):
        self.root = root
        root.title("Window Opacity — Dropdown selector (Topmost + CTRL passthrough + ` toggle)")
        root.geometry("720x360")
        root.resizable(False, False)

        frm = ttk.Frame(root, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        top_row = ttk.Frame(frm)
        top_row.pack(fill=tk.X)

        self.refresh_btn = ttk.Button(top_row, text="Refresh window list", command=self.refresh_windows)
        self.refresh_btn.pack(side=tk.LEFT)

        self.help_label = ttk.Label(top_row, text="Select a top-level window, control opacity. Affected windows become TOPMOST.")
        self.help_label.pack(side=tk.LEFT, padx=(8,0))

        info_row = ttk.Frame(frm)
        info_row.pack(fill=tk.X, pady=(8,0))
        ttk.Label(info_row, text="Hold CTRL (Left or Right) to temporarily pass mouse events through affected windows. Press ` to toggle persistent passthrough on the selected window.").pack(anchor=tk.W)

        # Combobox (dropdown)
        combo_row = ttk.Frame(frm)
        combo_row.pack(fill=tk.X, pady=(10,0))
        ttk.Label(combo_row, text="Open windows:").pack(anchor=tk.W)
        self.combo_var = tk.StringVar()
        self.combo = ttk.Combobox(combo_row, textvariable=self.combo_var, state="readonly", width=105)
        self.combo.pack(fill=tk.X)
        self.windows_list = []  # list of tuples (hwnd_int, title, class)
        self.combo.bind("<<ComboboxSelected>>", self.on_select)

        # Slider
        self.alpha_var = tk.IntVar(value=100)
        slider_row = ttk.Frame(frm)
        slider_row.pack(fill=tk.X, pady=(12,0))
        ttk.Label(slider_row, text="Opacity:").pack(anchor=tk.W)
        self.alpha_scale = ttk.Scale(slider_row, from_=0, to=100, orient=tk.HORIZONTAL,
                                     variable=self.alpha_var, command=self.on_scale)
        self.alpha_scale.pack(fill=tk.X)
        self.alpha_display = ttk.Label(slider_row, text="100%")
        self.alpha_display.pack(anchor=tk.E, pady=(4,0))

        # Buttons
        btn_row = ttk.Frame(frm)
        btn_row.pack(fill=tk.X, pady=(12,0))
        self.apply_btn = ttk.Button(btn_row, text="Apply to Selected (Topmost)", command=self.apply_to_selected)
        self.apply_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0,6))
        self.restore_btn = ttk.Button(btn_row, text="Restore Selected", command=self.restore_selected)
        self.restore_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0,6))
        self.restore_all_btn = ttk.Button(btn_row, text="Restore All", command=self.restore_all_click)
        self.restore_all_btn.pack(side=tk.LEFT, expand=True, fill=tk.X)

        exit_row = ttk.Frame(frm)
        exit_row.pack(fill=tk.X, pady=(10,0))
        self.exit_btn = ttk.Button(exit_row, text="Restore & Exit", command=self.exit_app)
        self.exit_btn.pack(fill=tk.X)

        # status
        self.status = ttk.Label(frm, text="", foreground="gray")
        self.status.pack(pady=(8,0))

        # CTRL state indicator
        ctrl_row = ttk.Frame(frm)
        ctrl_row.pack(fill=tk.X, pady=(6,0))
        self.ctrl_state_label = ttk.Label(ctrl_row, text="CTRL passthrough: OFF", foreground="red")
        self.ctrl_state_label.pack(anchor=tk.W)

        # Backtick (persistent passthrough) indicator
        backtick_row = ttk.Frame(frm)
        backtick_row.pack(fill=tk.X, pady=(4,0))
        self.backtick_state_label = ttk.Label(backtick_row, text="` passthrough (selected): OFF", foreground="red")
        self.backtick_state_label.pack(anchor=tk.W)

        # internal ctrl/backtick held flags & prev states for edge detection
        self.ctrl_held = False
        self.backtick_prev = False  # previous polled state for backtick key

        # initial populate and start polling
        self.root.after(100, self.refresh_windows)
        self.root.after(self.POLL_MS, self._poll_inputs)

        # Also bind the backtick to GUI when focused (nice to have)
        try:
            root.bind_all('`', lambda ev: self._toggle_backtick_for_selected())
        except Exception:
            pass

    def refresh_windows(self):
        try:
            wins = enum_top_level_windows()
            entries = []
            self.windows_list = []
            for hwnd, title, cls in wins:
                display_title = title if title else "<no title>"
                entry = f"{hwnd} — {display_title} ({cls})"
                entries.append(entry)
                self.windows_list.append((hwnd, display_title, cls))
            if not entries:
                entries = ["<no windows found>"]
                self.combo.config(values=entries)
                self.combo_var.set(entries[0])
                self.status.config(text="No top-level visible windows detected.")
            else:
                self.combo.config(values=entries)
                # keep previous selection if possible
                if not self.combo_var.get() and entries:
                    self.combo_var.set(entries[0])
                elif self.combo_var.get() not in entries:
                    self.combo_var.set(entries[0])
                self.status.config(text=f"Found {len(entries)} windows.")
        except Exception as e:
            self.status.config(text=f"Error enumerating windows: {e}")

    def on_select(self, _ev=None):
        sel = self.combo.current()
        if sel < 0 or sel >= len(self.windows_list):
            return
        hwnd = self.windows_list[sel][0]
        info = modified_windows.get(hwnd)
        if info:
            a = info.get("alpha", 255)
            pct = int(a * 100 / 255)
            self.alpha_var.set(pct)
            self.alpha_display.config(text=f"{pct}%")
            # update backtick label for this selection
            locked = info.get("passthrough_locked", False)
            if locked:
                self.backtick_state_label.config(text=f"` passthrough (selected): ON (locked)", foreground="green")
            else:
                self.backtick_state_label.config(text=f"` passthrough (selected): OFF", foreground="red")
        else:
            self.alpha_var.set(100)
            self.alpha_display.config(text="100%")
            self.backtick_state_label.config(text=f"` passthrough (selected): OFF", foreground="red")

    def on_scale(self, _ev=None):
        val = int(self.alpha_var.get())
        self.alpha_display.config(text=f"{val}%")

    def apply_to_selected(self):
        sel = self.combo.current()
        if sel < 0 or sel >= len(self.windows_list):
            messagebox.showinfo("No selection", "Please select a window from the dropdown.")
            return
        hwnd = self.windows_list[sel][0]
        val = int(self.alpha_var.get())
        ok = set_window_alpha(hwnd, val)
        if ok:
            self.status.config(text=f"Applied {val}% opacity to HWND {hwnd} and forced TOPMOST.")
        else:
            self.status.config(text=f"Attempted apply to HWND {hwnd}. Some apps ignore layered alpha.")

    def restore_selected(self):
        sel = self.combo.current()
        if sel < 0 or sel >= len(self.windows_list):
            messagebox.showinfo("No selection", "Please select a window from the dropdown.")
            return
        hwnd = self.windows_list[sel][0]
        restore_window(hwnd)
        self.status.config(text=f"Restored HWND {hwnd} (best-effort).")
        # update UI labels
        self.on_select()

    def restore_all_click(self):
        restore_all()
        self.status.config(text="Restored all modified windows.")
        # update UI labels
        self.on_select()

    def exit_app(self):
        restore_all()
        # If running from console, this will allow the main thread to exit
        try:
            self.root.quit()
        except Exception:
            pass

    def _is_ctrl_held(self):
        try:
            s1 = GetAsyncKeyState(VK_CONTROL) & 0x8000
            s2 = GetAsyncKeyState(VK_LCONTROL) & 0x8000
            s3 = GetAsyncKeyState(VK_RCONTROL) & 0x8000
            return bool(s1 or s2 or s3)
        except Exception:
            return False

    def _poll_inputs(self):
        try:
            # CTRL behavior (transient passthrough)
            pressed = self._is_ctrl_held()
            if pressed != self.ctrl_held:
                # state changed -> toggle passthrough on all modified windows that are NOT locked
                self.ctrl_held = pressed
                if pressed:
                    # enable passthrough on all modified windows that are not locked
                    for hwnd, info in list(modified_windows.items()):
                        try:
                            if info.get("passthrough_locked"):
                                continue
                            set_passthrough_for_hwnd(hwnd, enable=True)
                        except Exception:
                            pass
                    self.ctrl_state_label.config(text="CTRL passthrough: ON  (clicks go to windows behind)", foreground="green")
                    self.status.config(text="CTRL held: passthrough enabled for affected windows (except locked ones).")
                else:
                    # disable passthrough on all modified windows that are not locked
                    for hwnd, info in list(modified_windows.items()):
                        try:
                            if info.get("passthrough_locked"):
                                continue
                            set_passthrough_for_hwnd(hwnd, enable=False)
                        except Exception:
                            pass
                    self.ctrl_state_label.config(text="CTRL passthrough: OFF", foreground="red")
                    self.status.config(text="CTRL released: passthrough disabled for affected windows (except locked ones).")

            # Backtick key edge-detection for persistent toggle (global best-effort)
            try:
                cur_b = bool(GetAsyncKeyState(VK_OEM_3) & 0x8000)
            except Exception:
                cur_b = False
            if cur_b and not self.backtick_prev:
                # rising edge -> toggle persistent passthrough for selected window
                self._toggle_backtick_for_selected()
            self.backtick_prev = cur_b
        except Exception:
            pass
        # continue polling
        self.root.after(self.POLL_MS, self._poll_inputs)

    def _toggle_backtick_for_selected(self):
        """Toggle persistent passthrough (passthrough_locked) for the currently selected window."""
        sel = self.combo.current()
        if sel < 0 or sel >= len(self.windows_list):
            self.status.config(text="No window selected to toggle persistent passthrough.")
            return
        hwnd = self.windows_list[sel][0]
        info = modified_windows.get(hwnd)
        if info and info.get("passthrough_locked"):
            # currently locked -> unlock
            # Determine desired runtime passthrough state after unlocking (based on current ctrl state)
            desired_enable = self.ctrl_held
            ok = set_passthrough_for_hwnd(hwnd, enable=desired_enable, mark_locked=False)
            if ok:
                self.status.config(text=f"Persistent passthrough: OFF for HWND {hwnd}. (Now {'ON' if desired_enable else 'OFF'} due to CTRL state).")
                self.backtick_state_label.config(text=f"` passthrough (selected): OFF", foreground="red")
            else:
                self.status.config(text=f"Failed to disable persistent passthrough for HWND {hwnd}.")
        else:
            # enable persistent passthrough
            ok = set_passthrough_for_hwnd(hwnd, enable=True, mark_locked=True)
            if ok:
                self.status.config(text=f"Persistent passthrough: ON for HWND {hwnd}. (Window is click-through.)")
                self.backtick_state_label.config(text=f"` passthrough (selected): ON (locked)", foreground="green")
            else:
                self.status.config(text=f"Failed to enable persistent passthrough for HWND {hwnd}.")

def main():
    root = tk.Tk()
    style = ttk.Style(root)
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.exit_app)
    try:
        root.mainloop()
    finally:
        # Ensure cleanup on mainloop exit (double-safety)
        restore_all()

if __name__ == "__main__":
    main()
