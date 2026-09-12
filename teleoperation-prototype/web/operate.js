/* F5.3: session keys + display-only HUD from /teleop/state and /teleop/tool_pose. */
const SESSION_PATH = "/ws/session";

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

function postNamedPose(_name) {
  // F5.4: POST /api/named_pose
}

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

void postNamedPose;
