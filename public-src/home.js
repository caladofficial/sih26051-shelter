/* SIH26051 · landing page logic — 3D hero, live stats, scroll reveals, auth chip. */
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);

  /* ---------------- auth chip ---------------- */
  function renderAuth() {
    const el = $("lndAuth");
    if (!el || !window.SHI) return;
    const u = SHI.getUser();
    el.innerHTML = u
      ? `<span class="lnd-who">◈ ${u.username}</span><a class="btn lnd-ghost btn-sm" href="login.html" id="lndSignout">SIGN OUT</a>`
      : `<a class="btn lnd-ghost btn-sm" href="login.html">SIGN IN</a>`;
    const so = $("lndSignout");
    if (so) so.addEventListener("click", (e) => {
      e.preventDefault();
      SHI.logout();
      renderAuth();
    });
  }

  /* ---------------- live stats ---------------- */
  async function loadStats() {
    try {
      const j = await (window.SHI ? SHI.apiFetch("/api/stats") : fetch("/api/stats").then((r) => r.json()));
      const map = [
        ["SIMULATIONS", j.simulations], ["OPTIMIZATIONS", j.optimizations],
        ["DESIGNS", j.designs], ["SHELTERS", j.shelters],
      ];
      const box = $("heroStats");
      if (box) {
        box.querySelectorAll("b").forEach((b, i) => { if (map[i]) b.textContent = map[i][1]; });
      }
    } catch (e) { /* stats are decorative — fail silently */ }
  }

  /* ---------------- scroll reveals ---------------- */
  function initReveal() {
    const els = document.querySelectorAll(".lnd-sec, .hero-content");
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) { en.target.classList.add("vis"); io.unobserve(en.target); }
      });
    }, { threshold: 0.12 });
    els.forEach((el) => io.observe(el));
  }

  /* ---------------- 3D hero shelter ---------------- */
  let hero = null;
  function initHero3D() {
    if (typeof THREE === "undefined") return;
    const stage = $("heroStage");
    if (!stage) return;
    const w = stage.clientWidth || 900, h = stage.clientHeight || 520;
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, w / h, 0.05, 200);
    camera.position.set(6.4, 5.0, 7.6);
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, h);
    stage.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const d1 = new THREE.DirectionalLight(0xfff2dd, 1.05);
    d1.position.set(7, 10, 6); scene.add(d1);
    const d2 = new THREE.DirectionalLight(0x93c5ff, 0.4);
    d2.position.set(-6, 3, -7); scene.add(d2);

    const grid = new THREE.GridHelper(14, 28, 0x3a4047, 0x1e2329);
    scene.add(grid);

    const group = new THREE.Group();
    scene.add(group);

    const baseMat = (c) => new THREE.MeshStandardMaterial({ color: c, roughness: 0.85, metalness: 0.12 });
    const s = 1.0;
    // floor slab
    const slab = new THREE.Mesh(new THREE.BoxGeometry(3.2 * s, 0.12 * s, 3.2 * s), baseMat(0x6d6a63));
    slab.position.y = 0.06 * s; group.add(slab);
    // walls (4)
    const wall = baseMat(0x8a8177);
    const mk = (geo, y, x, z) => { const m = new THREE.Mesh(geo, wall); m.position.set(x, y, z); group.add(m); return m; };
    mk(new THREE.BoxGeometry(3.2 * s, 2.2 * s, 0.14 * s), 1.16 * s, 0, -1.53 * s);   // south (camera)
    mk(new THREE.BoxGeometry(3.2 * s, 2.2 * s, 0.14 * s), 1.16 * s, 0, 1.53 * s);    // north
    mk(new THREE.BoxGeometry(0.14 * s, 2.2 * s, 3.2 * s), 1.16 * s, -1.53 * s, 0);   // west
    mk(new THREE.BoxGeometry(0.14 * s, 2.2 * s, 3.2 * s), 1.16 * s, 1.53 * s, 0);    // east
    // window on south wall
    const win = new THREE.Mesh(new THREE.BoxGeometry(1.0 * s, 0.9 * s, 0.06 * s),
      new THREE.MeshStandardMaterial({ color: 0x2a3a4d, roughness: 0.15, metalness: 0.6, transparent: true, opacity: 0.85 }));
    win.position.set(0, 1.1 * s, -1.44 * s); group.add(win);
    // door north
    const door = new THREE.Mesh(new THREE.BoxGeometry(0.8 * s, 1.5 * s, 0.08 * s), baseMat(0x4c463e));
    door.position.set(0, 0.75 * s, 1.44 * s); group.add(door);
    // roof
    const roof = new THREE.Mesh(new THREE.ConeGeometry(2.55 * s, 1.15 * s, 4, 1),
      baseMat(0x3f3f46));
    roof.rotation.y = Math.PI / 4;
    roof.position.y = 2.32 * s; group.add(roof);
    // solar strip
    const pv = new THREE.Mesh(new THREE.BoxGeometry(1.6 * s, 0.05 * s, 1.2 * s),
      new THREE.MeshStandardMaterial({ color: 0x1d4ed8, roughness: 0.3, metalness: 0.5 }));
    pv.position.set(0, 2.98 * s, 0); pv.rotation.x = 0.5; group.add(pv);

    const resize = () => {
      const w2 = stage.clientWidth || 900, h2 = stage.clientHeight || 520;
      if (!w2 || !h2) return;
      renderer.setSize(w2, h2);
      camera.aspect = w2 / h2;
      camera.updateProjectionMatrix();
    };
    window.addEventListener("resize", resize);
    new ResizeObserver(resize).observe(stage);

    let raf = 0, t = 0, slow = false;
    (function loop() {
      raf = requestAnimationFrame(loop);
      if (document.hidden) return;
      t += 0.004;
      group.rotation.y = Math.sin(t * 0.55) * 0.55 + t * 0.06;
      group.position.y = Math.sin(t * 1.7) * 0.05;
      renderer.render(scene, camera);
    })();

    const io = new IntersectionObserver((en) => {
      slow = !en[0].isIntersecting;
    }, { threshold: 0.1 });
    io.observe(stage);

    hero = { renderer, stage };
  }

  /* ---------------- boot ---------------- */
  document.addEventListener("DOMContentLoaded", () => {
    renderAuth();
    if (window.SHI) SHI.onAuth(renderAuth);
    loadStats();
    initReveal();
    setTimeout(initHero3D, 60);
  });
})();
