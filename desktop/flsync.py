#!/usr/bin/env python3
"""FL Phone Sync - desktop app.

For users: open it, click "Sign in with Google", done. It keeps running in the
background and starts with the computer. Add --minimized to start hidden.
"""
import os
import queue
import sys
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import segno

import config
from engine import APP_NAME, QUALITY, Engine, resolve_fl_exe, set_autostart

MAX_LOG_LINES = 300


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_NAME)
        root.geometry("560x640")
        root.minsize(480, 520)
        self.ui = queue.Queue()
        self.email = None
        self.engine = Engine(log=lambda m: self.ui.put(("log", m)),
                             status=lambda m: self.ui.put(("status", m)),
                             account=lambda e: self.ui.put(("account", e)))
        self._build()
        self._show_account(None)
        self.engine.start()
        self._poll()
        root.protocol("WM_DELETE_WINDOW", self._hide)

    # ------------------------------------------------------------ layout --
    def _build(self):
        style = ttk.Style()
        style.configure("Big.TLabel", font=("TkDefaultFont", 20, "bold"))
        style.configure("Status.TLabel", font=("TkDefaultFont", 12, "bold"))
        style.configure("Accent.TButton", font=("TkDefaultFont", 12, "bold"), padding=(18, 10))

        self.body = ttk.Frame(self.root, padding=20)
        self.body.pack(fill="both", expand=True)

        # --- signed out: one button, nothing else
        self.welcome = ttk.Frame(self.body)
        ttk.Label(self.welcome, text="Your FL tracks on your phone", style="Big.TLabel").pack(anchor="w")
        ttk.Label(self.welcome, wraplength=480, justify="left", text=(
            "Sign in with Google here, then sign in with the same Google account on your phone. "
            "Every project you save in FL Studio shows up there automatically.")).pack(anchor="w", pady=(10, 22))
        self.signin_btn = ttk.Button(self.welcome, text="Sign in with Google", style="Accent.TButton",
                                     command=self._sign_in)
        self.signin_btn.pack(anchor="w")
        self.signin_hint = ttk.Label(self.welcome, foreground="gray", text="")
        self.signin_hint.pack(anchor="w", pady=(10, 0))

        # --- signed in
        self.main = ttk.Frame(self.body)
        top = ttk.Frame(self.main)
        top.pack(fill="x")
        self.acc_label = ttk.Label(top, text="")
        self.acc_label.pack(side="left")
        ttk.Button(top, text="Settings", command=self._toggle_settings).pack(side="right")

        self.status_var = tk.StringVar(value="Starting...")
        ttk.Label(self.main, textvariable=self.status_var, style="Status.TLabel").pack(anchor="w", pady=(14, 12))

        phone = ttk.LabelFrame(self.main, text="On your phone", padding=12)
        phone.pack(fill="x")
        self.qr_label = ttk.Label(phone)
        self.qr_label.pack(side="left")
        ptext = ttk.Frame(phone)
        ptext.pack(side="left", fill="both", expand=True, padx=(14, 0))
        ttk.Label(ptext, wraplength=300, justify="left", text=(
            "Scan with your phone camera and sign in with the same Google account. "
            "Then add it to your home screen.")).pack(anchor="w")
        link = ttk.Label(ptext, text=config.WEB_APP_URL, foreground="#2a6fdb", cursor="hand2",
                         wraplength=300)
        link.pack(anchor="w", pady=(8, 0))
        link.bind("<Button-1>", lambda _: webbrowser.open(config.WEB_APP_URL))
        self._make_qr()

        self.settings = self._build_settings(self.main)

        self.activity_label = ttk.Label(self.main, text="Activity")
        self.activity_label.pack(anchor="w", pady=(14, 4))
        logf = ttk.Frame(self.main)
        logf.pack(fill="both", expand=True)
        self.log = tk.Text(logf, height=8, wrap="word", state="disabled", relief="flat",
                           background=self.root.cget("background"))
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def _make_qr(self):
        uri = segno.make(config.WEB_APP_URL, error="m").png_data_uri(scale=4, border=2)
        self.qr_img = tk.PhotoImage(data=uri.split(",", 1)[1])
        self.qr_label.configure(image=self.qr_img)

    def _build_settings(self, parent):
        s = self.engine.settings
        box = ttk.LabelFrame(parent, text="Settings", padding=12)
        box.columnconfigure(1, weight=1)
        r = 0

        ttk.Label(box, text="Project folders").grid(row=r, column=0, sticky="nw")
        self.folders = tk.Listbox(box, height=3, activestyle="none")
        self.folders.grid(row=r, column=1, sticky="ew", padx=6)
        for f in s["project_folders"]:
            self.folders.insert("end", f)
        fb = ttk.Frame(box)
        fb.grid(row=r, column=2, sticky="n")
        ttk.Button(fb, text="Add", command=self._add_folder).pack(fill="x")
        ttk.Button(fb, text="Remove", command=self._remove_folder).pack(fill="x", pady=(4, 0))
        r += 1

        ttk.Label(box, text="FL Studio app").grid(row=r, column=0, sticky="w", pady=(8, 0))
        self.fl_var = tk.StringVar(value=s.get("fl_exe", ""))
        ttk.Entry(box, textvariable=self.fl_var).grid(row=r, column=1, sticky="ew", padx=6, pady=(8, 0))
        ttk.Button(box, text="Browse", command=self._pick_fl).grid(row=r, column=2, pady=(8, 0))
        self.fl_var.trace_add("write", lambda *_: self._set("fl_exe", self.fl_var.get().strip()))
        r += 1

        ttk.Label(box, text="Phone quality").grid(row=r, column=0, sticky="w", pady=(8, 0))
        self.q_var = tk.StringVar(value=QUALITY[s["quality"]]["label"])
        q = ttk.Combobox(box, textvariable=self.q_var, state="readonly",
                         values=[v["label"] for v in QUALITY.values()])
        q.grid(row=r, column=1, columnspan=2, sticky="ew", padx=6, pady=(8, 0))
        q.bind("<<ComboboxSelected>>", self._quality_changed)
        r += 1

        ttk.Label(box, text="Export folder").grid(row=r, column=0, sticky="w", pady=(8, 0))
        self.exp_var = tk.StringVar(value=s.get("export_folder", ""))
        ttk.Entry(box, textvariable=self.exp_var, state="readonly").grid(row=r, column=1, sticky="ew",
                                                                          padx=6, pady=(8, 0))
        eb = ttk.Frame(box)
        eb.grid(row=r, column=2, pady=(8, 0))
        ttk.Button(eb, text="Browse", command=self._pick_export).pack(side="left")
        ttk.Button(eb, text="Clear", command=lambda: self._export_set("")).pack(side="left", padx=(4, 0))
        r += 1

        self.auto_var = tk.BooleanVar(value=s["auto_render"])
        self.open_var = tk.BooleanVar(value=s["render_while_open"])
        self.boot_var = tk.BooleanVar(value=s["autostart"])
        for text, var, cmd in (
            ("Render projects automatically when they change", self.auto_var,
             lambda: self._set("auto_render", self.auto_var.get(), rescan=True)),
            ("Render even while FL Studio is open", self.open_var,
             lambda: self._set("render_while_open", self.open_var.get())),
            ("Start when the computer starts", self.boot_var, self._autostart_changed),
        ):
            ttk.Checkbutton(box, text=text, variable=var, command=cmd).grid(
                row=r, column=0, columnspan=3, sticky="w", pady=(6 if r else 0, 0))
            r += 1

        acts = ttk.Frame(box)
        acts.grid(row=r, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(acts, text="Check now", command=lambda: self.engine.send("sync")).pack(side="left")
        ttk.Button(acts, text="Render all projects", command=self._render_all).pack(side="left", padx=6)
        ttk.Button(acts, text="Sign out", command=lambda: self.engine.send("signout")).pack(side="right")
        ttk.Button(acts, text="Quit", command=self._quit).pack(side="right", padx=6)
        return box

    # ---------------------------------------------------------- actions --
    def _sign_in(self):
        self.signin_btn.state(["disabled"])
        self.signin_hint.configure(text="Finish signing in in your browser...")
        self.engine.send("signin")

    def _show_account(self, email):
        self.email = email
        if hasattr(self, "auto_var"):
            self.auto_var.set(self.engine.settings["auto_render"])
        if email:
            self.welcome.pack_forget()
            self.main.pack(fill="both", expand=True)
            self.acc_label.configure(text=f"Signed in as {email}")
            if self.engine.settings["autostart"]:
                self._apply_autostart(True)
        else:
            self.main.pack_forget()
            self.welcome.pack(fill="both", expand=True)
            self.signin_btn.state(["!disabled"])
            self.signin_hint.configure(text="")

    def _toggle_settings(self):
        if self.settings.winfo_ismapped():
            self.settings.pack_forget()
        else:
            self.settings.pack(fill="x", pady=(14, 0), before=self.activity_label)

    def _set(self, key, value, rescan=False, watchers=False):
        self.engine.settings[key] = value
        self.engine.save_settings()
        if watchers:
            self.engine.send("watchers")
        if rescan:
            self.engine.send("sync")

    def _apply_autostart(self, enabled):
        try:
            set_autostart(enabled)
        except Exception as e:
            self._log(f"Couldn't change start-up setting: {e}")

    def _autostart_changed(self):
        self._set("autostart", self.boot_var.get())
        self._apply_autostart(self.boot_var.get())

    def _folders_changed(self):
        self._set("project_folders", list(self.folders.get(0, "end")), watchers=True)

    def _add_folder(self):
        d = filedialog.askdirectory(title="Choose a folder with FL Studio projects")
        if d and d not in self.folders.get(0, "end"):
            self.folders.insert("end", d)
            self._folders_changed()

    def _remove_folder(self):
        for i in reversed(self.folders.curselection()):
            self.folders.delete(i)
        self._folders_changed()

    def _pick_fl(self):
        p = filedialog.askopenfilename(title="Find FL Studio (FL64.exe or FL Studio.app)")
        if p:
            self.fl_var.set(resolve_fl_exe(p) or p)

    def _pick_export(self):
        d = filedialog.askdirectory(title="Choose the folder you export audio to")
        if d:
            self._export_set(d)

    def _export_set(self, d):
        self.exp_var.set(d)
        self._set("export_folder", d, watchers=True)

    def _quality_changed(self, _):
        for key, q in QUALITY.items():
            if q["label"] == self.q_var.get():
                self._set("quality", key)

    def _render_all(self):
        if messagebox.askyesno(APP_NAME, "Render every project in your folders? With many projects this "
                                         "takes a while and opens FL Studio for each one."):
            self.engine.send("render_all")

    def _hide(self):
        # Closing the window keeps syncing in the background; Quit is in Settings.
        self.root.iconify()

    def _quit(self):
        self.engine.shutdown()
        self.root.destroy()

    # ---------------------------------------------------------------- ui --
    def _log(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", time.strftime("%H:%M  ") + msg + "\n")
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log.delete("1.0", f"{lines - MAX_LOG_LINES}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                kind, val = self.ui.get_nowait()
                if kind == "log":
                    self._log(val)
                    if not self.email and ("failed" in val or "keys" in val):
                        self.signin_btn.state(["!disabled"])
                        self.signin_hint.configure(text=val)
                elif kind == "status":
                    self.status_var.set(val)
                elif kind == "account":
                    self._show_account(val)
        except queue.Empty:
            pass
        self.root.after(200, self._poll)


def _selftest(root, app):
    """Used by the automatic tests: shows both screens, takes screenshots, quits."""
    shots = os.environ.get("FLPS_SCREENSHOT_DIR")

    def fail(*exc):                                    # any error in the window = failed test
        import traceback
        traceback.print_exception(*exc)
        os._exit(1)
    root.report_callback_exception = fail
    root.after(30000, lambda: os._exit(2))             # never hang a test run

    # prove the bundled ffmpeg works: make a 2 s tone and encode it like a real track
    import subprocess
    import imageio_ffmpeg
    from engine import NO_WINDOW, safe_work_dir
    src = safe_work_dir() / "selftest.wav"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=duration=2", str(src)], check=True, creationflags=NO_WINDOW)
    out = app.engine._encode(src, "aac256")
    if out.stat().st_size < 20_000:
        os._exit(3)
    print("ENCODE OK")
    app.engine.settings["autostart"] = False          # don't touch the test machine's login items
    root.attributes("-topmost", True)
    root.lift()

    def grab(name):
        if not shots:
            return
        try:
            from PIL import ImageGrab
            root.update()
            ImageGrab.grab().save(os.path.join(shots, name))
        except Exception as e:
            print(f"screenshot {name} failed: {e}")

    def step1():
        grab("1-welcome.png")
        app.ui.put(("account", "test@example.com"))
        app.ui.put(("status", "Up to date. Watching for saves."))
        app.ui.put(("log", "Uploaded Night Drive (1.9 MB)"))
        root.after(1500, step2)

    def step2():
        app._toggle_settings()
        root.after(800, step3)

    def step3():
        grab("2-signed-in.png")
        print("SELFTEST OK")
        app._quit()

    root.after(2500, step1)


def main():
    root = tk.Tk()
    app = App(root)
    if "--selftest" in sys.argv:
        _selftest(root, app)
    elif "--minimized" in sys.argv:
        root.iconify()
    root.mainloop()


if __name__ == "__main__":
    main()
