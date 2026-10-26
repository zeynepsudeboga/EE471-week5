const display = document.getElementById("display");
const micButton = document.getElementById("mic");
const voiceStatus = document.getElementById("voice-status");
const buttons = [...document.querySelectorAll(".keys button")];

let firstNumber = null;     // number entered before the operator (or the last result)
let operation = null;       // "add" | "subtract" | "multiply" | "divide"
let currentInput = "";      // number currently being typed
let expression = "";        // what the display shows while typing, e.g. "8 ÷ 0"
let showingResult = false;  // true right after "=" shows a result or error
let history = [];           // state saved before each operator, so Backspace can undo it

function show(text) {
    display.textContent = text;
}

function reset() {
    firstNumber = null;
    operation = null;
    currentInput = "";
    expression = "";
    history = [];
}

function saveState() {
    history.push({ firstNumber, operation, currentInput, expression, showingResult });
}

function restoreState() {
    ({ firstNumber, operation, currentInput, expression, showingResult } = history.pop());
}

// Backspace: delete the last character of the expression and keep the state in sync
function backspace() {
    if (currentInput === "" && operation !== null) {
        // Deleting an operator: go back to the state before it was pressed
        restoreState();
    } else {
        if (currentInput === "" && firstNumber !== null) {
            // A result is shown: make it editable like a typed number
            currentInput = String(firstNumber);
            expression = currentInput;
            firstNumber = null;
            showingResult = false;
        }
        // Remove a leftover minus sign together with the last digit (e.g. "-4" -> "")
        const count = /^-\d$/.test(currentInput) ? 2 : 1;
        currentInput = currentInput.slice(0, -count);
        expression = expression.slice(0, -count);
    }
    show(expression || "0");
}

function showError(message) {
    show(message);
    speak(message);
    reset();
    showingResult = true;
}

function clearAll() {
    reset();
    showingResult = false;
    show("0");
}

// Sends one calculation to the server. Returns the result, or throws with the error message.
async function calculate(a, b, op) {
    let response, data;
    try {
        response = await fetch("/api/calculate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ a: a, b: b, operation: op }),
        });
        data = await response.json();
    } catch (err) {
        throw new Error("Server error");
    }
    if (!response.ok) throw new Error(data.error);
    return data.result;
}

// Digit and "." buttons: build up the current number as text
function pressDigit(button) {
    const key = button.textContent;

    // Typing a digit after a result starts a fresh calculation
    if (showingResult) {
        reset();
        showingResult = false;
    }

    if (key === "." && currentInput.includes(".")) return;
    const added = currentInput === "" && key === "." ? "0." : key;
    currentInput += added;
    expression += added;
    show(expression);
}

// Operator buttons: remember the first number and the operation
async function pressOperator(button) {
    const symbol = button.textContent;

    if (currentInput !== "") {
        saveState();
        if (operation !== null) {
            // An operation is pending (e.g. "2 + 3"): let the server calculate it first
            try {
                firstNumber = await calculate(firstNumber, Number(currentInput), operation);
            } catch (err) {
                showError(err.message);
                return;
            }
        } else {
            firstNumber = Number(currentInput);
        }
        currentInput = "";
        expression += ` ${symbol} `;
    } else if (firstNumber === null) {
        return;
    } else if (operation !== null) {
        // Two operators in a row: replace the previous one
        expression = expression.slice(0, -3) + ` ${symbol} `;
    } else {
        // Continue from the previous result
        saveState();
        expression = `${firstNumber} ${symbol} `;
    }

    operation = button.dataset.op;
    showingResult = false;
    show(expression);
}

// "=" button: send the numbers to the server and show the answer
async function pressEquals() {
    if (firstNumber === null || operation === null || currentInput === "") {
        speak("is");
        return;
    }

    try {
        const result = await calculate(firstNumber, Number(currentInput), operation);
        reset();
        firstNumber = result;  // next operator continues from here
        expression = String(result);
        showingResult = true;
        show(expression);
        speak(`is ${String(result).replace(/^-/, "minus ")}`);
    } catch (err) {
        showError(err.message);
    }
}

// One button press, from mouse, keyboard or voice. Says the button's word, then runs its logic.
// Returns a promise so voice input can wait for each press before the next one.
function press(button) {
    if (button.classList.contains("equals")) return pressEquals();  // speaks "is <result>" itself
    speak(button.dataset.word);
    if (button.classList.contains("operator")) return pressOperator(button);
    return pressDigit(button);
}

buttons.forEach((button) => button.addEventListener("click", () => press(button)));

// Clicking the display clears everything
display.addEventListener("click", clearAll);

// Keyboard support: keys "press" the matching on-screen button
const operatorKeys = { "+": "add", "-": "subtract", "*": "multiply", "/": "divide" };

