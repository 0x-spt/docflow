# Решения и отложенные замечания: позиционирование и цифровой PDF (28.10.2026)

## Решения (Ruling)
- Ruling: integrations presented as implementation service ('подключаем'), no 'скоро' — user directive 'как будто продукт готов'; anti-goal on invented clients/numbers/prices kept — cost if wrong: client expects a connector not yet built; must be delivered during implementation
- Ruling: keep present-tense integration copy (section banner 'подключение — часть внедрения' qualifies it) — user directive 'сайт как будто продукт готов'; owner asked to proofread the specific promises — cost if wrong: clients expect amoCRM funnel stage / approvals / Telegram notifications that must be built during implementation
- Ruling: remove ® from logo/hero until the owner confirms registration — ГК РФ 1485 liability — cost if wrong: owner re-adds one character
- Ruling: keep 'проверяет каждое поле' copy — it is the owner's own product description — cost if wrong: copy slightly overstates (only fields with rules are checked)
- Final: parked — seed-schemas replaces whole builtin body (pushed total_consistent to old invoice_ru v1 rows) — Ruling: accepted now (that rule is a bug fix); convention going forward: semantic changes to builtin schemas bump version — cost if wrong: re-review of old invoices may surface a new issue
- Final: parked — digital.pdf 422 safety net catches all exceptions (missing font reported as data problem) — Ruling: deferred, logged with logger.exception; narrow to fpdf errors next release — cost if wrong: misconfiguration shown as 422
- Final: parked — clipped table cells end with '…' without an explicit note — Ruling: acceptable for MVP

## Отложенные замечания
- Task 1: minor (deferred): '№ … от …' line and table title use cell (no wrap) — long number clipped
- Task 1: minor (deferred): format_value NaN/inf raises; -0.001 → '-0,00'
- Task 1: minor (deferred): missing font → bare 500; created_at date in UTC; Dockerfile apt line style; targets:null → 422
- Task 2: minor (deferred): FAQ handwriting 'Да' could hedge; PhotoSheet skew w%9; checkedTargets Set{''}
- Task 2: minor (deferred): forced-colors tick hard-coded #fff on Highlight — low contrast in themes with light Highlight
- Task 3: minor (deferred): no e2e for failed-document 409/no-button path
- Final review minors (deferred): 5000-row PDF ~11 s, no cap/cache; missing glyphs dropped silently; ~80 string columns FPDFException
