import os
import glob
import time
import json
import shutil
import hashlib
import re
import sys
import gc
import ctypes
import subprocess
import wave
from threading import RLock, Lock
from flask import Flask, render_template, request, jsonify, send_from_directory

# ================= НОРМАЛИЗАЦИЯ ТЕКСТА =================
try:
    from ru_normalizr import Normalizer, NormalizeOptions
    NORMALIZATION_AVAILABLE = True
except ImportError:
    NORMALIZATION_AVAILABLE = False
    print("⚠️ ru-normalizr не установлен. Числа и символы не будут нормализованы.")

_normalizer = None
if NORMALIZATION_AVAILABLE:
    try:
        _normalizer = Normalizer(NormalizeOptions.tts())
        print("✅ ru-normalizr загружен")
    except Exception as e:
        print(f"⚠️ Не удалось создать Normalizer: {e}")
        NORMALIZATION_AVAILABLE = False


def normalize_text(text: str) -> str:
    if not NORMALIZATION_AVAILABLE or _normalizer is None:
        return text
    try:
        return _normalizer.normalize(text)
    except Exception as e:
        print(f"⚠️ Ошибка нормализации: {e}. Использую исходный текст.")
        return text


# ================= ABBREVIATIONS (JSON) =================
_ABBREV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'abbreviations.json')
try:
    with open(_ABBREV_FILE, 'r', encoding='utf-8') as _f:
        _ABBREV_DICT = json.load(_f)
    print(f"✅ abbreviations.json загружен ({len(_ABBREV_DICT)} записей)")
except Exception:
    _ABBREV_DICT = {}
    print("⚠️ abbreviations.json не найден — аббревиатуры без замены")


# ================= АНГЛИЙСКАЯ НОРМАЛИЗАЦИЯ (num2words) =================
try:
    from num2words import num2words
    EN_NORMALIZATION_AVAILABLE = True
    print("✅ English Normalizer (num2words) загружен")
except ImportError:
    EN_NORMALIZATION_AVAILABLE = False
    print("⚠️ num2words не установлен. Английские числа не будут озвучены. "
          "Установи: pip install num2words")


