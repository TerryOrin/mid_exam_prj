function updateText(node, text) {
  if (node) node.textContent = text;
}

function pauseAndReset(video) {
  if (!video) return;
  video.pause();
  video.currentTime = 0;
}

function isPermissionError(error) {
  const name = error?.name || error?.error?.name || "";
  const message = error?.message || error?.error?.message || String(error || "");
  return /notallowed|permission|denied|security/i.test(`${name} ${message}`);
}

function initARAframeGuide() {
  const app = document.getElementById("ar-image-tracking-app");
  if (!app) return;

  const hasMindFile = app.dataset.mindFileExists === "true";
  const isMindFileReady = app.dataset.mindFileReady === "true";
  const scene = document.getElementById("ar-aframe-scene");
  const statusLabel = document.getElementById("ar-status-label");
  const statusCopy = document.getElementById("ar-status-copy");
  const retryButton = document.getElementById("ar-retry-button");
  const activeBadge = document.getElementById("ar-active-badge");
  const activeVideo = document.getElementById("ar-active-video");
  const activeTitle = document.getElementById("ar-active-title");
  const activeCopy = document.getElementById("ar-active-copy");
  const foundFeedback = document.getElementById("ar-found-feedback-label");
  const videos = Array.from(document.querySelectorAll(".ar-source-video"));

  const setState = function (state, label, copy) {
    app.dataset.state = state;
    if (label) updateText(statusLabel, label);
    if (copy) updateText(statusCopy, copy);
  };

  const resetActiveTarget = function () {
    updateText(activeBadge, "等待辨識");
    updateText(activeVideo, "尚未播放影片");
    updateText(activeTitle, "請對準圖卡");
    updateText(activeCopy, "辨識到 target 後，影片會直接貼附在圖片位置上播放。");
    updateText(foundFeedback, "掃描中");
  };

  const stopOtherVideos = function (currentVideo) {
    videos.forEach(function (video) {
      if (video !== currentVideo) pauseAndReset(video);
    });
  };

  retryButton?.addEventListener("click", function () {
    window.location.reload();
  });

  if (!hasMindFile || !isMindFileReady) {
    videos.forEach(pauseAndReset);
    const missingMessage =
      "尚未建立圖片辨識檔 shuijing_targets.mind，請先用 MindAR Image Targets Compiler 建立 target 檔。";
    const invalidMessage =
      statusCopy?.textContent.trim() ||
      "圖片辨識檔 shuijing_targets.mind 可能損毀或編譯不完整，請重新用 MindAR Image Targets Compiler 產生 .mind 檔。";
    setState(
      "error",
      hasMindFile ? "target 檔損毀或不完整" : "缺少 target 檔",
      hasMindFile ? invalidMessage : missingMessage
    );
    updateText(activeBadge, hasMindFile ? "Target 檔錯誤" : "缺少 target");
    updateText(activeVideo, "未播放");
    updateText(activeTitle, hasMindFile ? "target 檔損毀或不完整" : "Image Tracking 尚未就緒");
    updateText(activeCopy, hasMindFile ? invalidMessage : missingMessage);
    updateText(foundFeedback, "無法啟動");
    return;
  }

  setState("booting", "AR 載入中", "正在啟動 A-Frame 與 MindAR，請允許瀏覽器使用相機。");
  resetActiveTarget();

  if (navigator.permissions?.query) {
    navigator.permissions
      .query({ name: "camera" })
      .then(function (permissionStatus) {
        if (permissionStatus.state === "denied") {
          setState("permission-denied", "相機權限已拒絕", "請在瀏覽器設定中允許 camera 後重新整理頁面。");
        }
      })
      .catch(function (error) {
        console.warn("Camera permission query failed", error);
      });
  }

  const targetPairs = [
    { target: "#target-0", video: "#ar-video-1", label: "入口導覽" },
    { target: "#target-1", video: "#ar-video-2", label: "水車地景" },
    { target: "#target-2", video: "#ar-video-3", label: "智慧魚塭" },
  ];

  targetPairs.forEach(function ({ target, video, label }) {
    const targetEl = document.querySelector(target);
    const videoEl = document.querySelector(video);

    if (!targetEl || !videoEl) {
      console.warn("A-Frame target/video pair missing, skipping", { target, video });
      return;
    }

    targetEl.addEventListener("targetFound", function () {
      stopOtherVideos(videoEl);
      videoEl.currentTime = 0;
      videoEl.play().catch(function (error) {
        console.warn("Video play failed", error);
      });

      setState("found", `已辨識：${label}`, "影片已覆蓋在圖片位置播放。");
      updateText(activeBadge, "辨識成功");
      updateText(activeVideo, videoEl.dataset.targetFilename || "影片播放中");
      updateText(activeTitle, label);
      updateText(activeCopy, videoEl.dataset.targetCopy || "影片正在覆蓋圖片位置播放。");
      updateText(foundFeedback, label);
    });

    targetEl.addEventListener("targetLost", function () {
      pauseAndReset(videoEl);
      setState("ready", "請重新對準圖片", "target 已離開畫面，請重新對準圖片。");
      resetActiveTarget();
    });
  });

  scene?.addEventListener("arReady", function () {
    if (app.dataset.state !== "found") {
      setState("ready", "相機已啟動", "請把鏡頭對準入口、水車或魚塭 target 圖片。");
      resetActiveTarget();
    }
  });

  scene?.addEventListener("arError", function (event) {
    const error = event.detail?.error || event.detail || event;
    videos.forEach(pauseAndReset);
    if (isPermissionError(error)) {
      setState("permission-denied", "相機權限已拒絕", "請在瀏覽器設定中允許 camera 後重新整理頁面。");
      return;
    }
    setState("error", "AR 啟動失敗", "A-Frame / MindAR 無法啟動，請確認 HTTPS、相機權限與 target 檔。");
  });

  scene?.addEventListener("loaded", function () {
    if (app.dataset.state === "booting") {
      updateText(statusCopy, "A-Frame 場景已載入，正在等待 MindAR 啟動相機。");
    }
  });

  document.addEventListener(
    "visibilitychange",
    function () {
      if (document.hidden) videos.forEach(pauseAndReset);
    },
    { passive: true }
  );
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initARAframeGuide, { once: true });
} else {
  initARAframeGuide();
}
