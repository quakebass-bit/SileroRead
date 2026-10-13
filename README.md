
# 📖 SileroRead

Self-hosted TTS reader with three engines: Silero (offline, Russian), Piper (offline, RU+EN), Edge TTS (cloud, RU+EN). Runs on Android via proot-distro Ubuntu, Windows, and Linux. No GPU required.

---

## ✨ Features

- 3 TTS engines: Silero (offline neural RU), Piper (offline RU+EN), Edge (cloud RU+EN)
- Automatic fallback: Silero → Edge Svetlana on failure
- Sentence-level chunking with abbreviation protection
- Text normalization: numbers, dates, abbreviations, Latin → Russian (via `ru-normalizr`)
- Predictive prefetch: 6 chunks ahead, 12-chunk buffer
- 10-minute disk cache
- Multi-format: `.txt`, `.fb2`, `.docx`, `.pdf`
- Reader UI: 4 themes, font/size, speed 0.75x–2x
- PWA, reading progress, lock screen, Media Session API

---

## 🏗️ Architecture

```
Browser (index.html) ←HTTP→ Flask (app.py)
                                │
                ┌───────────────┼───────────────┐
                ▼               ▼               ▼
            Silero          Piper           Edge TTS
            (torch)         (binary)        (CLI subprocess)
            offline         offline         cloud
```

---

## 🚀 Install — Android (proot-distro Ubuntu)

### 1. Termux: install proot-distro

```bash
pkg update && pkg upgrade -y
pkg install proot-distro -y
proot-distro install ubuntu
proot-distro login ubuntu
```

### 2. Ubuntu: system packages

```bash
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv python3.13-dev git wget build-essential
```

`python3.13-dev` is required for compiling `DAWG2` (dependency of `pymorphy3`). Without it, `pip install ru-normalizr` fails with `Python.h: No such file or directory`.

### 3. Clone project

```bash
cd ~
git clone https://github.com/quakebass-bit/SileroRead.git
cd SileroRead
```

### 4. Create venv

```bash
python3 -m venv venv
source venv/bin/activate
```

### 5. Install Python deps

**ARM64 — use CPU wheel index to avoid 3 GB of CUDA packages:**

```bash
pip install --upgrade pip
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
pip install numpy scipy Flask edge-tts
```

Verify torch:

```bash
python -c "import torch; print(torch.__version__); print(torch.cuda.is_available())"
```

Expected:
```
2.6.0+cpu
False
```

### 6. Install text normalization

```bash
pip install ru-normalizr
```

If `pip install ru-normalizr` fails with `struct.error: unpack requires a buffer of 4 bytes` at runtime — remove the broken compiled DAWG2 and use the pure-Python `dawg2-python` instead:

```bash
pip uninstall -y DAWG2
pip install dawg2-python
python -c "from ru_normalizr import Normalizer, NormalizeOptions; n = Normalizer(NormalizeOptions.tts()); print(n.normalize('В 1980 году'))"
```

Expected output:
```
В тысяча девятьсот восьмом году
```

### 7. Download models

**Silero v5 (Russian, ~60 MB):**

```bash
mkdir -p models
wget -O models/v5_ru.pt https://models.silero.ai/models/tts/ru/v5_ru.pt
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

**Piper voices:**

```bash
wget -O models/ru_RU-irina-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx
wget -O models/ru_RU-irina-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx.json
wget -O models/ru_RU-ruslan-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx
wget -O models/ru_RU-ruslan-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/ru/ru_RU/ruslan/medium/ru_RU-ruslan-medium.onnx.json
wget -O models/en_US-lessac-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
wget -O models/en_US-lessac-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
wget -O models/en_US-ryan-medium.onnx https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx
wget -O models/en_US-ryan-medium.onnx.json https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/medium/en_US-ryan-medium.onnx.json
```

### 8. Run

```bash
python app.py
```

Open on phone: `http://127.0.0.1:5001`

From other device on same Wi-Fi:
```bash
ip addr show | grep inet
```
Then open `http://<PHONE_IP>:5001`.

---

## 🚀 Install — Windows

