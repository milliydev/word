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
export function videoEmbed(url) {
  const s = String(url || "").trim();
  let m = s.match(/^https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|live\/)|youtu\.be\/)([\w-]{11})/);
  if (m) return `https://www.youtube-nocookie.com/embed/${m[1]}?rel=0&modestbranding=1`;
  m = s.match(/^https?:\/\/(?:www\.|player\.)?vimeo\.com\/(?:video\/)?(\d+)(?:\/([\w]+))?/);
  if (m) return `https://player.vimeo.com/video/${m[1]}${m[2] ? `?h=${m[2]}` : ""}`;
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
