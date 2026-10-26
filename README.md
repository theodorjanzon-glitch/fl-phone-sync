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

### 6. Mac signing (needed before Mac users can install it easily)

Since macOS 15, an unsigned app can't be opened with a right-click anymore. Users have to dig into **System Settings → Privacy & Security → Open Anyway** and enter their password. Most people give up there, so for Mac users this step matters.

1. Join the **Apple Developer Program** (99 USD/year) at <https://developer.apple.com/programs/>.
2. In Xcode or the developer website, create a **Developer ID Application** certificate. Export it from Keychain Access as a `.p12` file with a password.
3. Create an **app-specific password** at <https://account.apple.com> (Sign-In and Security → App-Specific Passwords).
4. Add these repository secrets:

| Name | Value |
|---|---|
| `MAC_CERT_P12_BASE64` | The `.p12` file as base64 (`base64 -i cert.p12 \| pbcopy` on a Mac) |
| `MAC_CERT_PASSWORD` | The `.p12` password |
| `MAC_SIGN_IDENTITY` | e.g. `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | Your Apple ID email |
| `APPLE_TEAM_ID` | Your 10-character team ID |
| `APPLE_APP_PASSWORD` | The app-specific password |

The next release is then signed and notarized by Apple automatically, and opens with a normal double-click. Without these secrets the build still works, just unsigned.

Windows is milder: unsigned apps show "Windows protected your PC" with **More info → Run anyway**. You can fix that later with a code-signing service such as Azure Trusted Signing.

## Launch checklist

| # | Step | Cost |
|---|---|---|
| 1 | GitHub repo + secrets + Pages (steps 1–4) | Free |
| 2 | Google project published to production (step 2) | Free |
| 3 | Apple Developer Program + signing secrets (step 6) | 99 USD/year |
| 4 | Release `v0.1.0` (step 5), check the Actions run is green | Free |
| 5 | First real user on a Mac: install, sign in, save a project in FL, check it plays on the phone | – |
| 6 | First real user on Windows: same | – |
| 7 | Share the phone app link – it's also the download page | – |

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

First run renders only projects changed in the last 30 days. **Settings → Render all projects** does the rest.

**Export folder:** anything exported from FL into `Music/FL Phone Sync` is uploaded too. On a Mac, if FL Studio turns out not to support automatic rendering, the app notices within 5 minutes, turns automatic rendering off and tells the user to export into this folder instead.

## Good to know

- **First-launch permission on Mac.** macOS asks whether FL Phone Sync may access the Documents folder (where FL keeps projects). Users need to click **Allow**.
- **The Mac build is for Apple Silicon Macs** (M1 and newer).
- **Mac auto-rendering is unconfirmed**, since FL's command-line renderer is documented for Windows. The app falls back to the export folder automatically if it doesn't work.
- Closing the computer app's window keeps it syncing in the background. **Settings → Quit** stops it.

## Automatic testing on Mac and Windows

Every push to GitHub runs **Actions → Test on Mac and Windows** on real Mac and Windows machines:

| Step | Checks |
|---|---|
| 1 | 10 engine tests: autosave/Backup filtering, paths with spaces, render → encode → upload with a fake FL Studio, unchanged saves skipped, real changes update the same Drive file, old projects skipped on first run, waiting while FL is open, export folder + FLAC, start-at-login on/off, Mac fallback when FL can't render |
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
