from __future__ import annotations

from flask import Flask

from .. import db
from .routes import api, pages


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["JSON_SORT_KEYS"] = False
    db.init_db()
    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")
    return app
