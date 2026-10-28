import pytest

CLINICS = {
    "title": "Клиники",
    "entries": [{"value": "Дента-Люкс"}, {"value": "Стомат-Сервис", "aliases": ["Стоматсервис"]}],
}


async def put(api, headers, name="clinics", body=CLINICS):
    return await api.client.put(f"/v1/dictionaries/{name}", headers=headers, json=body)


async def test_put_creates_then_replaces(api, auth):
    r = await put(api, auth)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "clinics" and len(r.json()["entries"]) == 2
    got = (await api.client.get("/v1/dictionaries/clinics", headers=auth)).json()
    assert got["title"] == "Клиники"
    assert got["entries"][1] == {"value": "Стомат-Сервис", "aliases": ["Стоматсервис"]}
    assert got["entries"][0] == {"value": "Дента-Люкс", "aliases": []}

    r = await put(api, auth, body={"title": "Клиники-партнёры", "entries": [{"value": "Улыбка+"}]})
    assert r.status_code == 200
    got = (await api.client.get("/v1/dictionaries/clinics", headers=auth)).json()
    assert got["title"] == "Клиники-партнёры"
    assert [e["value"] for e in got["entries"]] == ["Улыбка+"]


async def test_list_shows_sizes(api, auth):
    await put(api, auth)
    await put(api, auth, name="doctors", body={"title": "Врачи", "entries": [{"value": "Ким Д.О."}]})
    listed = (await api.client.get("/v1/dictionaries", headers=auth)).json()
    assert [(d["name"], d["size"]) for d in listed] == [("clinics", 2), ("doctors", 1)]
    assert "entries" not in listed[0]


async def test_delete(api, auth):
    await put(api, auth)
    assert (await api.client.delete("/v1/dictionaries/clinics", headers=auth)).status_code == 204
    assert (await api.client.get("/v1/dictionaries/clinics", headers=auth)).status_code == 404
    assert (await api.client.delete("/v1/dictionaries/clinics", headers=auth)).status_code == 404


async def test_isolated_between_tenants(api, auth, make_tenant):
    other = await make_tenant("Другой")
    await put(api, auth)
    assert (await api.client.get("/v1/dictionaries/clinics", headers=other)).status_code == 404
    assert (await api.client.get("/v1/dictionaries", headers=other)).json() == []
    assert (await api.client.delete("/v1/dictionaries/clinics", headers=other)).status_code == 404
    # у другого клиента может быть свой справочник с тем же именем
    assert (await put(api, other, body={"title": "Свои", "entries": [{"value": "X"}]})).status_code == 200
    assert len((await api.client.get("/v1/dictionaries/clinics", headers=auth)).json()["entries"]) == 2


@pytest.mark.parametrize(
    "name, body",
    [
        ("Clinics", CLINICS),  # имя — как slug схемы
        ("a", CLINICS),
        ("clinics", {"title": "T", "entries": [{"value": ""}]}),
        ("clinics", {"title": "T", "entries": [{"value": "  "}]}),
        ("clinics", {"title": "T", "entries": [{"value": "A"}, {"value": "a"}]}),  # дубль после нормализации
        ("clinics", {"title": "T", "entries": [{"value": "A"}, {"value": "B", "aliases": ["a"]}]}),
        ("clinics", {"title": "T", "entries": [{"value": "x" * 201}]}),
        ("clinics", {"title": "T", "entries": [{"value": f"v{i}"} for i in range(5001)]}),
    ],
)
async def test_invalid_422(api, auth, name, body):
    r = await put(api, auth, name=name, body=body)
    assert r.status_code == 422, r.text


async def test_requires_auth(api):
    assert (await api.client.get("/v1/dictionaries")).status_code == 401


async def _doc_with_issues(api, headers, issues):
    from test_documents import add_doc

    return await add_doc(api, headers, status="needs_review", issues=issues)


def _issue(code, message):
    return {"field": "clinic", "code": code, "message": message}


def _as_dicts(issues):
    return [{"field": i.field, "code": i.code, "message": i.message} for i in issues]


async def test_suggestions_from_recent_documents(api, auth):
    from docflow.schemas.model import DocSchema
    from docflow.validation.dictionaries import DictEntry, resolve_dictionaries

    await put(api, auth)
    # формат сообщений задаёт resolve_dictionaries: настоящие замечания из него и кладём в документы
    rule = {"kind": "dictionary", "params": {"name": "clinics"}}
    field = {"name": "clinic", "type": "string", "rules": [rule]}
    schema = DocSchema.model_validate(
        {"slug": "ss", "version": 1, "title": "s", "description": "s", "fields": [field]}
    )
    entries = [DictEntry("Дента-Люкс"), DictEntry("Стомат-Сервис")]

    def run(value):
        return resolve_dictionaries(schema, {"clinic": value}, {"clinics": entries})

    new, unknown, other = run("Дента-Лакс"), run("Дента Экспресс"), run("Зубик")
    assert [i.code for i in new] == ["corrected"]
    assert [i.code for i in unknown] == ["not_in_dictionary"]

    await _doc_with_issues(api, auth, _as_dicts(new))
    await _doc_with_issues(api, auth, _as_dicts(new))
    await _doc_with_issues(api, auth, _as_dicts(unknown))
    await _doc_with_issues(api, auth, _as_dicts(other))
    await _doc_with_issues(api, auth, [_issue("not_in_dictionary", "«Х» нет в справочнике «doctors»")])
    await _doc_with_issues(api, auth, [_issue("missing_required", "нет поля")])

    r = await api.client.get("/v1/dictionaries/clinics/suggestions", headers=auth)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert rows[0] == {"value": "Дента-Лакс", "code": "corrected", "count": 2, "nearest": "Дента-Люкс"}
    by_value = {x["value"]: x for x in rows}
    assert by_value["Дента Экспресс"]["code"] == "not_in_dictionary"
    assert by_value["Дента Экспресс"]["nearest"] == "Дента-Люкс"
    assert by_value["Зубик"]["nearest"] is None
    assert "Х" not in by_value and len(rows) == 3  # чужой справочник и не-справочные замечания не попадают


async def test_suggestions_missing_dictionary_404(api, auth):
    r = await api.client.get("/v1/dictionaries/nope/suggestions", headers=auth)
    assert r.status_code == 404


async def test_suggestions_isolated(api, auth, make_tenant):
    other = await make_tenant("Чужой")
    await put(api, auth)
    await put(api, other)
    await _doc_with_issues(api, other, [_issue("not_in_dictionary", "«Левая» нет в справочнике «clinics»")])
    assert (await api.client.get("/v1/dictionaries/clinics/suggestions", headers=auth)).json() == []
