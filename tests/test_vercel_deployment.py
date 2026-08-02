import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_uses_tmp_sqlite_database_for_serverless_runtime(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    import app as app_module
    app_module = importlib.reload(app_module)

    db_uri = app_module.app.config['SQLALCHEMY_DATABASE_URI']

    assert db_uri == 'sqlite:////tmp/hospital_queue.db'
