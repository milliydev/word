// KompSavod – o'z video pleyerimiz.
// YouTube videosini YouTube interfeysisiz ko'rsatadi: logotip, nom, "YouTube'da ko'rish",
// tavsiya etilgan videolar va o'ng tugma menyusi foydalanuvchiga ko'rinmaydi.
// Barcha boshqaruv (play, vaqt, ovoz, tezlik, to'liq ekran) bizning tugmalarimiz orqali.

let apiPromise = null;
function loadYouTubeApi() {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (apiPromise) return apiPromise;
  apiPromise = new Promise((resolve, reject) => {
    const prev = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => { prev?.(); resolve(window.YT); };
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api";
    s.async = true;
    s.onerror = () => { apiPromise = null; reject(new Error("Video yuklanmadi")); };
    document.head.appendChild(s);
  });
  return apiPromise;
}

const ICONS = {
  play: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z" fill="currentColor"/></svg>',
  pause: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor"/></svg>',
  vol: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z" fill="currentColor"/><path d="M16 8.5a5 5 0 0 1 0 7M18.5 6a8.5 8.5 0 0 1 0 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  mute: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9H4z" fill="currentColor"/><path d="M16.5 9.5l5 5M21.5 9.5l-5 5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  full: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  exitFull: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  replay: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>'
};

const SPEEDS = [0.75, 1, 1.25, 1.5, 2];

function fmt(t) {
  t = Math.max(0, Math.floor(t || 0));
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}

/**
 * box ichiga YouTube videosini o'z pleyerimiz bilan joylaydi.
 * @returns {{ destroy(): void }}
 */
