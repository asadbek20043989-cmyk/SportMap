# My Gym bot v2 — deployga tayyor (wip5)

## Holat
- Barcha modullar yozilgan; `pytest` — 88 ta test o'tdi (soxta Firestore/Telegram bilan).
- `main.py` va dispatcher yig'ilishi (`test_router_order_in_main`) hamda `main.py` mantig'i (`tests/test_main.py`) tekshirilgan.
- README.md yozilgan. Haqiqiy muhitda qo'lda tekshiruv ro'yxati: `DEPLOY_CHECKLIST.md`.

## wip5 da o'zgargan
- `disable_web_page_preview=True` (eskirgan parametr) olib tashlandi; o'rniga `DefaultBotProperties(link_preview_is_disabled=True)`.
- `tests/test_main.py` qo'shildi.
- `.env.example` ga `SHEET_CACHE_SECONDS`, `AI_LIMIT_PER_HOUR` qo'shildi.

## Qolgan (faqat sizda bajariladi)
- `DEPLOY_CHECKLIST.md` ni haqiqiy Telegram/Firestore/Sheets bilan o'tish.
