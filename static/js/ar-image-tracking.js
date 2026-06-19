"use strict";

function updateText(node, text) {
  if (node) node.textContent = text;
}

function pauseAndReset(video) {
  if (!video) return;
  video.pause();
  video.currentTime = 0;
}

function getCsrfToken() {
  return (
    window.__CSRF_TOKEN ||
    document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") ||
    ""
  );
}

function formatClockTime(date) {
  return new Intl.DateTimeFormat("zh-TW", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(date);
}

function extractIotSnapshot(payload) {
  const current = payload?.current || {};
  const temperature = Number(current.temperature_c?.value);
  const phValue = Number(current.ph?.value);
  const dissolvedOxygen = Number(current.dissolved_oxygen_mg_l?.value);

  if (
    Number.isNaN(temperature) ||
    Number.isNaN(phValue) ||
    Number.isNaN(dissolvedOxygen)
  ) {
    throw new Error("IoT API payload is missing expected numeric values.");
  }

  return {
    temperature_c: temperature,
    ph: phValue,
    dissolved_oxygen_mg_l: dissolvedOxygen,
    timestamp: payload?.generated_at || new Date().toISOString(),
    source: payload?.source || "iot-api",
  };
}

function simulateIotSnapshot() {
  const now = new Date();
  const seconds =
    now.getHours() * 3600 +
    now.getMinutes() * 60 +
    now.getSeconds() +
    now.getMilliseconds() / 1000;
  const angle = (seconds / 86400) * Math.PI * 2;

  const temperature = 25.4 + Math.sin(angle - 0.95) * 2.2 + Math.sin(seconds / 1800) * 0.35;
  const phValue = 7.35 + Math.sin(angle + 0.25) * 0.34 + Math.sin(seconds / 3600) * 0.08;
  const dissolvedOxygen =
    6.1 +
    Math.sin(angle + 1.8) * 0.65 -
    Math.exp(-((((now.getHours() + now.getMinutes() / 60) - 5.1) / 1.55) ** 2)) * 0.9;

  return {
    temperature_c: Math.max(20, Math.min(30, Number(temperature.toFixed(2)))),
    ph: Math.max(6.5, Math.min(8.5, Number(phValue.toFixed(2)))),
    dissolved_oxygen_mg_l: Math.max(4, Math.min(8, Number(dissolvedOxygen.toFixed(2)))),
    timestamp: now.toISOString(),
    source: "browser-fallback",
  };
}

const DOM = {
  exhibitMode: () => document.getElementById("exhibit-display-mode"),
  arImmersive: () => document.getElementById("ar-immersive-mode"),
  arScene: () => document.getElementById("ar-aframe-scene"),
  fallbackCamera: () => document.getElementById("ar-camera-fallback"),
  btnOpenAR: () => document.getElementById("btn-open-ar"),
  btnExitAR: () => document.getElementById("btn-exit-ar"),
  statusPill: () => document.getElementById("ar-status-pill"),
  statusLabel: () => document.getElementById("ar-status-label"),
  scanLine: () => document.getElementById("ar-scan-line"),
  voiceLog: () => document.getElementById("ar-voice-log"),
  voiceLogEmpty: () => document.getElementById("ar-voice-log-empty"),
  modelSelect: () => document.getElementById("ar-model-select"),
  modelStatus: () => document.getElementById("ar-model-status"),
  micBtn: () => document.getElementById("ar-mic-btn"),
  micHint: () => document.getElementById("ar-mic-hint"),
  ttsToggle: () => document.getElementById("ar-tts-toggle"),
  stopSpeechBtn: () => document.getElementById("ar-stop-speech-btn"),
  clearBtn: () => document.getElementById("ar-clear-btn"),
};

const STATE = {
  arMode: false,
  isBusy: false,
  holdActive: false,
  pendingRecorderStart: false,
  discardRecording: false,
  micInitialized: false,
  arEventsBound: false,
  sceneReady: false,
  currentAudio: null,
  currentSpeech: null,
  mediaStream: null,
  mediaRecorder: null,
  mediaMimeType: "",
  mediaChunks: [],
  fallbackCameraStream: null,
  scriptPromises: Object.create(null),
  ignoreMouseUntil: 0,
  arIotPanelBound: false,
};

function setArStatus(state, label) {
  const immersive = DOM.arImmersive();
  if (immersive) immersive.dataset.state = state;

  const pill = DOM.statusPill();
  if (pill) pill.dataset.state = state;

  updateText(DOM.statusLabel(), label);
}

function setMicState(state, hint) {
  const micBtn = DOM.micBtn();
  if (micBtn) micBtn.dataset.state = state;
  updateText(DOM.micHint(), hint);
}

function setBusy(isBusy, hint) {
  STATE.isBusy = isBusy;

  const micBtn = DOM.micBtn();
  if (micBtn) micBtn.disabled = isBusy;

  if (isBusy) {
    setMicState("busy", hint || "AI 辨識與思考中...");
  } else {
    setMicState("idle", hint || "按住說話");
  }
}

function removeStatusMessage() {
  const status = document.getElementById("ar-temp-status");
  if (status) status.remove();
}

function appendStatusMessage(text) {
  removeStatusMessage();

  const log = DOM.voiceLog();
  if (!log) return null;

  const el = document.createElement("p");
  el.id = "ar-temp-status";
  el.className = "ar-voice-status-msg";
  el.textContent = text;
  log.appendChild(el);
  log.scrollTop = log.scrollHeight;
  return el;
}

function appendMessage(role, text) {
  const log = DOM.voiceLog();
  const empty = DOM.voiceLogEmpty();
  if (!log || !text) return null;

  if (empty) empty.style.display = "none";

  const bubble = document.createElement("div");
  bubble.className = `ar-voice-bubble ar-voice-bubble--${role}`;

  const icon = document.createElement("div");
  icon.className = "ar-voice-bubble__icon";
  icon.innerHTML = role === "user"
    ? '<i class="bi bi-person-fill"></i>'
    : role === "system"
      ? '<i class="bi bi-exclamation-circle"></i>'
      : '<i class="bi bi-robot"></i>';

  const body = document.createElement("div");
  body.className = "ar-voice-bubble__body";
  body.textContent = text;

  bubble.append(icon, body);
  log.appendChild(bubble);
  log.scrollTop = log.scrollHeight;
  return bubble;
}

function clearVoiceLogUI() {
  const log = DOM.voiceLog();
  if (!log) return;

  Array.from(log.children).forEach((child) => {
    if (child.id !== "ar-voice-log-empty") child.remove();
  });

  const empty = DOM.voiceLogEmpty();
  if (empty) empty.style.display = "";
}

async function clearServerHistory() {
  try {
    await fetch(window.__AR_GUIDE_API_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken(),
      },
      body: JSON.stringify({ clear: true }),
    });
  } catch (error) {
    console.warn("Clear AR guide history failed:", error);
  }
}

