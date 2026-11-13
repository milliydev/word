// KompSavod – umumiy yordamchi funksiyalar va Firebase ulanishi
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js";
import { getAuth, onAuthStateChanged, signOut } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-auth.js";
import { getFirestore, doc, getDoc } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore.js";
import { getStorage } from "https://www.gstatic.com/firebasejs/10.12.2/firebase-storage.js";
import { firebaseConfig } from "./firebase-config.js";

export const isConfigured = !String(firebaseConfig.apiKey || "").startsWith("YOUR_");

export const app = isConfigured ? initializeApp(firebaseConfig) : null;
export const auth = app ? getAuth(app) : null;
export const db = app ? getFirestore(app) : null;
export const storage = app ? getStorage(app) : null;
if (auth) auth.languageCode = "uz";

/** Sozlanmagan bo'lsa, sahifa tepasida ogohlantirish chiqaradi */
export function showConfigWarning() {
  if (isConfigured) return false;
  const bar = document.createElement("div");
  bar.className = "config-warning";
  bar.textContent = "Sayt hali Firebase’ga ulanmagan: assets/firebase-config.js faylini to‘ldiring.";
  document.body.prepend(bar);
  return true;
}

/** Joriy foydalanuvchini bir marta kutib oladi */
export function currentUser() {
  return new Promise((resolve) => {
    if (!auth) return resolve(null);
    const stop = onAuthStateChanged(auth, (u) => { stop(); resolve(u); });
  });
}

/** Foydalanuvchi profilini (users/{uid}) o'qiydi */
export async function loadProfile(uid) {
  const snap = await getDoc(doc(db, "users", uid));
  return snap.exists() ? { id: snap.id, ...snap.data() } : null;
}

export async function logout() {
  if (auth) await signOut(auth);
  location.href = "/";
}

/** HTML'ga xavfsiz chiqarish (foydalanuvchi kiritgan matnlar uchun) */
export function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** Firestore vaqtini o'qiladigan sanaga aylantiradi */
export function fmtDate(ts, withTime = true) {
  if (!ts) return "—";
  const d = typeof ts.toDate === "function" ? ts.toDate() : new Date(ts);
  const pad = (n) => String(n).padStart(2, "0");
  const date = `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
  return withTime ? `${date} ${pad(d.getHours())}:${pad(d.getMinutes())}` : date;
}

/** +998901234567 → +998 90 123 45 67 */
export function fmtPhone(p) {
  const m = String(p || "").match(/^\+998(\d{2})(\d{3})(\d{2})(\d{2})$/);
  return m ? `+998 ${m[1]} ${m[2]} ${m[3]} ${m[4]}` : String(p || "");
}

/**
 * YouTube yoki Vimeo havolasini xavfsiz embed manziliga aylantiradi.
 * Boshqa saytlar qabul qilinmaydi.
 */
export function youtubeId(url) {
  const m = String(url || "").trim().match(/^https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|live\/)|youtu\.be\/)([\w-]{11})/);
  return m ? m[1] : null;
}

export function videoEmbed(url) {
  const s = String(url || "").trim();
  const yt = youtubeId(s);
  if (yt) return `https://www.youtube-nocookie.com/embed/${yt}?rel=0&modestbranding=1`;
  let m = s.match(/^https?:\/\/(?:www\.|player\.)?vimeo\.com\/(?:video\/)?(\d+)(?:\/([\w]+))?/);
  if (m) return `https://player.vimeo.com/video/${m[1]}?${m[2] ? `h=${m[2]}&` : ""}title=0&byline=0&portrait=0&dnt=1`;
  return null;
}

/** Ekran pastida qisqa xabar */
export function toast(message, kind = "info") {
  let box = document.getElementById("toast");
  if (!box) {
    box = document.createElement("div");
    box.id = "toast";
    box.setAttribute("role", "status");
    box.setAttribute("aria-live", "polite");
    document.body.appendChild(box);
  }
  box.textContent = message;
  box.className = `toast show ${kind}`;
  clearTimeout(box._t);
  box._t = setTimeout(() => { box.className = "toast"; }, 3800);
}

export const STATUS_LABELS = {
  pending: "Kutilmoqda",
  approved: "Ruxsat berilgan",
  rejected: "Rad etilgan",
  blocked: "Bloklangan"
};

export const REGIONS = [
  "Toshkent shahri", "Toshkent viloyati", "Andijon", "Buxoro", "Farg‘ona", "Jizzax",
  "Xorazm", "Namangan", "Navoiy", "Qashqadaryo", "Qoraqalpog‘iston", "Samarqand",
  "Sirdaryo", "Surxondaryo", "Chet elda"
];

