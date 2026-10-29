"""Вымышленные реквизиты с верными контрольными суммами (не принадлежат реальным лицам)."""
import random
from typing import NamedTuple

from docflow.validation.checksums import _INN10, _INN12_1, _INN12_2, _inn_digit, valid_account

_REGIONS = ("77", "78", "50", "47", "66")


class Bank(NamedTuple):
    name: str
    bik: str
    corr: str


# БИК вымышленный; корр. счёт — стандартная форма 30101810 + 000000000 + 3 последние цифры БИК
DEMO_BANK = Bank(name="АО «Банк Северный Демо»", bik="044599731", corr="30101810" + "000000000" + "731")


def _rand_digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randrange(10)) for _ in range(n))


def make_inn(rng: random.Random, *, person: bool = False) -> str:
    """10 цифр для юрлица, 12 для ИП."""
    if person:
        d = rng.choice(_REGIONS) + _rand_digits(rng, 8)
        d += str(_inn_digit(d, _INN12_1))
        return d + str(_inn_digit(d, _INN12_2))
    d = rng.choice(_REGIONS) + _rand_digits(rng, 7)
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
