from flask import Blueprint, render_template

from ... import __version__
from . import courts, events, facilities, targets  # noqa: F401  (register routes on the api blueprint)
from .common import api

pages = Blueprint("pages", __name__)


@pages.get("/")
def index():
    return render_template("index.html", version=__version__)


__all__ = ["api", "pages"]
