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
  ttsError.hidden = true;
  ttsButton.disabled = true;
  ttsButton.textContent = "Converting…";

  try {
    const response = await fetch("/api/tts", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: ttsText.value, voice: ttsVoice.value }),
    });

    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `Request failed (${response.status})`);
    }

    // Free the previous audio blob before creating a new one.
    if (ttsAudioUrl) URL.revokeObjectURL(ttsAudioUrl);
    ttsAudioUrl = URL.createObjectURL(await response.blob());

    ttsAudio.src = ttsAudioUrl;
    ttsDownload.href = ttsAudioUrl;
    ttsResult.hidden = false;
    ttsAudio.play();
  } catch (err) {
    ttsError.textContent = err.message;
    ttsError.hidden = false;
  } finally {
    ttsButton.disabled = false;
    ttsButton.textContent = "Convert";
  }
});
