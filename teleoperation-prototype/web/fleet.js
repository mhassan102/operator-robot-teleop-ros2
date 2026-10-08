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

  function cell(text) {
    var item = document.createElement("td");
    item.textContent = text || "";
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
      tr.appendChild(cell(robot.status));
      tr.appendChild(cell(robot.mode));
      var action = document.createElement("td");
      if (robot.status === "pending") {
        var approve = document.createElement("button");
        approve.type = "button";
        approve.className = "session-button";
        approve.textContent = "Approve";
        approve.addEventListener("click", function () {
          post("/api/approve", robot.robot_id);
        });
        action.appendChild(approve);
      } else if (robot.status === "online" && !snapshot.holding) {
        var operate = document.createElement("button");
        operate.type = "button";
        operate.className = "session-button";
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
