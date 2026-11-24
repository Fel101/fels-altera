"""Remote access checks run with fake identities, never a real Tailscale connection."""

from pathlib import Path

import pytest
import yaml
from flask import Flask, jsonify

from core.remote_access import install_remote_access


@pytest.fixture
def remote_client(altera_config, monkeypatch):
    from core import config

    monkeypatch.setattr(config, "LOCAL_CONFIG_PATH", altera_config.parent / "remote-test-local.yaml")
    altera_config.write_text(yaml.safe_dump({"remote_access": {
        "enabled": True,
        "tailscale_host": "altera.example.ts.net:8443",
        "allowed_logins": ["owner@example.com"],
    }}), encoding="utf-8")
    app = Flask(__name__, root_path=str(Path(__file__).resolve().parent.parent))
    install_remote_access(app)

    @app.route("/private", methods=["GET", "POST"])
    def private():
        return jsonify(ok=True)

    return app.test_client()


def remote_headers(**extra):
    return {"Tailscale-User-Login": "owner@example.com", "X-Forwarded-Proto": "https", **extra}


@pytest.mark.parametrize("base_url", ["http://localhost:8767", "http://127.0.0.1:8767", "http://[::1]:8767"])
def test_local_browser_still_works(remote_client, base_url):
    assert remote_client.get("/private", base_url=base_url).status_code == 200


def test_serve_owner_can_read_and_write(remote_client):
    base = "https://altera.example.ts.net:8443"
    assert remote_client.get("/private", base_url=base, headers=remote_headers()).status_code == 200
    assert remote_client.post("/private", base_url=base, headers=remote_headers(Origin=base)).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"X-Forwarded-Proto": "https"},
    remote_headers(**{"Tailscale-User-Login": "other@example.com"}),
    remote_headers(**{"X-Forwarded-Proto": "http"})])
def test_remote_access_denies_missing_or_wrong_identity(remote_client, headers):
    response = remote_client.get("/private", base_url="https://altera.example.ts.net:8443", headers=headers)
    assert response.status_code == 403
    assert response.json["code"] == "remote_access_denied"
    assert response.headers["Cache-Control"] == "no-store"


def test_forwarded_localhost_does_not_bypass_identity(remote_client):
    assert remote_client.get("/private", headers={"X-Forwarded-Proto": "https"}).status_code == 403


def test_wrong_host_or_nonlocal_backend_is_denied(remote_client):
    assert remote_client.get("/private", base_url="https://wrong.example", headers=remote_headers()).status_code == 403
    assert remote_client.get("/private", base_url="https://altera.example.ts.net:8443", headers=remote_headers(),
                             environ_overrides={"REMOTE_ADDR": "100.100.100.100"}).status_code == 403


@pytest.mark.parametrize("extra", [{"Origin": "https://other.example"}, {"Origin": "https://altera.example.ts.net"},
                                  {"Sec-Fetch-Site": "cross-site"}])
def test_other_websites_cannot_write(remote_client, extra):
    assert remote_client.post("/private", base_url="https://altera.example.ts.net:8443",
                              headers=remote_headers(**extra)).status_code == 403


def test_empty_allowlist_denies_remote_access(remote_client, altera_config):
    altera_config.write_text("remote_access:\n  enabled: true\n  tailscale_host: altera.example.ts.net:8443\n", encoding="utf-8")
    assert remote_client.get("/private", base_url="https://altera.example.ts.net:8443",
                             headers=remote_headers()).status_code == 403


def test_remote_access_is_opt_in(remote_client, altera_config):
    altera_config.write_text("remote_access:\n  enabled: false\n", encoding="utf-8")
    assert remote_client.get("/private").status_code == 200
    assert remote_client.get("/private", base_url="https://altera.example.ts.net:8443",
                             headers=remote_headers()).status_code == 403
    altera_config.write_text("{}", encoding="utf-8")
    assert remote_client.get("/private", base_url="https://altera.example.ts.net:8443",
                             headers=remote_headers()).status_code == 403


def test_saving_settings_keeps_remote_identity_in_local_config(altera_config, monkeypatch):
    from core import config

    local_path = altera_config.parent / "remote-save-local.yaml"
    monkeypatch.setattr(config, "LOCAL_CONFIG_PATH", local_path)
    settings = {"app": {"host": "127.0.0.1"}, "remote_access": {
        "enabled": True, "tailscale_host": "altera.example.ts.net:8443", "allowed_logins": ["owner@example.com"],
    }}
    config.save_config(settings)
    assert "remote_access" not in yaml.safe_load(altera_config.read_text("utf-8"))
    assert yaml.safe_load(local_path.read_text("utf-8"))["remote_access"] == settings["remote_access"]
    assert config.get_config()["remote_access"] == settings["remote_access"]


def test_worker_is_served_at_root_without_permanent_cache():
    from test_blueprints import create_app

    app = create_app("pages")
    response = app.test_client().get("/sw.js")
    assert response.status_code == 200
    assert response.mimetype == "application/javascript"
    assert response.headers["Cache-Control"] == "no-cache"
    assert b"self.addEventListener" in response.data
