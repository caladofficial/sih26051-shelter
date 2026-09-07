/* SIH26051 · login page — sign in / register against the platform API. */
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  let mode = "login";

  function setErr(msg) {
    const el = $("loginErr");
    if (!msg) { el.hidden = true; return; }
    el.textContent = "▲ " + msg;
    el.hidden = false;
  }

  function setMode(m) {
    mode = m;
    $("tabLogin").classList.toggle("on", m === "login");
    $("tabSignup").classList.toggle("on", m === "signup");
    $("authSubmit").textContent = m === "login" ? "SIGN IN →" : "CREATE ACCOUNT →";
    $("signupHint").hidden = m !== "signup";
    $("fPass").autocomplete = m === "login" ? "current-password" : "new-password";
    setErr(null);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("tabLogin").addEventListener("click", () => setMode("login"));
    $("tabSignup").addEventListener("click", () => setMode("signup"));

    $("pwToggle").addEventListener("click", () => {
      const f = $("fPass");
      const show = f.type === "password";
      f.type = show ? "text" : "password";
      $("pwToggle").textContent = show ? "🙈" : "👁";
    });

    // already signed in? go straight to the workspace
    if (window.SHI && SHI.isAuthed()) { location.href = "dashboard.html"; return; }

    $("authForm").addEventListener("submit", async (e) => {
      e.preventDefault();
      const user = $("fUser").value.trim();
      const pass = $("fPass").value;
      if (!user || !pass) { setErr("Enter a username and password"); return; }
      if (mode === "signup" && pass.length < 6) { setErr("Password must be at least 6 characters"); return; }
      const btn = $("authSubmit");
      btn.disabled = true;
      btn.textContent = mode === "login" ? "VERIFYING…" : "CREATING…";
      try {
        if (mode === "login") await SHI.login(user, pass);
        else await SHI.signup(user, pass);
        location.href = "dashboard.html";
      } catch (err) {
        setErr(err.message || "request failed");
        btn.disabled = false;
        btn.textContent = mode === "login" ? "SIGN IN →" : "CREATE ACCOUNT →";
      }
    });
  });
})();