function stopSpeechPlayback() {
  if (STATE.currentSpeech && window.speechSynthesis) {
    window.speechSynthesis.cancel();
    STATE.currentSpeech = null;
  }

  if (STATE.currentAudio) {
    STATE.currentAudio.pause();
    STATE.currentAudio = null;
  }

  const stopBtn = DOM.stopSpeechBtn();
  if (stopBtn) stopBtn.hidden = true;
}

function isAutoTtsEnabled() {
  return DOM.ttsToggle()?.checked ?? true;
}

function getSelectedModel() {
  return DOM.modelSelect()?.value || "";
}

function syncModelStatus(label) {
  const select = DOM.modelSelect();
  const selectedLabel = label || select?.selectedOptions?.[0]?.textContent || "";
  updateText(DOM.modelStatus(), selectedLabel ? `目前模型：${selectedLabel}` : "目前模型：--");
}

function speakWithBrowser(text) {
  if (!window.speechSynthesis || !text) return;

  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "zh-TW";
  utterance.rate = 0.94;
  utterance.pitch = 1;

  const voices = window.speechSynthesis.getVoices();
  const zhVoice = voices.find((voice) => voice.lang === "zh-TW") || voices.find((voice) => voice.lang.startsWith("zh"));
  if (zhVoice) utterance.voice = zhVoice;

  const stopBtn = DOM.stopSpeechBtn();
  if (stopBtn) stopBtn.hidden = false;

  STATE.currentSpeech = utterance;
  utterance.onend = () => {
    STATE.currentSpeech = null;
    if (stopBtn) stopBtn.hidden = true;
  };
  utterance.onerror = () => {
    STATE.currentSpeech = null;
    if (stopBtn) stopBtn.hidden = true;
  };

  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

function playResponseAudio(audioUrl, text) {
  stopSpeechPlayback();
  if (!isAutoTtsEnabled()) return;

  if (audioUrl) {
    const audio = new Audio(audioUrl);
    const stopBtn = DOM.stopSpeechBtn();
    if (stopBtn) stopBtn.hidden = false;

    STATE.currentAudio = audio;
    audio.onended = () => {
      STATE.currentAudio = null;
      if (stopBtn) stopBtn.hidden = true;
    };
    audio.onerror = () => {
      STATE.currentAudio = null;
      if (stopBtn) stopBtn.hidden = true;
      speakWithBrowser(text);
    };

    audio.play().catch(() => {
      STATE.currentAudio = null;
      if (stopBtn) stopBtn.hidden = true;
      speakWithBrowser(text);
    });
    return;
  }

  speakWithBrowser(text);
}

function preferredRecorderMimeType() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/mp4",
    "audio/webm",
    "audio/ogg;codecs=opus",
  ];

  if (!window.MediaRecorder) return "";
  return candidates.find((mimeType) => MediaRecorder.isTypeSupported(mimeType)) || "";
}

