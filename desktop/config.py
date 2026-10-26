"""Built-in settings. YOU (the developer) fill these in once before building.
End users never see or change them - they just click "Sign in with Google".

Google treats the secret of a "Desktop app" OAuth client as public by design,
so shipping it inside the app is the normal, intended way to do this.
"""

# Google Cloud -> Credentials -> OAuth client ID -> "Desktop app"
GOOGLE_DESKTOP_CLIENT_ID = "PASTE_DESKTOP_CLIENT_ID.apps.googleusercontent.com"
GOOGLE_DESKTOP_CLIENT_SECRET = "PASTE_DESKTOP_CLIENT_SECRET"

# Where the phone app is hosted (GitHub Pages, see README). Shown as a QR code after sign-in.
WEB_APP_URL = "https://YOUR-GITHUB-NAME.github.io/fl-phone-sync/"
