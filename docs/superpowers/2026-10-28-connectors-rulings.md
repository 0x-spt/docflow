# Решения и отложенные замечания: коннекторы (28.10.2026)

## Решения (Ruling)
- Ruling: without routing rules nothing is exported (client opts in explicitly) — avoids surprise writes into 1С/CRM — cost if wrong: client must create one rule
- Ruling: 1С documents created unposted (непроведённые) — accountant reviews — cost if wrong: extra click for the accountant
- Ruling: live accounts unavailable; connectors verified with respx against documented API contracts — cost if wrong: field-name mismatches discovered at first live connection
- Ruling: elevate stale-notification check, stale-routing skip, lease enforcement, non-retryable permanent SSRF rejection, (tenant_id, created_at) index into fix round 1 — anti-goals (export without operator corrections, invisible/duplicate deliveries) — cost if wrong: small extra work
- Ruling: fix round anyway with alphanumeric end-of-name token + endswith, Bitrix requisite repair, explicit = filters, 3xx/408/METHOD_NOT_FOUND mapping, amo typed values — Review Focus 1 (no duplicates) — cost if wrong: small extra work
- Task 4: parked — amo search async: retry within seconds of lost response may duplicate — Ruling: residual, mitigated by 10 s first retry; requires live check
- Ruling: ЦенаВключаетНДС derived from document (false when VAT is added on top) instead of always true — otherwise УПД totals short by VAT — cost if wrong: wrong VAT mode in 1С for some invoices
- Task 5: parked — СтавкаНДС enum vs catalog, СуммаВключаетНДС, substringof on Комментарий need live БП 3.0 confirmation — Ruling: documented as live-check items
- Ruling: include 1С form 'Дополнительно' grouping + Russian option labels and connector tile descriptions in the fix wave — user goal 'просто подключить'; proposed to user earlier without answer — cost if wrong: cosmetic rework
- Final: parked — tz='Europe' (directory-like) raises PermissionError/IsADirectoryError → 500 instead of 422 — Ruling: deferred one-line fix (catch OSError) — cost if wrong: 500 on a malformed request only
- Final: parked — Sheets header check makes field/label changes on an existing connection fail permanently until a new sheet — Ruling: accepted, documented; friendlier message later
- Final: parked — Google write check writes the same title (version-history entry, tiny rename race) — Ruling: accepted; viewer 403 to be confirmed live; Connector.test docstring 'без записи' outdated

## Отложенные замечания
- Task 1: minor (deferred): select/number config not type-checked; no length caps on secrets/mapping; export_to/notify reuse MAX_RULES name; Condition.value accepts bools
- Task 1: minor (deferred): pruning adds empty export_to/notify keys; stale stored secret key names could fail PATCH if connector drops a field; /test with key mismatch → 422 (by brief)
- Task 2: minor (deferred): export rows created in a separate transaction after status commit (same as webhooks); webhook payload exports may be [] on first delivery; silent skips at enqueue
- Task 2: minor (deferred): _check_urls/build_export_doc outside 240 s deadline; CancelledError after claim consumes attempt (bounded by cap)
- Task 3: minor (deferred): read-then-append race only if two jobs per Export; column A full read O(n); test() passes on read-only share; Excel period in UTC days; ~3 in-memory copies for 5000 docs
- Task 3: minor (deferred): _PARSED narrower than Sheets parsing (text dates, ₽/€); leading ' consumed; new sheet 26 columns limit
- Task 3: minor (deferred): format_dates one extra batchUpdate per document
- Task 4: minor (deferred): concurrent duplicate company creation across documents (no lock in CRMs)
- Task 4: minor (deferred): amo 304 now treated as redirect error (unreachable); double space before tag on cut; second requisite if found company has another ИНН
- Task 5: minor (deferred): 1С HTTP 500 for permanent business errors retried ~5 times (bounded); services into Товары unless field_names
- Task 5: minor (deferred): amount/qty division outside guard (tiny qty Overflow → retryable); window miss if issued edited after partial export (data frozen after approval — unlikely); urlsplit ValueError unwrapped; no-lines total and _vat_on_top not capped
- Task 6: minor (deferred): Telegram retry_after ignored; chat_id not format-checked; DNS rebinding window before smtplib resolve
- Task 6: minor (deferred): security=none+login refused only at connect (saves OK); _connect reads get_settings directly; worker httpx level untested; notify_new party fallback supplier→buyer changed
- Task 7: minor (deferred): -webkit-text-security not in Firefox; has_secrets global per destination; FREE_TARGET hardcoded; two fields to same 1С role overwrite silently
- Task 8: minor (deferred): free-text condition value without type hint; condition rows key={ci}; conditions on flat fields only
- Task 8: minor (deferred): fake SMTP server not closed if destination creation throws; e2e email destination not deleted; proxyFile shows array 422 details as «Ошибка 422»; Linux Docker needs extra_hosts host-gateway
- Task 9: minor (deferred): 1С OData usage right not mentioned; REST-service processing path vague; amoCRM «частная интеграция» wording; Telegram group privacy mode for getUpdates; Sheets column order not mentioned; app passwords need 2FA

## Требует проверки на живых системах
- Task 4: parked — amo search async: retry within seconds of lost response may duplicate — Ruling: residual, mitigated by 10 s first retry; requires live check
- Task 5: parked — СтавкаНДС enum vs catalog, СуммаВключаетНДС, substringof on Комментарий need live БП 3.0 confirmation — Ruling: documented as live-check items
- Final: parked — tz='Europe' (directory-like) raises PermissionError/IsADirectoryError → 500 instead of 422 — Ruling: deferred one-line fix (catch OSError) — cost if wrong: 500 on a malformed request only
- Final: parked — Sheets header check makes field/label changes on an existing connection fail permanently until a new sheet — Ruling: accepted, documented; friendlier message later
- Final: parked — Google write check writes the same title (version-history entry, tiny rename race) — Ruling: accepted; viewer 403 to be confirmed live; Connector.test docstring 'без записи' outdated
- Final review: clean (residuals parked)