async function ensureMicrophoneStream() {
  if (STATE.mediaStream && STATE.mediaStream.active) return STATE.mediaStream;

  STATE.mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  return STATE.mediaStream;
}

function releaseMicrophoneStream() {
  if (!STATE.mediaStream) return;
  STATE.mediaStream.getTracks().forEach((track) => track.stop());
  STATE.mediaStream = null;
}

function encodeWav(audioBuffer) {
  const sampleRate = audioBuffer.sampleRate;
  const channelData = audioBuffer.numberOfChannels === 1
    ? audioBuffer.getChannelData(0)
    : (() => {
        const length = audioBuffer.length;
        const mixed = new Float32Array(length);
        for (let channelIndex = 0; channelIndex < audioBuffer.numberOfChannels; channelIndex += 1) {
          const channel = audioBuffer.getChannelData(channelIndex);
          for (let sampleIndex = 0; sampleIndex < length; sampleIndex += 1) {
            mixed[sampleIndex] += channel[sampleIndex] / audioBuffer.numberOfChannels;
          }
        }
        return mixed;
      })();

  const buffer = new ArrayBuffer(44 + channelData.length * 2);
  const view = new DataView(buffer);

  function writeString(offset, value) {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  }

  writeString(0, "RIFF");
  view.setUint32(4, 36 + channelData.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, channelData.length * 2, true);

  let offset = 44;
  for (let index = 0; index < channelData.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, channelData[index]));
    view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    offset += 2;
  }

  return new Blob([buffer], { type: "audio/wav" });
}

async function convertBlobToWav(blob) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return blob;

  const audioContext = new AudioContextClass();
  try {
    const arrayBuffer = await blob.arrayBuffer();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer.slice(0));
    return encodeWav(audioBuffer);
  } catch (error) {
    console.warn("Audio conversion fallback to original blob:", error);
    return blob;
  } finally {
    if (typeof audioContext.close === "function") {
      await audioContext.close();
    }
  }
}

