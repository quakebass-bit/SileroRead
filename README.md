Держи. Полноценный `README.md` на английском, со всеми шагами установки, описанием фич и инструкциями для разных платформ. Просто скопируй в файл `README.md` в корне проекта и запушь.

```markdown
# 📖 SileroRead

**A self-hosted text-to-speech reader for books with three TTS engines — two offline, one cloud.**

Built and tested to run entirely on a phone (proot-distro Ubuntu on Android) with no GPU and no external API keys. Also works on Windows and Linux.

---

## ✨ Features

- **Three TTS engines** in one app:
  - 🟣 **Silero** — offline, neural, high-quality Russian (5 voices)
  - 🟢 **Piper** — offline, fast, multi-language (Russian + English)
  - 🔵 **Edge TTS** — cloud, Microsoft neural voices (Russian + English)
- **Automatic fallback** — if Silero fails, it silently switches to Edge TTS
- **Sentence-level chunking** — long paragraphs are split into 1–2 sentence chunks with abbreviation protection (`т.е.`, `Mr.`, initials, decimals)
- **Predictive prefetch** — while the current chunk plays, the next 6 chunks are generated in the background (12-chunk buffer)
- **10-minute audio cache** — repeated requests are served instantly from disk
- **Multi-format reader** — supports `.txt`, `.fb2`, `.docx`, `.pdf`
- **Reader UI** — themes (dark / light / sepia / OLED), font family, font size, playback speed (0.75x – 2x)
- **PWA support** — install as an app on Android/iOS
- **Reading progress** — saved per book, restored on reopen
- **Lock screen** — prevents accidental taps while listening
- **Media Session API** — control playback from your phone's lock screen / bluetooth headset

---

## 📱 Tested on

- **Android** via [proot-distro](https://github.com/termux/proot-distro) Ubuntu (ARM64)
- **Windows 10/11** (x86_64)
- **Linux** (x86_64, ARM64)

No GPU required — everything runs on CPU.

---

## 🏗️ Architecture

```
┌────────────────────┐        ┌─────────────────────┐
│  Browser (index)   │◄──────►│  Flask (app.py)     │
│  - Parses files    │  HTTP  │  - /api/split       │
│  - Prefetch queue  │        │  - /synthesize      │
│  - Audio player    │        │  - /save_progress   │
└────────────────────┘        └──────────┬──────────┘
                                         │
                    ┌────────────────────┼─────────────────────┐
                    ▼                    ▼                     ▼
              ┌──────────┐        ┌──────────┐          ┌──────────┐
              │  Silero  │        │  Piper   │          │ Edge TTS │
              │ (torch)  │        │ (binary) │          │  (CLI)   │
              │ offline  │        │ offline  │          │  cloud   │
              └──────────┘        └──────────┘          └──────────┘
```

---

## 🚀 Installation

### Option 1 — Android (proot-distro Ubuntu) — RECOMMENDED

#### 1. Install Termux and proot-distro

Install [Termux](https://f-droid.org/packages/com.termux/) from F-Droid (not Google Play — that version is outdated).

```bash
pkg update && pkg upgrade -y
pkg install proot-distro -y
proot-distro install ubuntu
proot-distro login ubuntu
```

#### 2. Inside Ubuntu — install system dependencies

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git wget build-essential python3-dev
```

#### 3. Clone the project

```bash
cd ~
git clone https://github.com/quakebass-bit/SileroRead.git
cd SileroRead
```

#### 4. Create venv and activate it

```bash
python3 -m venv venv
source venv/bin/activate
```

#### 5. Install Python dependencies

**⚠️ Important for ARM64:** install torch from the official CPU wheel index. The default PyPI index will try to pull CUDA libraries (~3 GB of useless NVIDIA packages).

```bash
pip install --upgrade pip
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
pip install numpy Flask edge-tts
```

Verify torch installed correctly:

```bash
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

Expected output:
```
2.6.0+cpu
False
```

#### 6. Download models

**Silero (Russian, ~60 MB):**
```bash
mkdir -p models
wget -O models/v4_ru.pt https://models.silero.ai/models/tts/ru/v4_ru.pt
```

**Piper binary (ARM64):**
```bash
mkdir -p piper
cd piper
wget https://github.com/rhasspy/piper/releases/download/v1.2.0/piper_arm64.tar.gz
tar -xzf piper_arm64.tar.gz --strip-components=1
chmod +x piper
rm piper_arm64.tar.gz
cd ..
```

**Piper voices (Russian + English):**

```bash
# Russian — Irina
wget -O models/ru_RU-irina-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx
wget -O models/ru_RU-irina-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json

# Russian — Ruslan
wget -O models/ru_RU-ruslan-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx
wget -O models/ru_RU-ruslan-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json

# English — Lessac
wget -O models/en_US-lessac-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget -O models/en_US-lessac-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json

# English — Ryan
wget -O models/en_US-ryan-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
wget -O models/en_US-ryan-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json
```

#### 7. Run the server

```bash
python app.py
```

Open in your phone's browser: `http://127.0.0.1:5001`

From another device on the same Wi-Fi network: `http://<PHONE_IP>:5001`
(get IP with `ip addr show | grep inet`)

---

### Option 2 — Windows (x86_64)

#### 1. Install Python 3.10+

