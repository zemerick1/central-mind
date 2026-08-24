"""Tests for the admin web UI: field validation and per-client API mode."""

import pytest
from starlette.testclient import TestClient

from centralmind.admin_web import _extract_api_mode, _validate_platform_fields, create_admin_app
from centralmind.clients_store import ClientsStore


class TestValidatePlatformFields:
    def test_valid_https_url_passes(self):
        errors = _validate_platform_fields({"central_base_url": "https://central.example.com"})
        assert "central_base_url" not in errors

    def test_valid_http_url_passes(self):
        errors = _validate_platform_fields({"sdc_apitoken": "tok", "central_base_url": "http://10.1.1.1:8080"})
        assert "central_base_url" not in errors

    def test_missing_scheme_is_rejected(self):
        errors = _validate_platform_fields({"central_base_url": "central.example.com"})
        assert "central_base_url" in errors

    def test_missing_netloc_is_rejected(self):
        errors = _validate_platform_fields({"central_base_url": "https://"})
        assert "central_base_url" in errors

    def test_non_http_scheme_is_rejected(self):
        errors = _validate_platform_fields({"central_base_url": "ftp://central.example.com"})
        assert "central_base_url" in errors

    def test_empty_url_is_not_an_error(self):
        # Blank just means "not configured" — that's valid.
        errors = _validate_platform_fields({"central_base_url": ""})
        assert "central_base_url" not in errors

    def test_bare_hostname_passes(self):
        errors = _validate_platform_fields({"mist_host": "api.mist.com"})
        assert "mist_host" not in errors

    def test_host_field_with_scheme_is_rejected(self):
        errors = _validate_platform_fields({"mist_host": "https://api.mist.com"})
        assert "mist_host" in errors

    def test_host_field_with_slash_is_rejected(self):
        errors = _validate_platform_fields({"mist_host": "api.mist.com/v1"})
        assert "mist_host" in errors

    def test_host_field_with_whitespace_is_rejected(self):
        errors = _validate_platform_fields({"mist_host": " api.mist.com "})
        assert "mist_host" in errors


class TestExtractApiMode:
    def test_blank_maps_to_none(self):
        assert _extract_api_mode({"api_mode": ""}) == (None, None)

    def test_missing_maps_to_none(self):
        assert _extract_api_mode({}) == (None, None)

    def test_valid_value_passes_through(self):
        assert _extract_api_mode({"api_mode": "readwrite"}) == ("readwrite", None)

    def test_invalid_value_is_rejected(self):
        mode, error = _extract_api_mode({"api_mode": "super-admin"})
        assert mode is None
        assert error is not None


@pytest.fixture
def store(tmp_path):
    return ClientsStore(path=tmp_path / "clients.json", key_path=tmp_path / "secret.key")


@pytest.fixture
def client(store):
    app = create_admin_app(store, "test-token")
    return TestClient(app)


class TestCreateClientFormValidation:
    def test_invalid_url_redisplays_form_without_saving(self, client, store):
        r = client.post(
            "/clients/new?token=test-token",
            data={"name": "Acme", "central_base_url": "not-a-url"},
        )
        assert r.status_code == 200
        assert "must be a full URL" in r.text
        assert store.is_empty()

    def test_valid_submission_creates_client_with_api_mode(self, client, store):
        r = client.post(
            "/clients/new?token=test-token",
            data={
                "name": "Acme",
                "central_base_url": "https://central.example.com",
                "central_client_id": "id1",
                "central_client_secret": "secret1",
                "api_mode": "readwrite",
            },
        )
        assert r.status_code == 200  # TestClient follows the redirect
        profiles = store.list()
        assert len(profiles) == 1
        assert profiles[0].api_mode == "readwrite"

    def test_invalid_api_mode_is_rejected(self, client, store):
        client.post(
            "/clients/new?token=test-token",
            data={"name": "Acme", "api_mode": "super-admin"},
        )
        assert store.is_empty()


class TestServerSettingsPage:
    def test_shows_no_override_by_default(self, client):
        r = client.get("/server-settings?token=test-token")
        assert r.status_code == 200
        assert "no admin override" in r.text

    def test_setting_override_persists(self, client, store):
        r = client.post("/server-settings?token=test-token", data={"server_api_mode": "readwrite"})
        assert r.status_code == 200  # TestClient follows the redirect
        assert store.get_server_api_mode() == "readwrite"

    def test_page_reflects_current_override(self, client, store):
        store.set_server_api_mode("all")
        r = client.get("/server-settings?token=test-token")
        assert "an admin override of <strong>all</strong>" in r.text

    def test_clearing_override_via_inherit_option(self, client, store):
        store.set_server_api_mode("readwrite")
        client.post("/server-settings?token=test-token", data={"server_api_mode": ""})
        assert store.get_server_api_mode() is None

    def test_invalid_value_is_ignored(self, client, store):
        client.post("/server-settings?token=test-token", data={"server_api_mode": "god-mode"})
        assert store.get_server_api_mode() is None

    def test_warning_copy_mentions_restart_requirement(self, client):
        r = client.get("/server-settings?token=test-token")
        assert "restarted" in r.text.lower()