function audioFileNameForBlob(blob) {
  if (blob.type === "audio/wav") return "speech.wav";
  if (blob.type === "audio/mp4") return "speech.m4a";
  if (blob.type === "audio/ogg") return "speech.ogg";
  return "speech.webm";
}

async function uploadAudioBlob(blob, originalMimeType) {
  const formData = new FormData();
  formData.append("audio", blob, audioFileNameForBlob(blob));
  formData.append("audio_mime_type", blob.type || originalMimeType || "");
  if (getSelectedModel()) formData.append("model", getSelectedModel());

  const response = await fetch(window.__AR_GUIDE_API_URL, {
    method: "POST",
    headers: {
      "X-CSRFToken": getCsrfToken(),
    },
    body: formData,
  });

  const payload = await response.json();
  if (!response.ok || payload.error) {
    throw new Error(payload.error || "語音導覽服務暫時無法使用。");
  }

  if (payload.transcript) appendMessage("user", payload.transcript);
  if (payload.text) appendMessage("ai", payload.text);
  if (payload.model?.label) syncModelStatus(payload.model.label);

  if (payload.text && isAutoTtsEnabled()) {
    appendStatusMessage("生成語音中...");
    window.setTimeout(() => {
      removeStatusMessage();
      playResponseAudio(payload.audio_url || "", payload.text);
    }, 180);
  } else {
    removeStatusMessage();
  }
}

async function processRecordedAudio() {
  if (!STATE.mediaChunks.length) {
    appendMessage("system", "沒有錄到語音，請再試一次。");
    return;
  }

  const rawBlob = new Blob(STATE.mediaChunks, {
    type: STATE.mediaMimeType || STATE.mediaRecorder?.mimeType || "audio/webm",
  });
  const originalMimeType = rawBlob.type;

  STATE.mediaChunks = [];
  appendStatusMessage("AI 辨識與思考中...");
  setBusy(true, "AI 辨識與思考中...");

  try {
    const uploadBlob = await convertBlobToWav(rawBlob);
    await uploadAudioBlob(uploadBlob, originalMimeType);
  } catch (error) {
    removeStatusMessage();
    appendMessage("system", error.message || "語音上傳失敗，請稍後再試。");
  } finally {
    setBusy(false, "按住說話");
    releaseMicrophoneStream();
  }
}

async function startHoldRecording(event) {
  if (STATE.isBusy || STATE.holdActive) return;
  if (event.type === "mousedown" && Date.now() < STATE.ignoreMouseUntil) return;

  event.preventDefault();
  stopSpeechPlayback();

  STATE.holdActive = true;
  STATE.pendingRecorderStart = true;
  setMicState("recording", "錄音中...");

  try {
    if (!window.MediaRecorder || !navigator.mediaDevices?.getUserMedia) {
      throw new Error("此瀏覽器不支援錄音功能。");
    }

    const stream = await ensureMicrophoneStream();
    if (!STATE.holdActive) {
      STATE.pendingRecorderStart = false;
      releaseMicrophoneStream();
      setMicState("idle", "按住說話");
      return;
    }

    const recorderOptions = {};
    const mimeType = preferredRecorderMimeType();
    if (mimeType) recorderOptions.mimeType = mimeType;

    const recorder = new MediaRecorder(stream, recorderOptions);
    STATE.mediaRecorder = recorder;
    STATE.mediaMimeType = recorder.mimeType || mimeType;
    STATE.mediaChunks = [];
    STATE.pendingRecorderStart = false;

    recorder.addEventListener("dataavailable", (recorderEvent) => {
      if (recorderEvent.data?.size) STATE.mediaChunks.push(recorderEvent.data);
    });

    recorder.addEventListener("stop", () => {
      const shouldProcess = !STATE.isBusy;
      STATE.mediaRecorder = null;
      if (STATE.discardRecording) {
        STATE.discardRecording = false;
        STATE.mediaChunks = [];
        return;
      }

      if (shouldProcess) {
        processRecordedAudio().catch((error) => {
          appendMessage("system", error.message || "語音辨識失敗。");
          removeStatusMessage();
          setBusy(false, "按住說話");
        });
      }
    });

    recorder.addEventListener("error", () => {
      appendMessage("system", "錄音失敗，請重新嘗試。");
      STATE.mediaRecorder = null;
      STATE.mediaChunks = [];
      STATE.pendingRecorderStart = false;
      STATE.holdActive = false;
      releaseMicrophoneStream();
      setBusy(false, "按住說話");
    });

    recorder.start();
  } catch (error) {
    STATE.holdActive = false;
    STATE.pendingRecorderStart = false;
    STATE.mediaRecorder = null;
    STATE.mediaChunks = [];
    releaseMicrophoneStream();
    setMicState("idle", "按住說話");
    appendMessage("system", error.message || "無法啟用麥克風，請確認權限設定。");
  }
}

