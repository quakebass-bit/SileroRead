"""
pimsleur_app.py — отдельный сервер для генерации уроков Пимслера и словарных аудио.
Порт: 5011
Использует тот же venv, что и SileroRead.
"""
import os
import sys
import gc
import wave
import re
import subprocess
from datetime import datetime
from threading import Lock
from flask import Flask, request, jsonify, render_template

# ---------- FFMPEG / PYDUB ----------
try:
    import imageio_ffmpeg
    from pydub import AudioSegment
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
    os.environ["PATH"] += os.pathsep + os.path.dirname(FFMPEG_EXE)
    HAS_STITCH = True
except ImportError as e:
    print(f"⚠️ pydub/imageio_ffmpeg не установлены: {e}")
    HAS_STITCH = False

# ---------- malloc_trim (Linux only) ----------
try:
    import ctypes
    _libc = ctypes.CDLL("libc.so.6")
    _HAS_MALLOC_TRIM = True
except Exception:
    _libc = None
    _HAS_MALLOC_TRIM = False

# ---------- ПУТИ ----------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_DIR = os.path.join(BASE_DIR, 'models')
RU_DIR = os.path.join(BASE_DIR, 'ru_audio')
EN_DIR = os.path.join(BASE_DIR, 'en_audio')
WORD_DIR = os.path.join(BASE_DIR, 'word_audio')
OUTPUT_DIR = os.path.join(BASE_DIR, 'output_lesson')

SILERO_RU_MODEL = os.path.join(MODELS_DIR, 'v5_ru.pt')
SILERO_EN_MODEL = os.path.join(MODELS_DIR, 'v3_en.pt')

SILERO_RU_VOICES = ['aidar', 'baya', 'kseniya', 'xenia', 'eugene']
SILERO_EN_VOICES = [f'en_{i}' for i in range(118)]

for d in [RU_DIR, EN_DIR, WORD_DIR, OUTPUT_DIR]:
    os.makedirs(d, exist_ok=True)

# ---------- ПАРАМЕТРЫ ПИМСЛЕРА (как в оригинале) ----------
EN_SPEED = 0.85
COEF_THINK = 1.2
EXTRA_THINK_MS = 1500
COEF_REPEAT = 1.2
EXTRA_REPEAT_MS = 1500
PAUSE_BETWEEN_PAIRS = 1000

# ---------- SILERO STATE ----------
_silero_lock = Lock()
_silero_model = None
_torch_module = None
_silero_loaded_path = None


def _rss_mb():
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return -1


def load_silero(model_path):
    """Загружает модель Silero. Если загружена другая — выгружает её сначала."""
    global _silero_model, _torch_module, _silero_loaded_path
    with _silero_lock:
        if _silero_model is not None and _silero_loaded_path == model_path:
            return _silero_model

        if _silero_model is not None:
            print(f"♻️ Выгружаю модель {os.path.basename(_silero_loaded_path or '?')}...", flush=True)
            del _silero_model
            _silero_model = None
            _silero_loaded_path = None
            gc.collect()
            if _HAS_MALLOC_TRIM:
                try:
                    _libc.malloc_trim(0)
                except Exception:
                    pass

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Модель не найдена: {model_path}")

        if _torch_module is None:
            import torch
            torch.set_num_threads(2)
            _torch_module = torch

        print(f"⏳ Загружаю {os.path.basename(model_path)}...", flush=True)
        importer = _torch_module.package.PackageImporter(model_path)
        _silero_model = importer.load_pickle("tts_models", "model")
        _silero_model.to(_torch_module.device('cpu'))
        _silero_loaded_path = model_path
        print(f"✅ Модель готова | RSS: {_rss_mb()} MB", flush=True)
        return _silero_model


def unload_silero():
    """Выгружает модель, освобождает память."""
    global _silero_model, _silero_loaded_path
    if _silero_model is not None:
        print(f"♻️ Выгружаю модель...", flush=True)
        del _silero_model
        _silero_model = None
        _silero_loaded_path = None
        gc.collect()
        if _HAS_MALLOC_TRIM:
            try:
                _libc.malloc_trim(0)
            except Exception:
                pass
        print(f"✅ Модель выгружена | RSS: {_rss_mb()} MB", flush=True)


