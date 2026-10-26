import os
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

# The .env file lives in the repo root, one level above this folder.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SPEECH_KEY = os.getenv("SPEECH_KEY")
SPEECH_REGION = os.getenv("SPEECH_REGION")

MAX_TEXT_LENGTH = 1000

VOICES = {
    "tr-TR-EmelNeural": "Turkish – Emel",
    "tr-TR-AhmetNeural": "Turkish – Ahmet",
    "en-US-AvaMultilingualNeural": "English (US) – Ava (multilingual)",
    "en-US-AndrewMultilingualNeural": "English (US) – Andrew (multilingual)",
}

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", voices=VOICES)


@app.route("/api/tts", methods=["POST"])
def tts():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()
    voice = data.get("voice", "")

    if not text:
        return jsonify(error="Please enter some text."), 400
    if len(text) > MAX_TEXT_LENGTH:
        return jsonify(error=f"Text is too long (max {MAX_TEXT_LENGTH} characters)."), 400
    if voice not in VOICES:
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


if __name__ == "__main__":
    app.run(port=5001, debug=True)
