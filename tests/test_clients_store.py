"""Tests for the encrypted multi-client credential store."""

from types import SimpleNamespace

import pytest

from centralmind.clients_store import ClientsStore


@pytest.fixture
def store(tmp_path):
    return ClientsStore(path=tmp_path / "clients.json", key_path=tmp_path / "secret.key")


class TestCrud:
    def test_new_store_is_empty(self, store):
        assert store.is_empty()
        assert store.list() == []
        assert store.get_default_id() is None

    def test_create_and_get(self, store):
        profile = store.create(name="Acme Corp", central_client_id="id1", central_client_secret="secret1")
        fetched = store.get(profile.id)
        assert fetched.name == "Acme Corp"
        assert fetched.central_client_id == "id1"

    def test_first_created_client_becomes_default(self, store):
        first = store.create(name="First")
        store.create(name="Second")
        assert store.get_default_id() == first.id

    def test_set_default(self, store):
        first = store.create(name="First")
        second = store.create(name="Second")
        store.set_default(second.id)
        assert store.get_default_id() == second.id

    def test_delete_reassigns_default(self, store):
        first = store.create(name="First")
        second = store.create(name="Second")
        store.delete(first.id)
        assert store.get_default_id() == second.id
        assert store.get(first.id) is None

    def test_configured_platforms(self, store):
        profile = store.create(
            name="Acme",
            central_client_id="id1",
            central_client_secret="secret1",
            mist_apitoken="tok",
        )
        assert set(profile.configured_platforms()) == {"central", "mist"}


class TestEncryptionRoundTrip:
    def test_data_persists_across_instances(self, tmp_path):
        path = tmp_path / "clients.json"
        key_path = tmp_path / "secret.key"

        store1 = ClientsStore(path=path, key_path=key_path)
        store1.create(name="Acme", central_client_secret="super-secret-value")

        store2 = ClientsStore(path=path, key_path=key_path)
        profiles = store2.list()
        assert len(profiles) == 1
        assert profiles[0].central_client_secret == "super-secret-value"

    def test_file_on_disk_is_not_plaintext(self, tmp_path):
        path = tmp_path / "clients.json"
        key_path = tmp_path / "secret.key"
        store = ClientsStore(path=path, key_path=key_path)
        store.create(name="Acme", central_client_secret="super-secret-value")

        raw = path.read_bytes()
        assert b"super-secret-value" not in raw
        assert b"Acme" not in raw

    def test_wrong_key_fails_to_decrypt(self, tmp_path):
        path = tmp_path / "clients.json"
        key_path = tmp_path / "secret.key"
        store = ClientsStore(path=path, key_path=key_path)
        store.create(name="Acme")

        other_key_path = tmp_path / "other.key"
        with pytest.raises(RuntimeError):
            ClientsStore(path=path, key_path=other_key_path)


class TestApiKey:
    def test_api_key_defaults_to_none(self, store):
        assert store.get_api_key() is None

    def test_api_key_persists(self, store, tmp_path):
        store.set_api_key("my-api-key")
        reopened = ClientsStore(path=store.path, key_path=store.key_path)
        assert reopened.get_api_key() == "my-api-key"


class TestServerApiModeOverride:
    def test_defaults_to_none(self, store):
        assert store.get_server_api_mode() is None

    def test_set_and_persists_across_instances(self, store):
        store.set_server_api_mode("readwrite")
        reopened = ClientsStore(path=store.path, key_path=store.key_path)
        assert reopened.get_server_api_mode() == "readwrite"

    def test_clearing_sets_back_to_none(self, store):
        store.set_server_api_mode("all")
        store.set_server_api_mode(None)
        assert store.get_server_api_mode() is None


class TestMigrateFromEnv:
    def test_migrates_legacy_config_when_store_empty(self, store):
        config = SimpleNamespace(
            central_client_id="legacy-id",
            central_client_secret="legacy-secret",
            central_base_url="https://legacy.example.com",
        )
        migrated = store.migrate_from_env(config)
        assert migrated is not None
        assert migrated.id == "default"
        assert store.get("default").central_client_id == "legacy-id"

    def test_does_not_migrate_when_no_credentials_set(self, store):
        config = SimpleNamespace(central_client_id="", central_client_secret="")
        migrated = store.migrate_from_env(config)
        assert migrated is None
        assert store.is_empty()

    def test_does_not_migrate_when_store_already_has_clients(self, store):
        store.create(name="Existing")
        config = SimpleNamespace(central_client_id="legacy-id", central_client_secret="legacy-secret")
        migrated = store.migrate_from_env(config)
        assert migrated is None
        assert len(store.list()) == 1
