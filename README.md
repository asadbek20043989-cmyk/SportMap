# My Gym bot

Toshkentdagi sport zallarini topish va ularga yozilish uchun Telegram bot (o'zbek / rus / ingliz tillarida).

- Foydalanuvchi tuman bo'yicha zal tanlaydi, savol beradi (Gemini faqat jadvaldagi ma'lumot asosida javob beradi), ariza yoki bepul sinovga yoziladi, byudjet va maqsad bo'yicha tavsiya oladi, yaqin zallarni topadi.
- Zal egasiga ariza darhol keladi va u tugmalar bilan holatini boshqaradi (CRM).
- Avtomatik eslatmalar: zalni ko'rib jim ketganlarga, yarim qolgan arizalarga, sinov kuniga, "keldingizmi?", egasiga 2 soatlik eslatma.
- Zallar **Google Sheets**'dan o'qiladi, arizalar **Firestore**'da saqlanadi va **Sheets**'dagi `Leads` varag'iga yoziladi.

## Fayllar

| Fayl | Vazifasi |
|---|---|
| `main.py` | Ishga tushirish nuqtasi (`python main.py`) |
| `handlers.py` | Foydalanuvchi oqimlari |
| `admin.py` | Zal egasi/admin buyruqlari va CRM tugmalari |
| `followup.py` | Scheduler (eslatmalar, SLA, Sheets'ga qayta yuborish, tozalash) |
| `db.py`, `storage.py` | Firestore va FSM holatini saqlash |
| `sheets.py` | Zallar jadvalini o'qish, leadlarni yozish |
| `ai.py` | Gemini |
| `gyms_sample.csv` | Zallar jadvali namunasi |

## 1. Mahalliy ishga tushirish

Python 3.11 yoki yangiroq kerak.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # so'ng .env ni to'ldiring (pastga qarang)
python main.py
```

Ishga tushgach, super adminlarga "🚀 My Gym bot ishga tushdi" xabari keladi: unda Firestore va Google Sheets holati ko'rsatiladi. Xabar kelmasa, jurnalni (log) o'qing.

## 2. Sozlash

### 2.1 Telegram
1. [@BotFather](https://t.me/BotFather) → `/newbot` → token oling → `.env` dagi `BOT_TOKEN` ga yozing.
2. O'zingizning Telegram ID'ingizni bilish uchun botga `/id` yuboring (yoki @userinfobot). Uni `SUPER_ADMIN_IDS` ga yozing (bir nechta bo'lsa vergul bilan: `111,222`).

### 2.2 Gemini
[aistudio.google.com](https://aistudio.google.com) → **Get API key** → `GEMINI_API_KEY`. Model `GEMINI_MODEL` (standart: `gemini-2.5-flash`).

### 2.3 Google Cloud, Firestore va Sheets (bitta service account hammasiga yetadi)

1. [Firebase console](https://console.firebase.google.com) da loyiha yarating (yoki mavjud Google Cloud loyihasidan foydalaning).
2. **Build → Firestore Database → Create database**. Rejim: **Native** (Production mode mos). Joylashuv: Toshkentga yaqin region (masalan `europe-west3`). Indekslar qo'lda yaratilmaydi: kod bunga muhtoj emas.
3. **Project settings → Service accounts → Generate new private key**. Yuklangan JSON — bu `service-account.json`. **Uni hech qachon git'ga qo'shmang** (`.gitignore`da bor).
4. [Google Cloud Console](https://console.cloud.google.com) → shu loyiha → **APIs & Services → Library** da **Google Sheets API** va **Google Drive API** ni yoqing.
5. Google Sheets'da yangi jadval oching. `Zallar` nomli varaq yarating (nomi aynan shunday), birinchi qatorga `gyms_sample.csv` dagi ustun nomlarini yozing (yoki CSV'ni import qiling).
6. Jadvalni **service account email'iga ulashing** (JSON ichidagi `client_email`) — **Editor** huquqi bilan. Aks holda bot jadvalni o'qiy olmaydi va leadlarni yoza olmaydi.
7. Jadval havolasidagi ID: `docs.google.com/spreadsheets/d/<SHU YERDA>/edit` → `SHEET_ID`.
8. Kompyuterda: `service-account.json` ni loyiha papkasiga qo'ying (`GOOGLE_CREDENTIALS_FILE`). Railway'da: faylning **butun matnini** `GOOGLE_CREDENTIALS_JSON` o'zgaruvchisiga qo'ying.

`Leads` varag'ini bot o'zi yaratadi. Agar u eski ustunlar bilan mavjud bo'lsa, `Leads_old_<sana>` deb qayta nomlanadi (ma'lumot yo'qolmaydi).

### 2.4 `Zallar` jadvali ustunlari

| Ustun | Izoh |
|---|---|
| `id` | Noyob, qisqa, lotin harflarda (`chil-power`). `:` bo'lmasin, 56 baytdan oshmasin |
| `name` | Zal nomi |
| `district` | Tuman (lotin/kirill, xato yozilgan bo'lsa ham taxminan taniydi) |
| `address`, `lat`, `lon` | Manzil va koordinata (yaqin zallar va xarita uchun) |
| `phone`, `tg_username` | Aloqa |
| `owner_tg_id` | Zal egasining Telegram ID'si: arizalar shu odamga boradi. Bo'sh bo'lsa, super adminlarga |
| `hours`, `prices`, `amenities`, `notes` | Matn; Gemini shu ma'lumotga tayanib javob beradi |
| `price_month` | Oylik narx so'mda (raqam) — tavsiya funksiyasi uchun |
| `photos` | `|` bilan ajratilgan Telegram `file_id` lar yoki to'g'ridan-to'g'ri rasm havolalari (`https://...jpg`; Google Drive "share" havolasi ishlamaydi). `file_id` olish: super admin sifatida botga rasm yuboring, bot `file_id` ni qaytaradi |
| `free_trial` | `ha` / `yo'q` |
| `active` | `ha` / `yo'q` (yo'q bo'lsa botda ko'rinmaydi) |

Tumanlar: Bektemir, Chilonzor, Mirobod, Mirzo Ulug'bek, Olmazor, Sergeli, Shayxontohur, Uchtepa, Yakkasaroy, Yangihayot, Yashnobod, Yunusobod.

> ⚠️ `gyms_sample.csv` dagi zallar va telefonlar **namuna**. Ishga tushirishdan oldin haqiqiy ma'lumot bilan almashtiring.

Jadvalni o'zgartirgach, bot uni 60 soniyada o'zi yangilaydi, yoki darrov `/reload` yuboring (u jadvaldagi xatolarni ham ko'rsatadi).

## 3. Railway'ga deploy

1. Kodni GitHub'ga yuklang (`.env` va `service-account.json` yuklanmaydi).
2. [railway.app](https://railway.app) → **New Project → Deploy from GitHub repo**.
3. **Variables** bo'limiga qo'shing:
   - `BOT_TOKEN`, `GEMINI_API_KEY`, `SHEET_ID`, `SUPER_ADMIN_IDS`
   - `GOOGLE_CREDENTIALS_JSON` — `service-account.json` ning butun matni
   - ixtiyoriy: `NOTIFY_SUPER_ADMINS`, `GEMINI_MODEL` va `.env.example` dagi boshqalar
4. Boshlang'ich buyruq `railway.json` da berilgan (`python main.py`), Python versiyasi `.python-version` dan olinadi. Bot — **worker** (port ochilmaydi), domen kerak emas.
5. **Replicas = 1** qoldiring: polling rejimida ikkita nusxa bir-biriga xalaqit beradi (`TelegramConflictError`).
6. Deploy tugagach, Deploy Logs'da `Bot: @...` qatori va adminlarga kelgan "ishga tushdi" xabarini tekshiring.

Eslatma: yangi versiya deploy qilinganda eski nusxa bir necha soniya ishlab turishi mumkin; bu vaqtda jurnalda `Conflict` xabari chiqishi normal, o'zi tuzaladi.

## 4. Buyruqlar

**Hamma uchun:** `/start`, `/menu`, `/lang` (eski nomi `/language`), `/help`, `/cancel`, `/privacy`, `/stop` (eslatmalarni o'chirish), `/resume` (yoqish), `/id` (Telegram ID'ni ko'rish).

**Zal egalari va adminlar:**

| Buyruq | Vazifasi |
|---|---|
| `/admin` | Buyruqlar ro'yxati |
| `/myleads` (eski nomi `/leads`) | Ochiq arizalar (kartada holat tugmalari bor) |
| `/gyms` | Zallar ro'yxati (egalar faqat o'zinikini ko'radi) |
| `/unanswered` | Gemini javob bera olmagan savollar |

**Faqat super adminlar:** `/stats` (statistika), `/reload` (jadvalni qayta o'qish va xatolarni ko'rsatish).

Ariza holatlari: 🆕 Yangi → 📞 Aloqa qilindi → ✅ Keldi / 💰 Sotib oldi / ❌ Rad etdi / 🚫 Kelmadi.

## 5. Testlar

```bash
pip install -r requirements-dev.txt
pytest
```

Testlar internetsiz ishlaydi: Firestore xotiradagi soxta ob'ekt bilan almashtirilgan (`tests/fakes.py`). Haqiqiy Telegram/Firestore/Sheets bilan sinov uchun `DEPLOY_CHECKLIST.md` ga qarang.

## 6. Muammolar

| Belgi | Sabab va yechim |
|---|---|
| `'BOT_TOKEN' to'ldirilmagan` | `.env` / Railway Variables to'ldirilmagan |
| "Google Sheets: ❌" | `SHEET_ID` noto'g'ri yoki jadval service account email'iga ulashilmagan; Sheets API yoqilmagan |
| "Firestore: ❌" | Firestore bazasi yaratilmagan yoki kalit boshqa loyihaniki. Bot ishlashda davom etadi, lekin FSM xotirada saqlanadi, arizalar faqat Sheets'ga yoziladi |
| Zal botda ko'rinmayapti | `active` = `yo'q`, `id` da `:` bor/juda uzun, yoki tuman tanilmagan: `/reload` aniq sababni aytadi |
| Egaga ariza bormayapti | `owner_tg_id` noto'g'ri yoki ega botni hali ishga tushirmagan (`/start`). Bu holda super adminga yuboriladi |
| `Conflict: terminated by other getUpdates` | Bot ikki joyda ishlayapti (kompyuter va Railway yoki 2 replika) |
| Jurnalda "Detected filter using positional arguments" | Firestore kutubxonasining ogohlantirishi, ishlashga ta'sir qilmaydi |
