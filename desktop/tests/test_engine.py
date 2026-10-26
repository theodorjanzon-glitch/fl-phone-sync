"""Run with:  cd desktop && python -m pytest -v"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time

import pytest

# keep test settings away from the real user's settings
os.environ["APPDATA"] = os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import engine  # noqa: E402

FAKE_FL = str(pathlib.Path(__file__).with_name("fake_fl.py"))


class FakeDrive:
    svc, email, folder_id = True, "test@example.com", "F"

    def __init__(self):
        self.uploads = []

    def connect(self, interactive=False):
        return True

    def upsert(self, local, mime, pid, title, props, existing_id=None):
        local = pathlib.Path(local)
        self.uploads.append(dict(title=title, mime=mime, size=local.stat().st_size, props=props,
                                 existing=existing_id, data=local.read_bytes()[:16]))
        return existing_id or "id_" + pid


@pytest.fixture
def setup(tmp_path, monkeypatch):
    proj = tmp_path / "My Beats"          # space in the path on purpose
    proj.mkdir()
    real_popen = subprocess.Popen

    def popen(cmd, *a, **kw):             # route "FL Studio" to the fake renderer
        if cmd and cmd[0] == FAKE_FL:
            cmd = [sys.executable, FAKE_FL, *cmd[1:]]
        return real_popen(cmd, *a, **kw)

    monkeypatch.setattr(engine.subprocess, "Popen", popen)
    monkeypatch.setattr(engine, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(engine, "fl_is_running", lambda: False)
    e = engine.Engine(lambda m: None, lambda m: None, lambda m: None)
    e.drive = FakeDrive()
    e.settings.update(project_folders=[str(proj)], fl_exe=FAKE_FL, quality="aac256",
                      export_folder="", auto_render=True)
    e.state = {"projects": {}, "exports": {}}
    return e, proj


def run_due(e):
    time.sleep(3.2)
    while (job := e._next_job()):
        e._process(*job)


def test_filters_autosaves_and_backups(tmp_path):
    assert engine.is_project_file(tmp_path / "Song.flp")
    assert not engine.is_project_file(tmp_path / "Song (autosave).flp")
    assert not engine.is_project_file(tmp_path / "Backup" / "Song.flp")
    assert not engine.is_project_file(tmp_path / "Song.wav")


def test_work_dir_has_no_spaces():
    assert " " not in str(engine.safe_work_dir())


def test_render_encode_upload(setup):
    e, proj = setup
    (proj / "Night Drive (v2).flp").write_bytes(b"version-1")
    e._rescan()
    run_due(e)
    assert len(e.drive.uploads) == 1
    up = e.drive.uploads[0]
    assert up["title"] == "Night Drive (v2).m4a" and up["mime"] == "audio/mp4"
    assert b"ftyp" in up["data"]                       # real MP4/AAC container
    assert 300_000 < up["size"] < 1_500_000           # ~20 s at 256 kbps


def test_unchanged_resave_is_skipped(setup):
    e, proj = setup
    f = proj / "Song.flp"
    f.write_bytes(b"same")
    e._rescan(); run_due(e)
    os.utime(f)                                        # "save" without changes
    e._rescan(); run_due(e)
    assert len(e.drive.uploads) == 1


def test_real_change_updates_same_drive_file(setup):
    e, proj = setup
    f = proj / "Song.flp"
    f.write_bytes(b"one")
    e._rescan(); run_due(e)
    f.write_bytes(b"two")
    e._rescan(); run_due(e)
    assert len(e.drive.uploads) == 2
    assert e.drive.uploads[1]["existing"] == "id_" + engine.project_id(f)


def test_old_projects_not_rendered_on_first_run(setup):
    e, proj = setup
    old = proj / "Old.flp"
    old.write_bytes(b"old")
    t = time.time() - 90 * 86400
    os.utime(old, (t, t))
    e._rescan(); run_due(e)
    assert e.drive.uploads == []


def test_waits_while_fl_is_open(setup, monkeypatch):
    e, proj = setup
    monkeypatch.setattr(engine, "fl_is_running", lambda: True)
    (proj / "Song.flp").write_bytes(b"x")
    e._rescan(); run_due(e)
    assert e.drive.uploads == [] and e.pending      # still queued


def test_export_folder_upload_flac(setup, tmp_path):
    e, proj = setup
    exp = tmp_path / "Exports"
    exp.mkdir()
    e.settings.update(export_folder=str(exp), quality="flac")
    subprocess.run([engine.imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=duration=5", str(exp / "Bounce.wav")], check=True)
    e._rescan(); run_due(e)
    assert e.drive.uploads[0]["title"] == "Bounce.flac"
    assert e.drive.uploads[0]["data"].startswith(b"fLaC")


def test_autostart_on_and_off():
    engine.set_autostart(True)
    if engine.IS_MAC:
        assert engine.MAC_AGENT.exists()
    elif engine.IS_WIN:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run")
        assert "--minimized" in winreg.QueryValueEx(k, engine.AUTOSTART_NAME)[0]
    engine.set_autostart(False)
    if engine.IS_MAC:
        assert not engine.MAC_AGENT.exists()


def test_mac_turns_off_auto_render_when_fl_cant_render(setup, monkeypatch, tmp_path):
    e, proj = setup
    dud = tmp_path / "dud_fl.py"                       # an "FL" that opens and never renders
    dud.write_text("import time; time.sleep(60)")
    real_popen = subprocess.Popen
    monkeypatch.setattr(engine.subprocess, "Popen",
                        lambda cmd, *a, **kw: real_popen([sys.executable, str(dud)], *a, **kw))
    monkeypatch.setattr(engine, "FIRST_AUDIO_TIMEOUT", 4)
    monkeypatch.setattr(engine, "IS_MAC", True)
    monkeypatch.setattr(e, "save_settings", lambda: None)
    (proj / "Song.flp").write_bytes(b"x")
    e._rescan(); run_due(e)
    assert e.settings["auto_render"] is False and e.drive.uploads == []
