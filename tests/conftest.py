import tempfile
from pathlib import Path

import pytest

from app import create_app
from app.db import init_db


@pytest.fixture
def app():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    attachments_dir = tempfile.mkdtemp()

    app = create_app({
        "TESTING": True,
        "DATABASE": db_path,
        "ATTACHMENTS_DIR": attachments_dir,
    })

    with app.app_context():
        init_db()

    yield app

    import os
    os.close(db_fd)
    Path(db_path).unlink(missing_ok=True)
    import shutil
    shutil.rmtree(attachments_dir, ignore_errors=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def runner(app):
    return app.test_cli_runner()
