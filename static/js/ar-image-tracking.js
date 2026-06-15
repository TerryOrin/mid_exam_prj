import * as THREE from "three";
import { MindARThree } from "/static/ar/mindar-image-three.prod.js";

function pauseAndReset(video) {
  if (!video) return;
  video.pause();
  video.currentTime = 0;
}

function updateText(node, text) {
  if (node) node.textContent = text;
}

function createVideoPlane(video, aspectRatio) {
  const texture = new THREE.VideoTexture(video);
  texture.encoding = THREE.sRGBEncoding;
  texture.minFilter = THREE.LinearFilter;
  texture.magFilter = THREE.LinearFilter;
  texture.format = THREE.RGBAFormat;

  const geometry = new THREE.PlaneGeometry(1, aspectRatio);
  const material = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.position.z = 0.01;
  return mesh;
}

function cleanupFailedVideo(instance) {
  if (!instance || !instance.video) return;
  try {
    const stream = instance.video.srcObject;
    if (stream && stream.getTracks) {
      stream.getTracks().forEach((track) => track.stop());
    }
  } catch (error) {
    console.warn("MindAR cleanup warning", error);
  }

  if (instance.video.remove) {
    instance.video.remove();
  }
  instance.video = null;
}

async function resolveCameraDevice() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: true,
  });
  const track = stream.getVideoTracks()[0];
  const settings = track ? track.getSettings() : {};
  const deviceId = settings.deviceId || "";
  stream.getTracks().forEach((item) => item.stop());
  return { deviceId };
}

async function getCameraPermissionState() {
  if (!navigator.permissions || !navigator.permissions.query) {
    return "";
  }

  try {
    const permissionStatus = await navigator.permissions.query({ name: "camera" });
    return permissionStatus.state || "";
  } catch (error) {
    console.warn("Camera permission query failed", error);
    return "";
  }
}

function describeCameraError(error) {
  if (!error) {
    return "無法啟動相機或 MindAR。請確認瀏覽器權限、HTTPS 或本機測試環境後重試。";
  }
  if (error.name === "NotAllowedError" || error.name === "SecurityError") {
    return "目前這個瀏覽器已拒絕相機權限。請在瀏覽器設定中重新允許 camera，或改用 Chrome / Edge 開啟此頁面後再試。";
  }
  if (error.name === "NotFoundError" || error.name === "DevicesNotFoundError") {
    return "找不到可用相機裝置。請確認電腦或手機有可使用的相機。";
  }
  if (error.name === "NotReadableError" || error.name === "TrackStartError") {
    return "相機目前被其他程式占用。請先關閉其他使用相機的應用程式後再試一次。";
  }
  if (error.name === "OverconstrainedError") {
    return "目前的相機條件不相容，請改用其他相機或重新整理後重試。";
  }
  return error.message || "無法啟動相機或 MindAR。請確認瀏覽器權限、HTTPS 或本機測試環境後重試。";
}

