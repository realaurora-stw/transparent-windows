import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import ctypes
import ctypes.wintypes as wt
import signal
import atexit
import sys
import os
from typing import Dict

# -------------------------------------------------------------------------
# BACKEND: WIN32 API & LOGIC
# -------------------------------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Constants
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
LWA_ALPHA = 0x00000002

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
HWND_TOPMOST = ctypes.c_void_p(-1)
HWND_NOTOPMOST = ctypes.c_void_p(-2)

# Safe wintypes
HWND = getattr(wt, "HWND", ctypes.c_void_p)
DWORD = getattr(wt, "DWORD", ctypes.c_ulong)
BOOL = getattr(wt, "BOOL", ctypes.c_int)

# State Storage
# hwnd (int) -> {"orig_ex": int, "alpha": int, "passthrough": bool, "is_topmost": bool, "passthrough_locked": bool}
modified_windows: Dict[int, Dict] = {}

def safe_GetWindowLongPtr(hwnd_int, index=GWL_EXSTYLE):
    try:
        return user32.GetWindowLongW(ctypes.c_void_p(hwnd_int), index)
    except:
        return 0

def safe_SetWindowLongPtr(hwnd_int, index, new_value):
    try:
        return user32.SetWindowLongW(ctypes.c_void_p(hwnd_int), index, new_value)
    except:
        return 0

