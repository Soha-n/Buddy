# Buddy

A Windows desktop-style web app that looks at your hardware, recommends the three
Ollama models that will actually run well on it, downloads the one you pick, and
then lets you chat with it. Everything runs locally — no data leaves the machine.

- **Frontend:** TypeScript + React 19 + Vite
- **Backend:** Python + FastAPI
- **Inference:** Ollama (local)
- **Retrieval:** SQLite FTS5 full-text search (no extra models)
- **Live data:** weather and web search work out of the box, no keys, no signup

## The flow

1. **Scan** — detects CPU, RAM, GPU + VRAM, free disk space and OS.
2. **Choose** — scores 16 curated models against those specs and shows the top 3,
   each with a plain-language explanation of *why* it fits your machine.
3. **Download** — pulls the selected model with live progress, speed and ETA.
4. **Chat** — streams the reply token by token.
5. **Attach** — drop in a PDF, Word doc, spreadsheet, CSV or image and ask
   questions about it. Retrieval is scoped to that one conversation.
6. **Search** — flip the Web toggle on and Buddy decides how much lookup each
   question deserves: none, an API call, snippets, or several full pages.

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
| GET | `/api/context/location` | Location used for "here" questions |
| POST | `/api/context/location` | Override the detected city |
| DELETE | `/api/context/location` | Forget the override, re-detect |

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
                         useWebSearchStatus, useLocation
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

Search works on first launch with nothing to configure. Buddy installs and runs
its **own SearXNG** — no Docker, no API key, no signup — and uses it for every
query.

| Provider | When | Notes |
| --- | --- | --- |
| **Built-in SearXNG** | always, once ready | Buddy's own instance on loopback. Unlimited, private, nothing shared. |
| **Your API key** | if the built-in one is down | `SEARCH_PROVIDER` + `SEARCH_API_KEY` (brave / tavily). |
| **Public search** | while installing, or on failure | DuckDuckGo. Keeps search from ever being unavailable. |

First launch clones SearXNG and installs it in the background — a few minutes —
during which public search answers everything. The status label upgrades itself
from "public search" to "private search" when the swap happens; no reload needed.

**Web search is therefore never "not set up".** If the built-in instance is
starting, or dies, or was never installed, a query still gets answered.

### Search depth is decided per question

Treating every question the same is what makes assistants feel slow. "What time
is it" does not need six web results, and "compare solar vs wind" cannot be
answered from a one-line snippet. So each question is classified first, and the
classification sets the budget:

| Intent | What it does | Network | Typical |
| --- | --- | --- | --- |
| **none** | Answers from the model alone — maths, code, writing, questions about your attached files | nothing | ~1.5s |
| **direct** | Calls a purpose-built API — weather, time, date | 1 request | ~2-4s |
| **lookup** | Search snippets only, no page fetches — a price, a score, who someone is | 1 request | ~6s |
| **research** | Snippets plus 3 pages read in full — comparisons, how-tos, analysis | 4 requests | ~19s |

Measured on this machine with `llama3.2:3b`. The classifier is rule-based rather
than a model call: asking a local model to classify would add two to four seconds
to a feature whose whole purpose is spending less time, and small models classify
inconsistently.

Weather is a good example of why "direct" exists. Searching for the temperature
returns weather-site landing pages, and scraping one yields whatever number sat in
a marketing div — possibly for the wrong city. A weather API returns the
temperature for a named place, as a number.

Note that with a document attached, a question about *that document* stays local
even with the toggle on — searching the web for your own spreadsheet would return
strangers' data.

### Follow-up questions

> "What's the weather in Tokyo?" → *26°C, mainly clear*
> "what about tomorrow?"

The second message means nothing on its own: classified alone it looks like idle
chat, and searched verbatim it returns noise. Short messages that lean on the
conversation are rewritten into standalone ones first — `what about tomorrow` +
the previous turn becomes `weather tokyo tomorrow`, which routes to the weather
API for the right city.

The rewrite feeds only the machinery — the classifier and the search query. The
model always receives your real words and the full history, and resolves pronouns
itself.

### Weather

