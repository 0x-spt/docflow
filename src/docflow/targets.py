"""Системы клиента, куда нужно выгружать данные (поле «Куда выгружать» в заявке на аудит)."""

TARGET_LABELS = {
    "1c": "1С",
    "bitrix24": "Битрикс24",
    "amocrm": "amoCRM",
    "google_sheets": "Google Таблицы",
    "excel": "Excel",
    "own_system": "Своя система",
    "unknown": "Пока не знаю",
}


def targets_text(targets) -> str:
    """«1С, Excel» — по-русски, в порядке выбора; неизвестный код показывается как есть."""
    return ", ".join(TARGET_LABELS.get(t, t) for t in targets or [])
