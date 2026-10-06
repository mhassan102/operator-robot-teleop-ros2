(function () {
  function post(path) {
    fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: "{}"
    }).catch(function () {});
  }

  function bind(id, path) {
    var button = document.getElementById(id);
    if (!button) return;
    button.addEventListener("click", function () { post(path); });
  }

  bind("session-stop", "/api/stop");
  bind("session-logout", "/api/logout");
  bind("session-quit", "/api/quit");

  function poll() {
    fetch("/api/session", {cache: "no-store"})
      .then(function (response) { return response.json(); })
      .then(function (snapshot) {
        if (!snapshot || !snapshot.view) return;
        if (snapshot.view === "login" && window.location.pathname !== "/") {
          window.location.assign("/");
          return;
        }
        if (snapshot.view === "config" && window.location.pathname !== "/config") {
          window.location.assign("/config");
          return;
        }
        if (
          snapshot.view === "operate" &&
          window.location.pathname !== "/operate" &&
          snapshot.operate_url
        ) {
          window.location.assign(snapshot.operate_url);
        }
      })
      .catch(function () {});
  }

  window.setInterval(poll, 300);
})();
