(function () {
  var status = document.getElementById("fleet-status");
  var body = document.getElementById("fleet-rows");
  var counts = {
    registered: document.getElementById("count-registered"),
    autonomous: document.getElementById("count-autonomous"),
    needs_manual: document.getElementById("count-needs-manual"),
    in_teleop: document.getElementById("count-in-teleop")
  };

  function post(path, robotId) {
    fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({robot_id: robotId})
    }).catch(function () {});
  }

  var PERSON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="8" r="3"></circle><path d="M5.5 19c1.1-3 3.4-4.5 6.5-4.5s5.4 1.5 6.5 4.5"></path></svg>';
  var COMPUTER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="13" height="10" rx="1.5"></rect><path d="M7 18h5M9.5 14v4"></path><path d="M18.2 3.2l.55 1.25 1.25.55-1.25.55-.55 1.25-.55-1.25-1.25-.55 1.25-.55.55-1.25z" fill="currentColor" stroke="none"></path></svg>';
  var DOT = {
    pending: "pending",
    starting: "pending",
    online: "online",
    running: "online",
    attached: "online",
    offline: "offline",
    stale: "offline",
    error: "offline"
  };

  function cell(text) {
    var item = document.createElement("td");
    item.textContent = text || "";
    return item;
  }

  function marked(text) {
    var item = document.createElement("td");
    var wrap = document.createElement("span");
    wrap.className = "fleet-mark";
    var dot = document.createElement("span");
    dot.className = "fleet-dot" + (DOT[text] ? " fleet-dot-" + DOT[text] : "");
    var label = document.createElement("span");
    label.textContent = text || "";
    wrap.appendChild(dot);
    wrap.appendChild(label);
    item.appendChild(wrap);
    return item;
  }

  function modeCell(mode) {
    var item = document.createElement("td");
    var wrap = document.createElement("span");
    wrap.className = "fleet-mode";
    if (mode === "manual" || mode === "autonomous") {
      wrap.classList.add("fleet-mode-" + mode);
      var icon = document.createElement("span");
      icon.className = "fleet-mode-icon";
      icon.innerHTML = mode === "manual" ? PERSON : COMPUTER;
      wrap.appendChild(icon);
    }
    var label = document.createElement("span");
    label.textContent = mode || "";
    wrap.appendChild(label);
    item.appendChild(wrap);
    return item;
  }

  function paint(snapshot) {
    if (!snapshot || window.location.pathname !== "/fleet") return;
    var numbers = snapshot.counts || {};
    counts.registered.textContent = String(numbers.registered || 0);
    counts.autonomous.textContent = String(numbers.autonomous || 0);
    counts.needs_manual.textContent = String(numbers.needs_manual || 0);
    counts.in_teleop.textContent = String(numbers.in_teleop || 0);
    if (status) {
      status.textContent = snapshot.fleet_seen
        ? (snapshot.status || "")
        : (snapshot.status || "Waiting for the fleet.");
    }
    if (!body) return;
    body.textContent = "";
    var rows = (snapshot.robots || []).slice().sort(function (left, right) {
      return String(left.robot_id).localeCompare(String(right.robot_id));
    });
    rows.forEach(function (robot) {
      var tr = document.createElement("tr");
      tr.appendChild(cell(robot.robot_id));
      tr.appendChild(cell(robot.location));
      tr.appendChild(marked(robot.status));
      tr.appendChild(modeCell(robot.mode));
      var action = document.createElement("td");
      if (robot.status === "pending") {
        var approve = document.createElement("button");
        approve.type = "button";
        approve.className = "fleet-approve";
        approve.textContent = "Approve";
        approve.addEventListener("click", function () {
          post("/api/approve", robot.robot_id);
        });
        action.appendChild(approve);
      } else if (robot.status === "online" && !snapshot.holding) {
        var operate = document.createElement("button");
        operate.type = "button";
        operate.className = "fleet-operate";
        operate.textContent = "Operate";
        operate.addEventListener("click", function () {
          post("/api/attach", robot.robot_id);
        });
        action.appendChild(operate);
      } else if (robot.operator && robot.operator !== snapshot.username) {
        action.textContent = "In use";
      }
      tr.appendChild(action);
      body.appendChild(tr);
    });
  }

  function poll() {
    fetch("/api/session", {cache: "no-store"})
      .then(function (response) { return response.json(); })
      .then(paint)
      .catch(function () {});
  }

  poll();
  window.setInterval(poll, 300);
})();