document.addEventListener("keydown", (event) => {
    // Leave browser shortcuts like Cmd+R or Ctrl+- alone
    if (event.ctrlKey || event.metaKey || event.altKey) return;

    const key = event.key;
    let button = null;

    if ((key >= "0" && key <= "9") || key === ".") {
        button = [...document.querySelectorAll(".digit")].find((b) => b.textContent === key);
    } else if (key in operatorKeys) {
        button = document.querySelector(`.operator[data-op="${operatorKeys[key]}"]`);
    } else if (key === "Enter" || key === "=") {
        button = document.getElementById("equals");
    } else if (key === "Escape") {
        clearAll();
    } else if (key === "Backspace") {
        backspace();
    } else {
        return;
    }

    // Stop the browser's own action (e.g. Enter re-clicking a focused button, "/" quick find)
    event.preventDefault();
    if (button) button.click();
});

// ---------- Voice output ----------
const speechCache = new Map();          // fixed button word -> promise of an audio URL
let speechQueue = Promise.resolve();    // phrases are played one after another, in order
let speechGeneration = 0;               // bumped when recording starts, to drop queued phrases
let currentAudio = null;

// Asks the server to synthesize the text. Returns an object URL for the WAV audio.
async function fetchSpeech(text) {
    const response = await fetch("/api/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
    });
    if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Speech failed (${response.status})`);
    }
    return URL.createObjectURL(await response.blob());
}

// Button words are fetched once and reused; a failed fetch is forgotten so it can be retried.
function cachedSpeech(word) {
    if (!speechCache.has(word)) {
        speechCache.set(word, fetchSpeech(word).catch((err) => {
            speechCache.delete(word);
            throw err;
        }));
    }
    return speechCache.get(word);
}

function playAudio(url) {
    return new Promise((resolve) => {
        currentAudio = new Audio(url);
        currentAudio.onended = currentAudio.onpause = currentAudio.onerror = resolve;
        currentAudio.play().catch(resolve);
    });
}

function speak(text) {
    if (isRecording()) return;  // never talk over the microphone

    const generation = speechGeneration;
    const isFixedWord = buttons.some((b) => b.dataset.word === text);
    const audioUrl = isFixedWord ? cachedSpeech(text) : fetchSpeech(text);  // start fetching right away

    speechQueue = speechQueue.then(async () => {
        try {
            const url = await audioUrl;
            if (generation === speechGeneration) await playAudio(url);
            if (!isFixedWord) URL.revokeObjectURL(url);
        } catch (err) {
            voiceStatus.textContent = err.message;
        }
    });
}

function stopSpeaking() {
    speechGeneration++;
    if (currentAudio) currentAudio.pause();
}

// ---------- Voice input ----------
let mediaRecorder = null;

function isRecording() {
    return mediaRecorder !== null && mediaRecorder.state === "recording";
}

function setStatus(message, { say = false } = {}) {
    voiceStatus.textContent = message;
    if (say) speak(message);
}

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// "Presses" the buttons for the recognized commands one after another.
async function runCommands(commands) {
    for (const label of commands) {
        const button = buttons.find((b) => b.textContent === label);
        button.classList.add("pressed");
        await press(button);
        await pause(150);
        button.classList.remove("pressed");
    }
}

async function sendRecording(blob) {
    micButton.disabled = true;
    setStatus("Recognizing…");

    // Chrome/Firefox record WebM; Safari records MP4 (same container as .m4a).
    const filename = blob.type.includes("mp4") ? "recording.m4a" : "recording.webm";
    const form = new FormData();
    form.append("audio", blob, filename);

    try {
        const response = await fetch("/api/voice-command", { method: "POST", body: form });
        const data = await response.json();
        if (!response.ok) {
            setStatus(data.text ? `${data.error} (heard: "${data.text}")` : data.error);
            speak(data.error);
            return;
        }
        setStatus(`Heard: "${data.text}"`);
        await runCommands(data.commands);
    } catch (err) {
        setStatus("Could not reach the server.", { say: true });
    } finally {
        micButton.disabled = false;
    }
}

async function startRecording() {
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
        setStatus("Microphone access was denied or is unavailable.", { say: true });
        return;
    }

    stopSpeaking();
    const chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = (e) => chunks.push(e.data);
    mediaRecorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop()); // turn off the mic indicator
        micButton.classList.remove("recording");
        sendRecording(new Blob(chunks, { type: mediaRecorder.mimeType }));
    };

    mediaRecorder.start();
    micButton.classList.add("recording");
    setStatus("Listening… click the microphone again to stop.");
}

micButton.addEventListener("click", () => {
    if (isRecording()) mediaRecorder.stop();
    else startRecording();
});
