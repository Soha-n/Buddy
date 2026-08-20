# Packaging Buddy

How the desktop app is built and shipped. For running Buddy from source, see
the repository README.

## What ships

The user downloads one small installer. It fetches everything else.

```
Buddy-Setup-1.0.0.exe   ~130 KB    downloaded by the user
  ├── buddy-app.zip     ~130 MB    from GitHub Releases
  │                                (backend + shell + UI + SearXNG)
  ├── WebView2            ~2 MB    from Microsoft, only if missing
  └── Ollama            ~700 MB    from ollama.com, only if missing
```

Installed per-user to `%LOCALAPPDATA%\Programs\Buddy`, so there is no UAC
prompt. User data lives separately in `%LOCALAPPDATA%\Buddy` and survives
updates and uninstalls.

The AI model is deliberately **not** bundled. Buddy's onboarding picks one that
fits the machine's actual VRAM and pulls it on first launch; a bundled model
would be the wrong model for most users and would add gigabytes to a download
that currently finishes in about a minute.

## Layout

```
build/
├── backend.spec               PyInstaller spec (two executables)
├── installer/
│   └── buddy-web-installer.nsi
└── scripts/
    ├── build-all.ps1          everything, in order → build/release/
    ├── build-searxng.ps1      the prebuilt search payload
    └── build-installer.ps1    the stub, pinned to a manifest
```

`build/release/` holds the finished assets. Everything else under `build/` is
scratch and is gitignored.

## Building

```powershell
# Everything. Assets land in build/release/.
./build/scripts/build-all.ps1 -Version 1.0.0

# Development only. Buddy still runs and search still works via fallbacks, but
# the private built-in instance will not - an end-user machine has neither git
# nor Python to install it at runtime. Do not ship a build made this way.
./build/scripts/build-all.ps1 -Version 1.0.0 -SkipSearxng

# The installer, after the assets are uploaded and the manifest is final.
./build/scripts/build-installer.ps1
```

Requires Node 22+, Rust 1.97+, Python 3.14 with the backend venv, and NSIS with
the INetC plugin. A tagged push runs all of it in CI
(`.github/workflows/release.yml`).

### Releasing

1. Tag: `git tag v1.0.0 && git push --tags`
2. CI builds the assets, uploads them, then builds the installer against their
   real hashes and uploads that too.
3. The release is created as a **draft** — check the assets, then publish.

The installer must be built after the assets exist, because it pins their
SHA-256 hashes.

## Design notes

Things here are not obvious, and changing them tends to reintroduce a bug.

### Two executables, two analyses

`code_runner` runs model-written chart scripts in a separate interpreter. In a
frozen build `sys.executable` is Buddy itself, so spawning it starts a second
copy of the whole application rather than running the script. The symptom is a
chart request that hangs until it times out.

So the spec builds `buddy-runner` from its own entry point (`run_script.py`)
and its own `Analysis`. Building both executables from the server's analysis
recreates the bug exactly, because the runner then *is* the server. `MERGE`
keeps the shared dependencies collected once.

This is also why the build is `--onedir`: `--onefile` gives `sys.executable` no
real interpreter to point at, and re-extracts ~150 MB on every launch.

### Two roots

`app/paths.py` separates read-only resources inside the bundle from writable
state in `%LOCALAPPDATA%\Buddy`. The install directory is replaced wholesale by
an update and is not writable at all in an all-users install, so nothing that
must survive can live there.

`BUDDY_DATA_DIR` overrides the writable root, which is what makes a portable
build possible and what the test scripts use.

### The port is discovered, not assumed

The backend binds port 0 and reports what it got on stdout as `BUDDY_PORT=`.
A fixed 8000 collides with Node dev servers and Jupyter, which users running a
local AI app are likely to have.

### The backend dies with the shell, guaranteed

Killing the child in the window handler covers a normal close. It does not
cover Task Manager, a crash, or a force-kill — and an orphaned backend holds
the database and the SearXNG port, so the next launch fails in a way the user
cannot diagnose.

The child is therefore also placed in a job object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The handle is owned by the shell process,
so the kernel enforces the guarantee no matter how the shell exits.

