# KompSavod – bitta faylli server

Butun sayt bitta `server.py` faylida: backend (Python, Flask) va frontend (React).
Ma'lumotlar `data/kompsavod.db` (SQLite) bazasida doimiy saqlanadi, har 6 soatda `data/backups/` papkasiga avtomatik zaxira nusxa olinadi.

## VS Code'da ochish (eng oson yo'l)

1. Arxivni oching (Extract) va VS Code'da **File → Open Folder** orqali `kompsavod` papkasini tanlang.
2. VS Code taklif qilgan **Python** kengaytmasini o'rnating.
3. `.vscode/launch.json` faylida `ADMIN_PHONE` qiymatiga o'z raqamingizni yozing.
4. Terminalda (Ctrl + `) bir marta yozing: `pip install -r requirements.txt`
5. **F5** tugmasini bosing – server ishga tushadi. Brauzerda http://localhost:8000 ni oching.

Yoki Windows'da `ishga_tushirish.bat` faylini ikki marta bosing (unda ham raqamingizni yozib qo'ying).

## 1. Kompyuterda ishga tushirish (terminal orqali)

1. Python 3.10 yoki yangisini o'rnating: https://www.python.org/downloads/ (o'rnatishda "Add Python to PATH" belgisini qo'ying).
2. Papka ichida terminal (cmd) oching va kutubxonalarni o'rnating:
   ```
   pip install -r requirements.txt
   ```
3. O'zingizni admin qilib, serverni ishga tushiring.

   **Windows (cmd):**
   ```
   set ADMIN_PHONE=+998901234567
   python server.py
   ```
   **Mac / Linux:**
   ```
   ADMIN_PHONE=+998901234567 python3 server.py
   ```
   `+998901234567` o'rniga o'z raqamingizni yozing.
4. Brauzerda oching: http://localhost:8000
5. "Kirish" → o'z raqamingiz → kod **terminal oynasida** chiqadi (sinov rejimi) → ro'yxatdan o'ting. Siz avtomatik admin bo'lasiz.

## 2. Haqiqiy SMS (Eskiz.uz)

1. https://eskiz.uz da ro'yxatdan o'ting, shartnoma tuzing va balansni to'ldiring.
2. SMS matni shablonini Eskiz'da tasdiqlating (masalan: `KompSavod: tasdiqlash kodi {code}. Kodni hech kimga bermang.`). Tasdiqlangan matn serverdagi `SMS_TEXT` bilan aynan bir xil bo'lishi kerak.
3. Serverni Eskiz ma'lumotlari bilan ishga tushiring:
   ```
   set ADMIN_PHONE=+998901234567
   set ESKIZ_EMAIL=sizning@email.uz
   set ESKIZ_PASSWORD=eskiz_kaliti
   python server.py
   ```
   Endi kodlar haqiqiy SMS bo'lib boradi.

## 3. Internetga chiqarish (kompsavod.uz)

Kompyuteringizda ishlagan sayt faqat sizga ko'rinadi. Hammaga ochiq bo'lishi uchun serverni doim yoniq turadigan **VPS** serverga qo'yish kerak (O'zbekistondagi hosting kompaniyalarining VPS xizmati yoki DigitalOcean, Hetzner va h.k.). Muhim: `data` papkasi saqlanib qoladigan joy bo'lishi shart, aks holda ma'lumotlar yo'qoladi. VPS'ga https (SSL) bilan qo'yilganda `HTTPS=1` va `TRUST_PROXY=1` sozlamalarini qo'shing.

## Ma'lumotlar xavfsizligi

- Saytda o'quvchi yoki kursni o'chirish funksiyasi umuman yo'q: faqat bloklash yoki yashirish mumkin.
- Har bir kirish (vaqt, IP manzil, qurilma) `logins` jadvalida, adminning har bir amali `audit_log` jadvalida saqlanadi.
- `data` papkasini vaqti-vaqti bilan boshqa joyga (fleshka, Google Drive) nusxalab qo'ying.
- `data/.secret_key` faylini hech kimga bermang va o'chirmang.
