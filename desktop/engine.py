"""FL Phone Sync - sync engine (no GUI code here).

Watches FL Studio project folders, detects which projects really changed,
renders them to audio with FL Studio's command-line renderer, encodes a
small high-quality file and uploads it to the user's Google Drive.
"""
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import config
import imageio_ffmpeg
import psutil
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


APP_NAME = "FL Phone Sync"
DRIVE_FOLDER = "FL Phone Sync"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]  # only files this app creates

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"

AUDIO_EXT = {".wav", ".mp3", ".flac", ".ogg", ".aif", ".aiff", ".m4a"}
SETTLE_SECONDS = 20        # wait this long after the last save before acting
RESCAN_SECONDS = 300       # full folder rescan as a safety net
RENDER_TIMEOUT = 30 * 60   # give up on a render after 30 minutes
FIRST_AUDIO_TIMEOUT = 5 * 60  # FL opened but never started writing audio -> command-line render unsupported
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WIN else 0

QUALITY = {
    "aac256": {
        "label": "AAC 256 kbps - recommended (~2 MB per minute)",
        "ext": ".m4a", "mime": "audio/mp4",
        "args": ["-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"],
    },
    "aac192": {
        "label": "AAC 192 kbps - smaller (~1.4 MB per minute)",
        "ext": ".m4a", "mime": "audio/mp4",
        "args": ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    },
    "flac": {
        "label": "FLAC lossless - exact quality (~6 MB per minute)",
        "ext": ".flac", "mime": "audio/flac",
        "args": ["-c:a", "flac", "-compression_level", "8"],
    },
}


# ---------------------------------------------------------------- paths --

def config_dir() -> Path:
    if IS_WIN:
        base = Path(os.environ.get("APPDATA", Path.home()))
    elif IS_MAC:
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "FLPhoneSync"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONF = config_dir()
SETTINGS_FILE = CONF / "settings.json"
STATE_FILE = CONF / "state.json"
TOKEN_FILE = CONF / "token.json"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):  # packaged with PyInstaller
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


def client_config():
    """The developer's built-in Google keys (config.py). A client_secret.json next to the app overrides them."""
    for p in (app_dir() / "client_secret.json", CONF / "client_secret.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    if config.GOOGLE_DESKTOP_CLIENT_ID.startswith("PASTE_"):
        return None
    return {"installed": {
        "client_id": config.GOOGLE_DESKTOP_CLIENT_ID,
        "client_secret": config.GOOGLE_DESKTOP_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }}


# ------------------------------------------------------------ autostart --

AUTOSTART_NAME = "FLPhoneSync"
MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.flphonesync.agent.plist"
LINUX_AUTOSTART = Path.home() / ".config" / "autostart" / "flphonesync.desktop"


def _launch_command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--minimized"]
    py = Path(sys.executable)
    if IS_WIN and (py.parent / "pythonw.exe").exists():
        py = py.parent / "pythonw.exe"           # no console window at login
    return [str(py), str(app_dir() / "flsync.py"), "--minimized"]


def set_autostart(enabled: bool):
    """Start the app (minimized) when the user logs in to the computer."""
    cmd = _launch_command()
    if IS_WIN:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(cmd))
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    elif IS_MAC:
        if enabled:
            args = "".join(f"<string>{a}</string>" for a in cmd)
            MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
            MAC_AGENT.write_text(
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
                '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0"><dict><key>Label</key><string>com.flphonesync.agent</string>'
                f'<key>ProgramArguments</key><array>{args}</array>'
                '<key>RunAtLoad</key><true/></dict></plist>\n', encoding="utf-8")
        else:
            MAC_AGENT.unlink(missing_ok=True)
    else:
        if enabled:
            LINUX_AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
            LINUX_AUTOSTART.write_text("[Desktop Entry]\nType=Application\nName=FL Phone Sync\n"
                                       f"Exec={subprocess.list2cmdline(cmd)}\n", encoding="utf-8")
        else:
            LINUX_AUTOSTART.unlink(missing_ok=True)


