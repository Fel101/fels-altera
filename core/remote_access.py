"""Restrict an optional Tailscale Serve endpoint to its configured owner."""

import ipaddress
from urllib.parse import urlsplit

from flask import jsonify, request

from core.config import get_config


def direct_local_request():
    """Whether this request addresses the loopback server without proxy identity."""
    forwarded = any(name in request.headers for name in
                    ("X-Forwarded-For", "X-Forwarded-Proto", "X-Forwarded-Host", "Tailscale-User-Login", "Forwarded"))
    try:
        hostname = urlsplit(f"//{request.host}").hostname
        return (ipaddress.ip_address(request.remote_addr or "").is_loopback
                and hostname in {"localhost", "127.0.0.1", "::1"} and not forwarded)
    except ValueError:
        return False


def install_remote_access(app):
    @app.before_request
    def check_remote_access():
        settings = get_config().get("remote_access") or {}

        def denied():
            response = jsonify(error="This connection is not allowed to access Altera.", code="remote_access_denied")
            response.status_code = 403
            response.headers["Cache-Control"] = "no-store"
            return response

        # Trust Serve's identity header only over the loopback-only backend connection.
        try:
            if not ipaddress.ip_address(request.remote_addr or "").is_loopback:
                return denied()
        except ValueError:
            return denied()

        login = request.headers.get("Tailscale-User-Login", "").casefold()
        if direct_local_request():
            return None

        if not settings.get("enabled", False):
            return denied()
        host = str(settings.get("tailscale_host", "")).lower()
        allowed = settings.get("allowed_logins") or []
        if not isinstance(allowed, list) or not login or login not in {str(value).casefold() for value in allowed}:
            return denied()
        if not host or request.host.lower() != host or request.headers.get("X-Forwarded-Proto") != "https":
            return denied()

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if (origin and origin.lower() != f"https://{host}") or request.headers.get("Sec-Fetch-Site") == "cross-site":
                return denied()
        return None
