/* ─── AR Image Tracking Guide – A-Frame version ─────────────────────────── */

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

/* ── Lightbox ────────────────────────────────────────────────────────────── */
function initTargetLightbox() {
  const lightbox = document.getElementById("ar-target-lightbox");
  const image = document.getElementById("ar-target-lightbox-image");
  const triggers = Array.from(document.querySelectorAll("[data-lightbox-image]"));
  const closeButtons = Array.from(document.querySelectorAll("[data-lightbox-close]"));

  if (!lightbox || !image || triggers.length === 0) return;

  if (lightbox.parentElement !== document.body) {
    document.body.appendChild(lightbox);
  }

  const openLightbox = function (src, title) {
    image.src = src || "";
    image.alt = title || "AR target";
    lightbox.classList.add("is-open");
    lightbox.setAttribute("aria-hidden", "false");
    document.body.classList.add("ar-lightbox-open");
  };

  const closeLightbox = function () {
    lightbox.classList.remove("is-open");
    lightbox.setAttribute("aria-hidden", "true");
    image.src = "";
    image.alt = "";
    document.body.classList.remove("ar-lightbox-open");
  };

  window.__closeArTargetLightbox = closeLightbox;

  triggers.forEach(function (trigger) {
    trigger.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      openLightbox(trigger.dataset.lightboxImage, trigger.dataset.lightboxTitle);
    });
  });

  closeButtons.forEach(function (btn) {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      closeLightbox();
    });
  });

  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && lightbox.classList.contains("is-open")) closeLightbox();
  });
}

/* ── AR Guide ────────────────────────────────────────────────────────────── */
function initARAframeGuide() {
  const app = document.getElementById("ar-image-tracking-app");
  if (!app) return;

  const hasMindFile     = app.dataset.mindFileExists === "true";
  const isMindFileReady = app.dataset.mindFileReady === "true";

  const scene       = document.getElementById("ar-aframe-scene");
  const statusLabel = document.getElementById("ar-status-label");
  const statusPill  = document.getElementById("ar-status-pill");
  const statusCopy  = document.getElementById("ar-status-copy");
  const retryBtn    = document.getElementById("ar-retry-button");
  const scanLine    = document.getElementById("ar-scan-line");

  // 底部卡片
  const idlePanel   = document.getElementById("ar-idle-hint");
  const foundPanel  = document.getElementById("ar-found-panel");
  const activeTitle = document.getElementById("ar-active-title");
  const foundTitle  = document.getElementById("ar-found-title");
  const activeCopy  = document.getElementById("ar-status-copy");
  const activeBadge = document.getElementById("ar-active-badge");
  const activeCopyFound = document.getElementById("ar-active-copy");

  const videos = Array.from(document.querySelectorAll(".ar-source-video"));

  /* helpers */
  const setState = function (state, label, copy) {
    app.dataset.state = state;
    if (label) updateText(statusLabel, label);
    if (copy)  updateText(statusCopy, copy);
    if (statusPill) statusPill.dataset.state = state;
  };

  const showIdleCard = function (title, hint) {
    if (idlePanel)  idlePanel.hidden  = false;
    if (foundPanel) foundPanel.hidden = true;
    if (title) updateText(activeTitle, title);
    if (hint)  updateText(activeCopy, hint);
  };

  const showFoundCard = function (title, badge, copy) {
    if (idlePanel)  idlePanel.hidden  = true;
    if (foundPanel) foundPanel.hidden = false;
    if (title) updateText(foundTitle, title);
    if (badge) updateText(activeBadge, badge);
    if (copy)  updateText(activeCopyFound, copy);
  };

  const resetToIdle = function () {
    showIdleCard("請把鏡頭對準圖卡", "將鏡頭對準入口、水車或魚塭任一張圖卡");
    if (scanLine) scanLine.classList.remove("is-hidden");
  };

  const stopAllVideos = function (except) {
    videos.forEach(function (v) { if (v !== except) pauseAndReset(v); });
  };

  retryBtn?.addEventListener("click", function () {
    window.location.reload();
  });

  /* 沒有 mind 檔或不可用 */
  if (!hasMindFile || !isMindFileReady) {
    videos.forEach(pauseAndReset);
    const msg = hasMindFile
      ? "圖片辨識檔可能損毀，請重新產生 .mind 檔"
      : "尚未建立圖片辨識檔，請先完成初始化";
    setState("error", "無法啟動", msg);
    showIdleCard(hasMindFile ? "辨識檔有誤" : "尚未就緒", msg);
    return;
  }

  setState("booting", "啟動中", "正在啟動相機，請允許瀏覽器使用相機…");
  showIdleCard("請把鏡頭對準圖卡", "正在啟動，請稍候…");

  /* 相機權限預檢 */
  if (navigator.permissions?.query) {
    navigator.permissions
      .query({ name: "camera" })
      .then(function (status) {
        if (status.state === "denied") {
          setState("permission-denied", "無相機權限", "請在瀏覽器設定中允許相機後重新整理頁面");
          showIdleCard("相機權限被拒絕", "請在設定中允許相機後重新整理");
        }
      })
      .catch(function () {});
  }

  /* target 對應 */
  const pairs = [
    { target: "#target-0", video: "#ar-video-1", label: "入口導覽" },
    { target: "#target-1", video: "#ar-video-2", label: "水車地景" },
    { target: "#target-2", video: "#ar-video-3", label: "智慧魚塭" },
  ];

  pairs.forEach(function ({ target, video, label }) {
    const targetEl = document.querySelector(target);
    const videoEl  = document.querySelector(video);
    if (!targetEl || !videoEl) return;

    targetEl.addEventListener("targetFound", function () {
      stopAllVideos(videoEl);
      videoEl.currentTime = 0;
      videoEl.play().catch(function (e) { console.warn("Video play failed", e); });
      if (scanLine) scanLine.classList.add("is-hidden");
      setState("found", "辨識成功", "");
      showFoundCard(
        label,
        videoEl.dataset.targetBadge || "辨識成功",
        videoEl.dataset.targetCopy  || "影片正在覆蓋圖片位置播放"
      );
    });

    targetEl.addEventListener("targetLost", function () {
      pauseAndReset(videoEl);
      setState("ready", "掃描中", "");
      resetToIdle();
    });
  });

  /* A-Frame 事件 */
  scene?.addEventListener("arReady", function () {
    if (app.dataset.state !== "found") {
      setState("ready", "掃描中", "");
      resetToIdle();
    }
  });

  scene?.addEventListener("arError", function (event) {
    const err = event.detail?.error || event.detail || event;
    videos.forEach(pauseAndReset);
    if (isPermissionError(err)) {
      setState("permission-denied", "無相機權限", "請允許相機後重新整理");
      showIdleCard("相機被拒絕", "請在設定中允許相機後重新整理頁面");
      return;
    }
    setState("error", "啟動失敗", "請確認 HTTPS 與相機權限後重試");
    showIdleCard("無法啟動 AR", "請確認 HTTPS 連線與相機權限後重試");
  });

  scene?.addEventListener("loaded", function () {
    if (app.dataset.state === "booting") {
      updateText(statusCopy, "場景已載入，正在等待相機…");
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) videos.forEach(pauseAndReset);
  }, { passive: true });
}

/* ── Init ────────────────────────────────────────────────────────────────── */
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", function () {
    initTargetLightbox();
    initARAframeGuide();
  }, { once: true });
} else {
  initTargetLightbox();
  initARAframeGuide();
}