def save_tensor_to_wav(audio_tensor, output_path, sample_rate):
    audio_data = (audio_tensor.clamp(-1.0, 1.0) * 32767).to(_torch_module.int16).cpu().numpy()
    with wave.open(output_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data.tobytes())


def synth_silero_ru(text, speaker, output_path):
    model = load_silero(SILERO_RU_MODEL)
    audio = model.apply_tts(
        text=text, speaker=speaker, sample_rate=24000,
        put_accent=True, put_yo=True
    )
    save_tensor_to_wav(audio, output_path, 24000)
    del audio


def synth_silero_en(text, speaker, output_path):
    model = load_silero(SILERO_EN_MODEL)
    audio = model.apply_tts(
        text=text, speaker=speaker, sample_rate=48000
    )
    save_tensor_to_wav(audio, output_path, 48000)
    del audio


# ---------- СКЛЕЙКА ----------
def slow_down_audio(input_path, output_path, tempo):
    cmd = [FFMPEG_EXE, '-y', '-i', input_path,
           '-filter:a', f'atempo={tempo}', output_path]
    subprocess.run(cmd, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True)


def build_lesson():
    """Склеивает ru_audio + en_audio в один mp3-урок."""
    ru_files = sorted([f for f in os.listdir(RU_DIR) if f.lower().endswith('.wav')])
    en_files = sorted([f for f in os.listdir(EN_DIR) if f.lower().endswith('.wav')])

    if not ru_files or not en_files:
        raise RuntimeError("Папки ru_audio или en_audio пусты")

    total = min(len(ru_files), len(en_files))
    print(f"\n🎬 Склейка {total} пар...\n")

    lesson = AudioSegment.empty()
    pause_between = AudioSegment.silent(duration=PAUSE_BETWEEN_PAIRS, frame_rate=48000)

    for i, (ru_fn, en_fn) in enumerate(zip(ru_files, en_files), 1):
        ru_path = os.path.join(RU_DIR, ru_fn)
        en_path = os.path.join(EN_DIR, en_fn)

        print(f"  [{i}/{total}] {ru_fn} + {en_fn}")

        # Замедляем EN через ffmpeg atempo
        temp_slow = os.path.join(EN_DIR, 'temp_slow_' + en_fn)
        slow_down_audio(en_path, temp_slow, EN_SPEED)

        ru_audio = AudioSegment.from_wav(ru_path).set_frame_rate(48000)
        en_audio = AudioSegment.from_wav(temp_slow).set_frame_rate(48000)

        os.remove(temp_slow)

        think_ms = int((len(en_audio) * COEF_THINK) + EXTRA_THINK_MS)
        repeat_ms = int((len(en_audio) * COEF_REPEAT) + EXTRA_REPEAT_MS)
        think = AudioSegment.silent(duration=think_ms, frame_rate=48000)
        repeat = AudioSegment.silent(duration=repeat_ms, frame_rate=48000)

        lesson += ru_audio + think + en_audio + repeat + en_audio + pause_between

    return lesson


def clear_dir(d):
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p):
            try:
                os.remove(p)
            except Exception:
                pass


def safe_filename(text):
    """Санитизация имени файла: убираем запрещённые символы, пробелы → _."""
    s = text
    for ch in '/\\:*?"<>|':
        s = s.replace(ch, '')
    s = re.sub(r'\s+', '_', s).strip('._')
    return s or 'word'


# ---------- FLASK ----------
app = Flask(__name__, template_folder='.')


@app.route('/')
def index():
    return render_template('pimsleur.html')


