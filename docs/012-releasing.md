# Releasing

## Quick Reference

```bash
# 1. Bump version
./scripts/bump-version.sh 0.2.0

# 2. Commit and tag
git add -A && git commit -m "chore: bump version to 0.2.0"
git tag v0.2.0

# 3a. CI release (recommended) — push the tag, GitHub Actions does the rest
git push && git push --tags

# 3b. Manual release — build locally, upload to GitHub
export TAURI_SIGNING_PRIVATE_KEY="..."
./scripts/build_app.sh --dmg-only
./scripts/generate-latest-json.sh
# Upload DMG + tar.gz + sig + latest.json at GitHub releases
```

---

## Version Management

The version is defined once in `pyproject.toml`. Python reads it at runtime
via `importlib.metadata`. The bump script syncs it to the static config files
that can't read it dynamically:

| File | Updated by |
|---|---|
| `pyproject.toml` | bump script (source of truth) |
| `src-tauri/tauri.conf.json` | bump script |
| `src-tauri/Cargo.toml` | bump script |
| `frontend/package.json` | bump script |
| `orchestrator/__init__.py` | reads from pyproject.toml at runtime |
| `orchestrator/api/app.py` | reads from `__version__` at runtime |

---

## CI Release (via GitHub Actions)

Pushing a `v*` tag triggers the release workflow (`.github/workflows/release.yml`):

1. Builds the PyInstaller sidecar
2. Signs sidecar binaries with Developer ID certificate
3. Builds the Tauri app (`cargo tauri build`)
4. Signs and notarizes the app with Apple
5. Creates a GitHub Release with:
   - `.dmg` — for manual download
   - `.app.tar.gz` + `.sig` — signed updater artifacts for auto-install
   - `latest.json` — version manifest for the Tauri updater

### Required GitHub Secrets

| Secret | How to get it |
|---|---|
| `APPLE_CERTIFICATE` | `base64 -i YourDevID.p12 \| pbcopy` (Developer ID Application cert exported as .p12) |
| `APPLE_CERTIFICATE_PASSWORD` | Password set when exporting the .p12 |
| `APPLE_ID` | Your Apple ID email |
| `APPLE_PASSWORD` | App-specific password from appleid.apple.com → Sign-In and Security |
| `APPLE_TEAM_ID` | `XT8BC8793B` |
| `TAURI_SIGNING_PRIVATE_KEY` | Generated with `cargo tauri signer generate` |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | Password for the signing key |

---

## Manual Release

Use this when CI isn't set up yet or you need to release quickly from your machine.

### 1. Bump version

```bash
./scripts/bump-version.sh 0.2.0
git add -A && git commit -m "chore: bump version to 0.2.0"
```

### 2. Build locally

```bash
# Build with updater signing (enables auto-install for users)
export TAURI_SIGNING_PRIVATE_KEY="your-key-here"
export TAURI_SIGNING_PRIVATE_KEY_PASSWORD="your-password"
./scripts/build_app.sh --dmg-only
```

Output in `src-tauri/target/release/bundle/`:
- `dmg/Orchestrator_0.2.0_aarch64.dmg`
- `macos/Orchestrator.app.tar.gz` (updater artifact)
- `macos/Orchestrator.app.tar.gz.sig` (signature)

### 3. Generate latest.json

```bash
./scripts/generate-latest-json.sh
```

This reads the `.sig` file and version from `tauri.conf.json` and generates
`latest.json` in the bundle directory. The script prints the full list of
files to upload.

### 4. Create the release on GitHub

Go to https://github.com/yudongqiu/orchestrator/releases/new

- **Tag**: `v0.2.0` (create new tag on publish)
- **Target**: `main`
- **Title**: `Orchestrator v0.2.0`
- **Description**: release notes
- **Attach** these files:
  1. `Orchestrator_0.2.0_aarch64.dmg` — for manual download
  2. `Orchestrator.app.tar.gz` — updater artifact
  3. `Orchestrator.app.tar.gz.sig` — signature
  4. `latest.json` — version manifest for auto-install
- Click **Publish release**

### 5. What users see

- **Update detection** works immediately — the app checks the GitHub Releases
  API, which reads the tag name and compares versions.
- **Auto-install** (download + replace + restart) works when the release
  includes `latest.json`, `.app.tar.gz`, and `.sig`. The Tauri updater
  fetches `latest.json`, downloads the tar.gz, verifies the signature,
  replaces the app, and restarts.
- **Fallback**: If signed artifacts are missing, clicking "Install Update"
  opens the release page in the browser for manual download.

### Building without updater signing (quick test)

If you just want a DMG without auto-install support:

```bash
./scripts/build_app.sh --dmg-only
# Upload only the DMG — update detection still works, but users
# will be directed to the release page to download manually.
```

---

## How Auto-Update Works

```
┌─────────────┐     GET /repos/.../releases/latest        ┌────────────┐
│  Settings   │ ──────────────────────────────────────►   │  GitHub    │
│  Page       │ ◄──────────────────────────────────────   │  API       │
│             │     { tag_name: "v0.2.0", ... }           └────────────┘
│             │
│  "Install   │     plugin:updater|check                  ┌────────────┐
│   Update"   │ ──────────────────────────────────────►   │  Tauri     │
│   clicked   │     plugin:updater|download_and_install   │  Updater   │
│             │ ──────────────────────────────────────►   │  Plugin    │
│             │     plugin:process|restart                │            │
│             │ ──────────────────────────────────────►   │  fetches   │
└─────────────┘                                           │  latest.json
                                                          │  downloads │
                  ┌──────────────────┐                    │  .tar.gz   │
                  │ Fallback: opens  │◄── if no signed    │  verifies  │
                  │ release page in  │    artifacts       │  .sig      │
                  │ browser          │                    │  replaces  │
                  └──────────────────┘                    │  app       │
                                                          └────────────┘
```

1. **Detection**: Python backend calls GitHub Releases API, compares tag version
   against `__version__`. No special files needed — works with any release.
2. **Installation**: Frontend invokes the Tauri updater plugin via IPC. The plugin
   fetches `latest.json` from the endpoint in `tauri.conf.json`, downloads the
   `.app.tar.gz`, verifies the signature, replaces the app bundle, and restarts.
3. **Fallback**: If Tauri IPC isn't available (dev mode) or signed artifacts are
   missing, the app opens the release page in the browser for manual download.
