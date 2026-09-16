from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, redirect, session, url_for

from .extensions import csrf, db, limiter

VIEW_ROLES = {"FRIEND", "INSIDER", "CONTROL"}


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    # The exact V3.1.12 FULL engine remains unchanged and keeps its tested SQLite/service layer.
    # Point LOCALAPPDATA at a server-private persistent directory before any engine module imports.
    engine_root = Path(os.environ.get("MF_V312_APP_DIR") or (Path(app.instance_path) / "v312_runtime"))
    engine_root.mkdir(parents=True, exist_ok=True)
    os.environ["LOCALAPPDATA"] = str(engine_root)

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
        VERSION="0.0.4",
        ENGINE_VERSION="3.1.12 FULL",
        V312_ENGINE_ROOT=str(engine_root),
    )
    if test_config:
        app.config.update(test_config)
    if app.config["SECRET_KEY"] == "dev-only-change-me" and not app.config.get("TESTING"):
        app.logger.warning("Development SECRET_KEY in use. Generate production secrets before deployment.")

    db.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    from .models import User
    from . import v312_models  # noqa: F401 - keep existing hosted evidence/publication tables registered

    @app.before_request
    def load_user():
        g.user = None
        g.view_role = None
        uid = session.get("user_id")
        if uid:
            user = db.session.get(User, uid)
            if user and user.is_active:
                g.user = user
                real_role = str(user.role or "FRIEND").upper()
                if real_role == "CONTROL":
                    requested = str(session.get("view_as", "CONTROL")).upper()
                    g.view_role = requested if requested in VIEW_ROLES else "CONTROL"
                else:
                    session.pop("view_as", None)
                    g.view_role = real_role
            else:
                session.clear()

    @app.context_processor
    def inject_product_context():
        real_role = str(getattr(getattr(g, "user", None), "role", "") or "").upper()
        return {
            "mf_version": app.config["VERSION"],
            "engine_version": app.config["ENGINE_VERSION"],
            "effective_role": getattr(g, "view_role", None),
            "real_role": real_role,
        }

    from .routes import bp as web_bp
    app.register_blueprint(web_bp)
    legacy_dashboard = app.view_functions.get("web.dashboard")

    from .preview import bp as preview_bp
    app.register_blueprint(preview_bp)

    # CONTROL bootstrap was a one-time installation path and is intentionally absent from 0.0.4.
    # Existing authenticated users and invite flows are the only account-entry surfaces now.

    # 0.0.4 canonical workstation: exact V3.1.12 FULL calculation engine + Flask presentation.
    from .full312_routes import bp as full312_bp
    app.register_blueprint(full312_bp)
    from .full312_controls import bp as full312_controls_bp
    app.register_blueprint(full312_controls_bp)

    from .full312_downloads import bp as full312_downloads_bp, download as full312_download
    app.register_blueprint(full312_downloads_bp)
    # Keep the original FULL export URL but replace the obsolete send_file(bytes) implementation.
    if "full312.export" in app.view_functions:
        app.view_functions["full312.export"] = full312_download

    # Retain old 0.0.2 compatibility endpoints for regression tests/bookmarks only. They are not
    # linked from the 0.0.4 UI and are not the canonical calculation source.
    from .v312_routes import bp as v312_bp
    app.register_blueprint(v312_bp)
    from .v312_flow_routes import bp as v312_flow_bp
    app.register_blueprint(v312_flow_bp)
    from .v312_settings import bp as v312_settings_bp
    app.register_blueprint(v312_settings_bp)

    # Stable release surfaces without editing the large legacy routes module.
    def release_health():
        return {
            "status": "ok",
            "version": app.config["VERSION"],
            "engine": app.config["ENGINE_VERSION"],
        }

    app.view_functions["web.health"] = release_health

    if legacy_dashboard is not None:
        def release_dashboard():
            if getattr(g, "user", None):
                real_role = str(g.user.role or "").upper()
                effective = str(getattr(g, "view_role", real_role) or real_role).upper()
                if real_role == "CONTROL" and effective == "CONTROL":
                    return redirect(url_for("full312.workspace"))
            return legacy_dashboard()
        app.view_functions["web.dashboard"] = release_dashboard

    with app.app_context():
        db.create_all()
    return app