@app.route('/api/pimsleur', methods=['POST'])
def api_pimsleur():
    data = request.json or {}
    pairs = data.get('pairs', [])
    ru_voice = data.get('ru_voice', 'xenia')
    en_voice = data.get('en_voice', 'en_0')

    if not pairs:
        return jsonify({'success': False, 'error': 'Нет пар'})

    print(f"\n{'=' * 60}")
    print(f"🚀 ПИМСЛЕР: {len(pairs)} пар")
    print(f"   RU голос: {ru_voice}")
    print(f"   EN голос: {en_voice}")
    print(f"{'=' * 60}\n")

    try:
        clear_dir(RU_DIR)
        clear_dir(EN_DIR)

        # ---- ФАЗА 1: RU ----
        print("📢 Фаза 1/3: генерация русского...\n")
        for i, pair in enumerate(pairs, 1):
            text = (pair.get('ru') or '').strip()
            if not text or not any(c.isalpha() for c in text):
                continue
            out = os.path.join(RU_DIR, f"{i:04d}_ru.wav")
            print(f"  [{i}/{len(pairs)}] RU: {text[:70]}")
            synth_silero_ru(text, ru_voice, out)
        unload_silero()

        # ---- ФАЗА 2: EN ----
        print("\n📢 Фаза 2/3: генерация английского...\n")
        for i, pair in enumerate(pairs, 1):
            text = (pair.get('en') or '').strip()
            if not text or not any(c.isalpha() for c in text):
                continue
            out = os.path.join(EN_DIR, f"{i:04d}_en.wav")
            print(f"  [{i}/{len(pairs)}] EN: {text[:70]}")
            synth_silero_en(text, en_voice, out)
        unload_silero()

        # ---- ФАЗА 3: СКЛЕЙКА ----
        print("\n📢 Фаза 3/3: склейка урока...")
        lesson = build_lesson()

        today = datetime.now().strftime('%Y-%m-%d')
        out_name = f"{today}_eng_lesson.mp3"
        out_path = os.path.join(OUTPUT_DIR, out_name)
        lesson.export(out_path, format='mp3', bitrate='192k')

        size_kb = os.path.getsize(out_path) / 1024
        print(f"\n✅ ГОТОВО: {out_name} ({size_kb:.0f} KB)")
        print(f"   Файл: {out_path}\n")

        return jsonify({
            'success': True,
            'message': f'Урок создан: {out_name}',
            'file': out_name
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/words', methods=['POST'])
def api_words():
    data = request.json or {}
    lines = data.get('lines', [])
    en_voice = data.get('en_voice', 'en_0')
    speed = float(data.get('speed', 1.0))
    lead_silence_ms = int(data.get('lead_silence_ms', 200))

    if not lines:
        return jsonify({'success': False, 'error': 'Нет строк'})

    print(f"\n{'=' * 60}")
    print(f"📝 СЛОВА: {len(lines)} строк")
    print(f"   EN голос: {en_voice}")
    print(f"   Скорость: {speed}")
    print(f"   Пауза в начале: {lead_silence_ms} мс")
    print(f"{'=' * 60}\n")

    try:
        results = []
        for i, line in enumerate(lines, 1):
            text = line.strip()
            if not text:
                continue

            base = safe_filename(text)
            out_path = os.path.join(WORD_DIR, f"{base}.mp3")
            wav_raw = os.path.join(WORD_DIR, f"{base}.raw.wav")
            wav_slow = os.path.join(WORD_DIR, f"{base}.slow.wav")

            print(f"  [{i}/{len(lines)}] {text[:70]}  →  {base}.mp3")
            synth_silero_en(text, en_voice, wav_raw)

            # Замедление через ffmpeg atempo
            if abs(speed - 1.0) > 0.001:
                slow_down_audio(wav_raw, wav_slow, speed)
                source_wav = wav_slow
            else:
                source_wav = wav_raw

            # Читаем WAV + добавляем тишину в начале
            audio = AudioSegment.from_wav(source_wav)
            if lead_silence_ms > 0:
                lead = AudioSegment.silent(duration=lead_silence_ms,
                                           frame_rate=audio.frame_rate)
                audio = lead + audio

            audio.export(out_path, format='mp3', bitrate='128k')

            # Чистим временные
            for tmp in (wav_raw, wav_slow):
                if os.path.exists(tmp):
                    os.remove(tmp)

            results.append(base + '.mp3')

        unload_silero()

        print(f"\n✅ ГОТОВО: {len(results)} файлов в {WORD_DIR}\n")
        return jsonify({
            'success': True,
            'message': f'Создано {len(results)} файлов в word_audio/',
            'files': results
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("🎧 Pimsleur Builder")
    print(f"📦 RU модель: {os.path.basename(SILERO_RU_MODEL)}")
    print(f"📦 EN модель: {os.path.basename(SILERO_EN_MODEL)}")
    print(f"🧹 malloc_trim: {'✅' if _HAS_MALLOC_TRIM else '❌'}")
    print(f"🎬 Стежки: {'✅' if HAS_STITCH else '❌'}")
    print("📍 UI: http://127.0.0.1:5011")
    print("=" * 60 + "\n")

    app.run(debug=False, host='0.0.0.0', port=5011, threaded=True)