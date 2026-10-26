import os
import subprocess
import tempfile
import threading
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
import imageio_ffmpeg
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

# The .env file lives in the repo root, one level above this folder.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

# ---------- Text to Speech settings ----------
MAX_TEXT_LENGTH = 1000

VOICES = {
    "tr-TR-EmelNeural": "Turkish – Emel",
    "tr-TR-AhmetNeural": "Turkish – Ahmet",
    "en-US-AvaMultilingualNeural": "English (US) – Ava (multilingual)",
    "en-US-AndrewMultilingualNeural": "English (US) – Andrew (multilingual)",
}

# ---------- Speech to Text settings ----------
LANGUAGES = {
    "tr-TR": "Turkish",
    "en-US": "English (US)",
}

ALLOWED_EXTENSIONS = {".wav", ".mp3", ".m4a", ".webm"}

# ---------- Translation settings ----------
# Target language code -> (display name, TTS voice used by the "Speak" buttons).
TRANSLATION_TARGETS = {
    "en": ("English", "en-US-AvaMultilingualNeural"),
    "tr": ("Turkish", "tr-TR-EmelNeural"),
    "de": ("German", "de-DE-KatjaNeural"),
    "fr": ("French", "fr-FR-DeniseNeural"),
    "it": ("Italian", "it-IT-ElsaNeural"),
    "es": ("Spanish", "es-ES-ElviraNeural"),
}

# /api/tts accepts the dropdown voices plus the translation voices.
TTS_VOICES = set(VOICES) | {voice for _, voice in TRANSLATION_TARGETS.values()}

# ---------- Pronunciation assessment settings ----------
PRONUNCIATION_LANGUAGES = {
    "en-US": "English (US)",
    "tr-TR": "Turkish",
}
MAX_REFERENCE_LENGTH = 500

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB upload limit


@app.route("/")
def index():
    return render_template(
        "index.html",
        voices=VOICES,
        languages=LANGUAGES,
        translation_targets=TRANSLATION_TARGETS,
        pronunciation_languages=PRONUNCIATION_LANGUAGES,
    )


# ---------- Text to Speech ----------
@app.route("/api/tts", methods=["POST"])
def tts():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    voice = data.get("voice", "")

    if not text:
        return jsonify(error="Please enter some text."), 400
    if len(text) > MAX_TEXT_LENGTH:
        return jsonify(error=f"Text is too long (max {MAX_TEXT_LENGTH} characters)."), 400
    if voice not in TTS_VOICES:
        return jsonify(error="Unknown voice."), 400

    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_config.speech_synthesis_voice_name = voice
    speech_config.set_speech_synthesis_output_format(
        speechsdk.SpeechSynthesisOutputFormat.Riff24Khz16BitMonoPcm  # RIFF = WAV with header
    )

    # audio_config=None keeps the audio in memory instead of playing it on the server.
    synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=None)
    result = synthesizer.speak_text_async(text).get()

    if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
        return Response(
            result.audio_data,
            mimetype="audio/wav",
            headers={"Content-Disposition": 'inline; filename="speech.wav"'},
        )

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        message = f"Speech synthesis canceled: {details.reason.name}"
        if details.error_details:
            message += f" – {details.error_details}"
        return jsonify(error=message), 502

    return jsonify(error=f"Unexpected synthesis result: {result.reason.name}"), 502


# ---------- Shared audio helpers ----------
def convert_to_wav(src_path, dst_path):
    """Convert any audio file to 16 kHz mono 16-bit PCM WAV, the format the Speech SDK expects."""
    # imageio-ffmpeg ships its own ffmpeg binary, so no system-wide install is needed.
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", src_path,
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst_path],
        check=True,
        capture_output=True,
    )


