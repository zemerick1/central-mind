"""Tests for TLS certificate generation, import, and validation."""

from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from centralmind import tls


@pytest.fixture
def cert_paths(tmp_path):
    return tmp_path / "cert.pem", tmp_path / "key.pem"


def _make_cert_key(subject_cn="Some Real CA Issued Cert", days_valid=30, key=None):
    key = key or rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days_valid))
        .sign(key, hashes.SHA256())
    )
    cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
    key_bytes = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return cert_bytes, key_bytes


class TestGenerateSelfSigned:
    def test_generates_cert_and_key_files(self, cert_paths):
        cert_path, key_path = cert_paths
        tls.generate_self_signed_cert(cert_path, key_path)
        assert cert_path.exists()
        assert key_path.exists()

    def test_cert_is_parseable_and_valid_now(self, cert_paths):
        cert_path, key_path = cert_paths
        tls.generate_self_signed_cert(cert_path, key_path)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        now = datetime.now(timezone.utc)
        assert cert.not_valid_before_utc <= now <= cert.not_valid_after_utc

    def test_includes_localhost_san(self, cert_paths):
        cert_path, key_path = cert_paths
        tls.generate_self_signed_cert(cert_path, key_path)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns_names = san.get_values_for_type(x509.DNSName)
        assert "localhost" in dns_names

    def test_private_key_matches_cert(self, cert_paths):
        cert_path, key_path = cert_paths
        tls.generate_self_signed_cert(cert_path, key_path)
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        assert cert.public_key().public_numbers() == key.public_key().public_numbers()


class TestEnsureCert:
    def test_generates_when_missing(self, cert_paths):
        cert_path, key_path = cert_paths
        result_cert, result_key = tls.ensure_cert(cert_path, key_path)
        assert result_cert == cert_path
        assert cert_path.exists() and key_path.exists()

    def test_does_not_regenerate_when_present(self, cert_paths):
        cert_path, key_path = cert_paths
        tls.ensure_cert(cert_path, key_path)
        original = cert_path.read_bytes()
        tls.ensure_cert(cert_path, key_path)
        assert cert_path.read_bytes() == original


class TestValidateCertKeyPair:
    def test_accepts_matching_pair(self):
        cert_bytes, key_bytes = _make_cert_key()
        leaf = tls.validate_cert_key_pair(cert_bytes, key_bytes)
        assert leaf.subject.rfc4514_string() == "CN=Some Real CA Issued Cert"

    def test_rejects_mismatched_key(self):
        cert_bytes, _ = _make_cert_key()
        _, other_key_bytes = _make_cert_key()
        with pytest.raises(tls.CertValidationError, match="does not match"):
            tls.validate_cert_key_pair(cert_bytes, other_key_bytes)

    def test_rejects_expired_cert(self):
        # not_valid_after in the past
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Expired")])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=60))
            .not_valid_after(now - timedelta(days=1))
            .sign(key, hashes.SHA256())
        )
        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        key_bytes = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        with pytest.raises(tls.CertValidationError, match="expired"):
            tls.validate_cert_key_pair(cert_bytes, key_bytes)

    def test_rejects_garbage_cert(self):
        _, key_bytes = _make_cert_key()
        with pytest.raises(tls.CertValidationError, match="Could not parse certificate"):
            tls.validate_cert_key_pair(b"not a cert", key_bytes)

    def test_rejects_garbage_key(self):
        cert_bytes, _ = _make_cert_key()
        with pytest.raises(tls.CertValidationError, match="Could not parse private key"):
            tls.validate_cert_key_pair(cert_bytes, b"not a key")


class TestImportCert:
    def test_import_replaces_existing_cert(self, cert_paths):
        cert_path, key_path = cert_paths
        tls.generate_self_signed_cert(cert_path, key_path)  # pre-existing self-signed

        cert_bytes, key_bytes = _make_cert_key(subject_cn="Enterprise CA Cert")
        leaf = tls.import_cert(cert_bytes, key_bytes, cert_path, key_path)

        assert leaf.subject.rfc4514_string() == "CN=Enterprise CA Cert"
        on_disk = x509.load_pem_x509_certificate(cert_path.read_bytes())
        assert on_disk.subject.rfc4514_string() == "CN=Enterprise CA Cert"

    def test_import_rejects_bad_pair_without_touching_disk(self, cert_paths):
        cert_path, key_path = cert_paths
        tls.generate_self_signed_cert(cert_path, key_path)
        original = cert_path.read_bytes()

        cert_bytes, _ = _make_cert_key()
        _, other_key_bytes = _make_cert_key()
        with pytest.raises(tls.CertValidationError):
            tls.import_cert(cert_bytes, other_key_bytes, cert_path, key_path)

        assert cert_path.read_bytes() == original


class TestCertInfo:
    def test_none_when_no_cert(self, cert_paths):
        cert_path, _key_path = cert_paths
        assert tls.cert_info(cert_path) is None

    def test_reports_self_signed(self, cert_paths):
        cert_path, key_path = cert_paths
        tls.generate_self_signed_cert(cert_path, key_path)
        info = tls.cert_info(cert_path)
        assert info["self_signed"] is True
        assert info["expired"] is False

    def test_reports_imported_as_not_self_signed(self, cert_paths):
        cert_path, key_path = cert_paths
        cert_bytes, key_bytes = _make_cert_key(subject_cn="Enterprise CA Cert")
        tls.import_cert(cert_bytes, key_bytes, cert_path, key_path)
        info = tls.cert_info(cert_path)
        assert info["self_signed"] is False
        assert "Enterprise CA Cert" in info["subject"]
