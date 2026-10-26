# EE471 Week 5 – Azure Speech

| Folder | What it is |
| --- | --- |
| `Week-5-Task-1/` | Speech website: text to speech, speech to text, diarization, speech translation, pronunciation assessment |
| `Week-5-Task-2/` | Voice-activated calculator |
| `transcripts/` | Vibe coding transcripts |

## Run

Create a `.env` file in the repo root like `.env.example` (your Azure Speech key and region). It is in `.gitignore`, so it is never pushed.

```
pip install -r Week-5-Task-1/requirements.txt
python3 Week-5-Task-1/app.py   # or Week-5-Task-2/app.py
```

Open http://localhost:5001 (both apps use port 5001, run one at a time).

## Notes

- Task 1, Part 3: `text-to-speech` and `speech-to-text` were developed on separate branches from the same starting point. Merging them into `main` caused conflicts, which were resolved by combining both features into one website.
- Task 2: all calculations run on the server with the `Calculator` class. Voice commands accept both words and digits, because Azure often returns "5 + 2" for "five plus two".
