/* F5.1: static operate shell. F5.2 opens /ws/session; do not send keys yet. */
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

function connectSession() {
  // F5.2: new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + SESSION_PATH)
}

function sendKey(_key, _down) {
  // F5.2: session socket sends {type:"key", key, down}
}

function postNamedPose(_name) {
  // F5.4: POST /api/named_pose
}

void SESSION_PATH;
void KEY_BINDINGS;
void connectSession;
void sendKey;
void postNamedPose;