### 1. Install Python 3.10+ from python.org (check "Add to PATH")

### 2. Clone and setup

```cmd
git clone https://github.com/quakebass-bit/SileroRead.git
cd SileroRead
python -m venv venv
venv\Scripts\activate
```

### 3. Install deps

```cmd
pip install --upgrade pip
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install numpy scipy Flask edge-tts ru-normalizr
```

### 4. Models

Download manually into `models/`:
- Silero v5: https://models.silero.ai/models/tts/ru/v5_ru.pt
- Piper: download `piper_windows_amd64.zip` from https://github.com/rhasspy/piper/releases, unpack into `piper/`, ensure `piper/piper.exe` exists
- Piper voices: same URLs as Android step 7

### 5. Run

```cmd
python app.py
```

Open `http://127.0.0.1:5001`.

---

## 🚀 Install — Linux (x86_64 / ARM64)

```bash
git clone https://github.com/quakebass-bit/SileroRead.git
cd SileroRead
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
```

**x86_64:**

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

**ARM64:**

```bash
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu
```

```bash
pip install numpy scipy Flask edge-tts ru-normalizr
```

Then follow Android step 7 for models and Piper binary (use the release for your architecture).

```bash
python app.py
```

---

## 🔄 Update from GitHub

```bash
cd ~/SileroRead
git pull
source venv/bin/activate
pip install numpy scipy Flask edge-tts ru-normalizr
python app.py
```

If `git pull` fails on local changes:

```bash
cd ~/SileroRead
git checkout -- .
git pull
```

---

## 🎮 Usage

1. Open UI.
2. Load `.txt` / `.fb2` / `.docx` / `.pdf`.
3. Pick voice:
   - **Silero** — offline, best Russian quality, first request slow (~10 s)
   - **Piper** — offline, fast, low memory
   - **Edge** — cloud, needs internet
4. Press **▶ READ**.

Controls: **▶ / ⏸ / ⏹**, speed 0.75x–2x, font family/size, theme (dark/light/sepia/OLED), lock.

---

## 📊 Performance (Android ARM64 mid-range CPU)

| Operation | Time |
|---|---|
| Server startup | ~1 s |
| First Silero request (torch + model load) | 5–15 s |
| Subsequent Silero chunks | 0.5–2 s |
| Piper chunks | 0.3–1 s |
| Edge TTS chunks | 0.8–2 s |
| `ru-normalizr` per sentence | 0.5–2 ms |

RAM with Silero loaded: ~600–900 MB.

CPU load:
- `ru-normalizr`: negligible
- Piper: 1 core, 100% during synthesis
- Silero: 2 cores, 100% during synthesis (phone gets warm)
- Edge: only network

---

## ⚠️ Common Issues

### `pip install torch` downloads 3 GB of `nvidia-*` packages

You got the CUDA build. Abort and reinstall CPU build:

```bash
pip uninstall -y torch
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
```

### `torch` not found on ARM64 via default PyPI

PyPI has no aarch64 wheels. Always use `--index-url https://download.pytorch.org/whl/cpu` on ARM.

### `fatal error: Python.h: No such file or directory` during `pip install ru-normalizr`

Missing Python dev headers. On Ubuntu:

```bash
apt install -y python3.13-dev
```

Replace `3.13` with your Python version (`python3 --version`).

### `struct.error: unpack requires a buffer of 4 bytes`

Broken compiled `DAWG2`. Replace with pure-Python version:

```bash
pip uninstall -y DAWG2
pip install dawg2-python
```

### `TypeError: normalize() got an unexpected keyword argument 'mode'`

You're using `ru-normalizr` ≥ 1.3.0. Use the new API:

```python
from ru_normalizr import Normalizer, NormalizeOptions
n = Normalizer(NormalizeOptions.tts())
n.normalize("В 1980 году")
```

### `No module named 'scipy'`

Silero v5 requires `scipy` (v4 did not):

```bash
pip install scipy
```

### Silero takes 10+ seconds on first request

Normal — torch loads the model into memory on first use. Subsequent requests are fast.