def set_window_alpha(hwnd_int, alpha_0_100):
    if not hwnd_int: return False
    
    if hwnd_int not in modified_windows:
        orig = safe_GetWindowLongPtr(hwnd_int, GWL_EXSTYLE)
        modified_windows[hwnd_int] = {"orig_ex": orig, "alpha": 255, "passthrough": False, "is_topmost": False, "passthrough_locked": False}

    cur_ex = safe_GetWindowLongPtr(hwnd_int, GWL_EXSTYLE)
    if not (cur_ex & WS_EX_LAYERED):
        safe_SetWindowLongPtr(hwnd_int, GWL_EXSTYLE, cur_ex | WS_EX_LAYERED)

    a_byte = int(max(0, min(100, int(alpha_0_100))) * 255 / 100)
    try:
        user32.SetLayeredWindowAttributes(ctypes.c_void_p(hwnd_int), 0, a_byte, LWA_ALPHA)
    except:
        return False
        
    modified_windows[hwnd_int]["alpha"] = a_byte

    # Force Topmost
    try:
        user32.SetWindowPos(ctypes.c_void_p(hwnd_int), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        modified_windows[hwnd_int]["is_topmost"] = True
    except:
        pass
    return True

def set_passthrough_for_hwnd(hwnd_int, enable=True, mark_locked=False):
    try:
        if hwnd_int not in modified_windows:
            orig = safe_GetWindowLongPtr(hwnd_int, GWL_EXSTYLE)
            modified_windows[hwnd_int] = {"orig_ex": orig, "alpha": 255, "passthrough": False, "is_topmost": False, "passthrough_locked": False}

        cur_ex = safe_GetWindowLongPtr(hwnd_int, GWL_EXSTYLE)
        
        if enable:
            if not (cur_ex & WS_EX_TRANSPARENT):
                safe_SetWindowLongPtr(hwnd_int, GWL_EXSTYLE, cur_ex | WS_EX_TRANSPARENT)
            
            modified_windows[hwnd_int]["passthrough"] = True
            if mark_locked:
                modified_windows[hwnd_int]["passthrough_locked"] = True
        else:
            if cur_ex & WS_EX_TRANSPARENT:
                safe_SetWindowLongPtr(hwnd_int, GWL_EXSTYLE, cur_ex & (~WS_EX_TRANSPARENT))
            
            modified_windows[hwnd_int]["passthrough"] = False
            modified_windows[hwnd_int]["passthrough_locked"] = False

        # Re-apply alpha just in case style change reset it
        info = modified_windows.get(hwnd_int)
        if info:
             user32.SetLayeredWindowAttributes(ctypes.c_void_p(hwnd_int), 0, info.get("alpha", 255), LWA_ALPHA)
        return True
    except:
        return False

def restore_window(hwnd_int):
    if hwnd_int in modified_windows:
        info = modified_windows[hwnd_int]
        try:
            user32.SetLayeredWindowAttributes(ctypes.c_void_p(hwnd_int), 0, 255, LWA_ALPHA)
            safe_SetWindowLongPtr(hwnd_int, GWL_EXSTYLE, info["orig_ex"])
            user32.SetWindowPos(ctypes.c_void_p(hwnd_int), HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except:
            pass
        del modified_windows[hwnd_int]

def restore_all():
    for h in list(modified_windows.keys()):
        restore_window(h)

# Cleanup Hooks
atexit.register(restore_all)
def signal_handler(signum, frame):
    restore_all()
    sys.exit(0)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# -------------------------------------------------------------------------
# UTILS
# -------------------------------------------------------------------------

def get_visible_windows():
    """Returns list of (hwnd, title) excluding system garbage."""
    EnumWindows = user32.EnumWindows
    EnumWindowsProc = ctypes.WINFUNCTYPE(BOOL, HWND, ctypes.c_void_p)
    IsWindowVisible = user32.IsWindowVisible
    GetWindowTextLength = user32.GetWindowTextLengthW
    GetWindowText = user32.GetWindowTextW
    
    wins = []
    
    @EnumWindowsProc
    def enum_proc(hwnd, lParam):
        if not IsWindowVisible(hwnd): return 1
        length = GetWindowTextLength(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buff, length + 1)
            title = buff.value
            # Filter out common junk
            if title not in ["Program Manager", "Settings", "Microsoft Text Input Application"]:
                 wins.append((hwnd, title))
        return 1
        
    EnumWindows(enum_proc, 0)
    return sorted(wins, key=lambda x: x[1].lower())

# -------------------------------------------------------------------------
# FRONTEND: MODERN UI (CustomTkinter)
# -------------------------------------------------------------------------

# Set Theme
ctk.set_appearance_mode("Dark") 
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # Window Config
        self.title("GhostWindow")
        self.geometry("500x650")
        self.resizable(False, False)
        
        # State
        self.windows_map = {} # title -> hwnd
        self.selected_hwnd = None
        self.poll_ms = 50
        self.ctrl_held = False
        self.backtick_prev = False
        self.programmatic_update = False # Prevent UI callbacks loop

        # --- LAYOUT ---
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1) # Content expands

        # 1. Header
        self.header_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, sticky="ew", pady=(20, 10), padx=20)
        
        self.lbl_title = ctk.CTkLabel(self.header_frame, text="GhostWindow", font=("Roboto Medium", 24))
        self.lbl_title.pack(side="left")
        
        self.lbl_ver = ctk.CTkLabel(self.header_frame, text="v2.0", text_color="gray", font=("Arial", 12))
        self.lbl_ver.pack(side="left", padx=10, pady=(8,0))

        # 2. Window Selection Card
        self.card_select = ctk.CTkFrame(self)
        self.card_select.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        
        ctk.CTkLabel(self.card_select, text="TARGET WINDOW", font=("Arial", 11, "bold"), text_color="#AAB0B5").pack(anchor="w", padx=15, pady=(15, 5))
        
        self.combo_var = ctk.StringVar(value="Select a window...")
        self.combo = ctk.CTkComboBox(self.card_select, variable=self.combo_var, command=self.on_window_select, width=300)
        self.combo.pack(fill="x", padx=15, pady=(0, 10))
        
        self.btn_refresh = ctk.CTkButton(self.card_select, text="Refresh List", command=self.refresh_windows, height=24, fg_color="transparent", border_width=1, text_color=("gray10", "#DCE4EE"))
        self.btn_refresh.pack(anchor="e", padx=15, pady=(0, 15))

        # 3. Controls Card
        self.card_controls = ctk.CTkFrame(self)
        self.card_controls.grid(row=2, column=0, sticky="new", padx=20, pady=10)
        
        # Slider Section
        ctk.CTkLabel(self.card_controls, text="OPACITY / VISIBILITY", font=("Arial", 11, "bold"), text_color="#AAB0B5").pack(anchor="w", padx=15, pady=(15, 5))
        
        self.slider_val_label = ctk.CTkLabel(self.card_controls, text="100%", font=("Arial", 20, "bold"))
        self.slider_val_label.pack(pady=(0, 5))
        
        self.slider = ctk.CTkSlider(self.card_controls, from_=10, to=100, number_of_steps=90, command=self.on_slider)
        self.slider.set(100)
        self.slider.pack(fill="x", padx=20, pady=(0, 20))

        self.sep = ctk.CTkProgressBar(self.card_controls, height=2, progress_color="#404040")
        self.sep.set(1) # full line
        self.sep.pack(fill="x", padx=0, pady=10)

        # Interaction Section
        ctk.CTkLabel(self.card_controls, text="INTERACTION MODE", font=("Arial", 11, "bold"), text_color="#AAB0B5").pack(anchor="w", padx=15, pady=(10, 5))

        # Switch: Locked Passthrough
        self.switch_lock_var = ctk.BooleanVar(value=False)
        self.switch_lock = ctk.CTkSwitch(self.card_controls, text="Click-Through Locked", variable=self.switch_lock_var, command=self.on_toggle_lock, font=("Arial", 13))
        self.switch_lock.pack(anchor="w", padx=15, pady=(5, 5))
        
        lbl_hint_1 = ctk.CTkLabel(self.card_controls, text="Shortcut: Press ` (Backtick) to toggle", font=("Arial", 10), text_color="gray")
        lbl_hint_1.pack(anchor="w", padx=54, pady=(0, 10))

        # Indicator: Temp Passthrough
        self.ctrl_indicator = ctk.CTkButton(self.card_controls, text="CTRL Key: Released", state="disabled", fg_color="transparent", border_width=1, border_color="#555", text_color="#888", width=200)
        self.ctrl_indicator.pack(anchor="w", padx=15, pady=(5, 0))
        
        lbl_hint_2 = ctk.CTkLabel(self.card_controls, text="Hold CTRL to temporarily click through transparent windows.", font=("Arial", 10), text_color="gray", wraplength=400, justify="left")
        lbl_hint_2.pack(anchor="w", padx=15, pady=(5, 20))

        # 4. Action Buttons
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=(10, 20))
        
        self.btn_restore = ctk.CTkButton(self.btn_frame, text="Restore Selected", fg_color="#C0392B", hover_color="#E74C3C", command=self.restore_current)
        self.btn_restore.pack(side="left", expand=True, fill="x", padx=(0, 5))

        self.btn_reset_all = ctk.CTkButton(self.btn_frame, text="Reset All & Exit", fg_color="#555", hover_color="#666", command=self.exit_app)
        self.btn_reset_all.pack(side="left", expand=True, fill="x", padx=(5, 0))

        # 5. Status Bar
        self.status_bar = ctk.CTkLabel(self, text="Ready.", text_color="gray", anchor="w", font=("Arial", 10))
        self.status_bar.grid(row=4, column=0, sticky="ew", padx=20, pady=(0, 10))

        # Init Data
        self.refresh_windows()
        
        # Start Poll Loop
        self.after(self.poll_ms, self.poll_inputs)

    def status(self, msg):
        self.status_bar.configure(text=msg)

    def refresh_windows(self):
        wins = get_visible_windows()
        self.windows_map = {f"{title}": hwnd for hwnd, title in wins}
        
        # Create display names (Handle duplicates if necessary, simpler here)
        display_names = list(self.windows_map.keys())
        
        if not display_names:
            display_names = ["No visible windows found"]
            self.combo.configure(state="disabled")
        else:
            self.combo.configure(state="normal")
            
        self.combo.configure(values=display_names)
        
        # Restore selection if exists
        if self.selected_hwnd:
            # Find title for hwnd
            found = False
            for title, h in self.windows_map.items():
                if h == self.selected_hwnd:
                    self.combo_var.set(title)
                    found = True
                    break
            if not found and display_names:
                self.combo_var.set(display_names[0])
                self.on_window_select(display_names[0])
        elif display_names:
             self.combo_var.set(display_names[0])
             self.on_window_select(display_names[0])

    def on_window_select(self, choice):
        if choice not in self.windows_map: return
        self.selected_hwnd = self.windows_map[choice]
        
        # Update UI to reflect window state if we already modified it
        self.programmatic_update = True
        if self.selected_hwnd in modified_windows:
            info = modified_windows[self.selected_hwnd]
            # Slider
            alpha_pct = int(info["alpha"] * 100 / 255)
            self.slider.set(alpha_pct)
            self.slider_val_label.configure(text=f"{alpha_pct}%")
            # Lock Switch
            is_locked = info.get("passthrough_locked", False)
            if is_locked:
                self.switch_lock.select()
            else:
                self.switch_lock.deselect()
        else:
            # Default state
            self.slider.set(100)
            self.slider_val_label.configure(text="100%")
            self.switch_lock.deselect()
        
        self.programmatic_update = False
        self.status(f"Target: {choice}")

    def on_slider(self, val):
        if not self.selected_hwnd: return
        val = int(val)
        self.slider_val_label.configure(text=f"{val}%")
        
        if set_window_alpha(self.selected_hwnd, val):
            self.status(f"Opacity set to {val}%")
        else:
            self.status("Failed to set opacity (System Window?)")

    def on_toggle_lock(self):
        if self.programmatic_update: return
        if not self.selected_hwnd: 
            self.switch_lock.deselect()
            return
            
        enable = self.switch_lock_var.get()
        if enable:
            set_passthrough_for_hwnd(self.selected_hwnd, enable=True, mark_locked=True)
            self.status("Click-through LOCKED ON for current window.")
        else:
            # When unlocking, check if CTRL is held to decide if we keep it semi-active
            should_be_active = self.ctrl_held
            set_passthrough_for_hwnd(self.selected_hwnd, enable=should_be_active, mark_locked=False)
            self.status("Click-through unlocked.")

    def restore_current(self):
        if self.selected_hwnd:
            restore_window(self.selected_hwnd)
            self.on_window_select(self.combo_var.get()) # Reset UI
            self.status("Restored original window state.")

    def exit_app(self):
        restore_all()
        self.destroy()
        sys.exit(0)

    # --- INPUT POLLING ---
    def poll_inputs(self):
        # 1. Check CTRL (Temporary Passthrough)
        try:
            vk_ctrl = 0x11
            # high-order bit set if down
            ctrl_down = bool(user32.GetAsyncKeyState(vk_ctrl) & 0x8000)
            
            if ctrl_down != self.ctrl_held:
                self.ctrl_held = ctrl_down
                self.update_ctrl_ui(ctrl_down)
                
                # Apply temporary passthrough to all modified windows that AREN'T locked
                for h, info in list(modified_windows.items()):
                    if not info.get("passthrough_locked"):
                        set_passthrough_for_hwnd(h, enable=ctrl_down)
        except:
            pass

        # 2. Check Backtick (Toggle Lock for Selection)
        try:
            vk_tick = 0xC0 # ` key
            tick_down = bool(user32.GetAsyncKeyState(vk_tick) & 0x8000)
            
            # Detect Rising Edge (Pressed now, wasn't before)
            if tick_down and not self.backtick_prev:
                if self.selected_hwnd:
                    # Toggle the UI switch, which triggers the logic via command
                    self.switch_lock.toggle() 
            
            self.backtick_prev = tick_down
        except:
            pass

        self.after(self.poll_ms, self.poll_inputs)

    def update_ctrl_ui(self, is_held):
        if is_held:
            self.ctrl_indicator.configure(text="CTRL Key: HELD (Passthrough Active)", fg_color="#2CC985", text_color="white", border_color="#2CC985")
        else:
            self.ctrl_indicator.configure(text="CTRL Key: Released", fg_color="transparent", text_color="#888", border_color="#555")

if __name__ == "__main__":
    try:
        app = App()
        app.mainloop()
    finally:
        restore_all()
