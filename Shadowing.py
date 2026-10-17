from flask import Flask, render_template, request, jsonify
import os
import glob
import re
import json
import hashlib
import wave
import gc
from threading import Lock
try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    GoogleTranslator = None
    TRANSLATOR_AVAILABLE = False
    print("⚠️ deep-translator не установлен. /translate будет недоступен.")

# ================= АНГЛИЙСКАЯ НОРМАЛИЗАЦИЯ (NUM2WORDS) =================
try:
    from num2words import num2words
    EN_NORMALIZATION_AVAILABLE = True
    print("✅ English Normalizer (num2words) загружен")
except ImportError:
    EN_NORMALIZATION_AVAILABLE = False
    print("⚠️ num2words не установлен. Числа не будут озвучены.")

def normalize_en_text(text: str) -> str:
    """Превращает цифры, валюты и спецсимволы в слова на английском языке для Silero."""
    if not text or not EN_NORMALIZATION_AVAILABLE:
        return text
    try:
        # 1. Валюты ($100 -> 100 dollars, £50 -> 50 pounds, €20 -> 20 euros)
        text = re.sub(r'\$(\d+(?:\.\d+)?)', r'\1 dollars', text)
        text = re.sub(r'£(\d+(?:\.\d+)?)', r'\1 pounds', text)
        text = re.sub(r'€(\d+(?:\.\d+)?)', r'\1 euros', text)

        # 2. Знаки процента (15% -> 15 percent)
        text = re.sub(r'(\d+)%', r'\1 percent', text)

        # 3. Порядковые числительные (1st, 2nd, 3rd, 21st, 4th)
        def replace_ordinal(match):
            val = int(match.group(1))
            return num2words(val, to='ordinal', lang='en')
        text = re.sub(r'\b(\d+)(?:st|nd|rd|th)\b', replace_ordinal, text, flags=re.IGNORECASE)

        # 4. Года (например, 1990-2029)
        def replace_year(match):
            val = int(match.group(0))
            if 1800 <= val <= 2099:
                return num2words(val, to='year', lang='en')
            return num2words(val, lang='en')
        text = re.sub(r'\b(1[89]\d{2}|20\d{2})\b', replace_year, text)

        # 5. Дроби и десятичные числа (3.14 -> three point one four)
        def replace_decimal(match):
            whole, frac = match.group(1), match.group(2)
            whole_str = num2words(int(whole), lang='en')
            frac_str = " ".join([num2words(int(digit), lang='en') for digit in frac])
            return f"{whole_str} point {frac_str}"
        text = re.sub(r'\b(\d+)\.(\d+)\b', replace_decimal, text)

        # 6. Обычные целые числа
        def replace_number(match):
            val = int(match.group(0))
            return num2words(val, lang='en')
        text = re.sub(r'\b\d+\b', replace_number, text)

        # 7. Базовая очистка спецсимволов
        text = text.replace('&', ' and ')
        text = text.replace('@', ' at ')
        text = text.replace('+', ' plus ')

        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        print(f"⚠️ Ошибка нормализации текста: {e}")
        return text
# =========================================================================


# ---------- malloc_trim (Linux/Android only) ----------
try:
    import ctypes
    _libc = ctypes.CDLL("libc.so.6")
    _HAS_MALLOC_TRIM = True
except Exception:
    _libc = None
    _HAS_MALLOC_TRIM = False

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_DIR = os.path.join(BASE_DIR, 'static', 'audio')
MODELS_DIR = os.path.join(BASE_DIR, 'models')
STATE_FILE = os.path.join(BASE_DIR, 'workspace_data.json')
PHRASES_FILE = os.path.join(BASE_DIR, 'saved_phrases.txt')
DICTIONARY_FILE = os.path.join(BASE_DIR, 'user_dictionary.json')

SILERO_EN_MODEL = os.path.join(MODELS_DIR, 'v3_en.pt')
SILERO_EN_VOICES = [f'en_{i}' for i in range(118)]

