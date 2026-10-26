// ---------- Shared helpers ----------
const $ = (id) => document.getElementById(id);

function showError(errorEl, message) {
  errorEl.textContent = message;
  errorEl.hidden = false;
}

function hideError(errorEl) {
  errorEl.hidden = true;
}

// Throws with the server's JSON error message (or the status code) if the response failed.
async function ensureOk(response) {
  if (response.ok) return;
  const data = await response.json().catch(() => ({}));
  throw new Error(data.error || `Request failed (${response.status})`);
}

// Calls /api/tts and returns an object URL for the WAV audio. The caller revokes it.
async function synthesize(text, voice) {
  const response = await fetch("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, voice }),
  });
  await ensureOk(response);
  return URL.createObjectURL(await response.blob());
}

// Wires up the upload + microphone controls of one section (see the audio_input macro).
// The audio is posted to `url` together with `fields()` ([name, value] pairs), and the
// JSON reply is passed to `render`. Errors go to the section's own error area.
function setupAudioInput({ prefix, url, busyText, fields, render }) {
  const file = $(`${prefix}-file`);
  const upload = $(`${prefix}-upload`);
  const start = $(`${prefix}-start`);
  const stop = $(`${prefix}-stop`);
  const send = $(`${prefix}-send`);
  const status = $(`${prefix}-status`);
  const preview = $(`${prefix}-preview`);
  const error = $(`${prefix}-error`);
  const result = $(`${prefix}-result`);

  let mediaRecorder = null;
  let recordedBlob = null;

  async function submit(blob, filename, button) {
    hideError(error);
    result.hidden = true;
    button.disabled = true;
    status.textContent = busyText;

    const form = new FormData();
    form.append("audio", blob, filename);
    for (const [name, value] of fields()) form.append(name, value);

    try {
      const response = await fetch(url, { method: "POST", body: form }).catch((err) => {
        throw new Error("Could not reach the server: " + err.message);
      });
      await ensureOk(response);
      render(await response.json());
      result.hidden = false;
    } catch (err) {
      showError(error, err.message);
    } finally {
      button.disabled = false;
      status.textContent = "";
    }
  }

  upload.addEventListener("click", () => {
    const chosen = file.files[0];
    if (!chosen) {
      showError(error, "Please choose an audio file first.");
      return;
    }
    submit(chosen, chosen.name, upload);
  });

  start.addEventListener("click", async () => {
    hideError(error);
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      showError(error, "Microphone access was denied or is unavailable.");
      return;
    }

    const chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
    mediaRecorder.onstop = () => {
      stream.getTracks().forEach((track) => track.stop()); // turn off the mic indicator
      recordedBlob = new Blob(chunks, { type: mediaRecorder.mimeType });
      if (preview.src) URL.revokeObjectURL(preview.src);
      preview.src = URL.createObjectURL(recordedBlob);
      preview.hidden = false;
      send.disabled = false;
      status.textContent = "Recording ready.";
    };

    mediaRecorder.start();
    start.disabled = true;
    stop.disabled = false;
    send.disabled = true;
    status.textContent = "Recording…";
  });

  stop.addEventListener("click", () => {
    mediaRecorder.stop();
    start.disabled = false;
    stop.disabled = true;
  });

  send.addEventListener("click", () => {
    // Chrome/Firefox record WebM; Safari records MP4 (same container as .m4a).
    const filename = recordedBlob.type.includes("mp4") ? "recording.m4a" : "recording.webm";
    submit(recordedBlob, filename, send);
  });
}

// ---------- Tabs ----------
const tabs = document.querySelectorAll(".tab");

function showTab(name) {
  tabs.forEach((tab) => {
    const active = tab.dataset.tab === name;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active);
    $(tab.dataset.tab).hidden = !active;
  });
}

tabs.forEach((tab) => tab.addEventListener("click", () => {
  showTab(tab.dataset.tab);
  history.replaceState(null, "", "#" + tab.dataset.tab);
}));

const initialTab = location.hash.slice(1);
showTab([...tabs].some((tab) => tab.dataset.tab === initialTab) ? initialTab : "tts");

// ---------- Text to Speech ----------
const ttsText = $("tts-text");
const ttsCount = $("tts-count");
const ttsVoice = $("tts-voice");
const ttsButton = $("tts-convert");
const ttsError = $("tts-error");
const ttsResult = $("tts-result");
const ttsAudio = $("tts-audio");
const ttsDownload = $("tts-download");

let ttsAudioUrl = null;

ttsText.addEventListener("input", () => {
  ttsCount.textContent = ttsText.value.length;
});