Download from [python.org](https://www.python.org/downloads/). Check **"Add Python to PATH"** during install.

#### 2. Clone the project

```cmd
git clone https://github.com/quakebass-bit/SileroRead.git
cd SileroRead
```

#### 3. Create venv and activate it

```cmd
python -m venv venv
venv\Scripts\activate
```

#### 4. Install dependencies

```cmd
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install numpy Flask edge-tts
```

#### 5. Download models

Follow **step 6** from the Android instructions above. Use a browser if `wget` is not available — just save files into the `models/` folder with the exact names shown.

For Piper on Windows, download `piper_windows_amd64.zip` from the [Piper releases page](https://github.com/rhasspy/piper/releases), unpack into `piper/`, ensure `piper/piper.exe` exists.

#### 6. Run

```cmd
python app.py
```

Open `http://127.0.0.1:5001`.

---

### Option 3 — Linux (x86_64 / ARM64)

```bash
git clone https://github.com/quakebass-bit/SileroRead.git
cd SileroRead
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# For x86_64:
pip install torch --index-url https://download.pytorch.org/whl/cpu

# For ARM64 (aarch64):
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu

pip install numpy Flask edge-tts
```

Then follow **step 6** from the Android instructions for models and Piper binary (use the appropriate Piper release for your arch).

```bash
python app.py
```

---

## 🎮 Usage

1. Open the web UI.
2. Click **📁 Choose File** and load a `.txt`, `.fb2`, `.docx`, or `.pdf`.
3. Pick a voice from the dropdown:
   - **Silero** (offline, Russian) — best quality, first request slow
   - **Piper** (offline, Russian/English) — fast, low memory
   - **Edge** (cloud, Russian/English) — requires internet
4. Press **▶ READ**.

The reader will:
- Split the book into sentence-level chunks (server-side).
- Prefetch up to 6 chunks ahead while playing.
- Highlight the current paragraph and scroll to it.
- Save your progress automatically.

**Controls:**
- **▶ / ⏸ / ⏹** — play / pause / stop
- **Speed** — 0.75x to 2x
- **Font family / size** — reader preferences
- **Theme** — dark / light / sepia / OLED
- **🔒 Lock** — prevent accidental taps

---

## 📊 Performance (Android, ARM64, mid-range CPU)

| Operation | Time |
|---|---|
| Server startup | ~1 s |
| First Silero request (torch + model load) | 5–15 s |
| Subsequent Silero chunks (1 sentence) | 0.5–2 s |
| Piper chunks | 0.3–1 s |
| Edge TTS chunks | 0.8–2 s |

Memory footprint with Silero loaded: **~600–900 MB RAM**.

---

## ⚠️ Common Issues

### `pip install torch` downloads 3 GB of `nvidia-*` packages

You installed the CUDA build by mistake. Abort, uninstall, reinstall:

```bash
pip uninstall -y torch
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
```

### `torch` fails to install on ARM64 via default PyPI

The default PyPI index does not carry aarch64 wheels. Always use `--index-url https://download.pytorch.org/whl/cpu` on ARM.

### Silero takes 10+ seconds on first request

This is normal — torch loads the model into memory the first time. Subsequent requests are fast. If you want the model warm at startup, change `get_silero_model()` in `app.py` to run once inside `if __name__ == '__main__':`.

### Edge TTS returns HTTP 403

This happens when the Python asyncio event loop on Windows mishandles WebSocket connections. We avoid this by **calling `edge-tts` as a CLI subprocess** instead of using the Python API directly. If it still fails, upgrade to the latest `edge-tts`:

```bash
pip install --upgrade edge-tts
```

### `Silero model not found` error

Download `v4_ru.pt` into `models/`:

```bash
wget -O models/v4_ru.pt https://models.silero.ai/models/tts/ru/v4_ru.pt
```

---

## 🛠️ Configuration

Edit these constants at the top of `app.py`:

| Constant | Default | Meaning |
|---|---|---|
| `CACHE_TTL_SECONDS` | `600` | Audio cache lifetime (seconds) |
| `MODELS_DIR` | `./models` | Where Silero/Piper models live |
| `AUDIO_DIR` | `./static/audio` | Where generated audio is stored |
| `torch.set_num_threads(2)` | `2` | CPU threads for Silero. Increase for faster synthesis on multi-core devices, decrease to save battery |

Frontend knobs (in `index.html`):

| Constant | Default | Meaning |
|---|---|---|
| `PREFETCH_AHEAD` | `6` | Chunks prefetched ahead of playback |
| `MAX_PLAYLIST` | `12` | Hard cap on buffer size (should be `2 × PREFETCH_AHEAD`) |

---

## 📁 Project structure

```
SileroRead/
├── app.py                  # Flask server (all three engines)
├── index.html              # UI (single-file, no build step)
├── manifest.json           # PWA manifest
├── sw.js                   # Service worker
├── models/                 # TTS models (Silero + Piper voices)
│   ├── v4_ru.pt
│   ├── ru_RU-irina-medium.onnx
│   └── ...
├── piper/                  # Piper binary (per platform)
│   └── piper
├── static/
│   └── audio/              # Generated audio cache (auto-cleaned)
└── progress.json           # Saved reading positions
```

---

## 🔧 How it works

### Sentence chunking (`split_into_chunks`)

Splits text on `.`, `?`, `!` while protecting:
- Russian abbreviations: `т.е.`, `т.к.`, `т.д.`, `г-н.`, `проф.`, `акад.`, etc.
- English abbreviations: `Mr.`, `Mrs.`, `Dr.`, `e.g.`, `i.e.`, `etc.`
- Initials: `А.С. Пушкин`, `J. K. Rowling`
- Decimals: `3.14`, `1.5`

Then groups sentences into chunks of 1–2, capped at 300 characters. Chunks shorter than 20 characters are merged into neighbors.

### Prefetch queue

The browser keeps a rolling buffer of up to 12 chunks. While chunk *N* plays in the `<audio>` element, chunks *N+1* through *N+6* are being generated by the server. If the buffer empties (slow network / slow synthesis), playback waits — but with 6-chunk lookahead this is rare.

### CLI orchestration for Edge TTS

Instead of using the `edge_tts` Python API (which has WebSocket issues on Windows and Termux), we shell out:

```python
subprocess.run([sys.executable, '-m', 'edge_tts',
                '-f', temp_txt, '-v', voice,
                '--write-media', output_path])
```

Each request runs in a clean subprocess — no shared event loop, no 403 errors.

---

## 📄 License

MIT — do whatever you want.

---

## 🙏 Credits

- [Silero Models](https://github.com/snakers4/silero-models) — Russian TTS
- [Piper](https://github.com/rhasspy/piper) — fast offline TTS
- [Edge TTS](https://github.com/rany2/edge-tts) — Microsoft neural voices via CLI
- [Flask](https://flask.palletsprojects.com/) — web framework
- [Mammoth.js](https://github.com/mwilliamson/mammoth.js) — DOCX parsing
- [PDF.js](https://mozilla.github.io/pdf.js/) — PDF parsing

---

**Enjoy listening. 📚🎧**
```