function stopHoldRecording(event) {
  if (event.type === "mouseup" && Date.now() < STATE.ignoreMouseUntil) return;
  if (!STATE.holdActive && !STATE.pendingRecorderStart) return;

  event.preventDefault();
  STATE.holdActive = false;

  if (STATE.pendingRecorderStart) {
    STATE.pendingRecorderStart = false;
    releaseMicrophoneStream();
    setMicState("idle", "按住說話");
    return;
  }

  const recorder = STATE.mediaRecorder;
  if (!recorder) {
    setMicState("idle", "按住說話");
    return;
  }

  if (recorder.state !== "inactive") recorder.stop();
  setMicState("busy", "AI 辨識與思考中...");
}

function bindMicControls() {
  if (STATE.micInitialized) return;
  STATE.micInitialized = true;

  const micBtn = DOM.micBtn();
  if (!micBtn) return;

  micBtn.addEventListener("touchstart", (event) => {
    STATE.ignoreMouseUntil = Date.now() + 900;
    startHoldRecording(event);
  }, { passive: false });
  micBtn.addEventListener("touchend", stopHoldRecording, { passive: false });
  micBtn.addEventListener("touchcancel", stopHoldRecording, { passive: false });
  micBtn.addEventListener("mousedown", startHoldRecording);

  document.addEventListener("mouseup", stopHoldRecording);
  DOM.modelSelect()?.addEventListener("change", () => syncModelStatus());

  DOM.stopSpeechBtn()?.addEventListener("click", stopSpeechPlayback);
  DOM.clearBtn()?.addEventListener("click", async () => {
    stopSpeechPlayback();
    removeStatusMessage();
    clearVoiceLogUI();
    await clearServerHistory();
    setBusy(false, "按住說話");
  });
}

