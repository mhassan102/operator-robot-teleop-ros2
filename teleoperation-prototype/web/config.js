(function () {
  var seen = -1;
  var hostname = document.getElementById("config-hostname");
  var help = document.getElementById("link-help");
  var network = document.getElementById("operator-network");
  var reviewStatus = document.getElementById("review-status");
  var sessionStatus = document.getElementById("session-status");
  var startButton = document.getElementById("start");
  var fields = {
    iface1: document.getElementById("iface1"),
    iface2: document.getElementById("iface2"),
    arm: document.getElementById("arm"),
    video: document.getElementById("video")
  };

  function follow(snapshot) {
    if (!snapshot || !snapshot.view) return;
    if (snapshot.view === "login") {
      window.location.assign("/");
      return;
    }
    if (snapshot.view === "operate" && snapshot.operate_url) {
      window.location.assign(snapshot.operate_url);
      return;
    }
    paint(snapshot);
  }

  function paint(snapshot) {
    if (hostname) hostname.textContent = snapshot.hostname || "";
    if (help) help.textContent = snapshot.link_help || help.textContent;
    if (network) network.textContent = snapshot.operator_network || "";
    if (reviewStatus) reviewStatus.textContent = snapshot.review_status || "";
    if (sessionStatus) sessionStatus.textContent = snapshot.status || "";
    if (startButton) startButton.disabled = !snapshot.start_enabled;
    if (snapshot.form_revision === seen) return;
    seen = snapshot.form_revision;
    var selected = snapshot.selected || {};
    Object.keys(fields).forEach(function (name) {
      fillSelect(fields[name], (snapshot.choices || {})[name] || [], selected[name]);
    });
    var link = selected.link || "tailscale";
    var radios = document.querySelectorAll('input[name="link"]');
    radios.forEach(function (radio) {
      radio.checked = radio.value === link;
    });
  }

  function fillSelect(select, choices, selected) {
    if (!select) return;
    select.textContent = "";
    choices.forEach(function (choice) {
      var option = document.createElement("option");
      option.value = choice.value == null ? "" : String(choice.value);
      option.textContent = choice.label;
      select.appendChild(option);
    });
    select.value = selected == null ? "" : String(selected);
  }

  function current() {
    var link = "tailscale";
    document.querySelectorAll('input[name="link"]').forEach(function (radio) {
      if (radio.checked) link = radio.value;
    });
    return {
      link: link,
      iface1: fields.iface1.value,
      iface2: fields.iface2.value || null,
      arm: fields.arm.value,
      video: fields.video.value
    };
  }

  function post(path) {
    fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(current())
    })
      .then(function (response) { return response.json(); })
      .then(function (reply) {
        if (sessionStatus) sessionStatus.textContent = reply.message || reply.status || "";
        if (reviewStatus && reply.review_status) reviewStatus.textContent = reply.review_status;
        follow(reply);
      })
      .catch(function () {
        if (sessionStatus) sessionStatus.textContent = "Cannot reach the registry.";
      });
  }

  document.getElementById("review").addEventListener("click", function () {
    post("/api/review");
  });
  startButton.addEventListener("click", function () {
    post("/api/start");
  });

  function poll() {
    fetch("/api/session", {cache: "no-store"})
      .then(function (response) { return response.json(); })
      .then(follow)
      .catch(function () {});
  }

  poll();
  window.setInterval(poll, 300);
})();
