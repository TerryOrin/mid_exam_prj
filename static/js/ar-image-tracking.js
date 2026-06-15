import * as THREE from "three";
import { MindARThree } from "mindar-image-three";

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
  // Three.js r152+ 改用 ColorSpace API；r160 已移除 sRGBEncoding
  if (THREE.SRGBColorSpace !== undefined) {
    texture.colorSpace = THREE.SRGBColorSpace;
  }
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
    video: { facingMode: { ideal: "environment" } },
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

// ES module 本身就是 deferred，不需要用 DOMContentLoaded 包裹
// 但保留它以確保 DOM 已完全解析
async function initAR() {
  console.log("🎬 AR Script Starting...");

  const app = document.getElementById("ar-image-tracking-app");
  if (!app) {
    console.error("❌ #ar-image-tracking-app not found!");
    return;
  }

  const hasMindFile = app.dataset.mindFileExists === "true";
  const mindFileSrc = app.dataset.mindFileSrc || "";
  const container = document.getElementById("mindar-container");

  console.log("📋 AR Config:", {
    hasMindFile,
    mindFileSrc,
    containerExists: !!container,
  });

  const statusLabel = document.getElementById("ar-status-label");
  const statusCopy = document.getElementById("ar-status-copy");
  const retryButton = document.getElementById("ar-retry-button");
  const activeBadge = document.getElementById("ar-active-badge");
  const activeVideo = document.getElementById("ar-active-video");
  const activeTitle = document.getElementById("ar-active-title");
  const activeCopy = document.getElementById("ar-active-copy");

  const sourceVideos = Array.from(document.querySelectorAll(".ar-source-video"));

  console.log("📹 Source videos found:", sourceVideos.length);

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
    console.error("❌ Missing required files/containers:", { hasMindFile, mindFileSrc, container: !!container });
    updateText(statusLabel, "缺少 target 檔");
    updateText(statusCopy, "請先生成 shuijing_targets.mind，之後重新整理頁面再啟動辨識。");
    return;
  }

  // 驗證 target 檔案是否可訪問
  console.log("🔍 Checking target file accessibility...");
  try {
    const headResponse = await fetch(mindFileSrc, { method: "HEAD" });
    if (!headResponse.ok) {
      throw new Error(`HTTP ${headResponse.status}`);
    }
    console.log("✅ Target file is accessible");
  } catch (error) {
    console.error("❌ Target file not accessible:", error);
    updateText(statusLabel, "Target 檔案無法存取");
    updateText(statusCopy, `無法加載 target 檔案 (${mindFileSrc})，請確認文件存在。`);
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    console.error("❌ getUserMedia not supported");
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
    console.log("🎥 Resolving camera device...");
    preferredCamera = await resolveCameraDevice();
    console.log("✅ Camera device resolved:", preferredCamera);
  } catch (error) {
    console.error("❌ Camera preflight failed", error);
    setFailureState(describeCameraError(error));
    return;
  }

  console.log("🚀 Initializing MindARThree with:", {
    imageTargetSrc: mindFileSrc,
    deviceId: preferredCamera.deviceId,
  });

  let mindarThree;
  try {
    mindarThree = new MindARThree({
      container,
      imageTargetSrc: mindFileSrc,
      maxTrack: 1,
      filterMinCF: 0.001,
      filterBeta: 1000,
      warmupTolerance: 3,
      missTolerance: 5,
      // shouldFaceUser 屬於建構子選項，不是事後設定的屬性
      // 設為 false 表示優先使用後鏡頭（適合手機；電腦預設前鏡頭）
      shouldFaceUser: false,
      uiScanning: false,
      uiLoading: false,
    });
    console.log("✅ MindARThree instance created");
  } catch (error) {
    console.error("❌ MindARThree creation failed", error);
    setFailureState("MindAR 初始化失敗：" + error.message);
    return;
  }

  const { renderer, scene, camera } = mindarThree;

  sourceVideos.forEach(function (video) {
    const targetIndex = Number(video.dataset.targetIndex);
    const aspectRatio = Number(video.dataset.videoHeight || "0.5625");
    const anchor = mindarThree.addAnchor(targetIndex);
    const plane = createVideoPlane(video, aspectRatio);
    anchor.group.add(plane);

    anchor.onTargetFound = function () {
      console.log("🎯 Target found:", targetIndex);
      sourceVideos.forEach(function (otherVideo) {
        if (otherVideo !== video) pauseAndReset(otherVideo);
      });

      pauseAndReset(video);
      video.play().catch(function (error) {
        console.warn("⚠️ Video play failed", error);
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
      console.log("❌ Target lost:", targetIndex);
      pauseAndReset(video);
      updateText(statusLabel, "等待辨識");
      updateText(statusCopy, "target 已離開畫面，請重新對準圖卡。");
      resetOverlay();
    };
  });

  try {
    console.log("🎬 Starting MindAR...");
    await mindarThree.start();
    console.log("✅ MindAR started successfully");

    // MindAR 建立的相機 video 預設 z-index: -2
    // 把 video 調到 z-index:1，canvas(WebGL) 調到 2，CSS canvas 調到 2
    // 這樣: 背景(#000) < video(相機) < canvas(AR 渲染層，alpha透明) < UI overlay(z-index:4)
    if (mindarThree.video) {
      console.log("📹 Camera video element:", {
        width: mindarThree.video.videoWidth,
        height: mindarThree.video.videoHeight,
        srcObject: !!mindarThree.video.srcObject,
        zIndex: mindarThree.video.style.zIndex,
        position: mindarThree.video.style.position,
        styleTop: mindarThree.video.style.top,
        styleLeft: mindarThree.video.style.left,
        styleWidth: mindarThree.video.style.width,
        styleHeight: mindarThree.video.style.height,
      });
      mindarThree.video.style.zIndex = "1";
    }

    // 讓 WebGL canvas 在 video 之上
    if (renderer && renderer.domElement) {
      renderer.domElement.style.zIndex = "2";
      renderer.domElement.style.position = "absolute";
      renderer.domElement.style.top = "0";
      renderer.domElement.style.left = "0";
    }

    // CSS renderer (用於 CSS3D 物件) 也要在 video 之上
    if (mindarThree.cssRenderer && mindarThree.cssRenderer.domElement) {
      mindarThree.cssRenderer.domElement.style.zIndex = "3";
    }

    console.log("📦 Container children after start:", container.childNodes.length, container.innerHTML.substring(0, 300));

    // 強制 resize 確保 container 尺寸正確（start 後 container 才有真實尺寸）
    if (typeof mindarThree.resize === "function") {
      mindarThree.resize();
    }

    resetTrackingState();
    renderer.setAnimationLoop(function () {
      renderer.render(scene, camera);
    });
  } catch (error) {
    console.error("❌ MindAR start failed:", error);
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
}

// 直接呼叫（ES module 已經是 deferred，DOM 一定已解析完畢）
initAR().catch(function (error) {
  console.error("❌ Unhandled AR init error:", error);
});