### PowerShell, not NSIS plugins

Hashing and extraction use `Get-FileHash` and `Expand-Archive`. The obvious
plugins are worse: `nsisunz` has no Unicode build, so in a Unicode installer it
fails at *runtime* rather than at compile time, and `Crypto` is another binary
to vendor. This also kept the stub at ~130 KB.

`INetC` is still required — the bundled `NSISdl` speaks plain HTTP only and
cannot fetch from GitHub at all.

### Scripts do not use `$ErrorActionPreference = 'Stop'`

Windows PowerShell turns any stderr output from a native executable into an
error record, and npm, PyInstaller, cargo and git all log progress to stderr.
Every native call checks `$LASTEXITCODE` explicitly instead, with a `trap` to
make `throw` fatal.

### Silent uninstall never prompts

`MessageBox` under `/S` has nobody to answer it and hangs forever. The
uninstaller checks `IfSilent` and keeps user data without asking.

### SearXNG cannot be cloned normally on Windows

Its tree contains paths like
`utils/templates/etc/nginx/default.apps-available/searxng.conf:socket`. A colon
is not legal in a Windows filename, so a plain `git clone` fails at checkout
with `invalid path` and leaves nothing usable behind.

`build-searxng.ps1` therefore clones with `--no-checkout`, sets a cone-mode
sparse checkout, and checks out only `searx`, `searxng_extra`,
`requirements.txt` and `LICENSE` by pathspec. Two things to preserve if that
code is touched:

- **No `--filter=blob:none`.** A partial clone leaves the blobs unfetched and
  the pathspec checkout then fails on unreadable objects.
- **Never a bare `git checkout <ref>`.** It re-expands the full tree and hits
  the illegal paths again, whatever the sparse config says.

### The payload ships an embeddable runtime, not a virtualenv

A virtualenv cannot be shipped. Its `python.exe` is a ~270 KB launcher that
finds the real runtime through `pyvenv.cfg`'s `home`, so it needs the base
interpreter's DLL and stdlib present on the machine. On a user's machine they
are not, and the launcher does not error - it **hangs**. Rewriting `pyvenv.cfg`
does not help, because there is no local runtime to point it at.

So `build-searxng.ps1` downloads Python's **embeddable distribution** (~11 MB,
self-contained, location-independent) and installs SearXNG's dependencies into
it with `pip --target`. Two consequences:

- The `python*._pth` file must be deleted. It pins `sys.path` and disables
  `site-packages`, so the installed dependencies would be unimportable.
- The interpreter sits at `venv/python.exe`, not `venv/Scripts/python.exe`.
  `searxng_manager._venv_python` checks both, because a source install still
  builds a real venv.

The payload is bundled into the app archive by `backend.spec`, which reads
`build/payload/searxng` **at freeze time** - so it has to be built *before*
PyInstaller runs. `build-all.ps1` checks the staged output afterwards and fails
the build if it is missing, because the spec skips it silently.

## Licensing

SearXNG is **AGPL-3.0**. Running it as a separate process reached over HTTP
keeps its licence confined to that process, but shipping its source in the
installer is distribution: the release must carry its licence and a source
offer. `build-searxng.ps1` copies `LICENSE-SEARXNG` into the payload.

Ollama is downloaded from its own release rather than rehosted. Copying their
binary would make this project responsible for shipping their security updates.

## Testing a build locally

```powershell
# Assemble an install layout without an installer.
mkdir build/apptest
cp build/dist/buddy-backend/* build/apptest/
cp desktop/src-tauri/target/release/buddy-desktop.exe build/apptest/Buddy.exe

# Run against a throwaway data directory.
$env:BUDDY_DATA_DIR = 'D:\path\to\scratch'
./build/apptest/Buddy.exe
```

To exercise the installer end to end, serve `build/release/` over HTTP and
compile the stub with `/DAPP_URL` pointing at it.

Worth checking after any change to the shell or the spec:

- a chart request returns an image rather than timing out,
- force-killing `Buddy.exe` leaves no `buddy-backend.exe` behind,
- a silent uninstall completes without hanging and keeps `%LOCALAPPDATA%\Buddy`.