ttsButton.addEventListener("click", async () => {
  hideError(ttsError);
  ttsButton.disabled = true;
  ttsButton.textContent = "Converting…";

  try {
    const audioUrl = await synthesize(ttsText.value, ttsVoice.value);

    // Free the previous audio blob before using the new one.
    if (ttsAudioUrl) URL.revokeObjectURL(ttsAudioUrl);
    ttsAudioUrl = audioUrl;

    ttsAudio.src = ttsAudioUrl;
    ttsDownload.href = ttsAudioUrl;
    ttsResult.hidden = false;
    ttsAudio.play();
  } catch (err) {
    showError(ttsError, err.message);
  } finally {
    ttsButton.disabled = false;
    ttsButton.textContent = "Convert";
  }
});

// ---------- Speech to Text ----------
const sttText = $("stt-text");
const sttCopy = $("stt-copy");

setupAudioInput({
  prefix: "stt",
  url: "/api/stt",
  busyText: "Transcribing…",
  fields: () => [["language", $("stt-language").value]],
  render: (data) => {
    sttText.value = data.text;
  },
});

sttCopy.addEventListener("click", async () => {
  await navigator.clipboard.writeText(sttText.value);
  sttCopy.textContent = "Copied!";
  setTimeout(() => (sttCopy.textContent = "Copy"), 1500);
});

// ---------- Diarization ----------
const SPEAKER_COLORS = 6; // matches .speaker-0 … .speaker-5 in style.css

setupAudioInput({
  prefix: "diarize",
  url: "/api/diarize",
  busyText: "Transcribing conversation…",
  fields: () => [["language", $("diarize-language").value]],
  render: (data) => {
    const conversation = $("diarize-conversation");
    conversation.replaceChildren();
    const speakers = []; // order of first appearance decides the color

    for (const { speaker, text } of data.segments) {
      if (!speakers.includes(speaker)) speakers.push(speaker);
      const colorClass = speaker === "Unknown"
        ? "speaker-unknown"
        : `speaker-${speakers.indexOf(speaker) % SPEAKER_COLORS}`;

      const line = document.createElement("div");
      line.className = `segment ${colorClass}`;
      const name = document.createElement("span");
      name.className = "speaker";
      name.textContent = speaker;
      line.append(name, document.createTextNode(text));
      conversation.append(line);
    }
  },
});

// ---------- Translation ----------
const translateError = $("translate-error");
const targetBoxes = document.querySelectorAll("#translate-targets input");
let speakUrl = null;

setupAudioInput({
  prefix: "translate",
  url: "/api/translate",
  busyText: "Translating…",
  fields: () => [
    ["source", $("translate-source").value],
    ...[...targetBoxes].filter((box) => box.checked).map((box) => ["targets", box.value]),
  ],
  render: (data) => {
    $("translate-original").textContent = data.original;
    const list = $("translate-list");
    list.replaceChildren();

    for (const [code, text] of Object.entries(data.translations)) {
      const item = document.createElement("li");
      const name = document.createElement("span");
      name.className = "lang";
      name.textContent = document.querySelector(`#translate-targets input[value="${code}"]`).dataset.name;
      const body = document.createElement("span");
      body.className = "text";
      body.textContent = text;
      const speak = document.createElement("button");
      speak.textContent = "Speak";
      speak.addEventListener("click", () => speakTranslation(text, data.voices[code], speak));
      item.append(name, body, speak);
      list.append(item);
    }
  },
});

async function speakTranslation(text, voice, button) {
  hideError(translateError);
  button.disabled = true;
  try {
    if (speakUrl) URL.revokeObjectURL(speakUrl);
    speakUrl = await synthesize(text, voice);
    await new Audio(speakUrl).play();
  } catch (err) {
    showError(translateError, err.message);
  } finally {
    button.disabled = false;
  }
}

// ---------- Pronunciation assessment ----------
function scoreClass(word) {
  if (word.error_type === "Omission" || word.accuracy < 60) return "poor";
  return word.accuracy < 80 ? "medium" : "good";
}

setupAudioInput({
  prefix: "pron",
  url: "/api/pronunciation",
  busyText: "Assessing…",
  fields: () => [
    ["language", $("pron-language").value],
    ["reference_text", $("pron-reference").value],
  ],
  render: (data) => {
    for (const key of ["pronunciation", "accuracy", "fluency", "completeness"]) {
      $(`pron-${key}`).textContent = Math.round(data[key]);
    }

    const words = $("pron-words");
    words.replaceChildren();
    for (const word of data.words) {
      const span = document.createElement("span");
      span.className = `word ${scoreClass(word)}`;
      span.textContent = word.word;
      span.title = `Accuracy ${Math.round(word.accuracy)} · ${word.error_type}`;
      if (word.error_type !== "None") {
        const tag = document.createElement("small");
        tag.textContent = word.error_type;
        span.append(tag);
      }
      words.append(span);
    }
  },
});