def safe_work_dir() -> Path:
    """FL's command line can't handle spaces in paths, so render somewhere without any."""
    candidates = [CONF / "work"]
    if IS_WIN:
        candidates.append(Path(os.environ.get("SystemDrive", "C:") + "\\FLPhoneSyncTmp"))
    else:
        candidates.append(Path("/tmp/flphonesync"))
    for c in candidates:
        if " " in str(c):
            continue
        try:
            c.mkdir(parents=True, exist_ok=True)
            return c
        except OSError:
            pass
    return Path(tempfile.mkdtemp(prefix="flps"))


def find_fl_exe() -> str:
    found = []
    if IS_WIN:
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
        for root in roots:
            il = Path(root) / "Image-Line"
            if il.is_dir():
                for d in il.glob("FL Studio*"):
                    for exe in ("FL64.exe", "FL.exe"):
                        if (d / exe).exists():
                            found.append(d / exe)
                            break
    elif IS_MAC:
        for app in Path("/Applications").glob("FL Studio*.app"):
            exe = resolve_fl_exe(str(app))
            if exe:
                found.append(Path(exe))
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # newest install first
    return str(found[0]) if found else ""


def resolve_fl_exe(path: str) -> str:
    """Accepts an .exe, a Mac .app bundle or the binary inside it."""
    p = Path(path)
    if p.suffix == ".app" and p.is_dir():
        macos = p / "Contents" / "MacOS"
        if macos.is_dir():
            bins = [f for f in macos.iterdir() if f.is_file() and os.access(f, os.X_OK)]
            bins.sort(key=lambda f: ("fl" not in f.name.lower(), f.name))
            return str(bins[0]) if bins else ""
        return ""
    return str(p) if p.exists() else ""


def fl_is_running() -> bool:
    for proc in psutil.process_iter(["name"]):
        name = (proc.info.get("name") or "").lower()
        if name in ("fl64.exe", "fl.exe", "osxfl") or name.startswith("fl studio"):
            return True
    return False


# -------------------------------------------------------------- helpers --

def is_project_file(p: Path) -> bool:
    if p.suffix.lower() != ".flp":
        return False
    name = p.name.lower()
    if "autosave" in name or "overwritten" in name:
        return False
    if any(part.lower() in ("backup", "backups") for part in p.parts):
        return False
    return True


