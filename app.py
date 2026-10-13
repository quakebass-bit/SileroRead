import os
import glob
import time
import json
import shutil
import hashlib
import re
import sys
import subprocess
import wave
from threading import Lock
from flask import Flask, render_template, request, jsonify, send_from_directory

# ================= НОРМАЛИЗАЦИЯ ТЕКСТА =================
try:
    from ru_normalizr import Normalizer, NormalizeOptions
    NORMALIZATION_AVAILABLE = True
except ImportError:
    NORMALIZATION_AVAILABLE = False
    print("⚠️ ru-normalizr не установлен. Числа и символы не будут нормализованы.")

# Создаём нормализатор ОДИН РАЗ при старте (TTS-режим)
_normalizer = None
if NORMALIZATION_AVAILABLE:
    try:
        _normalizer = Normalizer(NormalizeOptions.tts())
        print("✅ ru-normalizr загружен")
    except Exception as e:
        print(f"⚠️ Не удалось создать Normalizer: {e}")
        NORMALIZATION_AVAILABLE = False


def normalize_text(text: str) -> str:
    """Нормализация текста для TTS: числа, даты, аббревиатуры, латиница."""
    if not NORMALIZATION_AVAILABLE or _normalizer is None:
        return text
    try:
        return _normalizer.normalize(text)
    except Exception as e:
        print(f"⚠️ Ошибка нормализации: {e}. Использую исходный текст.")
        return text


app = Flask(__name__)

# ================= ПУТИ =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
AUDIO_DIR = os.path.join(STATIC_DIR, 'audio')
MODELS_DIR = os.path.join(BASE_DIR, 'models')
PROGRESS_FILE = os.path.join(BASE_DIR, 'progress.json')
TEMP_TXT = os.path.join(BASE_DIR, 'temp_input.txt')

CACHE_TTL_SECONDS = 600

os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

# ================= LOCK'И =================
silero_lock = Lock()
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

# ================= SILERO (ЛЕНИВАЯ) =================
SILERO_VOICES = {'silero-aidar', 'silero-baya', 'silero-kseniya', 'silero-xenia', 'silero-eugene'}
silero_model = None
torch_module = None


def get_silero_model():
    global silero_model, torch_module
    with silero_lock:
        if silero_model is not None:
            return silero_model
        model_path = os.path.join(MODELS_DIR, 'v4_ru.pt')
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Модель Silero не найдена: {model_path}. "
                f"Скачай вручную: https://models.silero.ai/models/tts/ru/v4_ru.pt"
            )
        if torch_module is None:
            import torch
            torch.set_num_threads(2)
            torch_module = torch
        print("⏳ Загружаю Silero в память...", flush=True)
        silero_model = torch_module.package.PackageImporter(model_path).load_pickle("tts_models", "model")
        silero_model.to(torch_module.device('cpu'))
        print("✅ Silero готова", flush=True)
        return silero_model


def save_tensor_to_wav(audio_tensor, output_path, sample_rate=24000):
    audio_data = (audio_tensor.clamp(-1.0, 1.0) * 32767).to(torch_module.int16).cpu().numpy()
    with wave.open(output_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data.tobytes())


def synthesize_silero(text, speaker, output_path):
    model = get_silero_model()
    sample_rate = 24000
    audio = model.apply_tts(
        text=text,
        speaker=speaker,
        sample_rate=sample_rate,
        put_accent=True,
        put_yo=True
    )
    save_tensor_to_wav(audio, output_path, sample_rate)


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


def split_into_chunks(text, max_chars=300, min_chars=20):
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
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
        if s_restored:
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
                    if curr:
                        sub_divided.append(curr)
                    curr = cl
            if curr:
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

    if not text:
        return jsonify({'success': False, 'error': 'Text is empty'})

    # ★ НОРМАЛИЗАЦИЯ ДЛЯ ВСЕХ ДВИЖКОВ
    normalized_text = normalize_text(text)

    cleanup_old_audio()

    hash_seed = f"{text}_{voice}".encode('utf-8')
    cache_hash = hashlib.md5(hash_seed).hexdigest()

    try:
        # --- 1. SILERO ---
        if voice in SILERO_VOICES:
            speaker_name = voice.replace('silero-', '')
            filename = f"speech_silero_{cache_hash}.wav"
            filepath = os.path.join(AUDIO_DIR, filename)

            if os.path.exists(filepath) and (time.time() - os.path.getmtime(filepath) < CACHE_TTL_SECONDS):
                return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                                'engine': 'silero', 'cached': True})

            try:
                synthesize_silero(text=normalized_text, speaker=speaker_name, output_path=filepath)
                return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                                'engine': 'silero', 'cached': False})
            except Exception as silero_err:
                print(f"⚠️ Silero упал ({silero_err}). Fallback → Edge Svetlana.", flush=True)
                fallback_filename = f"speech_fallback_{cache_hash}.mp3"
                fallback_filepath = os.path.join(AUDIO_DIR, fallback_filename)
                run_edge_tts_cli(text=normalized_text, voice='ru-RU-SvetlanaNeural',
                                 output_path=fallback_filepath, timeout_sec=25)
                return jsonify({'success': True, 'audio_url': f'/static/audio/{fallback_filename}',
                                'engine': 'edge_fallback', 'cached': False})

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
            result = subprocess.run(cmd, input=normalized_text.encode('utf-8'),
                                    capture_output=True, timeout=60)

            if result.returncode != 0:
                return jsonify({'success': False,
                                'error': result.stderr.decode('utf-8', errors='ignore')})

            return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                            'engine': 'piper', 'cached': False})

        # --- 3. EDGE ---
        else:
            filename = f"speech_{cache_hash}.mp3"
            filepath = os.path.join(AUDIO_DIR, filename)

            if os.path.exists(filepath) and (time.time() - os.path.getmtime(filepath) < CACHE_TTL_SECONDS):
                return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                                'engine': 'edge', 'cached': True})

            run_edge_tts_cli(text=normalized_text, voice=voice, output_path=filepath, timeout_sec=30)
            return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}',
                            'engine': 'edge', 'cached': False})

    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': 'Audio generation timed out.'}), 504
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    with app.app_context():
        cleanup_old_audio()

    print("\n" + "=" * 60)
    print("📖 SileroRead TTS Server")
    print("🔊 Engines: Silero (lazy) | Piper | Edge")
    print(f"📝 Normalization: {'✅ ru-normalizr' if NORMALIZATION_AVAILABLE else '❌ disabled'}")
    print(f"📍 Piper:  {PIPER_CMD}")
    print("📍 UI:     http://127.0.0.1:5001")
    print("=" * 60 + "\n")

    app.run(debug=False, host='0.0.0.0', port=5001, threaded=True)