"current temp" works with no setup: the question is recognised, the user's own
location is resolved on-device, and [wttr.in](https://github.com/chubin/wttr.in)
returns real figures. Apache-2.0, no API key, no non-commercial clause, and
self-hostable — the only keyless weather source that clears the bar for a
commercial product.

Coordinates are preferred over a place name where the OS provides them, because
wttr.in resolves a bare country to an arbitrary town inside it ("India" landed on
Tamia, ~200km away). It also reports the nearest weather *station* rather than the
place asked about — "Tokyo" comes back as "Shikinejima" — so the name the user
used is kept, and the station is mentioned separately only when it genuinely
differs.

Google is deliberately not scraped: it answers automated requests with a consent
or CAPTCHA wall, so a Google-backed answer would fail in the user's hands rather
than in testing.

### Location and time

Every prompt carries the current date, time and timezone, read from this
computer's clock. No network, and it is what stops a model from calling a
current-year event "upcoming" because its training data ended earlier.

Location also comes from the device — no IP geolocation service is contacted. It
is resolved **lazily**, only when a question actually needs a place, then cached
for the session, so someone who only asks about maths and code never triggers a
location probe at all.

Ask "what's the current temperature" and Buddy uses your own location without
being told. Manage models → *Your context* shows what was found, states plainly
whether it came from regional settings, the OS location service, or from you, and
lets you correct it.

Precise coordinates require the Windows Location Service, which raises an OS
consent prompt — so that is a deliberate *Use precise location* click, never a
side effect of asking a question.

### Provider order

    built-in SearXNG → your API key → public search

Each failure is recorded rather than collapsing into a generic error, and the
chain is deep enough that a query always reaches *some* provider. If the built-in
instance is still installing, the request is served by public search and upgrades
silently once SearXNG is up.

Public search is scraped, so it can be throttled — DuckDuckGo starts answering
`202` with an anti-bot page instead of results. That is detected explicitly,
because a challenge page otherwise parses as "zero results" and looks like a query
that found nothing.

## Licensing and commercial use

Buddy is built to be sold and to run entirely on the user's device. Nothing is
sent to a server we operate, and a stock build makes **no calls to any
third-party service**.

### Bundled dependencies — all permissive

| Licence | Packages |
| --- | --- |
| MIT | fastapi, pydantic, pydantic-settings, python-docx, openpyxl, anyio, h11, react, react-dom, react-markdown, remark-gfm, vite |
| BSD-3-Clause | pypdf, numpy, pandas, httpx, uvicorn, starlette, psutil, idna |
| Apache-2.0 | python-multipart, typescript |
| PSF | matplotlib |
| MPL-2.0 | certifi |

No GPL or AGPL code is linked into Buddy. Ollama is MIT; SQLite (and its FTS5
full-text search) is public domain. All of it may be used, modified and sold
without restriction.

`certifi` is MPL-2.0, which is weak copyleft at file level: using and shipping it
is unrestricted, and only modifying that file would require publishing the change.

### Network services — opt-in, never bundled

These are hosted APIs, not libraries, so their **terms of service** apply
regardless of any licence. Several free tiers forbid commercial use, so Buddy
calls none of them unless configured:

| Service | Default | Why |
| --- | --- | --- |
| **SearXNG** (built in) | **automatic** | Buddy installs and runs it on the user's machine. No third party, no ToS, unlimited. AGPL-3.0, separate process, never linked into Buddy. |
| **Brave / Tavily API** | opt-in, user's key | Terms are between the user and the provider. Safe to ship. |
| **wttr.in** (weather) | **on** | Apache-2.0, keyless, no non-commercial clause, self-hostable. |
| **DuckDuckGo / Mojeek** | fallback only | Scraping breaches their ToS, so it serves only while the built-in instance is unavailable. `ALLOW_SCRAPING_FALLBACK=false` disables it outright. |
| IP geolocation | **removed** | Free tiers were non-commercial. Replaced with OS APIs. |

Weather works with nothing configured, because wttr.in's licence permits it. Web
search says what it needs rather than scraping anyway.

`WEATHER_BASE_URL` can point at your own wttr.in instance, which removes the last
third party from weather answers.

### Location comes from the device

No IP geolocation service is contacted. Three on-device sources, in order:

1. **Manual** — the user typed their city. Always wins.
2. **Windows Location Service** — real coordinates via
   `System.Device.Location.GeoCoordinateWatcher`. Raises an OS consent prompt, so
   it is only attempted when the user clicks *Use precise location*.
3. **Regional settings** — timezone and home country. No prompt, no network,
   always available. Coarse, and labelled as such in the UI.

Date, time and timezone always come from the system clock and never leave the
machine.

### How the built-in search is installed

SearXNG ships as source plus Docker, with no binary release, so Buddy installs it
from source into `backend/data/searxng`. Three Windows-specific obstacles had to
be handled, and they are worth knowing about if it ever needs debugging:

| Problem | Why | Fix |
| --- | --- | --- |
| No Docker | Only source + container images are published | Clone + dedicated virtualenv |
| Python 3.14 too new | SearXNG requires ≤ 3.12 | Its venv is built with 3.10–3.12, found via the `py` launcher |
| `import pwd` | `searx/valkeydb.py` imports a Unix-only module at load time | A shim is written into its venv; Buddy runs with Valkey disabled |
| Filenames with `:` | Some nginx/uwsgi templates cannot exist on Windows, aborting checkout | `git clone --no-checkout` then a separate checkout, so valid files land |
| Missing `tzdata` | Windows has no system zoneinfo, so some engines fail to load | `tzdata` is pip-installed into its venv |

It is bound to `127.0.0.1` only, started as a child process, and stopped when
Buddy exits.

**Licence note.** SearXNG is AGPL-3.0. It runs as a *separate process* reached
over HTTP — never imported or linked into Buddy — so its licence stays confined to
that process and Buddy's own code remains yours to license as you choose. It is
also downloaded on the user's machine at first run rather than redistributed
inside Buddy, which keeps the two clearly separate.

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
