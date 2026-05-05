from pathlib import Path

from flask import Flask

from . import cli, db
from .routes import attachments, backup, checklist, reports, settings, tags, workspaces


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)

    project_root = Path(app.root_path).parent
    app.config.from_mapping(
        SECRET_KEY="dev",
        DATABASE=str(project_root / "data" / "reportboard.db"),
        ATTACHMENTS_DIR=str(project_root / "data" / "attachments"),
    )

    if test_config is not None:
        app.config.update(test_config)

    db.register(app)
    cli.register(app)
    app.register_blueprint(workspaces.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(attachments.bp)
    app.register_blueprint(checklist.bp)
    app.register_blueprint(settings.bp)
    app.register_blueprint(tags.bp)
    app.register_blueprint(backup.bp)

    return app
