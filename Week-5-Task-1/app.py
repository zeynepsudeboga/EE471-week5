import os
import subprocess
import tempfile
import threading
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
import imageio_ffmpeg
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

# The .env file lives in the repo root, one level above this folder.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

LANGUAGES = {
    "tr-TR": "Turkish",
    "en-US": "English (US)",
}

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload limit


@app.route("/")
def index():
    return render_template("index.html", languages=LANGUAGES)


def convert_to_wav(src_path, dst_path):
    """Convert any audio file to 16 kHz mono 16-bit PCM WAV, the format the Speech SDK expects."""
    # imageio-ffmpeg ships its own ffmpeg binary, so no system-wide install is needed.
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", src_path,
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst_path],
        check=True,
        capture_output=True,
    )


def transcribe(wav_path, language):
    """Transcribe the whole file with continuous recognition.

    Returns (text, cancel_message). cancel_message is set only if Azure canceled with an error.
    """
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_config.speech_recognition_language = language
    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

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
    done.wait(timeout=600)  # safety net so a request can't hang forever
    recognizer.stop_continuous_recognition()

    return " ".join(parts), (errors[0] if errors else None)


@app.route("/api/stt", methods=["POST"])
def stt():
    audio = request.files.get("audio")
    language = request.form.get("language", "")

    if audio is None or not audio.filename:
        return jsonify(error="Please upload or record some audio."), 400
    if language not in LANGUAGES:
        return jsonify(error="Unsupported language."), 400

    extension = Path(audio.filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        return jsonify(error="Unsupported file type (use wav, mp3, m4a or webm)."), 400

    # A temporary folder holds both the upload and the converted WAV and is deleted afterwards.
    with tempfile.TemporaryDirectory() as tmp:
        src_path = os.path.join(tmp, "input" + extension)
        wav_path = os.path.join(tmp, "converted.wav")
        audio.save(src_path)

        if os.path.getsize(src_path) == 0:
            return jsonify(error="The audio file is empty."), 400

        try:
            convert_to_wav(src_path, wav_path)
        except subprocess.CalledProcessError:
            return jsonify(error="Could not read the audio file. Is it a valid audio file?"), 400

        text, cancel_message = transcribe(wav_path, language)

    if cancel_message:
        return jsonify(error=cancel_message), 502
    if not text:
        return jsonify(error="No speech could be recognized in the audio."), 422
    return jsonify(text=text)


if __name__ == "__main__":
    app.run(port=5001, debug=True)