def normalize_en_text(text: str) -> str:
    """Превращает цифры и спецсимволы в английские слова для Silero."""
    if not text or not EN_NORMALIZATION_AVAILABLE:
        return text
    try:
        # 0. Аббревиатуры из JSON (длинные первыми)
        for abbr in sorted(_ABBREV_DICT.keys(), key=len, reverse=True):
            if abbr[-1] == '.':
                pattern = re.escape(abbr)
            else:
                pattern = r'\b' + re.escape(abbr) + r'\b'
            text = re.sub(pattern, ' ' + _ABBREV_DICT[abbr] + ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # 1. Валюты
        text = re.sub(r'\$(\d+(?:\.\d+)?)', r'\1 dollars', text)
        text = re.sub(r'£(\d+(?:\.\d+)?)', r'\1 pounds', text)
        text = re.sub(r'€(\d+(?:\.\d+)?)', r'\1 euros', text)

        # 2. Проценты
        text = re.sub(r'(\d+)%', r'\1 percent', text)

        # 3. Порядковые (1st, 2nd, 3rd, 21st, 4th)
        def replace_ordinal(match):
            val = int(match.group(1))
            return num2words(val, to='ordinal', lang='en')
        text = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', replace_ordinal, text, flags=re.IGNORECASE)

        # 4. Года (1800–2099)
        def replace_year(match):
            val = int(match.group(0))
            if 1800 <= val <= 2099:
                return num2words(val, to='year', lang='en')
            return num2words(val, lang='en')
        text = re.sub(r'\b(1[89]\d{2}|20\d{2})\b', replace_year, text)

        # 5. Дроби и десятичные (3.14 → three point one four)
        def replace_decimal(match):
            whole, frac = match.group(1), match.group(2)
            whole_str = num2words(int(whole), lang='en')
            frac_str = " ".join([num2words(int(d), lang='en') for d in frac])
            return f"{whole_str} point {frac_str}"
        text = re.sub(r'\b(\d+)\.(\d+)\b', replace_decimal, text)

        # 6. Обычные целые числа
        def replace_number(match):
            return num2words(int(match.group(0)), lang='en')
        text = re.sub(r'\b\d+\b', replace_number, text)

        # 7. Спецсимволы
        text = text.replace('&', ' and ')
        text = text.replace('@', ' at ')
        text = text.replace('+', ' plus ')

        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        print(f"⚠️ Ошибка EN-нормализации: {e}")
        return text
# =========================================================================


# ================= ОСВОБОЖДЕНИЕ ПАМЯТИ =================
try:
    _libc = ctypes.CDLL("libc.so.6")
    _HAS_MALLOC_TRIM = True
except Exception:
    _libc = None
    _HAS_MALLOC_TRIM = False


def _malloc_trim():
    """Возвращает освобождённые страницы glibc обратно ядру ОС (только Linux)."""
    if _HAS_MALLOC_TRIM:
        try:
            _libc.malloc_trim(0)
        except Exception:
            pass


def _get_rss_mb():
    """RSS текущего процесса в мегабайтах (только Linux)."""
    try:
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


def _get_swap_mb():
    """Swap текущего процесса в мегабайтах (только Linux)."""
    try:
        with open('/proc/self/status', 'r') as f:
            for line in f:
                if line.startswith('VmSwap:'):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


def _force_memory_release():
    """gc.collect + malloc_trim — освобождает память Python и возвращает её ОС."""
    gc.collect()
    _malloc_trim()


app = Flask(__name__)

# ================= ПУТИ =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
AUDIO_DIR = os.path.join(STATIC_DIR, 'audio')
MODELS_DIR = os.path.join(BASE_DIR, 'models')
PROGRESS_FILE = os.path.join(BASE_DIR, 'progress.json')
BOOKMARKS_FILE = os.path.join(BASE_DIR, 'bookmarks.json')
TEMP_TXT = os.path.join(BASE_DIR, 'temp_input.txt')

CACHE_TTL_SECONDS = 600

os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# ================= LOCK'И =================
silero_lock = RLock()
edge_tts_lock = Lock()

# ================= PIPER =================
def find_piper_executable():
    env_path = os.getenv("PIPER_CMD")
    if env_path:
        return env_path
    local_win = os.path.join(BASE_DIR, 'piper', 'piper.exe')
    local_linux = os.path.join(BASE_DIR, 'piper', 'piper')
    if os.name == 'nt' and os.path.exists(local_win):
        return local_win
    if os.path.exists(local_linux):
        return local_linux
    termux_path = "/root/piper/piper"
    if os.path.exists(termux_path):
        return termux_path
    system_piper = shutil.which("piper.exe") if os.name == 'nt' else shutil.which("piper")
    return system_piper if system_piper else ("piper.exe" if os.name == 'nt' else "piper")


PIPER_CMD = find_piper_executable()

MODELS = {
    'piper-en-lessac': 'en_US-lessac-medium.onnx',
    'piper-en-ryan': 'en_US-ryan-medium.onnx',
    'piper-ru-irina': 'ru_RU-irina-medium.onnx',
    'piper-ru-ruslan': 'ru_RU-ruslan-medium.onnx'
}

# ================= SILERO =================
SILERO_RU_MODEL = os.path.join(MODELS_DIR, 'v5_ru.pt')
SILERO_EN_MODEL = os.path.join(MODELS_DIR, 'v3_en.pt')

SILERO_RU_VOICES = {'aidar', 'baya', 'kseniya', 'xenia', 'eugene'}
SILERO_EN_VOICE_IDS = list(range(118))
SILERO_VOICES = ({f'silero-{v}' for v in SILERO_RU_VOICES} |
                 {f'silero-en_{i}' for i in SILERO_EN_VOICE_IDS})

SILERO_RELOAD_EVERY = 40
SILERO_RSS_LOG_EVERY = 10

silero_model = None
silero_model_path = None
torch_module = None
_silero_request_count = 0


def get_silero_model(model_path):
    global silero_model, silero_model_path, torch_module
    with silero_lock:
        if silero_model is not None and silero_model_path == model_path:
            return silero_model
        if silero_model is not None:
            print(f"♻️ Выгружаю {os.path.basename(silero_model_path or '?')}...", flush=True)
            del silero_model
            silero_model = None
            silero_model_path = None
            _force_memory_release()
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Модель Silero не найдена: {model_path}. "
                f"Скачай вручную с https://models.silero.ai/models/tts/"
            )
        if torch_module is None:
            import torch
            torch.set_num_threads(2)
            torch_module = torch
        print(f"⏳ Загружаю {os.path.basename(model_path)}...", flush=True)
        silero_model = torch_module.package.PackageImporter(model_path).load_pickle("tts_models", "model")
        silero_model.to(torch_module.device('cpu'))
        silero_model_path = model_path
        print(f"✅ Silero готова | RSS: {_get_rss_mb()} MB | Swap: {_get_swap_mb()} MB", flush=True)
        return silero_model