function loadScriptOnce(src, testFn) {
  if (!src) return Promise.reject(new Error("缺少 script 路徑。"));
  if (typeof testFn === "function" && testFn()) return Promise.resolve();
  if (STATE.scriptPromises[src]) return STATE.scriptPromises[src];

  STATE.scriptPromises[src] = new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-ar-src="${src}"]`);
    if (existing) {
      if (existing.dataset.loaded === "true") {
        resolve();
        return;
      }
      existing.addEventListener("load", () => resolve(), { once: true });
      existing.addEventListener("error", () => reject(new Error(`載入失敗：${src}`)), { once: true });
      return;
    }

    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.dataset.arSrc = src;
    script.onload = () => {
      script.dataset.loaded = "true";
      resolve();
    };
    script.onerror = () => reject(new Error(`載入失敗：${src}`));
    document.head.appendChild(script);
  });

  return STATE.scriptPromises[src];
}

async function ensureArRuntime() {
  if (!window.__AR_MIND_FILE_READY) return;

  await loadScriptOnce(window.__AR_AFRAME_SRC, () => Boolean(window.AFRAME));
  await loadScriptOnce(window.__AR_MINDAR_SRC, () => Boolean(window.MINDAR?.IMAGE));
}

function registerAframeIotPanelComponent() {
  if (!window.AFRAME || window.AFRAME.components["aframe-iot-panel"]) return;

  window.AFRAME.registerComponent("aframe-iot-panel", {
    schema: {
      panel: { type: "selector" },
      titletext: { type: "selector" },
      synctext: { type: "selector" },
      temperaturetext: { type: "selector" },
      phtext: { type: "selector" },
      dotext: { type: "selector" },
      apiurl: { type: "string", default: "" },
      apitoken: { type: "string", default: "" },
      intervalms: { type: "int", default: 10000 },
    },

    init() {
      this.intervalId = null;
      this.isTracking = false;

      this.handleFound = this.handleFound.bind(this);
      this.handleLost = this.handleLost.bind(this);

      this.el.addEventListener("targetFound", this.handleFound);
      this.el.addEventListener("markerFound", this.handleFound);
      this.el.addEventListener("targetLost", this.handleLost);
      this.el.addEventListener("markerLost", this.handleLost);

      this.setPanelVisible(false);
      this.renderSnapshot(simulateIotSnapshot(), true);
    },

    pause() {
      this.stopPolling();
    },

    remove() {
      this.stopPolling();
      this.el.removeEventListener("targetFound", this.handleFound);
      this.el.removeEventListener("markerFound", this.handleFound);
      this.el.removeEventListener("targetLost", this.handleLost);
      this.el.removeEventListener("markerLost", this.handleLost);
    },

    setPanelVisible(isVisible) {
      if (this.data.panel) {
        this.data.panel.setAttribute("visible", Boolean(isVisible));
      }
    },

    renderSnapshot(snapshot, isFallback = false) {
      const stampText = `${isFallback ? "模擬資料" : "即時資料"} ${formatClockTime(new Date(snapshot.timestamp || Date.now()))}`;
      const titleText = isFallback ? "AIOT 即時水質（模擬）" : "AIOT 即時水質";

      this.data.titletext?.setAttribute("value", titleText);
      this.data.synctext?.setAttribute("value", stampText);
      this.data.temperaturetext?.setAttribute(
        "value",
        `溫度  ${Number(snapshot.temperature_c).toFixed(2)} °C`
      );
      this.data.phtext?.setAttribute(
        "value",
        `pH    ${Number(snapshot.ph).toFixed(2)}`
      );
      this.data.dotext?.setAttribute(
        "value",
        `DO    ${Number(snapshot.dissolved_oxygen_mg_l).toFixed(2)} mg/L`
      );
    },

    async fetchSnapshot() {
      if (!this.data.apiurl) {
        return { snapshot: simulateIotSnapshot(), isFallback: true };
      }

      const headers = { Accept: "application/json" };
      if (this.data.apitoken) {
        headers["X-IoT-Token"] = this.data.apitoken;
      }

      try {
        const response = await fetch(this.data.apiurl, {
          headers,
          cache: "no-store",
        });
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(payload.error || "IoT API request failed.");
        }
        return { snapshot: extractIotSnapshot(payload), isFallback: false };
      } catch (error) {
        console.warn("AR IoT panel falling back to simulated data:", error);
        return { snapshot: simulateIotSnapshot(), isFallback: true };
      }
    },

    async refreshPanel() {
      const { snapshot, isFallback } = await this.fetchSnapshot();
      if (!this.isTracking) return;
      this.renderSnapshot(snapshot, isFallback);
    },

    startPolling() {
      this.stopPolling();
      this.intervalId = window.setInterval(() => {
        this.refreshPanel().catch((error) => {
          console.warn("AR IoT panel refresh failed:", error);
        });
      }, this.data.intervalms || 10000);
    },

    stopPolling() {
      if (this.intervalId) {
        window.clearInterval(this.intervalId);
        this.intervalId = null;
      }
    },

    async handleFound() {
      this.isTracking = true;
      this.setPanelVisible(true);
      await this.refreshPanel();
      this.startPolling();
    },

    handleLost() {
      this.isTracking = false;
      this.stopPolling();
      this.setPanelVisible(false);
    },
  });
}

function setupArIotPanel() {
  if (STATE.arIotPanelBound) return;
  if (!window.AFRAME || !window.__AR_MIND_FILE_READY) return;

  const targetNode = document.querySelector("[data-iot-panel-target='true']");
  const panelNode = document.getElementById("ar-iot-panel");
  if (!targetNode || !panelNode) return;

  registerAframeIotPanelComponent();
  targetNode.setAttribute(
    "aframe-iot-panel",
    [
      "panel: #ar-iot-panel",
      "titletext: #ar-iot-title",
      "synctext: #ar-iot-sync",
      "temperaturetext: #ar-iot-temp",
      "phtext: #ar-iot-ph",
      "dotext: #ar-iot-do",
      `apiurl: ${window.__AR_IOT_DATA_API_URL || ""}`,
      `apitoken: ${window.__AR_IOT_API_TOKEN || ""}`,
      "intervalms: 10000",
    ].join("; ")
  );
  STATE.arIotPanelBound = true;
}

function bindArTargetEvents() {
  const videos = Array.from(document.querySelectorAll(".ar-source-video"));
  const pairs = [
    { target: "#target-0", video: "#ar-video-1", label: "風雲水井" },
    { target: "#target-1", video: "#ar-video-2", label: "水車地景" },
    { target: "#target-2", video: "#ar-video-3", label: "智慧魚塭" },
  ];

  pairs.forEach(({ target, video, label }) => {
    const targetNode = document.querySelector(target);
    const videoNode = document.querySelector(video);
    if (!targetNode || !videoNode) return;

    targetNode.addEventListener("targetFound", () => {
      videos.forEach((item) => {
        if (item !== videoNode) pauseAndReset(item);
      });

      videoNode.currentTime = 0;
      videoNode.play().catch((error) => console.warn("AR video play blocked:", error));
      DOM.scanLine()?.classList.add("is-hidden");
      setArStatus("found", `辨識到：${label}`);
    });

    targetNode.addEventListener("targetLost", () => {
      pauseAndReset(videoNode);
      DOM.scanLine()?.classList.remove("is-hidden");
      setArStatus("ready", "掃描中");
    });
  });
}

function bindArSceneEvents() {
  if (STATE.arEventsBound) return;
  STATE.arEventsBound = true;

  bindArTargetEvents();

  const scene = DOM.arScene();
  if (!scene) return;

  scene.addEventListener("arReady", () => {
    STATE.sceneReady = true;
    setArStatus("ready", "掃描中");
  });

  scene.addEventListener("arError", () => {
    STATE.sceneReady = false;
    setArStatus("error", "AR 啟動失敗");
    appendMessage("system", "AR 鏡頭初始化失敗，請確認相機權限與 HTTPS 設定。");
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      document.querySelectorAll(".ar-source-video").forEach((video) => pauseAndReset(video));
    }
  }, { passive: true });
}

async function startFallbackCamera() {
  const video = DOM.fallbackCamera();
  if (!video) return;

  if (STATE.fallbackCameraStream?.active) {
    video.srcObject = STATE.fallbackCameraStream;
    setArStatus("ready", "掃描中");
    return;
  }

  STATE.fallbackCameraStream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "environment" },
    audio: false,
  });

  video.srcObject = STATE.fallbackCameraStream;
  setArStatus("ready", "掃描中");
}

function stopFallbackCamera() {
  if (!STATE.fallbackCameraStream) return;
  STATE.fallbackCameraStream.getTracks().forEach((track) => track.stop());
  STATE.fallbackCameraStream = null;

  const video = DOM.fallbackCamera();
  if (video) video.srcObject = null;
}

function pauseArTracking() {
  const scene = DOM.arScene();
  const system = scene?.systems?.["mindar-image-system"];
  if (system?.pause) {
    try {
      system.pause();
    } catch (error) {
      console.warn("Pause AR tracking failed:", error);
    }
  }
}

function resumeArTracking() {
  const scene = DOM.arScene();
  const system = scene?.systems?.["mindar-image-system"];
  if (system?.unpause && STATE.sceneReady) {
    try {
      system.unpause();
      setArStatus("ready", "掃描中");
      return true;
    } catch (error) {
      console.warn("Resume AR tracking failed:", error);
    }
  }
  return false;
}

async function enterArMode() {
  if (STATE.arMode) return;
  STATE.arMode = true;

  document.body.classList.add("ar-mode-active");
  DOM.exhibitMode()?.style.setProperty("display", "none");
  clearVoiceLogUI();
  removeStatusMessage();

  const immersive = DOM.arImmersive();
  if (immersive) {
    immersive.hidden = false;
    immersive.style.display = "";
  }

  setArStatus("booting", "啟動中");
  bindMicControls();
  await clearServerHistory();

  try {
    if (!window.__AR_MIND_FILE_READY) {
      await startFallbackCamera();
      return;
    }

    bindArSceneEvents();

    if (resumeArTracking()) return;

    await ensureArRuntime();
    setupArIotPanel();
  } catch (error) {
    setArStatus("error", "AR 載入失敗");
    appendMessage("system", error.message || "AR runtime 載入失敗。");
  }
}

function exitArMode() {
  if (!STATE.arMode) return;
  STATE.arMode = false;

  document.body.classList.remove("ar-mode-active");
  DOM.exhibitMode()?.style.setProperty("display", "");

  const immersive = DOM.arImmersive();
  if (immersive) immersive.hidden = true;

  stopSpeechPlayback();
  removeStatusMessage();
  pauseArTracking();
  stopFallbackCamera();
  releaseMicrophoneStream();

  if (STATE.mediaRecorder?.state && STATE.mediaRecorder.state !== "inactive") {
    STATE.discardRecording = true;
    STATE.mediaRecorder.stop();
  }
  STATE.mediaRecorder = null;
  STATE.mediaChunks = [];
  STATE.holdActive = false;
  STATE.pendingRecorderStart = false;
  setBusy(false, "按住說話");
}

function initTargetLightbox() {
  const lightbox = document.getElementById("ar-target-lightbox");
  const image = document.getElementById("ar-target-lightbox-image");
  const triggers = Array.from(document.querySelectorAll("[data-lightbox-image]"));
  const closeNodes = Array.from(document.querySelectorAll("[data-lightbox-close]"));
  if (!lightbox || !image || !triggers.length) return;

  if (lightbox.parentElement !== document.body) {
    document.body.appendChild(lightbox);
  }

  const open = (src, title) => {
    image.src = src || "";
    image.alt = title || "";
    lightbox.classList.add("is-open");
    lightbox.setAttribute("aria-hidden", "false");
    document.body.classList.add("ar-lightbox-open");
  };

  const close = () => {
    lightbox.classList.remove("is-open");
    lightbox.setAttribute("aria-hidden", "true");
    image.src = "";
    image.alt = "";
    document.body.classList.remove("ar-lightbox-open");
  };

  triggers.forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      open(trigger.dataset.lightboxImage, trigger.dataset.lightboxTitle);
    });
  });

  closeNodes.forEach((node) => {
    node.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      close();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && lightbox.classList.contains("is-open")) close();
  });
}

function init() {
  initTargetLightbox();
  syncModelStatus();

  DOM.btnOpenAR()?.addEventListener("click", () => {
    enterArMode().catch((error) => {
      setArStatus("error", "AR 啟動失敗");
      appendMessage("system", error.message || "無法進入 AR 模式。");
    });
  });

  DOM.btnExitAR()?.addEventListener("click", exitArMode);

  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices();
  }
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}