### Edge TTS returns HTTP 403

Avoided by calling `edge-tts` as CLI subprocess. If it still fails:

```bash
pip install --upgrade edge-tts
```

### `Silero model not found`

```bash
wget -O models/v5_ru.pt https://models.silero.ai/models/tts/ru/v5_ru.pt
```

---

## 🛠️ Configuration

`app.py` constants:

| Constant | Default | Meaning |
|---|---|---|
| `CACHE_TTL_SECONDS` | `600` | Audio cache lifetime (seconds) |
| `MODELS_DIR` | `./models` | Silero/Piper models |
| `AUDIO_DIR` | `./static/audio` | Generated audio cache |
| `torch.set_num_threads(2)` | `2` | CPU threads for Silero. Higher = faster but hotter; lower = cooler but slower |

`index.html` constants:

| Constant | Default | Meaning |
|---|---|---|
| `PREFETCH_AHEAD` | `6` | Chunks prefetched ahead |
| `MAX_PLAYLIST` | `12` | Buffer cap (keep at `2 × PREFETCH_AHEAD`) |

---

## 📁 Project structure

```
SileroRead/
├── app.py                  # Flask server
├── index.html              # UI (single file)
├── manifest.json           # PWA manifest
├── sw.js                   # Service worker
├── models/                 # TTS models
│   ├── v5_ru.pt            # Silero v5
│   ├── ru_RU-irina-medium.onnx
│   └── ...
├── piper/                  # Piper binary
│   └── piper
├── static/audio/           # Generated audio (auto-cleaned)
└── progress.json           # Saved positions
```

---

## 🔧 How it works

### Text normalization

Before synthesis, text passes through `ru-normalizr` in TTS mode:

```python
from ru_normalizr import Normalizer, NormalizeOptions
_normalizer = Normalizer(NormalizeOptions.tts())  # created once at startup
normalized = _normalizer.normalize(text)
```

Expands: numbers (`1980` → `тысяча девятьсот восьмой`), dates, times (`10:07` → `десять часов семь минут`), abbreviations (`т.е.` → `то есть`), Latin (`iPhone` → `айфоун`), initials, currency, phone numbers.

Applied to **all three engines** (Silero, Piper, Edge) — none of them read raw digits reliably.

### Sentence chunking

Splits on `.`, `?`, `!` with protection for:
- Russian abbreviations: `т.е.`, `т.к.`, `т.д.`, `г-н.`, `проф.`, etc.
- English: `Mr.`, `Mrs.`, `Dr.`, `e.g.`, `i.e.`, `etc.`
- Initials: `А.С.`, `J.K.`
- Decimals: `3.14`

Groups into 1–2 sentence chunks (max 300 chars). Chunks <20 chars merged into neighbors.

### Prefetch queue

Browser keeps rolling buffer of up to 12 chunks. While chunk N plays, N+1..N+6 are generated in background. If buffer empties, playback pauses — rare with 6-chunk lookahead.

### CLI orchestration for Edge TTS

Instead of `edge_tts` Python API (WebSocket 403 on Windows/Termux), runs as subprocess:

```python
subprocess.run([sys.executable, '-m', 'edge_tts',
                '-f', temp_txt, '-v', voice,
                '--write-media', output_path])
```

Each request = clean subprocess = no 403.

---

## 📄 License

MIT.

---

## 🙏 Credits

- [Silero Models](https://github.com/snakers4/silero-models) — Russian neural TTS
- [Piper](https://github.com/rhasspy/piper) — fast offline TTS
- [Edge TTS](https://github.com/rany2/edge-tts) — Microsoft voices via CLI
- [ru-normalizr](https://github.com/denismakogon/ru-normalizr) — Russian text normalization for TTS
- [Flask](https://flask.palletsprojects.com/)
- [Mammoth.js](https://github.com/mwilliamson/mammoth.js) — DOCX
- [PDF.js](https://mozilla.github.io/pdf.js/) — PDF

---

**Enjoy. 📚🎧**
`
Пушь на GitHub, заходи с телефона — эмодзи должны отобразиться нормально. 🚀
