# PitWall native app

`PitWall.app` is a SwiftUI window around the existing engine. It starts
`main.py` (frozen by PyInstaller) as a child process with `--no-browser`,
shows `dashboard.html` inside the app, and stops the engine with SIGINT on
Quit so the session is archived. User state lives in
`~/Library/Application Support/PitWall`.

- `project.yml` is the source of truth. After editing it, run `xcodegen` in this folder.
- **Debug** (⌘R in Xcode) runs the source checkout through `build-venv`,
  so Python changes show up without PyInstaller. A `.env` in the repo puts
  it in licence mode; to get LOCAL DEV MODE, set `PITWALL_API_URL` to an
  empty value in the scheme's environment.
- **Release** bundles `dist/PitWall` (`scripts/embed_engine.sh`) and fails
  if it is missing. `packaging/mac/build_dmg.sh` builds the app and the DMG.

Development overrides (environment variables on the app):

| Variable | Effect |
|---|---|
| `PITWALL_HOME` | state folder instead of Application Support |
| `PITWALL_DASHBOARD_PORT` | dashboard port (default 8777) |
| `PITWALL_ENGINE_ARGS` | extra `main.py` flags, e.g. `--port 20790 --no-tts` |
| `PITWALL_SNAPSHOT` | Debug only: writes a PNG of the window 5 s after load |

## Signing for distribution

1. Sign in to Xcode (Settings → Accounts). Then set `DEVELOPMENT_TEAM` in
   `project.yml` to your Team ID and run `xcodegen`.
2. Create a **Developer ID Application** certificate (Xcode → Settings →
   Accounts → Manage Certificates → +).
3. Build and sign:
   `TEAM_ID=… SIGN_IDENTITY="Developer ID Application: … (…)" packaging/mac/build_dmg.sh`
   This signs every Mach-O in the engine with the hardened runtime
   (`Engine.entitlements`).
4. Notarize and staple:
   `xcrun notarytool submit dist/PitWall.dmg --keychain-profile pitwall --wait`
   then `xcrun stapler staple dist/PitWall.dmg`. Store the profile once with
   `xcrun notarytool store-credentials pitwall`.

The engine is built for Apple silicon only, the same as the PyInstaller build.