def save_tensor_to_wav(audio_tensor, output_path, sample_rate=24000):
    audio_data = (audio_tensor.clamp(-1.0, 1.0) * 32767).to(torch_module.int16).cpu().numpy()
    with wave.open(output_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data.tobytes())


def synthesize_silero(text, voice_id, output_path):
    """voice_id: 'xenia' (RU) или 'en_56' (EN)."""
    global silero_model, silero_model_path, _silero_request_count
    is_english = voice_id.startswith('en_')

    with silero_lock:
        if is_english:
            model = get_silero_model(SILERO_EN_MODEL)
            sample_rate = 24000
            with torch_module.no_grad():
                audio = model.apply_tts(text=text, speaker=voice_id, sample_rate=sample_rate)
        else:
            model = get_silero_model(SILERO_RU_MODEL)
            sample_rate = 24000
            with torch_module.no_grad():
                audio = model.apply_tts(
                    text=text, speaker=voice_id, sample_rate=sample_rate,
                    put_accent=True, put_yo=True
                )

        save_tensor_to_wav(audio, output_path, sample_rate)
        del audio
        _force_memory_release()

        _silero_request_count += 1
        if _silero_request_count % SILERO_RSS_LOG_EVERY == 0:
            print(
                f"📊 Silero #{_silero_request_count} | "
                f"RSS: {_get_rss_mb()} MB | Swap: {_get_swap_mb()} MB",
                flush=True
            )
        if _silero_request_count % SILERO_RELOAD_EVERY == 0:
            rss_before = _get_rss_mb()
            swap_before = _get_swap_mb()
            print(
                f"♻️ Перезагрузка Silero после {_silero_request_count} запросов "
                f"(RSS до: {rss_before} MB, Swap: {swap_before} MB)",
                flush=True
            )
            silero_model = None
            silero_model_path = None
            _force_memory_release()
            print(
                f"✅ Silero выгружена | RSS после: {_get_rss_mb()} MB | Swap: {_get_swap_mb()} MB",
                flush=True
            )


