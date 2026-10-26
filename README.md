# EE471 Week 5 – Azure Speech

Two Flask web apps built with vibe coding on top of the Azure Speech service.

| Folder | What it is |
| --- | --- |
| `Week-5-Task-1/` | Speech website: text to speech, speech to text, real-time diarization, speech translation, pronunciation assessment |
| `Week-5-Task-2/` | Voice-activated calculator (based on Mini Project 1 and the Week 4 calculator) |
| `transcripts/` | Vibe coding transcripts (Claude Code conversations), one file per part |

## Setup

1. Create an Azure Speech resource and copy its key and region.
2. Create a `.env` file in the repository root (it is in `.gitignore`, so it is never pushed):

   ```
   SPEECH_KEY=your-key
   SPEECH_REGION=your-region
   SPEECH_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
   ```

3. Create a virtual environment and install the packages:

   ```
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r Week-5-Task-1/requirements.txt
   ```

   `imageio-ffmpeg` provides ffmpeg, which converts browser recordings (WebM) and uploaded files to the 16 kHz mono WAV format the Speech SDK needs.

Both apps use port 5001, so run one at a time. Then open http://localhost:5001.

## Task 1 – Speech website

```
python3 Week-5-Task-1/app.py
```

| Endpoint | Feature |
| --- | --- |
| `POST /api/tts` | Text to speech: returns a WAV file to play or download |
| `POST /api/stt` | Speech to text: uploaded file or browser microphone, continuous recognition for the whole audio |
| `POST /api/diarize` | Real-time diarization: who said what (Guest-1, Guest-2, ...) |
| `POST /api/translate` | Speech translation into one or more languages, each with a "Speak" button |
| `POST /api/pronunciation` | Pronunciation assessment against a reference text (accuracy, fluency, completeness, per-word scores) |

Errors are returned as JSON `{"error": "..."}` with 400 (bad input), 422 (no speech recognized) or 502 (Azure request canceled).

Git history: `text-to-speech` and `speech-to-text` were developed on separate branches from the same starting point. Merging both into `main` caused add/add conflicts in `app.py`, `index.html`, `script.js` and `style.css`, which were resolved by combining both features into one website (Part 3).

## Task 2 – Voice-activated calculator

```
python3 Week-5-Task-2/app.py
```

- **Voice input:** the microphone button on the left records a command such as "five plus two is". `POST /api/voice-command` recognizes it and the calculator presses the matching buttons. Both words and digits/symbols are accepted, because Azure often returns "5 + 2" for spoken numbers.
- **Voice output:** every button press speaks its word ("three", "plus", "five", "is eight") via `POST /api/speak`. Synthesized words are cached on the server, so each word is requested from Azure only once.
- All calculations run on the server with the `Calculator` class (`POST /api/calculate`).

| Say / press | Button |
| --- | --- |
| zero … nine | 0 … 9 |
| point | . |
| over | ÷ |
| times | × |
| plus | + |
| minus | − |
| is | = |
