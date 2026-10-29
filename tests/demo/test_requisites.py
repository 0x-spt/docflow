import random

from docflow.demo.requisites import DEMO_BANK, make_account, make_inn, make_kpp
from docflow.validation.checksums import valid_account, valid_bik, valid_inn, valid_kpp


def test_inn_valid():
    rng = random.Random(1)
    legal = [make_inn(rng) for _ in range(200)]
    persons = [make_inn(rng, person=True) for _ in range(200)]
    assert all(valid_inn(i) and len(i) == 10 for i in legal)
    assert all(valid_inn(i) and len(i) == 12 for i in persons)


def test_kpp_valid():
    rng = random.Random(2)
    for _ in range(200):
        inn = make_inn(rng)
        kpp = make_kpp(inn, rng)
        assert valid_kpp(kpp)
        assert len(kpp) == 9 and kpp.startswith(inn[:4]) and kpp.endswith("01001")


def test_account_matches_bik():
    rng = random.Random(3)
    assert valid_bik(DEMO_BANK.bik)
    assert len(DEMO_BANK.corr) == 20 and DEMO_BANK.corr.endswith(DEMO_BANK.bik[-3:])
    for _ in range(200):
        acc = make_account(DEMO_BANK.bik, rng)
        assert len(acc) == 20 and acc.startswith("40702810")
        assert valid_account(acc, DEMO_BANK.bik)


def test_deterministic():
    a, b = random.Random(1), random.Random(1)
    seq_a = [make_inn(a), make_kpp("7701234567", a), make_account(DEMO_BANK.bik, a)]
    seq_b = [make_inn(b), make_kpp("7701234567", b), make_account(DEMO_BANK.bik, b)]
    assert seq_a == seq_b


def test_not_known_real():
    rng = random.Random(4)
    assert all(make_inn(rng) != "7707083893" for _ in range(500))
    assert DEMO_BANK.bik != "044525225"