def project_id(p: Path) -> str:
    key = str(p.resolve())
    if IS_WIN or IS_MAC:  # case-insensitive file systems
        key = key.lower()
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def file_hash(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def default_settings() -> dict:
    docs = Path.home() / "Documents" / "Image-Line" / "FL Studio" / "Projects"
    return {
        "project_folders": [str(docs)] if docs.is_dir() else [],
        "export_folder": str(Path.home() / "Music" / "FL Phone Sync"),
        "fl_exe": find_fl_exe(),
        "auto_render": True,
        "render_while_open": False,
        "quality": "aac256",
        "backfill_days": 30,
        "autostart": True,
    }


def _perm_hint(e) -> str:
    if IS_MAC and isinstance(e, PermissionError):
        return (" (macOS blocked access. Open System Settings > Privacy & Security > Files and Folders "
                "and allow FL Phone Sync to access your Documents folder.)")
    return ""


def under(path: Path, folder: str) -> bool:
    if not folder:
        return False
    try:
        path.resolve().relative_to(Path(folder).resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- drive --

class Drive:
    def __init__(self):
        self.svc = None
        self.folder_id = None
        self.email = None

    offline_hint = "Sign in with Google to start syncing"

    def _creds(self, interactive: bool):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        creds = None
        if TOKEN_FILE.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            except Exception:
                creds = None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        if not creds or not creds.valid:
            if not interactive:
                return None
            cfg = client_config()
            if not cfg:
                raise RuntimeError("This build has no Google keys yet. The developer needs to fill in config.py.")
            flow = InstalledAppFlow.from_client_config(cfg, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300,
                                          success_message="Signed in. You can close this tab and go back to FL Phone Sync.")
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def connect(self, interactive=False) -> bool:
        from googleapiclient.discovery import build
        creds = self._creds(interactive)
        if not creds:
            return False
        self.svc = build("drive", "v3", credentials=creds, cache_discovery=False)
        about = self.svc.about().get(fields="user(emailAddress)").execute()
        self.email = about["user"]["emailAddress"]
        self.folder_id = self._ensure_folder()
        return True

    def sign_out(self):
        self.svc = self.folder_id = self.email = None
        TOKEN_FILE.unlink(missing_ok=True)

    def _ensure_folder(self) -> str:
        q = (f"name='{DRIVE_FOLDER}' and mimeType='application/vnd.google-apps.folder' "
             "and trashed=false")
        files = self.svc.files().list(q=q, spaces="drive", fields="files(id)").execute().get("files", [])
        if files:
            return files[0]["id"]
        body = {"name": DRIVE_FOLDER, "mimeType": "application/vnd.google-apps.folder"}
        return self.svc.files().create(body=body, fields="id").execute()["id"]

    def upsert(self, local: Path, mime: str, pid: str, title: str, props: dict, existing_id=None) -> str:
        """Upload a new file, or update the existing one so Drive keeps older versions as revisions."""
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
        body = {"name": title, "mimeType": mime, "appProperties": {"pid": pid, **props}}
        if not existing_id:
            q = (f"'{self.folder_id}' in parents and trashed=false and "
                 f"appProperties has {{ key='pid' and value='{pid}' }}")
            hits = self.svc.files().list(q=q, spaces="drive", fields="files(id)").execute().get("files", [])
            existing_id = hits[0]["id"] if hits else None
        media = MediaFileUpload(str(local), mimetype=mime, resumable=True)
        if existing_id:
            try:
                return self.svc.files().update(fileId=existing_id, body=body, media_body=media,
                                               fields="id").execute()["id"]
            except HttpError as e:
                if e.resp.status != 404:
                    raise
                media = MediaFileUpload(str(local), mimetype=mime, resumable=True)
        body["parents"] = [self.folder_id]
        return self.svc.files().create(body=body, media_body=media, fields="id").execute()["id"]


# --------------------------------------------------------------- engine --

class RenderUnsupported(RuntimeError):
    pass


class _Handler(FileSystemEventHandler):
    def __init__(self, engine):
        self.engine = engine

    def on_any_event(self, event):
        if event.is_directory:
            return
        for attr in ("src_path", "dest_path"):
            path = getattr(event, attr, None)
            if path:
                self.engine.on_fs_change(Path(path))


class Engine:
    def __init__(self, log, status, account):
        self.log, self.status, self.account = log, status, account
        self.settings = {**default_settings(), **load_json(SETTINGS_FILE, {})}
        self.state = load_json(STATE_FILE, {"projects": {}, "exports": {}})
        self.state.setdefault("projects", {})
        self.state.setdefault("exports", {})
        self.drive = Drive()
        self.last_connect = 0.0
        try:
            Path(self.settings["export_folder"]).mkdir(parents=True, exist_ok=True)
        except (OSError, KeyError):
            pass
        self.pending = {}            # path -> [kind, due_time]
        self.lock = threading.Lock()
        self.commands = queue.Queue()
        self.stop_evt = threading.Event()
        self.observer = None
        self.last_rescan = 0.0
        self.thread = threading.Thread(target=self._run, daemon=True)

    # ---- lifecycle
    def start(self):
        self.restart_watchers()
        self.thread.start()

    def shutdown(self):
        self.stop_evt.set()
        if self.observer:
            self.observer.stop()

    def save_settings(self):
        save_json(SETTINGS_FILE, self.settings)

    def _save_state(self):
        save_json(STATE_FILE, self.state)

    def send(self, *command):
        self.commands.put(command)

    def restart_watchers(self):
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)
        obs = Observer()
        folders = [f for f in self.settings["project_folders"] if Path(f).is_dir()]
        ef = self.settings.get("export_folder")
        if ef and Path(ef).is_dir() and not any(under(Path(ef), f) for f in folders):
            folders.append(ef)
        handler = _Handler(self)
        for f in folders:
            try:
                obs.schedule(handler, f, recursive=True)
            except Exception as e:
                self.log(f"Can't watch {f}: {e}{_perm_hint(e)}")
        obs.start()
        self.observer = obs
        self.last_rescan = 0.0  # rescan on next loop

    # ---- change detection
    def classify(self, p: Path):
        if p.suffix.lower() in AUDIO_EXT and under(p, self.settings.get("export_folder")):
            return "export"
        if is_project_file(p) and any(under(p, f) for f in self.settings["project_folders"]):
            return "project" if self.settings["auto_render"] else None
        return None

    def on_fs_change(self, p: Path):
        kind = self.classify(p)
        if kind:
            self.schedule(p, kind, SETTLE_SECONDS)

    def schedule(self, p: Path, kind: str, delay: float):
        due = time.time() + delay
        with self.lock:
            cur = self.pending.get(str(p))
            if cur and delay > 0:
                due = max(due, cur[1])
            self.pending[str(p)] = [kind, due]

    def _next_job(self):
        now = time.time()
        with self.lock:
            due = [(v[1], k, v[0]) for k, v in self.pending.items() if v[1] <= now]
            if not due:
                return None
            _, path, kind = min(due)
            del self.pending[path]
            return Path(path), kind

    def _rescan(self, force_all=False):
        cutoff = time.time() - self.settings["backfill_days"] * 86400
        if self.settings["auto_render"] or force_all:
            for folder in self.settings["project_folders"]:
                if not Path(folder).is_dir():
                    continue
                for f in Path(folder).rglob("*.flp"):
                    if not is_project_file(f):
                        continue
                    try:
                        st = f.stat()
                    except OSError:
                        continue
                    rec = self.state["projects"].setdefault(project_id(f), {})
                    is_new = "doneMtime" not in rec
                    rec["path"] = str(f)
                    if force_all:
                        rec["doneMtime"] = rec["failedHash"] = None
                        self.schedule(f, "project", 0)
                    elif rec.get("doneMtime") == st.st_mtime:
                        continue
                    elif is_new and st.st_mtime < cutoff:
                        rec["doneMtime"] = st.st_mtime   # old project: don't render on first scan
                    else:
                        self.schedule(f, "project", 3)
        ef = self.settings.get("export_folder")
        if ef and Path(ef).is_dir():
            for f in Path(ef).rglob("*"):
                if f.suffix.lower() not in AUDIO_EXT:
                    continue
                try:
                    st = f.stat()
                except OSError:
                    continue
                rec = self.state["exports"].get(str(f), {})
                if rec.get("mtime") != st.st_mtime or rec.get("size") != st.st_size:
                    if not rec and st.st_mtime < cutoff:
                        self.state["exports"][str(f)] = {"mtime": st.st_mtime, "size": st.st_size}
                    else:
                        self.schedule(f, "export", 3)
        self._save_state()

    # ---- worker loop
    def _try_connect(self):
        self.last_connect = time.time()
        try:
            if self.drive.connect(interactive=False):
                self.account(self.drive.email)
                self.log(f"Connected to {self.drive.email}")
            else:
                self.status(self.drive.offline_hint)
        except Exception as e:
            self.log(f"Couldn't connect: {e}")

    def _run(self):
        self._try_connect()
        while not self.stop_evt.is_set():
            self._handle_commands()
            if not self.drive.svc and time.time() - self.last_connect > 60:
                self._try_connect()
            if time.time() - self.last_rescan > RESCAN_SECONDS:
                try:
                    self._rescan()
                except Exception as e:
                    self.log(f"Couldn't scan folders: {e}{_perm_hint(e)}")
                self.last_rescan = time.time()
            job = self._next_job()
            if job:
                self._process(*job)
            else:
                self._idle_status()
                self.stop_evt.wait(1.0)

    def _idle_status(self):
        with self.lock:
            n = len(self.pending)
        if not self.drive.svc:
            self.status(self.drive.offline_hint)
        elif n:
            self.status(f"{n} change(s) queued")
        else:
            self.status("Up to date. Watching for saves.")

    def _handle_commands(self):
        while True:
            try:
                cmd = self.commands.get_nowait()
            except queue.Empty:
                return
            name = cmd[0]
            try:
                if name == "signin":
                    self.status("Waiting for Google sign-in in your browser...")
                    if self.drive.connect(interactive=True):
                        self.account(self.drive.email)
                        self.log(f"Signed in as {self.drive.email}")
                        self.last_rescan = 0.0
                elif name == "signout":
                    self.drive.sign_out()
                    self.account(None)
                    self.log("Signed out")
                elif name == "sync":
                    self._rescan()
                    self.last_rescan = time.time()
                    self.log("Checked all folders for changes")
                elif name == "render_all":
                    self._rescan(force_all=True)
                    self.last_rescan = time.time()
                    self.log("Queued every project for rendering")
                elif name == "watchers":
                    self.restart_watchers()
            except Exception as e:
                self.account(self.drive.email)
                self.log(f"{name} failed: {e}")

    def _process(self, p: Path, kind: str):
        if not p.exists():
            return
        if not self.drive.svc:
            self.schedule(p, kind, 30)
            return
        try:
            if kind == "project":
                self._process_project(p)
            else:
                self._process_export(p)
        except Exception as e:
            self.log(f"{p.name}: {e}")
        finally:
            self._save_state()

    def _process_project(self, p: Path):
        st = p.stat()
        if time.time() - st.st_mtime < SETTLE_SECONDS - 1:   # still being saved
            self.schedule(p, "project", SETTLE_SECONDS)
            return
        rec = self.state["projects"].setdefault(project_id(p), {})
        rec["path"] = str(p)
        h = file_hash(p)
        if h in (rec.get("renderedHash"), rec.get("failedHash")):
            rec["doneMtime"] = st.st_mtime        # saved, but nothing actually changed
            return
        if fl_is_running() and not self.settings["render_while_open"]:
            self.status("Waiting for FL Studio to close before rendering...")
            self.schedule(p, "project", 30)
            return
        exe = resolve_fl_exe(self.settings.get("fl_exe", ""))
        if not exe:
            self.log("Set the FL Studio location in Settings to render projects.")
            rec["failedHash"], rec["doneMtime"] = h, st.st_mtime
            return
        self.status(f"Rendering {p.stem}...")
        self.log(f"Rendering {p.stem}")
        try:
            wav = self._render(exe, p)
        except RenderUnsupported as e:
            rec["failedHash"], rec["doneMtime"] = h, st.st_mtime
            if IS_MAC:
                self.settings["auto_render"] = False
                self.save_settings()
                self.log(f"{e} Automatic rendering doesn't seem to work with FL Studio on this Mac, so it's "
                         f"turned off. Export your tracks from FL into {self.settings['export_folder']} "
                         f"and they go to your phone.")
                self.account(self.drive.email)      # lets the window refresh its settings
                return
            raise
        except Exception:
            rec["failedHash"], rec["doneMtime"] = h, st.st_mtime   # retry only after the next real change
            raise
        q = self.settings["quality"]
        out = self._encode(wav, q)
        self.status(f"Uploading {p.stem}...")
        fid = self.drive.upsert(out, QUALITY[q]["mime"], project_id(p), p.stem + QUALITY[q]["ext"],
                                {"project": p.stem[:90], "saved": iso(st.st_mtime), "src": "render", "q": q},
                                rec.get("driveId"))
        rec.update(renderedHash=h, failedHash=None, doneMtime=st.st_mtime, driveId=fid, uploaded=iso(time.time()))
        self.log(f"Uploaded {p.stem} ({out.stat().st_size / 1e6:.1f} MB)")

    def _process_export(self, p: Path):
        st = p.stat()
        rec = self.state["exports"].setdefault(str(p), {})
        if rec.get("mtime") == st.st_mtime and rec.get("size") == st.st_size and rec.get("driveId"):
            return
        time.sleep(2)
        if p.stat().st_size != st.st_size:    # FL is still writing the export
            self.schedule(p, "export", 10)
            return
        pid, prec = self._match_project(p.stem)
        q = self.settings["quality"]
        self.status(f"Encoding {p.name}...")
        out = self._encode(p, q)
        self.status(f"Uploading {p.stem}...")
        existing = (prec or {}).get("driveId") or rec.get("driveId")
        fid = self.drive.upsert(out, QUALITY[q]["mime"], pid, p.stem + QUALITY[q]["ext"],
                                {"project": p.stem[:90], "saved": iso(st.st_mtime), "src": "export", "q": q},
                                existing)
        rec.update(mtime=st.st_mtime, size=st.st_size, driveId=fid)
        if prec is not None:
            prec["driveId"] = fid
        self.log(f"Uploaded export {p.name} ({out.stat().st_size / 1e6:.1f} MB)")

    def _match_project(self, stem: str):
        """An export named like a project replaces that project's track on the phone."""
        for pid, rec in self.state["projects"].items():
            if rec.get("path") and Path(rec["path"]).stem.lower() == stem.lower():
                return pid, rec
        return "x" + hashlib.sha1(stem.lower().encode()).hexdigest()[:19], None

    # ---- render + encode
    def _render(self, exe: str, flp: Path) -> Path:
        work = safe_work_dir() / "render"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", flp.stem)[:60] or "project"
        tmp_flp = work / f"{safe}.flp"
        shutil.copy2(flp, tmp_flp)
        out = work / f"{safe}.wav"
        proc = subprocess.Popen([exe, "/R", "/Ewav", str(tmp_flp)], cwd=str(work),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        start, last_size, stable_since = time.time(), -1, None
        try:
            while True:
                if self.stop_evt.wait(2):
                    raise RuntimeError("stopped")
                exited = proc.poll() is not None
                wavs = [out] if out.exists() else list(work.glob("*.wav"))
                if wavs:
                    out = wavs[0]
                    size = out.stat().st_size
                    if size > 0 and size == last_size:
                        stable_since = stable_since or time.time()
                        if exited or time.time() - stable_since > 15:
                            break
                    else:
                        last_size, stable_since = size, None
                elif exited and time.time() - start > 10:
                    raise RenderUnsupported("FL Studio closed without producing audio.")
                elif time.time() - start > FIRST_AUDIO_TIMEOUT:
                    raise RenderUnsupported("FL Studio didn't start rendering within 5 minutes.")
                if time.time() - start > RENDER_TIMEOUT:
                    raise RuntimeError("Render took longer than 30 minutes and was stopped.")
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        return out

    def _encode(self, src: Path, quality: str) -> Path:
        q = QUALITY[quality]
        out = safe_work_dir() / ("upload" + q["ext"])
        out.unlink(missing_ok=True)
        cmd = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error",
               "-i", str(src), "-vn", "-map_metadata", "-1", *q["args"], str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NO_WINDOW)
        if r.returncode != 0 or not out.exists():
            raise RuntimeError(f"Encoding failed: {r.stderr.strip()[:200]}")
        return out
