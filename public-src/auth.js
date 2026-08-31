/* SIH26051 · shared auth layer — loaded BEFORE app.js / home.js on every page.
   Exposes window.SHI (auth + api helpers) so all pages share one session. */
"use strict";
(function () {
  var K_TOKEN = "sih-token", K_USER = "sih-user";
  var SHI = window.SHI = window.SHI || {};

  SHI.getToken = function () {
    try { return localStorage.getItem(K_TOKEN); } catch (_) { return null; }
  };
  SHI.getUser = function () {
    try {
      var u = localStorage.getItem(K_USER);
      return u ? JSON.parse(u) : null;
    } catch (_) { return null; }
  };
  SHI.setAuth = function (token, user) {
    try {
      if (token) localStorage.setItem(K_TOKEN, token);
      else localStorage.removeItem(K_TOKEN);
      if (user) localStorage.setItem(K_USER, JSON.stringify(user));
      else localStorage.removeItem(K_USER);
    } catch (_) {}
  };
  SHI.isAuthed = function () { return !!SHI.getToken(); };

  /* auth-aware fetch: JSON in, JSON out; throws Error(detail) on failure */
  SHI.apiFetch = async function (path, options) {
    options = options || {};
    var headers = Object.assign({}, options.headers || {});
    var tok = SHI.getToken();
    if (tok) headers["Authorization"] = "Bearer " + tok;
    var res = await fetch(path, Object.assign({}, options, { headers: headers }));
    if (res.status === 401 && SHI.isAuthed()) {
      SHI.setAuth(null, null);           // session expired — clear and notify
      window.dispatchEvent(new CustomEvent("sih:auth", { detail: null }));
    }
    if (!res.ok) {
      var msg = String(res.status);
      try { var j = await res.json(); msg = j.detail || msg; } catch (_) {}
      throw new Error(msg);
    }
    return res.json();
  };

  function session(json) {
    SHI.setAuth(json.token, json.user);
    window.dispatchEvent(new CustomEvent("sih:auth", { detail: json.user }));
    return json.user;
  }

  SHI.login = async function (username, password) {
    return session(await SHI.apiFetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password })
    }));
  };
  SHI.signup = async function (username, password) {
    return session(await SHI.apiFetch("/api/auth/signup", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: username, password: password })
    }));
  };
  SHI.logout = function () {
    SHI.setAuth(null, null);
    window.dispatchEvent(new CustomEvent("sih:auth", { detail: null }));
  };
  SHI.onAuth = function (cb) {
    window.addEventListener("sih:auth", function (e) { cb(e.detail); });
    var cur = SHI.getUser();
    if (cur) setTimeout(function () { cb(cur); }, 0);
    return cur;
  };
})();