document.addEventListener("DOMContentLoaded", async function () {
  const app = document.getElementById("ar-image-tracking-app");
  if (!app) return;

  const hasMindFile = app.dataset.mindFileExists === "true";
  const mindFileSrc = app.dataset.mindFileSrc || "";
  const container = document.getElementById("mindar-container");

  const statusLabel = document.getElementById("ar-status-label");
  const statusCopy = document.getElementById("ar-status-copy");
  const retryButton = document.getElementById("ar-retry-button");
  const activeBadge = document.getElementById("ar-active-badge");
  const activeVideo = document.getElementById("ar-active-video");
  const activeTitle = document.getElementById("ar-active-title");
  const activeCopy = document.getElementById("ar-active-copy");

  const sourceVideos = Array.from(document.querySelectorAll(".ar-source-video"));

  const resetOverlay = function () {
    updateText(activeBadge, "等待辨識");
    updateText(activeVideo, "尚未播放影片");
    updateText(activeTitle, "請對準圖卡");
    updateText(activeCopy, "辨識到 target 後，影片會直接貼附在圖片位置上播放，並在 targetLost 時自動停止。");
  };

  const setFailureState = function (message) {
    sourceVideos.forEach(pauseAndReset);
    updateText(statusLabel, "AR 啟動失敗");
    updateText(
      statusCopy,
      message || "無法啟動相機或 MindAR。請確認瀏覽器權限、HTTPS 或本機測試環境後重試。"
    );
    updateText(activeBadge, "啟動失敗");
    updateText(activeVideo, "未播放");
    updateText(activeTitle, "請檢查環境");
    updateText(activeCopy, "若相機權限被拒絕、瀏覽器不支援 WebGL，或 target 檔有誤，Image Tracking AR 會無法開始。");
  };

  const resetTrackingState = function () {
    sourceVideos.forEach(pauseAndReset);
    updateText(statusLabel, "等待辨識");
    updateText(statusCopy, "相機已就緒，請對準任一 target 圖卡。");
    resetOverlay();
  };

  if (!hasMindFile || !mindFileSrc || !container) {
    updateText(statusLabel, "缺少 target 檔");
    updateText(statusCopy, "請先生成 shuijing_targets.mind，之後重新整理頁面再啟動辨識。");
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    setFailureState("目前瀏覽器環境不支援相機存取。請改用支援 getUserMedia 的瀏覽器。");
    return;
  }

  retryButton?.addEventListener("click", function () {
    window.location.reload();
  });

  const permissionState = await getCameraPermissionState();
  if (permissionState === "denied") {
    setFailureState(
      "目前這個瀏覽器已拒絕相機權限。請在瀏覽器設定中重新允許 camera，或改用 Chrome / Edge 開啟此頁面後再試。"
    );
    return;
  }

  if (permissionState === "prompt") {
    updateText(statusLabel, "等待授權");
    updateText(statusCopy, "瀏覽器即將要求相機權限，請允許後再開始 Image Tracking AR。");
  }

  let preferredCamera = { deviceId: "" };
  try {
    preferredCamera = await resolveCameraDevice();
  } catch (error) {
    console.error("Camera preflight failed", error);
    setFailureState(describeCameraError(error));
    return;
  }

  const mindarThree = new MindARThree({
    container,
    imageTargetSrc: mindFileSrc,
    maxTrack: 1,
    filterMinCF: 0.001,
    filterBeta: 1000,
    warmupTolerance: 3,
    missTolerance: 5,
    userDeviceId: preferredCamera.deviceId || null,
  });

  const { renderer, scene, camera } = mindarThree;

  sourceVideos.forEach(function (video) {
    const targetIndex = Number(video.dataset.targetIndex);
    const aspectRatio = Number(video.dataset.videoHeight || "0.75");
    const anchor = mindarThree.addAnchor(targetIndex);
    const plane = createVideoPlane(video, aspectRatio);
    anchor.group.add(plane);

    anchor.onTargetFound = function () {
      sourceVideos.forEach(function (otherVideo) {
        if (otherVideo !== video) pauseAndReset(otherVideo);
      });

      pauseAndReset(video);
      video.play().catch(function (error) {
        console.warn("MindAR video play failed", error);
      });

      updateText(statusLabel, "辨識成功");
      updateText(
        statusCopy,
        `${video.dataset.targetTitle || "已辨識 target"} 已鎖定，影片正在覆蓋圖片位置播放。`
      );
      updateText(activeBadge, video.dataset.targetBadge || "辨識成功");
      updateText(activeVideo, video.dataset.targetFilename || "影片播放中");
      updateText(activeTitle, video.dataset.targetTitle || "已辨識 target");
      updateText(activeCopy, video.dataset.targetCopy || "影片正在覆蓋圖片位置播放。");
    };

    anchor.onTargetLost = function () {
      pauseAndReset(video);
      updateText(statusLabel, "等待辨識");
      updateText(statusCopy, "target 已離開畫面，請重新對準圖卡。");
      resetOverlay();
    };
  });

  try {
    mindarThree.shouldFaceUser = true;
    await mindarThree.start();
    resetTrackingState();
    renderer.setAnimationLoop(function () {
      renderer.render(scene, camera);
    });
  } catch (error) {
    console.error("MindAR start failed", error);
    cleanupFailedVideo(mindarThree);
    setFailureState(describeCameraError(error));
    return;
  }

  document.addEventListener(
    "visibilitychange",
    function () {
      if (document.hidden) {
        sourceVideos.forEach(pauseAndReset);
      }
    },
    { passive: true }
  );
});
