from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, g, session

from .extensions import csrf, db, limiter

VIEW_ROLES = {"FRIEND", "INSIDER", "CONTROL"}
_AUTO_PREFIX = re.compile(r"^\[AUTO\s+[^\]]+\]\s*", re.I)


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    env = os.environ.get("MF_ENV", "development").lower()
    db_url = os.environ.get("MF_DATABASE_URL", "").strip()
    if not db_url:
        db_url = "sqlite:///" + str(Path(app.instance_path) / "market_forensics_dev.db")
    if db_url.startswith("sqlite:///instance/"):
        db_url = "sqlite:///" + str(Path(app.instance_path) / db_url.split("sqlite:///instance/", 1)[1])
    if env == "production" and db_url.startswith("sqlite"):
        raise RuntimeError("Market Forensics production requires MariaDB via MF_DATABASE_URL; SQLite is not a supported production core.")

    app.config.update(
        SECRET_KEY=os.environ.get("MF_SECRET_KEY", "dev-only-change-me"),
        SQLALCHEMY_DATABASE_URI=db_url,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 240} if not db_url.startswith("sqlite") else {},
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env == "production",
        PERMANENT_SESSION_LIFETIME=timedelta(days=int(os.environ.get("MF_SESSION_DAYS", "7"))),
        SITE_NAME=os.environ.get("MF_SITE_NAME", "Market Forensics"),
        LOGO_URL=os.environ.get("MF_LOGO_URL", "").strip(),
        VERSION="0.2.7",
        APP_ENV=env,
        AUTO_MIGRATE=os.environ.get("MF_AUTO_MIGRATE", "1") == "1",
    )
    if test_config:
        app.config.update(test_config)
    if app.config["SECRET_KEY"] == "dev-only-change-me" and not app.config.get("TESTING"):
        app.logger.warning("Development SECRET_KEY in use. Production must provide MF_SECRET_KEY.")

    db.init_app(app); csrf.init_app(app); limiter.init_app(app)

    from . import models as account_models  # noqa: F401
    from . import core_models  # noqa: F401
    from .formatting import format_money, format_number, get_number_format
    from .models import User
    from .trace import bp as trace_bp, install_trace

    install_trace(app)

    @app.before_request
    def load_user():
        g.user = None; g.view_role = None; g.number_format = "AUTO"
        uid = session.get("user_id")
        if uid:
            user = db.session.get(User, uid)
            if user and user.is_active:
                g.user = user; g.number_format = get_number_format(user.id)
                real_role = str(user.role or "FRIEND").upper()
                if real_role == "CONTROL":
                    requested = str(session.get("view_as", "CONTROL")).upper()
                    g.view_role = requested if requested in VIEW_ROLES else "CONTROL"
                else:
                    session.pop("view_as", None); g.view_role = real_role
            else:
                session.clear()

    @app.template_filter("mf_num")
    def mf_num(value, decimals=1): return format_number(value, getattr(g, "number_format", "AUTO"), decimals)

    @app.template_filter("mf_money")
    def mf_money(value, decimals=1): return format_money(value, getattr(g, "number_format", "AUTO"), decimals)

    @app.template_filter("clean_auto")
    def clean_auto(value): return _AUTO_PREFIX.sub("", str(value or "").strip())

    @app.context_processor
    def inject_product_context():
        real_role = str(getattr(getattr(g, "user", None), "role", "") or "").upper()
        return {
            "mf_version": app.config["VERSION"], "effective_role": getattr(g, "view_role", None), "real_role": real_role,
            "number_format": getattr(g, "number_format", "AUTO"), "site_name": app.config["SITE_NAME"], "logo_url": app.config.get("LOGO_URL", ""),
        }

    from .auth import bp as auth_bp
    from .routes import bp as web_bp
    from . import research_routes  # noqa: F401
    from . import workspace_routes  # noqa: F401
    from . import support_routes  # noqa: F401
    from . import alert_routes  # noqa: F401
    from .preview import bp as preview_bp
    app.register_blueprint(auth_bp); app.register_blueprint(web_bp); app.register_blueprint(preview_bp); app.register_blueprint(trace_bp)

    if app.config.get("AUTO_MIGRATE"):
        from .schema import bootstrap_schema
        from .upgrade_020 import migrate_semantic_preferences
        from .upgrade_026 import migrate_local_web_parity
        with app.app_context():
            schema_result = bootstrap_schema(migrate_legacy=True)
            preference_result = migrate_semantic_preferences()
            parity_result = migrate_local_web_parity()
            app.config["SCHEMA_BOOTSTRAP_RESULT"] = {
                "schema": schema_result,
                "preferences": preference_result,
                "parity": parity_result,
                "release": "0.2.6",
            }
    return app