export function mountYouTubePlayer(box, videoId, { title = "", onEnded } = {}) {
  box.classList.add("ks-player", "is-idle");
  box.tabIndex = 0;
  box.setAttribute("aria-label", title ? `Video: ${title}` : "Video");
  box.innerHTML = `
    <div class="ksp-stage"><div class="ksp-frame"></div></div>
    <div class="ksp-shield" aria-hidden="true"></div>
    <div class="ksp-cover">
      <button class="ksp-big" type="button" aria-label="Ijro etish">${ICONS.play}</button>
      <p class="ksp-msg"></p>
    </div>
    <div class="ksp-bar">
      <button class="ksp-btn ksp-play" type="button" aria-label="Ijro etish">${ICONS.play}</button>
      <span class="ksp-time"><span class="ksp-cur">0:00</span> / <span class="ksp-dur">0:00</span></span>
      <input class="ksp-seek" type="range" min="0" max="1000" step="1" value="0" aria-label="Video vaqti">
      <button class="ksp-btn ksp-mute" type="button" aria-label="Ovozni o‘chirish">${ICONS.vol}</button>
      <input class="ksp-vol" type="range" min="0" max="100" step="1" value="100" aria-label="Ovoz balandligi">
      <button class="ksp-btn ksp-speed" type="button" aria-label="Tezlik">1x</button>
      <button class="ksp-btn ksp-fs" type="button" aria-label="To‘liq ekran">${ICONS.full}</button>
    </div>`;

  const $ = (s) => box.querySelector(s);
  const cover = $(".ksp-cover"), big = $(".ksp-big"), msg = $(".ksp-msg");
  const playBtn = $(".ksp-play"), seek = $(".ksp-seek"), cur = $(".ksp-cur"), dur = $(".ksp-dur");
  const muteBtn = $(".ksp-mute"), vol = $(".ksp-vol"), speedBtn = $(".ksp-speed"), fsBtn = $(".ksp-fs");

  let player = null, ready = false, timer = null, hideT = null, seeking = false, destroyed = false, speedIdx = 1, pendingPlay = false;

  const setState = (s) => {
    box.classList.remove("is-idle", "is-playing", "is-paused", "is-ended", "is-loading");
    box.classList.add(`is-${s}`);
    const playing = s === "playing";
    playBtn.innerHTML = playing ? ICONS.pause : ICONS.play;
    playBtn.setAttribute("aria-label", playing ? "Pauza" : "Ijro etish");
    big.innerHTML = s === "ended" ? ICONS.replay : ICONS.play;
    big.setAttribute("aria-label", s === "ended" ? "Qayta ko‘rish" : "Ijro etish");
    msg.textContent = s === "ended" ? "Dars tugadi" : "";
  };

  const tick = () => {
    if (!ready || seeking) return;
    const d = player.getDuration() || 0, t = player.getCurrentTime() || 0;
    cur.textContent = fmt(t); dur.textContent = fmt(d);
    seek.value = d ? Math.round((t / d) * 1000) : 0;
    seek.style.setProperty("--p", `${seek.value / 10}%`);
  };

  const toggle = () => {
    if (!ready) { box.classList.add("is-loading"); pendingPlay = true; return; }
    const st = player.getPlayerState();
    if (st === 1 || st === 3) player.pauseVideo();
    else { if (st === 0) player.seekTo(0, true); player.playVideo(); }
  };

  const showBar = () => {
    box.classList.add("show-bar");
    clearTimeout(hideT);
    hideT = setTimeout(() => { if (box.classList.contains("is-playing")) box.classList.remove("show-bar"); }, 2500);
  };

  // Video ustidagi shaffof qatlam: barcha bosishlarni ushlaydi, YouTube elementlariga yetkazmaydi
  $(".ksp-shield").addEventListener("click", toggle);
  $(".ksp-shield").addEventListener("dblclick", () => toggleFs());
  big.addEventListener("click", toggle);
  playBtn.addEventListener("click", toggle);
  box.addEventListener("contextmenu", (e) => e.preventDefault());
  box.addEventListener("pointermove", showBar);
  box.addEventListener("touchstart", showBar, { passive: true });

  seek.addEventListener("input", () => {
    seeking = true;
    const d = ready ? player.getDuration() : 0;
    cur.textContent = fmt((seek.value / 1000) * d);
    seek.style.setProperty("--p", `${seek.value / 10}%`);
  });
  seek.addEventListener("change", () => {
    if (ready) player.seekTo((seek.value / 1000) * player.getDuration(), true);
    seeking = false;
  });

  const syncVol = () => {
    const m = player.isMuted() || player.getVolume() === 0;
    muteBtn.innerHTML = m ? ICONS.mute : ICONS.vol;
    muteBtn.setAttribute("aria-label", m ? "Ovozni yoqish" : "Ovozni o‘chirish");
    vol.value = m ? 0 : player.getVolume();
  };
  muteBtn.addEventListener("click", () => {
    if (!ready) return;
    if (player.isMuted() || player.getVolume() === 0) { player.unMute(); if (!player.getVolume()) player.setVolume(60); }
    else player.mute();
    setTimeout(syncVol, 50);
  });
  vol.addEventListener("input", () => {
    if (!ready) return;
    player.setVolume(+vol.value);
    +vol.value ? player.unMute() : player.mute();
    setTimeout(syncVol, 50);
  });

  speedBtn.addEventListener("click", () => {
    speedIdx = (speedIdx + 1) % SPEEDS.length;
    speedBtn.textContent = `${SPEEDS[speedIdx]}x`;
    if (ready) player.setPlaybackRate(SPEEDS[speedIdx]);
  });

  // To'liq ekran — iframe emas, butun pleyer (bizning qatlamlar bilan) kattalashadi
  const isFs = () => document.fullscreenElement === box || document.webkitFullscreenElement === box || box.classList.contains("ksp-pseudo-fs");
  function toggleFs() {
    if (isFs()) {
      if (box.classList.contains("ksp-pseudo-fs")) { box.classList.remove("ksp-pseudo-fs"); document.body.style.overflow = ""; }
      else (document.exitFullscreen || document.webkitExitFullscreen)?.call(document);
    } else if (box.requestFullscreen) box.requestFullscreen().catch(() => pseudoFs());
    else if (box.webkitRequestFullscreen) box.webkitRequestFullscreen();
    else pseudoFs();
    setTimeout(syncFs, 100);
  }
  function pseudoFs() { box.classList.add("ksp-pseudo-fs"); document.body.style.overflow = "hidden"; syncFs(); }
  function syncFs() {
    fsBtn.innerHTML = isFs() ? ICONS.exitFull : ICONS.full;
    fsBtn.setAttribute("aria-label", isFs() ? "To‘liq ekrandan chiqish" : "To‘liq ekran");
  }
  fsBtn.addEventListener("click", toggleFs);
  document.addEventListener("fullscreenchange", syncFs);
  document.addEventListener("webkitfullscreenchange", syncFs);

  box.addEventListener("keydown", (e) => {
    if (e.target.matches("input")) return;
    const k = e.key.toLowerCase();
    if (k === " " || k === "k") { e.preventDefault(); toggle(); }
    else if (k === "f") toggleFs();
    else if (k === "m") muteBtn.click();
    else if (ready && (k === "arrowright" || k === "arrowleft")) {
      e.preventDefault();
      player.seekTo(player.getCurrentTime() + (k === "arrowright" ? 5 : -5), true);
      tick();
    }
    showBar();
  });

  setState("idle");

  loadYouTubeApi().then((YT) => {
    if (destroyed) return;
    player = new YT.Player($(".ksp-frame"), {
      host: "https://www.youtube-nocookie.com",
      videoId,
      playerVars: {
        controls: 0, disablekb: 1, fs: 0, rel: 0, iv_load_policy: 3,
        modestbranding: 1, playsinline: 1, cc_load_policy: 0, enablejsapi: 1,
        origin: location.origin
      },
      events: {
        onReady: () => {
          ready = true;
          const f = player.getIframe();
          f.setAttribute("tabindex", "-1");
          f.setAttribute("title", title || "Video");
          tick(); syncVol();
          timer = setInterval(tick, 250);
          if (pendingPlay) { pendingPlay = false; player.playVideo(); }
        },
        onStateChange: (e) => {
          // -1 boshlanmagan, 0 tugadi, 1 ijro, 2 pauza, 3 yuklanmoqda, 5 tayyor
          if (e.data === 1) { setState("playing"); showBar(); }
          else if (e.data === 2) setState("paused");
          else if (e.data === 0) { setState("ended"); tick(); onEnded?.(); }
          else if (e.data === 3) box.classList.add("is-loading");
        },
        onError: () => { setState("idle"); msg.textContent = "Videoni ochib bo‘lmadi. Keyinroq urinib ko‘ring."; }
      }
    });
  }).catch(() => { msg.textContent = "Videoni yuklab bo‘lmadi. Internetni tekshiring."; });

  return {
    destroy() {
      destroyed = true;
      clearInterval(timer); clearTimeout(hideT);
      document.removeEventListener("fullscreenchange", syncFs);
      document.removeEventListener("webkitfullscreenchange", syncFs);
      document.body.style.overflow = "";
      try { player?.destroy(); } catch {}
    }
  };
}
