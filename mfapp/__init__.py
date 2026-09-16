from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, session

from .extensions import csrf, db, limiter


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db_url = os.environ.get("MF_DATABASE_URL", "sqlite:///instance/market_forensics.db")
    if db_url.startswith("sqlite:///instance/"):
        db_url = "sqlite:///" + str(Path(app.instance_path) / db_url.split("sqlite:///instance/", 1)[1])
    app.config.update(
        SECRET_KEY=os.environ.get("MF_SECRET_KEY", "dev-only-change-me"),
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("MF_ENV", "development") == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(days=int(os.environ.get("MF_SESSION_DAYS", "7"))),
        SITE_NAME=os.environ.get("MF_SITE_NAME", "Market Forensics"),
        VERSION="0.0.1",
    )
    if test_config:
        app.config.update(test_config)
    if app.config["SECRET_KEY"] == "dev-only-change-me" and not app.config.get("TESTING"):
        app.logger.warning("Development SECRET_KEY in use. Generate production secrets before deployment.")
    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    from .models import User

    @app.before_request
    def load_user():
        g.user = None
        uid = session.get("user_id")
        if uid:
            user = db.session.get(User, uid)
            if user and user.is_active:
                g.user = user
            else:
                session.clear()

    from .routes import bp
    app.register_blueprint(bp)

    from .bootstrap import bp as bootstrap_bp
    app.register_blueprint(bootstrap_bp)

    with app.app_context():
        db.create_all()
    return app
