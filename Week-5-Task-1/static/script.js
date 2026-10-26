// ---------- Shared helpers ----------
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

// ---------- Text to Speech ----------
const ttsText = document.getElementById("tts-text");
const ttsCount = document.getElementById("tts-count");
const ttsVoice = document.getElementById("tts-voice");
const ttsButton = document.getElementById("tts-convert");
const ttsError = document.getElementById("tts-error");
const ttsResult = document.getElementById("tts-result");
const ttsAudio = document.getElementById("tts-audio");
const ttsDownload = document.getElementById("tts-download");

let ttsAudioUrl = null;

ttsText.addEventListener("input", () => {
  ttsCount.textContent = ttsText.value.length;
});

ttsButton.addEventListener("click", async () => {
  hideError(ttsError);
  ttsButton.disabled = true;
  ttsButton.textContent = "Converting…";

  try {
    const response = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: ttsText.value, voice: ttsVoice.value }),
    });
    await ensureOk(response);

    // Free the previous audio blob before creating a new one.
    if (ttsAudioUrl) URL.revokeObjectURL(ttsAudioUrl);
    ttsAudioUrl = URL.createObjectURL(await response.blob());

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
const sttLanguage = document.getElementById("stt-language");
const sttFile = document.getElementById("stt-file");
const sttUpload = document.getElementById("stt-upload");
const sttStart = document.getElementById("stt-start");
const sttStop = document.getElementById("stt-stop");
const sttSend = document.getElementById("stt-send");
const sttStatus = document.getElementById("stt-status");
const sttPreview = document.getElementById("stt-preview");
const sttError = document.getElementById("stt-error");
const sttResult = document.getElementById("stt-result");
const sttText = document.getElementById("stt-text");
const sttCopy = document.getElementById("stt-copy");

let mediaRecorder = null;
let recordedBlob = null;

// Sends a file/blob to the server and shows the transcript or the error.
async function transcribe(blob, filename, button) {
  hideError(sttError);
  sttResult.hidden = true;
  button.disabled = true;
  sttStatus.textContent = "Transcribing…";

  const form = new FormData();
  form.append("audio", blob, filename);
  form.append("language", sttLanguage.value);

  try {
    const response = await fetch("/api/stt", { method: "POST", body: form }).catch((err) => {
      throw new Error("Could not reach the server: " + err.message);
    });
    await ensureOk(response);
    const data = await response.json();
    sttText.value = data.text;
    sttResult.hidden = false;
  } catch (err) {
    showError(sttError, err.message);
  } finally {
    button.disabled = false;
    sttStatus.textContent = "";
  }
}

sttUpload.addEventListener("click", () => {
  const file = sttFile.files[0];
  if (!file) {
    showError(sttError, "Please choose an audio file first.");
    return;
  }
  transcribe(file, file.name, sttUpload);
});

sttStart.addEventListener("click", async () => {
  hideError(sttError);
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    showError(sttError, "Microphone access was denied or is unavailable.");
    return;
  }

  const chunks = [];
  mediaRecorder = new MediaRecorder(stream);
  mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
  mediaRecorder.onstop = () => {
    stream.getTracks().forEach((track) => track.stop()); // turn off the mic indicator
    recordedBlob = new Blob(chunks, { type: mediaRecorder.mimeType });
    sttPreview.src = URL.createObjectURL(recordedBlob);
    sttPreview.hidden = false;
    sttSend.disabled = false;
    sttStatus.textContent = "Recording ready.";
  };

  mediaRecorder.start();
  sttStart.disabled = true;
  sttStop.disabled = false;
  sttSend.disabled = true;
  sttStatus.textContent = "Recording…";
});

sttStop.addEventListener("click", () => {
  mediaRecorder.stop();
  sttStart.disabled = false;
  sttStop.disabled = true;
});

sttSend.addEventListener("click", () => {
  // Chrome/Firefox record WebM; Safari records MP4 (same container as .m4a).
  const filename = recordedBlob.type.includes("mp4") ? "recording.m4a" : "recording.webm";
  transcribe(recordedBlob, filename, sttSend);
});

sttCopy.addEventListener("click", async () => {
  await navigator.clipboard.writeText(sttText.value);
  sttCopy.textContent = "Copied!";
  setTimeout(() => (sttCopy.textContent = "Copy"), 1500);
});
