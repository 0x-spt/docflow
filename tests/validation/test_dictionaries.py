from docflow.schemas.model import DocSchema
from docflow.validation.dictionaries import DictEntry, match, resolve_dictionaries
from docflow.validation.engine import decide_status

CLINICS = [DictEntry("Дента-Люкс"), DictEntry("Улыбка+"), DictEntry("Стомат-Сервис", aliases=["Стоматсервис"])]
DOCTORS = [DictEntry("Иванов С.С."), DictEntry("Петрова А.В."), DictEntry("Смирнова О.Л."), DictEntry("Ким Д.О.")]


def schema(*fields):
    return DocSchema.model_validate({"slug": "t_doc", "title": "T", "description": "d", "fields": list(fields)})


def field(field_name, **params):
    return {"name": field_name, "type": "string", "rules": [{"kind": "dictionary", "params": params}]}


CLINIC = field("clinic", name="clinics")
DOCTOR = field("doctor", name="doctors")
STRICT = field("clinic", name="clinics", fuzzy=False)


def test_exact_match_ignores_case_punctuation_and_yo():
    assert match("ДЕНТА-ЛЮКС", CLINICS).value == "Дента-Люкс"
    assert match("Иванов. С.С", DOCTORS).value == "Иванов С.С."
    assert match("стоматсервис", CLINICS).value == "Стомат-Сервис"  # алиас
    assert match("Иванов. С.С", DOCTORS).exact


def test_fuzzy_match_one_letter_off():
    m = match("Дента-Локс", CLINICS)
    assert m.value == "Дента-Люкс" and not m.exact
    assert match("Пеппова А.В", DOCTORS).value == "Петрова А.В."


def test_garbage_does_not_match():
    assert match("Drama - Love", CLINICS) is None
    assert match("Университет", CLINICS) is None
    assert match("", CLINICS) is None


def test_ambiguous_does_not_autocorrect():
    # два близких варианта — выбирать за человека нельзя
    entries = [DictEntry("Иванов С.С."), DictEntry("Иванов С.А.")]
    assert match("Иванов С.", entries) is None


def test_resolve_exact_canonicalizes_silently():
    data = {"clinic": "ДЕНТА-ЛЮКС"}
    issues = resolve_dictionaries(schema(CLINIC), data, {"clinics": CLINICS})
    assert data["clinic"] == "Дента-Люкс"
    assert issues == []


def test_resolve_fuzzy_corrects_and_reports_but_stays_ok():
    data = {"clinic": "Дента-Локс"}
    issues = resolve_dictionaries(schema(CLINIC), data, {"clinics": CLINICS})
    assert data["clinic"] == "Дента-Люкс"
    assert [(i.field, i.code) for i in issues] == [("clinic", "corrected")]
    assert "Дента-Локс" in issues[0].message and "Дента-Люкс" in issues[0].message
    assert decide_status(issues) == "ok"  # исправление — информация, не повод для проверки


def test_resolve_unknown_value_needs_review_and_keeps_original():
    data = {"clinic": "Drama - Love"}
    issues = resolve_dictionaries(schema(CLINIC), data, {"clinics": CLINICS})
    assert data["clinic"] == "Drama - Love"
    assert [(i.field, i.code) for i in issues] == [("clinic", "not_in_dictionary")]
    assert decide_status(issues) == "needs_review"


def test_resolve_strict_dictionary_never_autocorrects():
    data = {"clinic": "Дента-Локс"}
    issues = resolve_dictionaries(schema(STRICT), data, {"clinics": CLINICS})
    assert data["clinic"] == "Дента-Локс"
    assert [i.code for i in issues] == ["not_in_dictionary"]
    assert "Дента-Люкс" in issues[0].message  # подсказка ближайшего


def test_resolve_missing_dictionary_needs_review():
    issues = resolve_dictionaries(schema(DOCTOR), {"doctor": "Ким Д.О."}, {})
    assert [(i.field, i.code) for i in issues] == [("doctor", "dictionary_missing")]
    assert "doctors" in issues[0].message


def test_resolve_skips_empty_values():
    assert resolve_dictionaries(schema(CLINIC), {"clinic": None}, {"clinics": CLINICS}) == []


def test_uncertain_field_matched_exactly_is_still_uncertain():
    # справочник не снимает неуверенность модели: точное совпадение лишь нормализует запись
    data = {"clinic": "дента-люкс", "_uncertain": ["clinic"]}
    assert resolve_dictionaries(schema(CLINIC), data, {"clinics": CLINICS}) == []
    assert data["_uncertain"] == ["clinic"]