# ================= EDGE TTS =================
def run_edge_tts_cli(text, voice, output_path, timeout_sec=30):
    with edge_tts_lock:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Пустой фрагмент текста")
        with open(TEMP_TXT, 'w', encoding='utf-8') as f:
            f.write(clean_text)
        cmd = [
            sys.executable, '-m', 'edge_tts',
            '-f', TEMP_TXT,
            '-v', voice,
            '--write-media', output_path
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if proc.returncode != 0:
            err = proc.stderr.strip() or f"Код ошибки: {proc.returncode}"
            raise RuntimeError(err)
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise FileNotFoundError("Аудиофайл не создан")


# ================= КЭШ И УТИЛИТЫ =================
def cleanup_old_audio():
    now = time.time()
    for ext in ('*.mp3', '*.wav'):
        for f in glob.glob(os.path.join(AUDIO_DIR, ext)):
            try:
                if os.stat(f).st_mtime < (now - CACHE_TTL_SECONDS):
                    os.remove(f)
            except Exception:
                pass


def _has_speakable_chars(s: str) -> bool:
    """Проверяет, есть ли в строке хотя бы одна буква или цифра."""
    return bool(re.search(r'[A-Za-zА-Яа-яЁё0-9]', s or ''))


def split_into_chunks(text, max_chars=300, min_chars=20):
    cleaned = " ".join(text.split()).strip()
    if not cleaned or not _has_speakable_chars(cleaned):
        return []
    abbreviations = [
        "т.е.", "т.к.", "т.д.", "т.п.", "и т.д.", "и т.п.",
        "г-н.", "г-жа.", "д-р.", "проф.", "акад.", "доц.",
        "руб.", "коп.", "Mr.", "Mrs.", "Ms.", "Dr.", "e.g.", "i.e.", "etc."
    ]
    working_text = cleaned
    replacements = {}
    working_text = re.sub(r'(\d+)\.(\d+)', r'\1@@DOT@@\2', working_text)
    working_text = re.sub(r'\b([A-ZА-ЯЁ])\.\s*([A-ZА-ЯЁ])\.', r'\1@@DOT@@\2@@DOT@@', working_text)
    working_text = re.sub(r'\b([A-ZА-ЯЁ])\.\s+(?=[A-ZА-ЯЁ])', r'\1@@DOT@@ ', working_text)
    for idx, abbr in enumerate(abbreviations):
        matches = list(re.finditer(re.escape(abbr), working_text, flags=re.IGNORECASE))
        for m in reversed(matches):
            token = f"@@ABBR_{idx}_{m.start()}@@"
            replacements[token] = m.group(0)
            working_text = working_text[:m.start()] + token + working_text[m.end():]
    raw_sentences = re.split(r'(?<=[\.\?!])\s+(?=[A-ZА-ЯЁ\d«"\(])', working_text)
    restored_sentences = []
    for s in raw_sentences:
        s_restored = s.replace('@@DOT@@', '.')
        for token, original in replacements.items():
            if token in s_restored:
                s_restored = s_restored.replace(token, original)
        s_restored = s_restored.strip()
        if s_restored and _has_speakable_chars(s_restored):
            restored_sentences.append(s_restored)
    sub_divided = []
    for sent in restored_sentences:
        if len(sent) > max_chars:
            clauses = re.split(r'(?<=[\,\;\:\—])\s+', sent)
            curr = ""
            for cl in clauses:
                if len(curr) + len(cl) + 1 <= max_chars:
                    curr = f"{curr} {cl}".strip()
                else:
                    if curr and _has_speakable_chars(curr):
                        sub_divided.append(curr)
                    curr = cl
            if curr and _has_speakable_chars(curr):
                sub_divided.append(curr)
        else:
            sub_divided.append(sent)
    chunks = []
    buffer = ""
    for s in sub_divided:
        if not buffer:
            buffer = s
        elif len(buffer) + len(s) + 1 <= max_chars:
            buffer = f"{buffer} {s}"
            chunks.append(buffer)
            buffer = ""
        else:
            chunks.append(buffer)
            buffer = s
    if buffer:
        chunks.append(buffer)
    merged = []
    for chunk in chunks:
        if not _has_speakable_chars(chunk):
            continue
        if merged and (len(chunk) < min_chars or len(merged[-1]) < min_chars):
            merged[-1] = f"{merged[-1]} {chunk}".strip()
        else:
            merged.append(chunk)
    return merged


def load_progress_data():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_progress_data(data):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# ================= ЗАКЛАДКИ =================
def load_bookmarks_data():
    if os.path.exists(BOOKMARKS_FILE):
        try:
            with open(BOOKMARKS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_bookmarks_data(data):
    with open(BOOKMARKS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# ================= РОУТЫ =================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory(STATIC_DIR, 'manifest.json', mimetype='application/manifest+json')


@app.route('/sw.js')
def serve_sw():
    response = send_from_directory(STATIC_DIR, 'sw.js', mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    return response


@app.route('/save_progress', methods=['POST'])
def save_progress():
    data = request.json or {}
    book_name = data.get('book_name')
    index = data.get('index')
    if not book_name:
        return jsonify({'success': False, 'error': 'Missing book_name'})
    progress = load_progress_data()
    progress[book_name] = {
        'index': index,
        'updated_at': time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_progress_data(progress)
    return jsonify({'success': True})


@app.route('/load_progress', methods=['GET'])
def load_progress():
    book_name = request.args.get('book_name')
    progress = load_progress_data()
    return jsonify(progress.get(book_name, {'index': 0}))


@app.route('/api/bookmarks', methods=['GET'])
def get_bookmarks():
    book_name = request.args.get('book_name')
    if not book_name:
        return jsonify({'success': False, 'error': 'Missing book_name'})
    data = load_bookmarks_data()
    return jsonify({'success': True, 'bookmarks': data.get(book_name, [])})


@app.route('/api/bookmarks/save', methods=['POST'])
def save_bookmark():
    data = request.json or {}
    book_name = data.get('book_name')
    if not book_name:
        return jsonify({'success': False, 'error': 'Missing book_name'})
    unit_index = data.get('unit_index')
    if unit_index is None:
        return jsonify({'success': False, 'error': 'Missing unit_index'})

    paragraph_index = data.get('paragraph_index', 0)
    text = (data.get('text') or '')[:300]
    name = (data.get('name') or '').strip() or text[:40]

    bookmarks = load_bookmarks_data()
    book_bm = bookmarks.get(book_name, [])

    bm = {
        'id': int(time.time() * 1000),
        'unit_index': unit_index,
        'paragraph_index': paragraph_index,
        'text': text,
        'name': name,
        'created_at': time.strftime("%Y-%m-%d %H:%M:%S")
    }
    book_bm.append(bm)
    book_bm.sort(key=lambda x: x['unit_index'])
    bookmarks[book_name] = book_bm
    save_bookmarks_data(bookmarks)
    return jsonify({'success': True, 'bookmark': bm})


@app.route('/api/bookmarks/delete', methods=['POST'])
def delete_bookmark():
    data = request.json or {}
    book_name = data.get('book_name')
    bm_id = data.get('id')
    if not book_name or bm_id is None:
        return jsonify({'success': False, 'error': 'Missing params'})

    bookmarks = load_bookmarks_data()
    book_bm = bookmarks.get(book_name, [])
    book_bm = [b for b in book_bm if b['id'] != bm_id]
    bookmarks[book_name] = book_bm
    save_bookmarks_data(bookmarks)
    return jsonify({'success': True})


@app.route('/api/split', methods=['POST'])
def api_split():
    data = request.get_json(silent=True) or {}
    if 'paragraphs' in data and isinstance(data['paragraphs'], list):
        results = [split_into_chunks(p) for p in data['paragraphs']]
        return jsonify({'results': results})
    text = data.get('text', '')
    if not text:
        return jsonify({'chunks': []})
    return jsonify({'chunks': split_into_chunks(text)})


@app.route('/synthesize', methods=['POST'])
def synthesize():
    text = (request.form.get('text') or (request.json or {}).get('text') or '').strip()
    voice = (request.form.get('voice') or (request.json or {}).get('voice') or 'ru-RU-DmitryNeural').strip()

    if not text or not _has_speakable_chars(text):
        return jsonify({'success': False, 'error': 'Text has no speakable characters'}), 400

    cleanup_old_audio()

    hash_seed = f"{text}_{voice}".encode('utf-8')
    cache_hash = hashlib.md5(hash_seed).hexdigest()

    try:
        # --- 1. SILERO ---
        if voice in SILERO_VOICES:
            speaker_name = voice.replace('silero-', '')
            is_english = speaker_name.startswith('en_')
            tts_text = normalize_en_text(text) if is_english else normalize_text(text)
            if not _has_speakable_chars(tts_text):
                return jsonify({'success': False, 'error': 'Normalized text is empty'}), 400

            filename = f"speech_silero_{cache_hash}.wav"
            filepath = os.path.join(AUDIO_DIR, filename)

            if os.path.exists(filepath) and (time.time() - os.path.getmtime(filepath) < CACHE_TTL_SECONDS):
                return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                                'engine': 'silero', 'cached': True})

            try:
                synthesize_silero(text=tts_text, voice_id=speaker_name, output_path=filepath)
                return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                                'engine': 'silero', 'cached': False})
            except Exception as silero_err:
                print(f"⚠️ Silero упал ({silero_err})", flush=True)
                return jsonify({'success': False, 'error': f'Silero: {silero_err}'}), 500

        # --- 2. PIPER ---
        elif voice in MODELS:
            model_name = MODELS[voice]
            model_path = os.path.join(MODELS_DIR, model_name)

            if not os.path.exists(model_path):
                return jsonify({'success': False, 'error': f'Model {model_name} not found'})

            filename = f"speech_{cache_hash}.wav"
            filepath = os.path.join(AUDIO_DIR, filename)

            if os.path.exists(filepath) and (time.time() - os.path.getmtime(filepath) < CACHE_TTL_SECONDS):
                return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                                'engine': 'piper', 'cached': True})

            cmd = [PIPER_CMD, '--model', model_path, '--output_file', filepath]
            result = subprocess.run(cmd, input=normalize_text(text).encode('utf-8'),
                                    capture_output=True, timeout=60)

            if result.returncode != 0:
                return jsonify({'success': False,
                                'error': result.stderr.decode('utf-8', errors='ignore')})

            return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                            'engine': 'piper', 'cached': False})

        else:
            return jsonify({'success': False, 'error': f'Неизвестный голос: {voice}'}), 400

    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Audio generation timed out.'}), 504
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    with app.app_context():
        cleanup_old_audio()

    print("\n" + "=" * 60)
    print("📖 SileroRead TTS Server")
    print("🔊 Engine: Silero (RU + EN)")
    print(f"📦 RU модель: {os.path.basename(SILERO_RU_MODEL)}")
    print(f"📦 EN модель: {os.path.basename(SILERO_EN_MODEL)}")
    print(f"📝 RU Normalization: {'✅ ru-normalizr' if NORMALIZATION_AVAILABLE else '❌ disabled'}")
    print(f"📝 EN Normalization: {'✅ num2words' if EN_NORMALIZATION_AVAILABLE else '❌ disabled'}")
    print(f"🧹 malloc_trim:   {'✅ available' if _HAS_MALLOC_TRIM else '❌ not available'}")
    print(f"♻️  Silero reload: every {SILERO_RELOAD_EVERY} requests")
    print("📍 UI:     http://127.0.0.1:5008")
    print("=" * 60 + "\n")

    app.run(debug=False, host='0.0.0.0', port=5008, threaded=True)