def check_audio(audio):
    """Return an error response if the upload is missing or has an unsupported type, else None."""
    if audio is None or not audio.filename:
        return jsonify(error="Please upload or record some audio."), 400
    if Path(audio.filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        return jsonify(error="Unsupported file type (use wav, mp3, m4a or webm)."), 400
    return None


def save_as_wav(audio, tmp):
    """Save the upload into the tmp folder and convert it to WAV.

    Returns (wav_path, error_response); exactly one of them is None.
    """
    src_path = os.path.join(tmp, "input" + Path(audio.filename).suffix.lower())
    wav_path = os.path.join(tmp, "converted.wav")
    audio.save(src_path)

    if os.path.getsize(src_path) == 0:
        return None, (jsonify(error="The audio file is empty."), 400)

    try:
        convert_to_wav(src_path, wav_path)
    except subprocess.CalledProcessError:
        return None, (jsonify(error="Could not read the audio file. Is it a valid audio file?"), 400)
    return wav_path, None


def cancel_message(details, what):
    message = f"{what} canceled: {details.reason.name}"
    if details.error_details:
        message += f" – {details.error_details}"
    return message


def run_until_done(recognizer, start, stop, what="Speech recognition"):
    """Run a continuous recognizer/transcriber until the whole file is processed.

    The caller connects its own result handler before calling this.
    Returns an error message if Azure canceled with an error, else None.
    """
    errors = []
    done = threading.Event()

    def on_canceled(evt):
        # Reaching the end of the file also fires "canceled" with reason EndOfStream; that's not an error.
        if evt.cancellation_details.reason == speechsdk.CancellationReason.Error:
            errors.append(cancel_message(evt.cancellation_details, what))
        done.set()

    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(lambda evt: done.set())

    start()
    done.wait(timeout=600)  # safety net so a request can't hang forever
    stop()

    return errors[0] if errors else None


def speech_config_for(language):
    speech_config = speechsdk.SpeechConfig(subscription=SPEECH_KEY, region=SPEECH_REGION)
    speech_config.speech_recognition_language = language
    return speech_config


# ---------- Speech to Text ----------
def transcribe(wav_path, language):
    """Transcribe the whole file with continuous recognition.

    Returns (text, cancel_message). cancel_message is set only if Azure canceled with an error.
    """
    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config_for(language), audio_config=audio_config
    )

    parts = []

    def on_recognized(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
            parts.append(evt.result.text)

    recognizer.recognized.connect(on_recognized)
    error = run_until_done(
        recognizer, recognizer.start_continuous_recognition, recognizer.stop_continuous_recognition
    )
    return " ".join(parts), error


@app.route("/api/stt", methods=["POST"])
def stt():
    audio = request.files.get("audio")
    language = request.form.get("language", "")

    if error := check_audio(audio):
        return error
    if language not in LANGUAGES:
        return jsonify(error="Unsupported language."), 400

    # A temporary folder holds both the upload and the converted WAV and is deleted afterwards.
    with tempfile.TemporaryDirectory() as tmp:
        wav_path, error = save_as_wav(audio, tmp)
        if error:
            return error
        text, cancel_error = transcribe(wav_path, language)

    if cancel_error:
        return jsonify(error=cancel_error), 502
    if not text:
        return jsonify(error="No speech could be recognized in the audio."), 422
    return jsonify(text=text)


# ---------- Diarization ----------
def diarize(wav_path, language):
    """Transcribe the file and label each final segment with its speaker (Guest-1, Guest-2, ...).

    Returns (segments, cancel_message).
    """
    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    transcriber = speechsdk.transcription.ConversationTranscriber(
        speech_config=speech_config_for(language), audio_config=audio_config
    )

    segments = []

    def on_transcribed(evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
            segments.append({"speaker": evt.result.speaker_id, "text": evt.result.text})

    transcriber.transcribed.connect(on_transcribed)
    error = run_until_done(
        transcriber,
        lambda: transcriber.start_transcribing_async().get(),
        lambda: transcriber.stop_transcribing_async().get(),
        what="Conversation transcription",
    )
    return segments, error


@app.route("/api/diarize", methods=["POST"])
def diarize_route():
    audio = request.files.get("audio")
    language = request.form.get("language", "")

    if error := check_audio(audio):
        return error
    if language not in LANGUAGES:
        return jsonify(error="Unsupported language."), 400

    with tempfile.TemporaryDirectory() as tmp:
        wav_path, error = save_as_wav(audio, tmp)
        if error:
            return error
        segments, cancel_error = diarize(wav_path, language)

    if cancel_error:
        return jsonify(error=cancel_error), 502
    if not segments:
        return jsonify(error="No speech could be recognized in the audio."), 422
    return jsonify(segments=segments)


# ---------- Translation ----------
def translate(wav_path, source, targets):
    """Recognize the file in the source language and translate it into every target.

    Returns (original_text, {target: translated_text}, cancel_message).
    """
    translation_config = speechsdk.translation.SpeechTranslationConfig(
        subscription=SPEECH_KEY, region=SPEECH_REGION
    )
    translation_config.speech_recognition_language = source
    for target in targets:
        translation_config.add_target_language(target)

    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    recognizer = speechsdk.translation.TranslationRecognizer(
        translation_config=translation_config, audio_config=audio_config
    )

    originals = []
    translations = {target: [] for target in targets}

    def on_recognized(evt):
        if evt.result.reason == speechsdk.ResultReason.TranslatedSpeech and evt.result.text:
            originals.append(evt.result.text)
            for target in targets:
                translations[target].append(evt.result.translations.get(target, ""))

    recognizer.recognized.connect(on_recognized)
    error = run_until_done(
        recognizer,
        recognizer.start_continuous_recognition,
        recognizer.stop_continuous_recognition,
        what="Speech translation",
    )
    joined = {target: " ".join(p for p in parts if p) for target, parts in translations.items()}
    return " ".join(originals), joined, error


@app.route("/api/translate", methods=["POST"])
def translate_route():
    audio = request.files.get("audio")
    source = request.form.get("source", "")
    targets = request.form.getlist("targets")

    if error := check_audio(audio):
        return error
    if source not in LANGUAGES:
        return jsonify(error="Unsupported source language."), 400
    if not targets:
        return jsonify(error="Please choose at least one target language."), 400
    if any(target not in TRANSLATION_TARGETS for target in targets):
        return jsonify(error="Unsupported target language."), 400

    with tempfile.TemporaryDirectory() as tmp:
        wav_path, error = save_as_wav(audio, tmp)
        if error:
            return error
        original, translations, cancel_error = translate(wav_path, source, targets)

    if cancel_error:
        return jsonify(error=cancel_error), 502
    if not original:
        return jsonify(error="No speech could be recognized in the audio."), 422
    voices = {target: TRANSLATION_TARGETS[target][1] for target in targets}
    return jsonify(original=original, translations=translations, voices=voices)


# ---------- Pronunciation assessment ----------
def assess_pronunciation(wav_path, language, reference_text):
    """Score one utterance against the reference text.

    Returns (result_dict, error_response); exactly one of them is None.
    """
    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config_for(language), audio_config=audio_config
    )
    assessment_config = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=True,
    )
    assessment_config.apply_to(recognizer)

    # A single utterance is enough here: the reference text is a short sentence or two.
    result = recognizer.recognize_once_async().get()

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        return None, (jsonify(error=cancel_message(details, "Pronunciation assessment")), 502)
    if result.reason != speechsdk.ResultReason.RecognizedSpeech or not result.text:
        return None, (jsonify(error="No speech could be recognized in the audio."), 422)

    assessment = speechsdk.PronunciationAssessmentResult(result)
    # With miscue enabled, silence comes back as "." with every reference word omitted.
    if all(w.error_type == "Omission" for w in assessment.words):
        return None, (jsonify(error="No speech could be recognized in the audio."), 422)
    words = [
        {"word": w.word, "accuracy": w.accuracy_score, "error_type": w.error_type}
        for w in assessment.words
    ]
    return {
        "recognized_text": result.text,
        "accuracy": assessment.accuracy_score,
        "fluency": assessment.fluency_score,
        "completeness": assessment.completeness_score,
        "pronunciation": assessment.pronunciation_score,
        "words": words,
    }, None


@app.route("/api/pronunciation", methods=["POST"])
def pronunciation_route():
    audio = request.files.get("audio")
    language = request.form.get("language", "")
    reference_text = request.form.get("reference_text", "").strip()

    if error := check_audio(audio):
        return error
    if language not in PRONUNCIATION_LANGUAGES:
        return jsonify(error="Unsupported language."), 400
    if not reference_text:
        return jsonify(error="Please enter the reference text."), 400
    if len(reference_text) > MAX_REFERENCE_LENGTH:
        return jsonify(
            error=f"Reference text is too long (max {MAX_REFERENCE_LENGTH} characters)."
        ), 400

    with tempfile.TemporaryDirectory() as tmp:
        wav_path, error = save_as_wav(audio, tmp)
        if error:
            return error
        result, error = assess_pronunciation(wav_path, language, reference_text)

    if error:
        return error
    return jsonify(result)


if __name__ == "__main__":
    app.run(port=5001, debug=True)
