(function () {
  var form = document.getElementById("login-form");
  var username = document.getElementById("username");
  var password = document.getElementById("password");
  var status = document.getElementById("login-status");

  function show(text) {
    if (status) status.textContent = text || "";
  }

  function follow(snapshot) {
    if (!snapshot) return;
    if (snapshot.view === "fleet") {
      window.location.assign("/fleet");
      return;
    }
    if (snapshot.view === "config") {
      window.location.assign("/config");
      return;
    }
    if (snapshot.view === "operate" && snapshot.operate_url) {
      window.location.assign(snapshot.operate_url);
      return;
    }
    show(snapshot.message || snapshot.status || "");
  }

  function poll() {
    fetch("/api/session", {cache: "no-store"})
      .then(function (response) { return response.json(); })
      .then(follow)
      .catch(function () {});
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    fetch("/api/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        username: username.value,
        password: password.value
      })
    })
      .then(function (response) { return response.json(); })
      .then(function (reply) {
        if (reply.ok) password.value = "";
        follow(reply);
      })
      .catch(function () {
        show("Cannot reach the registry.");
      });
  });

  poll();
  window.setInterval(poll, 300);
})();
