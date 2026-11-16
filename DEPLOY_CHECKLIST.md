# Deploydan oldingi tekshiruv (haqiqiy muhitda)

Testlar faqat mantiqni tekshiradi. Quyidagilar haqiqiy Telegram, Firestore va Sheets bilan **bir marta qo'lda** o'tkazilishi kerak. Avval alohida test bot (BotFather'da ikkinchi bot) va nusxa jadval bilan sinang.

## 0. O'rnatish
- [ ] `pip install -r requirements-dev.txt && pytest` — hammasi yashil (88 ta test).
- [ ] `python main.py` xatosiz ishga tushadi, jurnalda `Bot: @...`

## 1. Ishga tushish
- [ ] Super adminga "🚀 ... ishga tushdi" xabari keldi, `Firestore: ✅` va `Google Sheets: ✅ N ta faol zal`
- [ ] Telegram menyusida buyruqlar ko'rinadi; admin hisobida `/stats`, `/reload` ham bor; oddiy hisobda yo'q

## 2. Foydalanuvchi oqimi (oddiy hisobdan)
- [ ] `/start` → til tanlash → tuman → zal ro'yxati → zal kartasi (rasm bilan va rasmsiz)
- [ ] ❓ Savol: jadvalda bor narsa (to'g'ri javob), yo'q narsa (javob topilmadi → `/unanswered` da ko'rinadi), begona mavzu
- [ ] 🎁 Bepul sinov: ism → telefon (tugma va qo'lda) → maqsad → kun → vaqt → tasdiq
- [ ] Ariza: Firestore `leads` da hujjat bor; Sheets `Leads` varag'ida qator bor; zal egasiga xabar keldi
- [ ] Bir xil arizani qayta yuborish: takroriy ariza rad etiladi (24 soat); kuniga 5 tadan keyin limit
- [ ] 🎯 Tavsiya va 📍 Yaqin zallar (lokatsiya yuborish)
- [ ] `/cancel` oqim o'rtasida; oqim o'rtasida bot qayta ishga tushirilsa, davom ettirish mumkin (FSM Firestore'da)

## 3. Zal egasi / admin
- [ ] Ariza kartasidagi tugmalar holatni o'zgartiradi, karta tahrirlanadi, begona odam bosganda rad etiladi
- [ ] `/myleads`, `/gyms`, `/unanswered` — egada faqat o'z zali, adminda hammasi
- [ ] `/stats`
- [ ] `/reload`: jadvalga ataylab xato qo'ying (noto'g'ri tuman, `id` da `:`), xabarda ko'rinishini tekshiring; jadvalni buzib (SHEET_ID xato) eski ro'yxat saqlanishini tekshiring

## 4. Scheduler (vaqtni qisqartirib sinang)
Sinov uchun vaqtincha: `SCHEDULER_INTERVAL_SECONDS=30`, `FOLLOWUP_FIRST_HOURS=0`, `FOLLOWUP_ABANDON_HOURS=0`, `QUIET_START_HOUR=24`, `QUIET_END_HOUR=0` (tinch soatlarni o'chiradi), `LEAD_SLA_HOURS` ni kichik qiling. Sinovdan keyin qiymatlarni qaytaring.
- [ ] Zalni ko'rib jim ketish → follow-up xabari, `/stop` dan keyin kelmaydi
- [ ] Ariza yarim qoldirilsa → "davom ettiring" xabari
- [ ] Sinovga yozilib, `remind_at`/`confirm_at` ni Firestore'da qo'lda hozirga qo'ying → eslatma va "keldingizmi?" keladi, ✅/❌ tugmalari ishlaydi
- [ ] Ariza "Yangi"da qolsa → SLA eslatmasi egaga keladi
- [ ] Sheets'ni vaqtincha ulashishdan olib tashlab ariza yuboring → `sheet_ok=false` bo'ladi; ruxsatni qaytarsangiz ~5 daqiqada qator Sheets'ga tushadi, takrorlanmaydi
- [ ] Botni bloklab, qayta yoqish: `blocked` belgisi qo'yiladi/olinadi

## 5. Railway
- [ ] Variables to'liq, `GOOGLE_CREDENTIALS_JSON` butun matn
- [ ] Replicas = 1
- [ ] Redeploydan keyin bot javob beradi, jurnalda xato yo'q
- [ ] Namuna zallar (`gyms_sample.csv`) haqiqiy zallar bilan almashtirilgan
- [ ] Tinch soat (`QUIET_*`) va boshqa sinov qiymatlari standartga qaytarilgan
