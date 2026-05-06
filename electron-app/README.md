# RpaClaw Electron Desktop Application

## Development

### Prerequisites

- Node.js 20+
- Python 3.13
- Windows 10/11

### Setup

1. Install dependencies:
   ```bash
   npm install
   ```

2. Build TypeScript:
   ```bash
   npm run build
   ```

3. Run in development mode:
   ```bash
   npm run dev
   ```

### Development Mode

In development mode, the app expects:
- Backend running at `http://127.0.0.1:12001`
- Task-service running at `http://127.0.0.1:12002`

Start these manually before running Electron:

```bash
# Terminal 1: Backend
cd ../RpaClaw/backend
uv run uvicorn backend.main:app --host 127.0.0.1 --port 12001

# Terminal 2: Task-service
cd ../RpaClaw/task-service
uv run uvicorn app.main:app --host 127.0.0.1 --port 12002

# Terminal 3: Electron
cd electron-app
npm run dev
```

## Building

### Full Build

Run the PowerShell build script from the project root:

```powershell
.\build-windows.ps1
```

This will:
1. Build frontend (Vue 3)
2. Prepare Python + Node runtime environment (embedded runtimes + dependencies + Playwright)
3. Build Electron app and create installer

### Document Skill Prerequisites

The desktop installer now bundles:

- Python runtime and backend Python dependencies
- Node.js runtime
- bundled `docx` and `pptxgenjs` modules for the built-in document skills

Users still need to install some desktop tools manually if they want the full local document skill set:

- `Pandoc` for some DOCX text-extraction workflows
- `LibreOffice` for DOCX/PPTX/XLSX conversion and formula recalculation workflows
- `Tesseract OCR` for OCR on scanned PDFs

The end-user setup steps are documented in [../docs/windows-desktop-document-skills-guide.zh-CN.md](../docs/windows-desktop-document-skills-guide.zh-CN.md).

### Partial Builds

Skip steps if already done:

```powershell
.\build-windows.ps1 -SkipFrontend
.\build-windows.ps1 -SkipPython
.\build-windows.ps1 -SkipElectron
```

### Output

Installer will be created at:
```
electron-app/release/RpaClaw Setup 1.0.0.exe
```

## Testing

### Local Testing

1. Build the installer
2. Install on a test Windows VM (clean environment)
3. Run through first-run wizard
4. Test all features:
   - Create session and chat
   - Record RPA skill
   - Schedule task
   - Restart app (verify data persistence)

### Debugging

Logs are written to:
- Backend: `%USERPROFILE%\RpaClaw\logs\backend.log`
- Task-service: `%USERPROFILE%\RpaClaw\logs\task-service.log`

Packaged app files:
- Extra environment overrides: `<install-dir>\.env`
- Setup wizard config: `<install-dir>\app-config.json`

Electron DevTools: Press `Ctrl+Shift+I` in the app window

## Authentication Mode

The desktop app uses local storage and local browser automation by default:

```env
STORAGE_BACKEND=local
```

This does not imply no-login mode. The packaged desktop runtime defaults to:

```env
AUTH_PROVIDER=local
```

That means users sign in with local accounts and their models, credentials,
skills, RPA sessions, and MCP tools remain separated by user id. The bootstrap
admin account is created on first start; use `admin` / `admin123` unless the
password has already been changed.

For a single-user, no-login desktop build, explicitly override the packaged
environment in `<install-dir>\.env`:

```env
AUTH_PROVIDER=none
```

Only use `AUTH_PROVIDER=none` when tenant isolation is not required. In that
mode assets are owned by the local development identity `local_admin`.

## Project Structure

```
electron-app/
├── src/
│   ├── main.ts              # Main process
│   ├── preload.ts           # Preload script
│   ├── config.ts            # Config management
│   ├── process-manager.ts   # Backend process lifecycle
│   ├── types.ts             # TypeScript types
│   └── wizard/              # First-run wizard
│       ├── wizard.html
│       ├── wizard.ts
│       └── wizard.css
├── resources/
│   └── icon.ico             # App icon
├── package.json             # Electron Builder config
└── tsconfig.json            # TypeScript config
```

## Troubleshooting

### "Python not found"

Ensure Python embeddable package is in `build/python/` directory.

### "Backend failed to start"

Check backend logs in `%USERPROFILE%\RpaClaw\logs\backend.log`.

Common issues:
- Port 12001 already in use
- Missing dependencies
- Python path incorrect

### "Playwright browser not found"

Ensure Playwright Chromium is installed:
```bash
build/python/python.exe -m playwright install chromium
```
