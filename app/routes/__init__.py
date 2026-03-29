"""app/routes package"""
from flask import Flask


def register_blueprints(app: Flask) -> None:
    from app.routes.health import bp as health_bp
    from app.routes.devices import bp as devices_bp
    from app.routes.stream import bp as stream_bp

    app.register_blueprint(health_bp)
    app.register_blueprint(devices_bp)
    app.register_blueprint(stream_bp)
