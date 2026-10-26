import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
import imageio_ffmpeg
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, request, send_from_directory

from custom_classes import Calculator

# The .env file lives in the repo root, one level above this folder.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

VOICE = "en-US-AvaNeural"
MAX_SPEAK_LENGTH = 200
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm", ".ogg"}

speech_cache = {}  # (text, voice) -> WAV bytes, kept in memory while the server runs

# Spoken command word -> calculator button label.
COMMAND_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "point": ".", "over": "÷", "times": "×", "plus": "+", "minus": "−", "is": "=",
}

# Azure often writes symbols instead of words, e.g. "5 + 2." for "five plus two is".
COMMAND_SYMBOLS = {
    "+": "+",
    "-": "−", "−": "−", "–": "−",
    "x": "×", "×": "×", "*": "×",
    "/": "÷", "÷": "÷",
    "=": "=",
}

# A number like "12" or "2.5", a word, or one of the symbols above.
# A sentence-ending period ("5 + 2.") is not part of a number, so it is skipped.
TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?|[a-z]+|[+\-−–×*/÷=]")

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit
calc = Calculator()

OPERATIONS = {
    "add": calc.add,
    "subtract": calc.subtract,
    "multiply": calc.multiply,
    "divide": calc.divide,
}


def is_number(value):
    # bool is a subclass of int in Python, so exclude it explicitly
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/calculate", methods=["POST"])
def calculate():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    for field in ("a", "b", "operation"):
        if field not in data:
            return jsonify({"error": f"Missing field: {field}"}), 400

    a, b, operation = data["a"], data["b"], data["operation"]

    if not is_number(a) or not is_number(b):
        return jsonify({"error": "Both 'a' and 'b' must be numbers."}), 400

    if operation not in OPERATIONS:
        return jsonify({"error": f"Unknown operation: {operation}"}), 400

    try:
        result = OPERATIONS[operation](a, b)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Show and speak whole numbers without ".0" (9 / 3 -> 3), but keep real decimals like 2.5
    if isinstance(result, float) and result.is_integer():
        result = int(result)

    return jsonify({"result": result})


# ---------- Voice input ----------
def text_to_commands(text):
    """Turn recognized text into button labels, e.g. "5 + 2 is." -> ["5", "+", "2", "="]."""
    commands = []
    for token in TOKEN_PATTERN.findall(text.lower()):
        if token[0].isdigit():
            commands.extend(token)  # "12" -> "1", "2" and "2.5" -> "2", ".", "5"
        elif token in COMMAND_WORDS:
            commands.append(COMMAND_WORDS[token])
        elif token in COMMAND_SYMBOLS:
            commands.append(COMMAND_SYMBOLS[token])
        # Anything else ("um", "the", ...) is ignored.
    return commands


def convert_to_wav(src_path, dst_path):
    """Convert any audio file to 16 kHz mono 16-bit PCM WAV, the format the Speech SDK expects."""
    # imageio-ffmpeg ships its own ffmpeg binary, so no system-wide install is needed.
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", src_path,
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst_path],
        check=True,
        capture_output=True,
    )


def recognize(wav_path):
    """Recognize the whole file with continuous recognition.

    Returns (text, cancel_message). cancel_message is set only if Azure canceled with an error.
    """
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_config.speech_recognition_language = "en-US"
    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

    # Short words like "is" and "over" are recognized much more reliably with a phrase list.
    phrase_list = speechsdk.PhraseListGrammar.from_recognizer(recognizer)
    for word in COMMAND_WORDS:
        phrase_list.addPhrase(word)

    parts = []
    errors = []
    done = threading.Event()

    def on_recognized(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
            parts.append(evt.result.text)

    def on_canceled(evt):
        # Reaching the end of the file also fires "canceled" with reason EndOfStream; that's not an error.
        if evt.cancellation_details.reason == speechsdk.CancellationReason.Error:
            message = f"Speech recognition canceled: {evt.cancellation_details.reason.name}"
            if evt.cancellation_details.error_details:
                message += f" – {evt.cancellation_details.error_details}"
            errors.append(message)
        done.set()

    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(lambda evt: done.set())

    recognizer.start_continuous_recognition()
    done.wait(timeout=60)  # safety net so a request can't hang forever
    recognizer.stop_continuous_recognition()

    return " ".join(parts), (errors[0] if errors else None)


@app.route("/api/voice-command", methods=["POST"])
def voice_command():
    audio = request.files.get("audio")
    if audio is None or not audio.filename:
        return jsonify({"error": "No audio received."}), 400

    extension = Path(audio.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify({"error": "Unsupported audio type."}), 400

    # A temporary folder holds both the upload and the converted WAV and is deleted afterwards.
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "input" + extension)
        wav_path = os.path.join(tmp, "converted.wav")
        audio.save(src_path)

        if os.path.getsize(src_path) == 0:
            return jsonify({"error": "The recording is empty."}), 400

        try:
            convert_to_wav(src_path, wav_path)
        except subprocess.CalledProcessError:
            return jsonify({"error": "Could not read the recording."}), 400

        text, cancel_message = recognize(wav_path)

    if cancel_message:
        return jsonify({"error": cancel_message}), 502

    commands = text_to_commands(text)
    if not commands:
        return jsonify({"error": "Sorry, I didn't catch a calculator command.", "text": text}), 422
    return jsonify({"text": text, "commands": commands})


# ---------- Voice output ----------
@app.route("/api/speak", methods=["POST"])
def speak():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()

    if not text:
        return jsonify({"error": "Nothing to say."}), 400
    if len(text) > MAX_SPEAK_LENGTH:
        return jsonify({"error": f"Text is too long (max {MAX_SPEAK_LENGTH} characters)."}), 400

    # Each text is synthesized by Azure only once while the server runs (saves free-tier requests).
    if (text, VOICE) in speech_cache:
        return Response(speech_cache[(text, VOICE)], mimetype="audio/wav")

    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_config.speech_synthesis_voice_name = VOICE
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm  # RIFF = WAV with header
    )

    # audio_config=None keeps the audio in memory instead of playing it on the server.
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        speech_cache[(text, VOICE)] = result.audio_data
        return Response(result.audio_data, mimetype="audio/wav")

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        message = f"Speech synthesis canceled: {details.reason.name}"
        if details.error_details:
            message += f" – {details.error_details}"
        return jsonify({"error": message}), 502

    return jsonify({"error": f"Unexpected synthesis result: {result.reason.name}"}), 502


if __name__ == "__main__":
    app.run(debug=True, port=5001)
