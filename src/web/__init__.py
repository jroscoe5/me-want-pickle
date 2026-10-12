from __future__ import annotations

from flask import Flask

from .. import db
from ..watcher.scheduler import start_scheduler
from .routes import api, pages


def create_app(scheduler: bool = True) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["JSON_SORT_KEYS"] = False
    db.init_db()
    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")
    if scheduler:
        start_scheduler()
    return app
