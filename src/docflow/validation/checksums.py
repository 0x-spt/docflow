"""Контрольные суммы реквизитов по официальным алгоритмам (ФНС, ПФР, ЦБ РФ).

Разделители (пробелы, дефисы) во входной строке допускаются и отбрасываются.
"""
import re

_SEP = re.compile(r"[\s\-]")
_INN10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_1 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12_2 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_ACCOUNT_W = (7, 1, 3)


def _clean(s: str) -> str:
    return _SEP.sub("", s)


def _digits(s: str, *lengths: int) -> str | None:
    s = _clean(s)
    return s if s.isascii() and s.isdigit() and len(s) in lengths else None


def _inn_digit(digits: str, weights: tuple[int, ...]) -> int:
    return sum(int(d) * w for d, w in zip(digits[: len(weights)], weights, strict=True)) % 11 % 10


def valid_inn(s: str) -> bool:
    d = _digits(s, 10, 12)
    if d is None:
        return False
    if len(d) == 10:
        return _inn_digit(d, _INN10) == int(d[9])
    return _inn_digit(d, _INN12_1) == int(d[10]) and _inn_digit(d, _INN12_2) == int(d[11])


def valid_kpp(s: str) -> bool:
    # контрольной суммы у КПП нет, только формат (в 5-6 позициях допустимы латинские буквы)
    return re.fullmatch(r"\d{4}[\dA-Z]{2}\d{3}", _clean(s)) is not None


def valid_ogrn(s: str) -> bool:
    d = _digits(s, 13, 15)
    if d is None:
        return False
    mod = 11 if len(d) == 13 else 13
    return int(d[:-1]) % mod % 10 == int(d[-1])


def valid_snils(s: str) -> bool:
    d = _digits(s, 11)
    if d is None:
        return False
    if int(d[:9]) <= 1_001_998:  # номера до 001-001-998 включительно не проверяются
        return True
    total = sum(int(c) * (9 - i) for i, c in enumerate(d[:9]))
    if total > 101:
        total %= 101
    if total in (100, 101):
        total = 0
    return total == int(d[9:])


def valid_bik(s: str) -> bool:
    d = _digits(s, 9)
    return d is not None and d.startswith("04")


def valid_account(account: str, bik: str) -> bool:
    """Ключ р/с: веса 7,1,3 по (последние 3 цифры БИК + 20 цифр счёта), сумма произведений кратна 10."""
    acc, b = _digits(account, 20), _digits(bik, 9)
    if acc is None or b is None:
        return False
    return sum(int(d) * _ACCOUNT_W[i % 3] for i, d in enumerate(b[-3:] + acc)) % 10 == 0
