"""Вымышленные реквизиты с верными контрольными суммами, которые не могут принадлежать реальным лицам.

ИНН: 1–2 цифры — регион, 3–4 — номер инспекции ФНС; инспекции с номером 00 нет ни в одном регионе, поэтому
ИНН вида «РР00…» никогда не выдавался. БИК: 04 + код территории по ОКАТО (3–4 цифры) + …; территории
с кодом 00 нет, поэтому БИК 0400… не принадлежит ни одному банку. Ничего не сверяется с внешними реестрами.
"""
import random
from typing import NamedTuple

from docflow.validation.checksums import _INN10, _INN12_1, _INN12_2, _inn_digit, valid_account

_REGIONS = ("77", "78", "50", "47", "66")
NO_INSPECTION = "00"  # цифры 3–4 ИНН: несуществующая инспекция
NO_TERRITORY = "0400"  # первые 4 цифры БИК: Россия + несуществующая территория 00


class Bank(NamedTuple):
    name: str
    bik: str
    corr: str


# корр. счёт — стандартная форма 30101810 + 000000000 + 3 последние цифры БИК
_BIK = NO_TERRITORY + "00" + "731"
DEMO_BANK = Bank(name="АО «Банк Северный Демо»", bik=_BIK, corr="30101810" + "000000000" + _BIK[-3:])


def _rand_digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randrange(10)) for _ in range(n))


def make_inn(rng: random.Random, *, region: str | None = None, person: bool = False) -> str:
    """10 цифр для юрлица, 12 для ИП; region — две цифры (по умолчанию случайный)."""
    head = (region or rng.choice(_REGIONS)) + NO_INSPECTION
    if person:
        d = head + _rand_digits(rng, 6)
        d += str(_inn_digit(d, _INN12_1))
        return d + str(_inn_digit(d, _INN12_2))
    d = head + _rand_digits(rng, 5)
    return d + str(_inn_digit(d, _INN10))


def make_kpp(inn: str, rng: random.Random) -> str:
    """9 цифр: код инспекции из начала ИНН + 01001 (rng — для единообразия сигнатуры)."""
    return inn[:4] + "01001"


def make_account(bik: str, rng: random.Random) -> str:
    """Расчётный счёт 40702810…; ключ на индексе 8 подбирается под БИК."""
    tail = _rand_digits(rng, 11)
    for key in range(10):
        acc = f"40702810{key}{tail}"
        if valid_account(acc, bik):
            return acc
    raise AssertionError("ключ счёта не найден")  # недостижимо: вес индекса 8 взаимно прост с 10
