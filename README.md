# FL Phone Sync

Save a project in FL Studio and the latest version is on your phone a minute later.

## What your users do

| Where | Steps |
|---|---|
| Computer | Download the app, open it, click **Sign in with Google**. That's it – it starts with the computer and runs in the background. |
| Phone | Scan the QR code the computer app shows (or open the link), click **Sign in with Google** with the same account, then **Add to Home Screen**. |

No accounts to create, no settings, no keys. Signing in to the same Google account on both is what connects them: the computer uploads to a folder called **FL Phone Sync** in that account's Google Drive, and the phone reads from it. The app can only see files it created itself – never the rest of anyone's Drive. Users also don't cost you anything, since tracks are stored in their own Drive.

---

## What you do once (as the developer, ~20 minutes)

Everything is built and hosted for free by GitHub. You never have to build on your own computer, and you don't need a Mac to make the Mac app.

### 1. Put the code on GitHub
Create a repository (for example `fl-phone-sync`) and upload everything in this folder, including the hidden `.github` folder.

### 2. Create the Google keys
1. Go to <https://console.cloud.google.com> and create a project called "FL Phone Sync".
2. **APIs & Services → Library → Google Drive API → Enable.**
3. **OAuth consent screen:** User type **External**, app name "FL Phone Sync", your email. Add the scope `.../auth/drive.file`. Then **Publish app** (set it to *In production*). `drive.file` is a non-sensitive scope, so this is a self-serve click, not a Google review, and users won't see an "unverified app" warning.
4. **Credentials → Create OAuth client ID → Desktop app.** Note the client ID and client secret.
5. **Credentials → Create OAuth client ID → Web application.** Under *Authorized JavaScript origins* add `https://YOUR-GITHUB-NAME.github.io`. Note the client ID.

Both keys must be in this same Google project – that's what lets the phone see what the computer uploaded.

### 3. Give the keys to GitHub
Repository **Settings → Secrets and variables → Actions → New repository secret**, three times:

| Name | Value |
|---|---|
| `GOOGLE_DESKTOP_CLIENT_ID` | Desktop client ID |
| `GOOGLE_DESKTOP_CLIENT_SECRET` | Desktop client secret |
| `GOOGLE_WEB_CLIENT_ID` | Web client ID |

### 4. Turn on the phone app
Repository **Settings → Pages → Source: GitHub Actions**. Then **Actions → Publish phone app → Run workflow**. The app goes live at `https://YOUR-GITHUB-NAME.github.io/fl-phone-sync/` and updates itself whenever you change `webapp/`.

### 5. Build the computer app
**Releases → Draft a new release**, create a tag like `v1.0.0`, publish. GitHub builds `FL-Phone-Sync-Windows.exe` and `FL-Phone-Sync-Mac.zip` and attaches them (about 5 minutes). The download buttons on the phone app's page always point to the newest release, so that page doubles as your download site.

---

## How syncing works

| Step | What happens |
|---|---|
| You save in FL | The app notices the `.flp` changed and waits 20 seconds in case you save again. |
| Change check | It compares a fingerprint of the file, so saving without changes does nothing. Autosaves and the `Backup` folder are ignored. |
| Render | When FL Studio is closed, it renders the project in the background with FL's command-line renderer. |
| Encode | WAV → **AAC 256 kbps (.m4a)**, about 2 MB per minute. FLAC (lossless) is an option in Settings. |
| Upload | The track on Drive is updated rather than duplicated, so Drive keeps older uploads as versions (about 30 days). The phone shows them under **Versions**. |
| Every 5 minutes | A full folder check catches anything missed, including changes made while the computer app was closed. |

First run renders only projects changed in the last 30 days. **Settings → Render all projects** does the rest. Settings also has an optional **export folder**: audio exported from FL into it is uploaded too, which is the fallback if automatic rendering doesn't work on someone's setup.

## Good to know

- **First-launch warnings.** Without code signing, Windows shows "Windows protected your PC" (click *More info → Run anyway*) and Mac says the app is from an unidentified developer (right-click → *Open* the first time). Removing these needs an Apple Developer account (99 USD/year) and a Windows code-signing certificate. Worth it once you have real users.
- **The Mac build is for Apple Silicon Macs** (M1 and newer).
- **Mac auto-rendering is experimental**, since FL's command-line renderer is documented for Windows. The export folder works on both.
- Closing the computer app's window keeps it syncing in the background. **Settings → Quit** stops it.

## Automatic testing on Mac and Windows

Every push to GitHub runs **Actions → Test on Mac and Windows** on real Mac and Windows machines:

| Step | Checks |
|---|---|
| 1 | 9 engine tests: autosave/Backup filtering, paths with spaces, render → encode → upload with a fake FL Studio, unchanged saves skipped, real changes update the same Drive file, old projects skipped on first run, waiting while FL is open, export folder + FLAC, start-at-login on/off |
| 2 | Opens the real window from source, encodes a test tone, screenshots both screens |
| 3 | Builds the app exactly like a release |
| 4 | Launches the built app and does the same checks, which proves the packaged app (including ffmpeg) works |

A green check means it passed on both systems. Screenshots of the window are under **Artifacts** at the bottom of each run. Run the engine tests yourself with `cd desktop && python -m pytest -v`.

What it can't test: real FL Studio rendering (FL can't be installed on GitHub's machines) and Google sign-in (needs a real person to click). Try those once by hand on a real Mac and PC.

## Running from source (for development)

```bash
cd desktop
pip install -r requirements.txt
python flsync.py
```
Fill in `desktop/config.py` (or drop a `client_secret.json` next to `flsync.py`) and the client ID at the top of `webapp/index.html`. Test the phone app locally with `cd webapp && python -m http.server 8000` (add `http://localhost:8000` to the web client's authorized origins).
