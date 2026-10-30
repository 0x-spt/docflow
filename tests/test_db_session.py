from docflow.db.session import make_engine


def test_engines_hide_statement_parameters(tmp_path):
    # ошибка БД с параметрами раскрыла бы token_enc и token_hash в логе
    assert make_engine("sqlite+aiosqlite:///:memory:").sync_engine.hide_parameters is True
    assert make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'a.db').as_posix()}").sync_engine.hide_parameters is True