/** Logotip (SVG) – oq kvadrat ichida yashil "K", ko'k "S" va to'q sariq kursor */
export const LOGO_SVG = `<svg width="40" height="40" viewBox="0 0 512 512" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><linearGradient id="ksBg" x1="256" y1="24" x2="256" y2="488" gradientUnits="userSpaceOnUse"><stop stop-color="#FFFFFF"></stop><stop offset="1" stop-color="#EDF0F7"></stop></linearGradient><linearGradient id="ksGr" x1="0" y1="170" x2="0" y2="345" gradientUnits="userSpaceOnUse"><stop stop-color="#36C270"></stop><stop offset="1" stop-color="#128A49"></stop></linearGradient><linearGradient id="ksBl" x1="0" y1="190" x2="0" y2="330" gradientUnits="userSpaceOnUse"><stop stop-color="#4F90F7"></stop><stop offset="1" stop-color="#2460EA"></stop></linearGradient><linearGradient id="ksOr" x1="0" y1="232" x2="0" y2="312" gradientUnits="userSpaceOnUse"><stop stop-color="#FCA046"></stop><stop offset="1" stop-color="#E9581F"></stop></linearGradient></defs><rect x="24" y="24" width="464" height="464" rx="116" fill="url(#ksBg)"></rect><g transform="translate(-28 0)" stroke-linecap="round" stroke-linejoin="round"><g stroke="url(#ksGr)" stroke-width="40" fill="none"><path d="M170 170 V342"></path><path d="M184 256 L248 178"></path><path d="M184 256 L248 334"></path></g><path d="M404 202 H360 a29 29 0 0 0 0 58 H378 a29 29 0 0 1 0 58 H322" fill="none" stroke="url(#ksBl)" stroke-width="40"></path><path transform="translate(206 232) scale(1.85)" d="M0 0 L0 33 L8 25.5 L13 37 L18.5 34.5 L13.5 23.5 L24 23.5 Z" fill="url(#ksOr)" stroke="#FFFFFF" stroke-width="5"></path></g></svg>`;

/* ---------- Kurs rasmlari (tiniq SVG, istalgan o'lchamda aniq) ---------- */
export const COURSE_ICONS = [
  ["auto", "Avtomatik (kurs nomiga qarab)"],
  ["word", "Word"],
  ["excel", "Excel"],
  ["sheets", "Google Sheets"],
  ["powerpoint", "PowerPoint"],
  ["windows", "Windows"],
  ["internet", "Internet"],
  ["computer", "Kompyuter"]
];

/** Kurs nomidan mos rasmni topadi */
export function courseIconKey(course) {
  const k = course?.icon;
  if (k && k !== "auto" && COURSE_ICONS.some(([x]) => x === k)) return k;
  const t = String(course?.title || "").toLowerCase();
  if (/sheets?\b|google\s*(jadval|table)|гугл\s*(шит|табл)/.test(t)) return "sheets";
  if (/word|ворд|vord/.test(t)) return "word";
  if (/excel|eksel|эксел|ексел/.test(t)) return "excel";
  if (/power\s*point|pptx?\b|taqdimot|презентац/.test(t)) return "powerpoint";
  if (/windows|vindows|виндовс|windovs/.test(t)) return "windows";
  if (/internet|интернет|brauzer|browser|chrome|google|telegram/.test(t)) return "internet";
  return "computer";
}

