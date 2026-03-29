"""
Flask Application Factory
--------------------------
Creates and configures the Flask application.  Import ``create_app()`` in
``main.py`` and in tests.
"""

import logging
from flask import Flask, jsonify


def create_app() -> Flask:
    from app.config import config

    # ── Logging ───────────────────────────────────────────────────────────
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    app = Flask(__name__)
    app.config["DEVICE_ID"] = config.DEVICE_ID
    # Disable default redirect for trailing slashes (avoids 308 confusion)
    app.url_map.strict_slashes = False
    # Don't sort JSON keys — preserves insertion order
    app.json.sort_keys = False

    # ── Blueprints ────────────────────────────────────────────────────────
    from app.routes import register_blueprints
    register_blueprints(app)

    # ── Global error handlers ─────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(exc):
        return jsonify({"error": "not_found", "message": str(exc)}), 404

    @app.errorhandler(405)
    def method_not_allowed(exc):
        return jsonify({"error": "method_not_allowed", "message": str(exc)}), 405

    @app.errorhandler(500)
    def internal_error(exc):
        logging.getLogger(__name__).exception("Unhandled exception")
        return jsonify({"error": "internal_error", "message": "An unexpected error occurred."}), 500

    # ── Teardown ──────────────────────────────────────────────────────────
    @app.teardown_appcontext
    def _teardown(_exc):
        pass  # Reserved for future DB/resource cleanup

    return app
