# v1 (deploy qilingan) → v2 (wip5) ga o'tish

## Nima o'zgaradi
| | v1 | v2 |
|---|---|---|
| Fayllar | `main.py` ichida hammasi | `main.py` + `handlers.py`, `admin.py`, `followup.py`, `common.py`, `db.py`, `storage.py`, `recommend.py`, `utils.py`... — **butun papkani almashtiring** |
| Buyruqlar | `/language`, `/leads`, `/mystats` | `/lang` (`/language` ham ishlaydi), `/myleads` (`/leads` ham ishlaydi), **`/mystats` olib tashlangan** (o'rniga `/myleads`, `/stats` faqat super admin) |
| Yangi | | `/help /privacy /stop /resume /admin /unanswered`, ariza CRM tugmalari, avtomatik eslatmalar, tavsiya, yaqin zallar |
| Sheets `Leads` | 11 ustun | 14 ustun (`phone_verified, visit, lead_id` qo'shildi) |
| Firestore | `users`, `leads`, `gym_stats` | + `fsm`, `views`, `abandon`, `unanswered`, `stats`. `gym_stats` endi o'qilmaydi |
| Env | | Yangi o'zgaruvchilar **ixtiyoriy** (standart qiymatlari bor). Majburiylari o'zgarmagan |

## Ma'lumot yo'qolmaydi, lekin bilib qo'ying
1. **`Leads` varag'i**: sarlavhalar boshqacha bo'lgani uchun bot birinchi ariza yozishda eski varaqni avtomatik `Leads_old_<sana>_<vaqt>` deb qayta nomlaydi va yangi `Leads` yaratadi. Agar boshqa jadval/hisobot/Looker `Leads` varag'iga bog'langan bo'lsa, uni yangi varaqqa qaytadan ulang. Xohlasangiz, ishga tushirishdan oldin o'zingiz 3 ta ustun qo'shib qo'ysangiz ham bo'ladi, lekin tartib `date, gym_id, gym, name, phone, phone_verified, goal, type, visit, lang, source, tg_id, tg_username, lead_id` bo'lishi shart. Eng oson yo'l: avtomatik qayta nomlashga yo'l qo'yish.
2. **Eski arizalar Firestore'da** qoladi. Ular `/myleads` (admin) da ko'rinadi va tugmalar ishlaydi (kodda moslashtirilgan), lekin `/stats` dagi holat (Yangi/Keldi...) hisobiga kirmaydi va eslatmalar yuborilmaydi.
3. **Eski statistika** (`gym_stats`: ko'rishlar, savollar, leadlar) yangi `/stats` da ko'rinmaydi; hisoblash noldan boshlanadi. Ma'lumot Firestore'da saqlanib qoladi.
4. **Foydalanuvchilar** (`users`) va tanlagan tili saqlanadi, qayta til tanlash shart emas.
5. **Zallar jadvali** (`Zallar`) o'zgarishsiz ishlaydi. Yangi ixtiyoriy ustun: `price_month` (oylik narx, so'mda). Bo'sh bo'lsa, `prices` matnidagi "1 oy: 350 000" dan olinadi. Narxi aniqlanmagan zal "tavsiya" natijalarida chiqmaydi, `/reload` buni ko'rsatadi.
6. **Zal `id`** ichida `:` bo'lmasligi va 56 baytdan oshmasligi shart (v1 dagi `g001` kabilar mos).
7. Eski `file_id` rasmlar ishlayveradi.

## Deploy tartibi (Railway)
1. **Zaxira**: Sheets'da *File → Make a copy*; hozirgi ishlayotgan kodni GitHub'da tag/branch qilib qo'ying (`v1`) — orqaga qaytish uchun.
2. Variables'da hech narsani o'chirmang. Qo'shish shart emas. (Ixtiyoriy ro'yxat: `.env.example`.)
3. Yangi papkani repoga yuklang: **eski fayllarni to'liq almashtiring**, `railway.json`, `.python-version`, `requirements.txt` ham. `.env` va `service-account.json` repoga tushmasin.
4. **Replicas = 1** ekanini tekshiring. Redeploy paytida bir necha soniya `Conflict: terminated by other getUpdates` xabari chiqishi mumkin, o'zi tuzaladi.
5. Deploy loglarida `Bot: @...` ni, super adminga "🚀 ishga tushdi, Firestore ✅, Google Sheets ✅" xabarini kuting. ❌ chiqsa, README oxiridagi "Muammolar" jadvaliga qarang.
6. Darhol `DEPLOY_CHECKLIST.md` dagi 2–3 bo'limlarni (ariza oqimi, CRM tugmalari, `/reload`) o'zingiz o'tkazing. Eng yaxshisi: avval alohida test bot (BotFather'da ikkinchi bot, boshqa `BOT_TOKEN`) va nusxa jadval bilan.
7. **Eslatmalar** (follow-up) yangi botda avtomatik yoqiladi. Ular faqat deploydan keyingi harakatlarga ishlaydi (eski foydalanuvchilarga yuborilmaydi). Kerak bo'lmasa `FOLLOWUP_ENABLED=0`. Tinch soatlar: 21:00–09:00 Toshkent vaqti.
8. Zal egalariga yangi tugmalar (✅ Keldi, 💰 Sotib oldi...) haqida ayting va `owner_tg_id` to'g'riligini tekshiring.

## Orqaga qaytish
Railway'da oldingi deployment'ni **Redeploy** qiling (yoki `v1` tagni). Firestore va Sheets'ga qo'shilgan yangi ma'lumotlar eski kodga xalaqit bermaydi. `Leads` varag'ini esa qo'lda: yangisini `Leads_new`, `Leads_old_...` ni `Leads` deb qayta nomlang (aks holda v1 yangi 14 ustunli varaqqa 11 ustunli qator yozadi).
