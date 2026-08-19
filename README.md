# Buddy

A Windows desktop-style web app that looks at your hardware, recommends the three
Ollama models that will actually run well on it, downloads the one you pick, and
then lets you chat with it. Everything runs locally — no data leaves the machine.

- **Frontend:** TypeScript + React 19 + Vite
- **Backend:** Python + FastAPI
- **Inference:** Ollama (local)
- **Retrieval:** SQLite FTS5 full-text search (no extra models)
- **Live data:** optional web search via SearXNG or DuckDuckGo

## The flow

1. **Scan** — detects CPU, RAM, GPU + VRAM, free disk space and OS.
2. **Choose** — scores 16 curated models against those specs and shows the top 3,
   each with a plain-language explanation of *why* it fits your machine.
3. **Download** — pulls the selected model with live progress, speed and ETA.
4. **Chat** — streams the reply token by token.
5. **Attach** — drop in a PDF, Word doc, spreadsheet, CSV or image and ask
   questions about it. Retrieval is scoped to that one conversation.
6. **Search** — flip the Web toggle on for a message and Buddy looks the answer
   up before replying. Off by default.

## Requirements

- Windows 10/11
- [Python 3.11+](https://python.org)
- [Node.js 18+](https://nodejs.org)
- [Ollama](https://ollama.com/download) — installed and running

The app detects and explains missing prerequisites rather than failing silently:
"Ollama isn't installed" and "Ollama is installed but not running" are shown as
separate states, because they need different fixes.

## Quick start

```powershell
.\start.ps1
```

The script creates the virtual environment, installs dependencies on first run,
checks that Ollama is reachable, launches both servers in their own windows and
opens the browser.

If PowerShell blocks the script:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## Manual start

Two terminals.

**Backend:**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

**Frontend:**

```powershell
cd frontend
npm install
npm run dev
```

Then open <http://localhost:5173>.

## How models are ranked

A two-stage process.

**Hard filter** removes anything that genuinely cannot run: not enough RAM to
load the model, or not enough free disk to download it. Missing a GPU is *not* a
filter — CPU-only inference is valid, just slower.

**Weighted score** over what remains:

| Factor | Weight | Why |
| --- | --- | --- |
| RAM headroom | 40% | Running out of memory is the actual failure mode |
| VRAM fit | 30% | Determines whether the model can offload to the GPU |
| CPU capability | 15% | Matters most when there is no GPU to offload to |
| Quality tier | 15% | Breaks ties toward the more capable model |

Only 75% of total RAM is treated as usable, leaving room for the OS and browser.
A final rule enforces family diversity, so the top 3 aren't three sizes of the
same model.

Every reason string shown in the UI is generated from your measured numbers, not
written per model — so the explanation always describes *your* machine.

## Detecting VRAM on Windows

Reading VRAM reliably is the fiddliest part of the app, so it uses three sources
in descending order of trust:

1. **`nvidia-smi`** — exact, NVIDIA only.
2. **Registry `HardwareInformation.qwMemorySize`** — a true 64-bit value, any vendor.
3. **`Win32_VideoController.AdapterRAM`** — used for adapter *names*; its VRAM
   figure is a 32-bit field that saturates at 4 GB, so an 8 GB card reports 4 GB.

Values from source 3 are flagged as unreliable, excluded from GPU scoring, and
called out in the UI rather than silently trusted.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Backend + Ollama status |
| GET | `/api/specs?refresh=` | Detected hardware |
| GET | `/api/recommendations?limit=` | Ranked models + exclusions |
| GET | `/api/models/installed` | Locally available models |
| POST | `/api/models/pull` | Download a model (SSE stream) |
| DELETE | `/api/models/{name}` | Remove a model |
| POST | `/api/chat` | Chat completion (SSE stream) |
| POST | `/api/attachments/{conversation_id}` | Upload files to a chat |
| GET | `/api/attachments/{conversation_id}` | Attachments + indexing status |
| GET | `/api/attachments/file/{id}` | Stored image bytes |
| DELETE | `/api/attachments/{id}` | Remove an attachment |
| POST | `/api/attachments/run-code` | Execute an approved chart script |
| GET | `/api/capabilities/vision-check` | Can this model read images? |
| GET | `/api/websearch/status` | Which search provider is usable |

Interactive docs at <http://127.0.0.1:8000/docs>.

Both streaming endpoints use SSE over POST, consumed with `fetch` +
`ReadableStream` (the native `EventSource` cannot send a POST body). Ollama
reports some failures *inside* a `200` response body, so every stream frame is
checked for an error and surfaced as a terminal `error` event.

## Project layout

```
backend/
  app/
    main.py              FastAPI app, CORS, startup Ollama probe
    config.py            Settings from env / .env
    models/
      schemas.py         Pydantic request/response models
      catalog.py         Catalog loader
      catalog.json       The 16 curated models
    services/
      specs.py           CPU / RAM / disk detection (psutil + stdlib fallbacks)
      gpu.py             Layered GPU + VRAM detection
      scoring.py         Filter, score, reasoning generation
      ollama.py          Async Ollama REST client
    routers/
      system.py          /health, /specs, /recommendations
      models.py          /models/*, pull streaming
      chat.py            /chat streaming
      attachments.py     Upload, status, chart execution
      capabilities.py    Vision gating
    sse.py               SSE framing + anti-buffering headers
frontend/
  src/
    App.tsx              Step machine: scan -> choose -> download -> chat
    api/
      config.ts          Resolves API base URL (single Tauri swap point)
      sseClient.ts       fetch + ReadableStream SSE reader
      client.ts          Typed REST wrappers
    hooks/               useHealth, useSystemSpecs, useModelPull, useChat,
                         useAttachments, useVisionCheck, useChartRunner,
                         useWebSearchStatus
    components/          HealthGate, SpecsPanel, ModelCard, ChatView, ...
    types/api.ts         Mirrors of the backend schemas
```

## Files in a chat

Attach a PDF, Word document, spreadsheet, CSV, text file or image to any
conversation. Files belong to the chat you uploaded them to — retrieval never
reaches across conversations.

**How a document becomes answerable.** Text is extracted, split on paragraph
boundaries into ~1200-character chunks with overlap, and indexed with SQLite's
built-in FTS5. At question time the chunks are ranked with BM25 and the best few
are injected into the prompt.

FTS5 ships inside SQLite, so this needs **no embedding model and no download** —
indexing a document is a plain INSERT. The tradeoff is honest: it matches words,
not meaning, so a question worded entirely differently from the document can
miss. Two things blunt that:

- Query terms are OR-ed rather than AND-ed, so partial overlap still ranks, and
  BM25 puts a chunk matching several terms above one matching a single term.
- A query that matches nothing falls back to the document's opening chunks,
  because showing the model the document beats showing it nothing.

Stopwords are dropped and punctuation stripped before the query reaches FTS5, so
a question containing `AND`, `NEAR(` or `%` searches for those words instead of
becoming a malformed FTS5 expression.

Indexing runs *after* the upload responds, so a long PDF does not look like a
stalled upload. Each file carries a status the UI polls until it reads `ready`.

**Spreadsheets are handled differently from prose.** Chunked rows retrieve
badly — "what were Q3 sales" matches the header, not the number — so tabular
files also produce a *data summary*: real column names, dtypes, a row preview
and `describe()` statistics. That summary is **always** injected rather than
retrieved, because the model needs actual column names before it can write code
against them, and a schema that only appeared on a lucky keyword match would
make generated code intermittently wrong.

**Attachments belong to the message they were sent with.** Files clear out of
the composer the moment you send, and stay attached to that turn in the
transcript. Later questions never re-attach them — the chat already knows which
document it is talking about, because retrieval covers everything uploaded to
the conversation.

## Charts from your data

Ask for a graph and the model replies with a Python block using pandas and
matplotlib against your uploaded file. The code is shown with a **Run chart**
button — nothing executes until you click it.

Model-written code running locally is the one genuinely risky part of this
feature, so it is bounded four independent ways:

| Limit | What it stops |
| --- | --- |
| AST import allowlist | `os`, `subprocess`, `socket`, `requests` and friends are rejected before a process starts |
| Source pattern check | `eval`, `exec`, `__import__`, `open`, `__subclasses__` |
| Separate `-I` process, temp cwd | A crash cannot take the API down; the script only sees files from this conversation |
| 30s timeout, capped stdout | Runaway loops and print floods |

None of this is a boundary strong enough for deliberately hostile code — that
needs a container. It is defence against a model steered wrong by prompt
injection hidden in an uploaded document, combined with the fact that you have
to approve each run.

matplotlib is forced onto the `Agg` backend and `plt.show()` is neutralized;
without that, a `show()` call opens a GUI window and hangs the subprocess.

## Images and text-only models

Most local models cannot see. Capability comes from Ollama's own `/api/show`
`capabilities` array rather than guessing from the tag name, because a wrong
guess means either a silently ignored image or a blocked upload that would have
worked.

Attach an image to a text-only model and sending is **blocked**, with a
one-click switch to a vision model you already have installed. Nothing is ever
recommended for download — which models to install is your call. The same rule
is enforced in `/api/chat`, so it cannot be bypassed by a hand-made request; an
image is never quietly dropped.

Manage Models and the model picker both label every model **Text only** or
**Text + images**, read from Ollama's capability data for installed models.

**Images survive a model switch.** When the vision model *you selected* answers
about an image, a second background generation with that same model describes it
in detail — visible text, numbers,
chart axes and values — and stores that description on the attachment. Every
later turn injects it as plain text, so you can switch to a text-only model and
keep asking about a picture it cannot see.

The description is deliberately a separate call, not a reuse of the assistant's
reply. Reusing the answer to "what's the total?" would record only the total,
and a later question about anything else in the image would find nothing.

## Real-time information

Local models only know what they were trained on. The **Web** toggle next to the
message box lets Buddy look things up before answering — live prices, weather,
news, anything after the model's cutoff. Results are injected as cited sources,
and the reply shows which pages were consulted.

**It is off by default and set per message.** Sending your question to a search
engine is the one thing in Buddy that leaves the machine, so it stays an explicit
choice rather than a setting you enable once and forget.

**With the toggle off, the model says so instead of guessing.** Ask for today's
Bitcoin price and it replies that this needs live information and tells you to
turn on Web — rather than either inventing a number or reciting a stale one from
training. Ordinary questions are answered normally and never mention the toggle.

**Why a toggle rather than letting the model decide.** Ollama supports tool
calling, and the obvious design is to hand the model a `web_search` tool. Tested
against the installed models, it misfired in both directions: `qwen3:4b` answered
"the 2026 Super Bowl has not yet occurred (as of 2023)" instead of searching,
while `llama3.2:3b` called the tool to compute `17 x 23`. Vision models report no
tool support at all, so the feature would have silently never worked there. A
toggle behaves identically on every model and is never surprising.

### Providers

| Provider | When it is used | Notes |
| --- | --- | --- |
| **SearXNG** | Automatically, if one answers at `SEARXNG_URL` | Self-hosted, private, real JSON API. Preferred when present. |
| **DuckDuckGo** | Otherwise | No API key, works on a bare machine. HTML scrape, so markup changes can break it. |

Buddy detects SearXNG once per session and uses it if reachable — it does not
install or manage one. To get private search, run your own instance (its
container needs Docker) and point `SEARXNG_URL` at it; Buddy switches over on the
next restart with no code change.

### How a search turn works

1. The question is sent to the provider; up to 6 results come back.
2. The top 3 result pages are fetched **concurrently** and reduced to readable
   text — snippets alone are enough for a price but too thin to explain anything.
3. Each fetch may fail independently. Plenty of sites refuse unattended requests
   (Wikipedia answers `403` to a plain GET; so do several exchanges), so a
   refusal degrades that result to snippet-only rather than failing the search.
4. Results are injected as numbered sources with an instruction to prefer them
   over training data, and to stop treating a post-cutoff date as the future.

Search composes with uploaded files: with a document attached and the toggle on,
one answer can cite your private notes *and* the live market price.

## Configuration

Both `.env` files are optional; the defaults work out of the box.

**`backend/.env`** — see `.env.example`. Notably `OLLAMA_HOST`, `CORS_ORIGINS`,
`OLLAMA_MODELS` (free disk space is measured on the volume that actually holds
the model store), and `SEARXNG_URL` (default `http://127.0.0.1:8888`; a reachable
instance there is preferred over DuckDuckGo for web search).

**`frontend/.env`** — `VITE_API_BASE`, defaulting to `http://127.0.0.1:8000`.

## Adding models to the catalog

Append an entry to `backend/app/models/catalog.json` and restart the backend. The
`min_ram_gb` / `recommended_ram_gb` figures are what drive scoring; the rule of
thumb used throughout is `min_ram ≈ download_size + 1.5 GB` of runtime overhead,
with `recommended ≈ min + 2 GB` of headroom.

## Packaging as a desktop app

The frontend was built to be wrapped later. All network access goes through
`src/api/config.ts`, there are no `window.location`-derived URLs, asset paths are
relative (`base: './'`), and the backend's CORS list already includes Tauri's dev
port. Wrapping it means pointing a Tauri/Electron shell at the built `dist/`,
spawning the Python backend as a sidecar, and setting `VITE_API_BASE`.
