import pytest

from docflow.validation.checksums import (
    valid_account,
    valid_bik,
    valid_inn,
    valid_kpp,
    valid_ogrn,
    valid_snils,
)

_W = (7, 1, 3)


def make_account(prefix8: str, tail11: str, bik: str) -> str:
    """Собирает 20-значный р/с с корректным ключом (9-я цифра счёта) для данного БИК."""
    assert len(prefix8) == 8 and len(tail11) == 11
    for key in "0123456789":
        acc = prefix8 + key + tail11
        digits = bik[-3:] + acc
        if sum(int(d) * _W[i % 3] for i, d in enumerate(digits)) % 10 == 0:
            return acc
    raise AssertionError("ключ не найден")


BIK = "044525225"
ACCOUNT = make_account("40702810", "00000000001", BIK)


@pytest.mark.parametrize("v", ["7707083893", "500100732259", "770-708 3893", "7728168971"])
def test_inn_valid(v):
    assert valid_inn(v)


@pytest.mark.parametrize("v", ["7707083894", "500100732258", "12345", "", "77070838ab", "77070838931"])
def test_inn_invalid(v):
    assert not valid_inn(v)


def test_kpp():
    assert valid_kpp("770701001")
    assert valid_kpp("7707 4D001")
    assert not valid_kpp("77070100")
    assert not valid_kpp("7707010011")
    assert not valid_kpp("77a701001")


def test_ogrn():
    assert valid_ogrn("1027700132195")  # ПАО Сбербанк
    assert valid_ogrn("304500116000157")  # ОГРНИП
    assert not valid_ogrn("1027700132196")
    assert not valid_ogrn("304500116000158")
    assert not valid_ogrn("102770013219")


def test_snils():
    assert valid_snils("112-233-445 95")
    assert valid_snils("11223344595")
    assert not valid_snils("112-233-445 96")
    assert not valid_snils("1122334459")


def test_snils_special_cases():
    # сумма < 100 -> контрольное число равно сумме (145-000-100: 79)
    assert valid_snils("14500010079")
    assert not valid_snils("14500010080")
    # сумма == 100 или 101 -> 00
    assert valid_snils("09400000000")
    assert valid_snils("92000000400")
    assert not valid_snils("09400000001")
    # сумма > 101 -> остаток от деления на 101 (999-999-999: 405 % 101 = 1)
    assert valid_snils("99999999901")
    assert not valid_snils("99999999900")
    # номера <= 001-001-998 не проверяются
    assert valid_snils("00000010012")


def test_bik():
    assert valid_bik("044525225")
    assert valid_bik("04-4525225")
    assert not valid_bik("014525225")
    assert not valid_bik("04452522")
    assert not valid_bik("0445252255")


def test_account():
    assert len(ACCOUNT) == 20
    assert valid_account(ACCOUNT, BIK)
    assert valid_account(" ".join([ACCOUNT[:5], ACCOUNT[5:]]), BIK)
    # меняем одну цифру -> контрольная сумма ломается (веса 7,1,3 взаимно просты с 10)
    for pos in (0, 8, 15, 19):
        bad = ACCOUNT[:pos] + str((int(ACCOUNT[pos]) + 1) % 10) + ACCOUNT[pos + 1 :]
        assert not valid_account(bad, BIK)
    assert not valid_account(ACCOUNT[:-1], BIK)
    assert not valid_account(ACCOUNT, "123")