dict_lock = Lock()
_silero_lock = Lock()
_silero_model = None
_torch_module = None
_silero_loaded_path = None

os.makedirs(AUDIO_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)


# --- ИНИЦИАЛИЗАЦИЯ И СИНТЕЗ SILERO ---

def load_silero(model_path):
    """Загружает модель Silero в память при первом обращении."""
    global _silero_model, _torch_module, _silero_loaded_path
    with _silero_lock:
        if _silero_model is not None and _silero_loaded_path == model_path:
            return _silero_model

        if _silero_model is not None:
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
            raise FileNotFoundError(f"Модель Silero не найдена по пути: {model_path}")

        if _torch_module is None:
            import torch
            torch.set_num_threads(2)
            _torch_module = torch

        print(f"⏳ Загружаю модель Silero: {os.path.basename(model_path)}...", flush=True)
        importer = _torch_module.package.PackageImporter(model_path)
        _silero_model = importer.load_pickle("tts_models", "model")
        _silero_model.to(_torch_module.device('cpu'))
        _silero_loaded_path = model_path
        print("✅ Модель Silero готова к работе!", flush=True)
        return _silero_model


def save_tensor_to_wav(audio_tensor, output_path, sample_rate=48000):
    audio_data = (audio_tensor.clamp(-1.0, 1.0) * 32767).to(_torch_module.int16).cpu().numpy()
    with wave.open(output_path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(audio_data.tobytes())


def synth_silero_en(text, speaker, output_path):
    model = load_silero(SILERO_EN_MODEL)
    with _silero_lock:
        audio = model.apply_tts(
            text=text,
            speaker=speaker,
            sample_rate=48000
        )
        save_tensor_to_wav(audio, output_path, 48000)
        del audio


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ СЛОВАРЯ ---

def normalize_phrase(phrase: str) -> str:
    """Приводит слово/фразу к нижнему регистру и срезает пунктуацию/пробелы по краям."""
    if not phrase:
        return ""
    phrase = phrase.lower().replace('**', '')
    phrase = re.sub(r'^[^\w]+|[^\w]+$', '', phrase, flags=re.UNICODE)
    phrase = re.sub(r'\s+', ' ', phrase).strip()
    return phrase


def load_user_dictionary() -> dict:
    """Чтение единой базы слов."""
    if not os.path.exists(DICTIONARY_FILE):
        return {}
    try:
        with open(DICTIONARY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_user_dictionary(data: dict):
    """Сохранение единой базы слов."""
    with open(DICTIONARY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cleanup_old_audio():
    """Очистка папки от старых аудио (.wav и .mp3)"""
    for ext in ('*.wav', '*.mp3'):
        for f in glob.glob(os.path.join(AUDIO_DIR, ext)):
            try:
                os.remove(f)
            except Exception:
                pass


# --- API МАРШРУТЫ СЛОВАРЯ ---

@app.route('/api/dictionary', methods=['GET'])
def get_dictionary():
    with dict_lock:
        dictionary = load_user_dictionary()
    return jsonify({'success': True, 'dictionary': dictionary})


@app.route('/api/dictionary/save', methods=['POST'])
def save_dictionary_word():
    phrase = request.form.get('phrase', '')
    translation = request.form.get('translation', '').strip()

    key = normalize_phrase(phrase)
    if not key:
        return jsonify({'success': False, 'error': 'Пустая фраза'}), 400
    if not translation:
        return jsonify({'success': False, 'error': 'Пустой перевод'}), 400

    with dict_lock:
        dictionary = load_user_dictionary()
        dictionary[key] = translation
        save_user_dictionary(dictionary)

    return jsonify({'success': True, 'key': key, 'translation': translation})


@app.route('/api/dictionary/delete', methods=['POST'])
def delete_dictionary_word():
    phrase = request.form.get('phrase', '')
    key = normalize_phrase(phrase)

    if not key:
        return jsonify({'success': False, 'error': 'Пустая фраза'}), 400

    with dict_lock:
        dictionary = load_user_dictionary()
        if key in dictionary:
            del dictionary[key]
            save_user_dictionary(dictionary)

    return jsonify({'success': True, 'key': key})


# --- МАРШРУТЫ ЧТЕНИЯ И ОЗВУЧКИ SILERO ---

@app.route('/')
def index():
    saved_state = "null"
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            saved_state = f.read()
    return render_template('shadow.html', saved_state=saved_state)


@app.route('/reset', methods=['POST'])
def reset():
    """Очищает ТОЛЬКО папку с аудио (словарь и проект остаются)"""
    cleanup_old_audio()
    return jsonify({'success': True})


@app.route('/translate', methods=['POST'])
def translate_word():
    if not TRANSLATOR_AVAILABLE:
        return jsonify({'success': False, 'error': 'deep-translator не установлен'})
    word = request.form.get('word', '').strip()
    if not word:
        return jsonify({'success': False, 'error': 'Пустое слово'})
    try:
        translated = GoogleTranslator(source='en', target='ru').translate(word)
        return jsonify({'success': True, 'translation': translated})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/save_phrase', methods=['POST'])
def save_phrase():
    phrase = request.form.get('phrase', '').strip()
    if not phrase:
        return jsonify({'success': False, 'error': 'Пустая фраза'})
    try:
        with open(PHRASES_FILE, 'a', encoding='utf-8') as f:
            f.write(phrase + '\n')
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/process_text', methods=['POST'])
def process_text():
    """Разбивает текст на предложения"""
    raw_text = request.form.get('text', '').strip()
    if not raw_text:
        return jsonify({'success': False, 'error': 'Текст пустой!'})

    sentences = re.split(r'(?<=[.!?])\s+', raw_text.replace('\r\n', '\n').replace('\n', ' '))
    
    generated_files = []
    for idx, sentence in enumerate(sentences, 1):
        sentence = sentence.strip()
        if not sentence:
            continue
        generated_files.append({
            'id': idx,
            'text': sentence,
            'audio_url': ''
        })

    with open(STATE_FILE, 'w', encoding='utf-8') as f:
        json.dump({'text': raw_text, 'files': generated_files}, f, ensure_ascii=False)

    return jsonify({'success': True, 'files': generated_files})


@app.route('/synthesize_sentence', methods=['POST'])
def synthesize_sentence():
    """Генерирует аудио для одного предложения через локальный Silero (v3_en.pt)"""
    sentence_id = request.form.get('id')
    sentence = request.form.get('text', '').strip()
    voice = request.form.get('voice', 'en_56').strip()

    if voice not in SILERO_EN_VOICES:
        voice = 'en_56'

    if not sentence or not sentence_id:
        return jsonify({'success': False, 'error': 'Нет данных'})

    # Очищаем от спецсимволов Markdown и проверяем наличие букв
    clean_sentence = sentence.replace('*', '').strip()
    clean_sentence = normalize_en_text(clean_sentence)
    if not any(c.isalpha() for c in clean_sentence):
        return jsonify({'success': False, 'error': 'В предложении нет букв для озвучки'})

    # Включаем голос в хэш файла, чтобы при смене голоса генерировался новый файл
    hash_input = f"{voice}_{clean_sentence}"
    text_hash = hashlib.md5(hash_input.encode('utf-8')).hexdigest()[:8]
    filename = f"{sentence_id}_{voice}_{text_hash}.wav"
    filepath = os.path.join(AUDIO_DIR, filename)

    if os.path.exists(filepath):
        return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}'})

    try:
        synth_silero_en(clean_sentence, voice, filepath)
        return jsonify({'success': True, 'audio_url': f'/static/audio/{filename}'})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    print("\n" + "=" * 60)
    print("📖 Shadowing Reader (Silero TTS Edition)")
    print(f"📦 EN модель: {SILERO_EN_MODEL}")
    print("📍 UI: http://127.0.0.1:5001")
    print("=" * 60 + "\n")
    app.run(debug=False, host='0.0.0.0', port=5001, threaded=True)