let icoSeq = 0;
/** Kurs rasmi SVG ko'rinishida */
export function courseIcon(course, size = 64) {
  const key = courseIconKey(course);
  const id = `ci${++icoSeq}`;
  const wrap = (body) => `<svg class="course-ico-svg" width="${size}" height="${size}" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${esc(COURSE_ICONS.find(([x]) => x === key)[1])}">${body}</svg>`;
  const tile = (c1, c2, glyph) => `
    <linearGradient id="${id}t" x1="4" y1="13" x2="26" y2="35" gradientUnits="userSpaceOnUse"><stop stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient>
    <rect x="3" y="13" width="23" height="23" rx="3.5" fill="url(#${id}t)"/>
    <path d="${glyph}" fill="#fff" fill-rule="evenodd"/>`;
  const sheet = (cols) => `
    <clipPath id="${id}c"><rect x="12" y="5" width="32" height="38" rx="4"/></clipPath>
    <g clip-path="url(#${id}c)">${cols.map((c, i) => `<rect x="12" y="${5 + i * 9.5}" width="32" height="9.6" fill="${c}"/>`).join("")}</g>`;
  switch (key) {
    case "word":
      return wrap(`<defs></defs>${sheet(["#41A5EE", "#2B7CD3", "#185ABD", "#103F91"])}
        ${tile("#2368C4", "#1A4EA6", "M7.3 18.2h2.6l1.6 8.4 1.9-8.4h2.3l1.9 8.4 1.6-8.4h2.6l-2.8 12.1h-2.5l-1.95-8.2-1.95 8.2H10.1z")}`);
    case "excel":
      return wrap(`${sheet(["#33C481", "#21A366", "#107C41", "#185C37"])}
        <rect x="27.5" y="5" width="1.2" height="38" fill="#0B5A2F" opacity=".35"/>
        ${tile("#18884F", "#0F6234", "M8.4 18.2h3l2.6 4.3 2.6-4.3h2.9l-4 6 4.1 6.1h-3l-2.7-4.4-2.7 4.4H8.3l4.1-6.1z")}`);
    case "sheets":
      return wrap(`<path d="M12 3h17.5L39 12.5V42a3 3 0 0 1-3 3H12a3 3 0 0 1-3-3V6a3 3 0 0 1 3-3z" fill="#0F9D58"/>
        <path d="M31 12.5l8 8v-8z" fill="#0B7A44" opacity=".55"/>
        <path d="M29.5 3v7.5a2 2 0 0 0 2 2H39z" fill="#87CEAC"/>
        <rect x="15" y="21" width="18" height="15" rx="1.2" fill="#fff"/>
        <g fill="#0F9D58"><rect x="21.6" y="23.4" width="1.6" height="11"/><rect x="17.2" y="26" width="13.6" height="1.6"/><rect x="17.2" y="30.2" width="13.6" height="1.6"/></g>
        <g fill="#fff"><rect x="15" y="21" width="18" height="2.4"/></g>`);
    case "powerpoint":
      return wrap(`<circle cx="28" cy="24" r="17" fill="#ED6C47"/>
        <path d="M28 24V7a17 17 0 0 1 17 17z" fill="#FF8F6B"/>
        <path d="M11 24a17 17 0 0 0 34 0z" fill="#D35230"/>
        ${tile("#CA4C28", "#B33A17", "M9.6 18.2h5.1a3.9 3.9 0 0 1 0 7.8h-2.6v4.3H9.6zm2.5 2.2v3.4h2.4a1.7 1.7 0 0 0 0-3.4z")}`);
    case "windows":
      return wrap(`<defs><linearGradient id="${id}w" x1="6" y1="6" x2="42" y2="42" gradientUnits="userSpaceOnUse"><stop stop-color="#00BCF2"/><stop offset="1" stop-color="#0063C6"/></linearGradient></defs>
        <g fill="url(#${id}w)"><rect x="6" y="6" width="17" height="17" rx="1.5"/><rect x="25" y="6" width="17" height="17" rx="1.5"/><rect x="6" y="25" width="17" height="17" rx="1.5"/><rect x="25" y="25" width="17" height="17" rx="1.5"/></g>`);
    case "internet":
      return wrap(`<defs><linearGradient id="${id}g" x1="8" y1="6" x2="40" y2="42" gradientUnits="userSpaceOnUse"><stop stop-color="#4FC3F7"/><stop offset="1" stop-color="#1565C0"/></linearGradient></defs>
        <circle cx="24" cy="24" r="19" fill="url(#${id}g)"/>
        <g fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" opacity=".95">
          <ellipse cx="24" cy="24" rx="8" ry="19"/><path d="M5 24h38M8 14.5h32M8 33.5h32M24 5v38"/></g>`);
    default:
      return wrap(`<defs><linearGradient id="${id}s" x1="8" y1="8" x2="40" y2="32" gradientUnits="userSpaceOnUse"><stop stop-color="#5B8CFF"/><stop offset="1" stop-color="#3A44D6"/></linearGradient></defs>
        <rect x="4" y="7" width="40" height="27" rx="3.5" fill="#1F2460"/>
        <rect x="7" y="10" width="34" height="21" rx="1.5" fill="url(#${id}s)"/>
        <path d="M14 17l4 3.5-4 3.5M21 24.5h7" fill="none" stroke="#3DDBB5" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>
        <path d="M19 34h10l1.5 5h-13z" fill="#2A2F70"/><rect x="13" y="39" width="22" height="2.6" rx="1.3" fill="#1F2460"/>`);
  }
}
