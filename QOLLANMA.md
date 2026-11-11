# KompSavod sayti – ishga tushirish qo‘llanmasi

## Saytda nimalar bor

- **Bosh sahifa** (`/`): dizayndagi sahifa, kurslar ro‘yxati admin paneldan avtomatik chiqadi.
- **Kirish / ro‘yxatdan o‘tish** (`/kirish`): telefon raqam → SMS kod → (yangi bo‘lsa) ism va hudud. Raqam SMS bilan tasdiqlanadi, soxta raqam bilan kirib bo‘lmaydi.
- **O‘quvchi kabineti** (`/kabinet`): ruxsat kutayotgan, rad etilgan yoki ruxsat berilgan holatni ko‘rsatadi; ruxsat berilgan kurslarning video darslari shu yerda.
- **Admin panel** (`/admin`): barcha o‘quvchilar (ism, telefon, hudud, ro‘yxatdan o‘tgan va oxirgi kirgan vaqti), ruxsat berish / rad etish / bloklash, qaysi kursga ruxsat berilishini tanlash, Excel’ga yuklab olish, kurs va darslarni qo‘shish va tahrirlash.

## Ma’lumotlar xavfsizligi

- Barcha ma’lumotlar Google’ning **Firebase Firestore** bazasida saqlanadi.
- `firestore.rules` qoidalari bo‘yicha o‘quvchi va kurslarni **hech kim o‘chira olmaydi**, faqat holatini o‘zgartirish yoki yashirish mumkin.
- O‘quvchi o‘ziga ruxsat yoki admin huquqini bera olmaydi, boshqalarning ma’lumotini ko‘ra olmaydi.
- Video havolalarni faqat ruxsat berilgan o‘quvchi ko‘radi.
- Qo‘shimcha himoya uchun: Firestore → **Disaster recovery** bo‘limida avtomatik zaxira nusxa (backup) yoqing va vaqti-vaqti bilan admin paneldan “Excel’ga yuklab olish” qiling.

## 1. Firebase loyihasini ochish

1. https://console.firebase.google.com ga Google akkauntingiz bilan kiring.
2. **Create a project** → nomi: `kompsavod` → Google Analytics shart emas → **Create**.
3. Bosh sahifadagi **</>** (Web) belgisini bosing → nomi: `kompsavod-web` → **Register app**.
4. Chiqqan `firebaseConfig = { ... }` qismini nusxa olib, menga yuboring (yoki `assets/firebase-config.js` faylga qo‘ying). Bu kalitlar maxfiy emas.

## 2. SMS orqali kirishni yoqish

1. **Build → Authentication → Get started → Sign-in method → Phone → Enable → Save**.
2. **Authentication → Settings → Authorized domains** ga qo‘shing: `kompsavod.uz`, `www.kompsavod.uz` va Vercel bergan manzil (masalan `kompsavod.vercel.app`).
3. **Authentication → Settings → SMS region policy**: O‘zbekistonga ruxsat berilganini tekshiring (faqat O‘zbekistonni “Allow” qilsangiz, boshqa davlatlardan soxta so‘rovlar kamayadi).
4. **Haqiqiy SMS yuborish uchun loyihani Blaze (to‘lovli) tarifga o‘tkazish kerak**: chap pastdagi **Upgrade** → bank kartani ulang. Har bir SMS uchun oz miqdorda pul yechiladi. Byudjetdan oshib ketmaslik uchun Google Cloud’da **Budget alert** (masalan oyiga 10$) qo‘yib qo‘ying.
5. Bepul sinash uchun: **Sign-in method → Phone → Phone numbers for testing** ga sinov raqami va kod qo‘shing (masalan `+998 90 000 00 01` → `123456`). Bu raqamga SMS ketmaydi, pul ham olinmaydi.

## 3. Ma’lumotlar bazasini ochish

1. **Build → Firestore Database → Create database**.
2. Joylashuv: `eur3 (europe-west)` yoki yaqinroq Yevropa hududi → **Production mode** → **Create**.
3. **Rules** bo‘limiga `firestore.rules` faylining to‘liq matnini qo‘ying → **Publish**.

## 4. O‘zingizni admin qilish

1. Sayt ishga tushgach, `/kirish` orqali o‘z raqamingiz bilan ro‘yxatdan o‘ting.
2. Firebase → **Firestore Database → Data → users** → o‘zingizning yozuvingizni oching.
3. `role` maydonini `student` dan **`admin`** ga o‘zgartiring → **Update**.
4. Saytga qayta kiring: endi `/admin` paneli ochiladi.

## 5. Kurs va dars qo‘shish

1. Videoni YouTube’ga yuklang, ko‘rinishini **Unlisted** (havola orqali) qiling.
2. Admin panel → **Kurslar va darslar** → **Yangi kurs qo‘shish** → nomi, tavsifi, narxi → “Saytda ko‘rsatilsin” belgisi → **Kursni qo‘shish**.
3. Pastdagi **Yangi dars qo‘shish** formasiga dars nomi va YouTube havolasini qo‘ying.
4. Kurs darhol bosh sahifada paydo bo‘ladi.

## 6. O‘quvchiga ruxsat berish

1. O‘quvchi ro‘yxatdan o‘tadi → admin panelda “Kutilmoqda” holatida chiqadi.
2. To‘lovni tekshirgach, **Ruxsat berish** → kurslarni belgilang → izoh (masalan, “Click orqali to‘ladi”) → **Ruxsat berish**.
3. O‘quvchi kabinetida darslar ochiladi.

## Eslatma

YouTube “Unlisted” videolar havolasi bor odamga ochiq bo‘ladi. Ruxsat berilgan o‘quvchi havolani boshqalarga tarqatishi mumkin. Kuchliroq himoya kerak bo‘lsa, keyinchalik Vimeo (domen bo‘yicha cheklash bilan) ishlatish mumkin.
