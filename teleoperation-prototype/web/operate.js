/* F5 session keys / HUD / named poses. F6 camera uses MediaMTX WHEP reader. */
const SESSION_PATH = "/ws/session";
const DEFAULT_CAM = "http://127.0.0.1:8889/cam";

const KEY_BINDINGS = {
  w: "+x",
  s: "x-",
  a: "+y",
  d: "y-",
  r: "+z",
  f: "z-",
  j: "+yaw",
  l: "yaw-",
  u: "+roll",
  o: "roll-",
  i: "+pitch",
  k: "pitch-",
  g: "open",
  h: "close",
  " ": "stop",
};

const held = new Set();
let socket = null;
let poseInFlight = false;

function setHud(id, text) {
  const el = document.getElementById(id);
  if (el) {
    el.textContent = text;
  }
}

function formatPose(pose) {
  if (!pose || typeof pose.x !== "number" || typeof pose.y !== "number" || typeof pose.z !== "number") {
    return "—";
  }
  return `${pose.x.toFixed(3)} ${pose.y.toFixed(3)} ${pose.z.toFixed(3)}`;
}

function applyState(msg) {
  if (msg.connection_state) {
    setHud("hud-connection", msg.connection_state);
  }
  if (msg.watchdog_state) {
    setHud("hud-watchdog", msg.watchdog_state);
  }
  if (msg.session_id) {
    setHud("hud-session", msg.session_id);
  }
  setHud("hud-pose", formatPose(msg.pose));
}

function pageIsLive() {
  return document.visibilityState === "visible" && document.hasFocus();
}

function boundKey(event) {
  if (event.key === " ") {
    return " ";
  }
  if (event.key.length === 1) {
    const key = event.key.toLowerCase();
    if (Object.prototype.hasOwnProperty.call(KEY_BINDINGS, key)) {
      return key;
    }
  }
  return null;
}

function connectSession() {
  const proto = location.protocol === "https:" ? "wss://" : "ws://";
  socket = new WebSocket(proto + location.host + SESSION_PATH);
  socket.addEventListener("message", (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === "session" && msg.session_id) {
        setHud("hud-session", msg.session_id);
      }
      if (msg.type === "state") {
        applyState(msg);
      }
    } catch (_err) {
      /* ignore non-JSON */
    }
  });
  socket.addEventListener("close", () => {
    setHud("hud-connection", "TIMEOUT");
    socket = null;
  });
  socket.addEventListener("error", () => {
    setHud("hud-connection", "TIMEOUT");
  });
}

function sendKey(key, down) {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify({ type: "key", key, down }));
}

function sendStop() {
  sendKey(" ", true);
}

function onKeyDown(event) {
  const key = boundKey(event);
  if (key == null) {
    return;
  }
  event.preventDefault();
  if (!pageIsLive()) {
    return;
  }
  if (event.repeat || held.has(key)) {
    return;
  }
  held.add(key);
  sendKey(key, true);
}

function onKeyUp(event) {
  const key = boundKey(event);
  if (key == null) {
    return;
  }
  event.preventDefault();
  if (!held.has(key)) {
    return;
  }
  held.delete(key);
  sendKey(key, false);
}

function onBlurOrHide() {
  held.clear();
  sendStop();
}

function poseButtons() {
  return document.querySelectorAll(".pose-list button[data-pose]");
}

function setPoseBusy(busy) {
  poseInFlight = busy;
  poseButtons().forEach((btn) => {
    btn.disabled = busy;
  });
}

function setPoseStatus(ok, message) {
  const el = document.getElementById("pose-status");
  if (!el) {
    return;
  }
  el.textContent = message || "";
  el.classList.toggle("ok", ok === true);
  el.classList.toggle("fail", ok === false);
}

async function postNamedPose(name) {
  if (poseInFlight) {
    return;
  }
  setPoseBusy(true);
  setPoseStatus(null, "planning " + name + "…");
  try {
    const res = await fetch("/api/named_pose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    let data = {};
    try {
      data = await res.json();
    } catch (_err) {
      data = {};
    }
    const message = data.message || ("HTTP " + res.status);
    setPoseStatus(Boolean(data.ok), message);
  } catch (err) {
    setPoseStatus(false, String(err));
  } finally {
    setPoseBusy(false);
  }
}

function cameraWhepUrl() {
  const raw = new URLSearchParams(window.location.search).get("cam") || DEFAULT_CAM;
  let base = (raw || DEFAULT_CAM).trim();
  base = base.replace(/\/+$/, "");
  if (!base) {
    base = DEFAULT_CAM;
  }
  if (!/\/whep$/i.test(base)) {
    base = base + "/whep";
  }
  return base;
}

function setCameraMsg(text) {
  const el = document.getElementById("camera-msg");
  if (!el) {
    return;
  }
  if (text) {
    el.textContent = text;
    el.hidden = false;
  } else {
    el.textContent = "";
    el.hidden = true;
  }
}

function startCamera() {
  const video = document.getElementById("camera");
  if (!video) {
    return;
  }
  if (typeof MediaMTXWebRTCReader !== "function") {
    setCameraMsg("camera unavailable");
    return;
  }
  const whepUrl = cameraWhepUrl();
  setCameraMsg("camera: connecting…");
  const reader = new MediaMTXWebRTCReader({
    url: whepUrl,
    onError: () => {
      setCameraMsg("camera: connecting…");
    },
    onTrack: (event) => {
      video.srcObject = event.streams[0];
      video.play().catch(() => {});
      setCameraMsg("");
    },
  });
  window.addEventListener("beforeunload", () => {
    reader.close();
  });
}

startCamera();
connectSession();

document.addEventListener("keydown", onKeyDown);
document.addEventListener("keyup", onKeyUp);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "hidden") {
    onBlurOrHide();
  }
});
window.addEventListener("blur", onBlurOrHide);

const stopButton = document.getElementById("normal-stop");
if (stopButton) {
  stopButton.addEventListener("click", () => {
    sendStop();
  });
}

poseButtons().forEach((btn) => {
  btn.addEventListener("click", () => {
    const name = btn.getAttribute("data-pose");
    if (name) {
      postNamedPose(name);
    }
  });
});
