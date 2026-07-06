"""TLS certificate management for the network-facing HTTP transport.

Supports two paths: a self-signed certificate auto-generated on first use
(so `--transport http` is HTTPS out of the box with zero setup), and
importing a real certificate + private key issued by an enterprise or
public CA (e.g. an internal PKI, or a Let's Encrypt-obtained pair).
"""

import ipaddress
import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

logger = logging.getLogger(__name__)

DEFAULT_TLS_DIR = Path(os.environ.get("CENTRALMIND_TLS_DIR") or (Path.home() / ".centralmind" / "tls"))
DEFAULT_CERT_PATH = DEFAULT_TLS_DIR / "cert.pem"
DEFAULT_KEY_PATH = DEFAULT_TLS_DIR / "key.pem"

# Fixed subject/issuer used for auto-generated certs, so we can reliably
# distinguish "self-signed" from "imported" later just by reading the cert.
_SELF_SIGNED_CN = "CentralMind Local (auto-generated)"

_SELF_SIGNED_VALIDITY = timedelta(days=825)


def _detect_local_names() -> tuple[list[str], list[str]]:
    """Best-effort local hostnames/IPs to include as cert SANs."""
    dns_names = {"localhost"}
    ip_addrs = {"127.0.0.1", "::1"}

    hostname = socket.gethostname()
    dns_names.add(hostname)
    try:
        dns_names.add(socket.getfqdn())
    except OSError:
        pass

    try:
        _, _, ip_list = socket.gethostbyname_ex(hostname)
        ip_addrs.update(ip_list)
    except OSError:
        pass

    return sorted(dns_names), sorted(ip_addrs)


def generate_self_signed_cert(cert_path: Path, key_path: Path) -> None:
    """Generate a fresh self-signed cert + key pair and write them to disk."""
    cert_path.parent.mkdir(parents=True, exist_ok=True)

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    dns_names, ip_addrs = _detect_local_names()
    san = [x509.DNSName(name) for name in dns_names]
    for ip in ip_addrs:
        try:
            san.append(x509.IPAddress(ipaddress.ip_address(ip)))
        except ValueError:
            continue

    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _SELF_SIGNED_CN)])
    now = datetime.now(timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + _SELF_SIGNED_VALIDITY)
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    if os.name != "nt":
        os.chmod(key_path, 0o600)

    logger.info(f"Generated self-signed TLS certificate at {cert_path} (valid until {cert.not_valid_after_utc.date()})")


def ensure_cert(cert_path: Path = DEFAULT_CERT_PATH, key_path: Path = DEFAULT_KEY_PATH) -> tuple[Path, Path]:
    """Return an existing cert/key pair, generating a self-signed one on first use."""
    if not cert_path.exists() or not key_path.exists():
        generate_self_signed_cert(cert_path, key_path)
    return cert_path, key_path


class CertValidationError(ValueError):
    pass


def validate_cert_key_pair(cert_bytes: bytes, key_bytes: bytes) -> x509.Certificate:
    """Parse and cross-validate a cert (chain) + private key. Returns the
    leaf certificate (first one in the file) on success.

    Raises CertValidationError with a human-readable reason on failure.
    """
    try:
        # A chain file may contain multiple PEM blocks; the leaf is first.
        leaf_pem = cert_bytes.split(b"-----END CERTIFICATE-----")[0] + b"-----END CERTIFICATE-----"
        leaf_cert = x509.load_pem_x509_certificate(leaf_pem)
    except Exception as e:
        raise CertValidationError(f"Could not parse certificate: {e}") from e

    try:
        private_key = serialization.load_pem_private_key(key_bytes, password=None)
    except Exception as e:
        raise CertValidationError(f"Could not parse private key (must be unencrypted PEM): {e}") from e

    cert_public_numbers = leaf_cert.public_key().public_numbers()
    key_public_numbers = private_key.public_key().public_numbers()
    if cert_public_numbers != key_public_numbers:
        raise CertValidationError("Private key does not match the certificate's public key.")

    now = datetime.now(timezone.utc)
    if leaf_cert.not_valid_after_utc < now:
        raise CertValidationError(f"Certificate expired on {leaf_cert.not_valid_after_utc.date()}.")
    if leaf_cert.not_valid_before_utc > now:
        raise CertValidationError(f"Certificate is not valid until {leaf_cert.not_valid_before_utc.date()}.")

    return leaf_cert


def import_cert(
    cert_bytes: bytes,
    key_bytes: bytes,
    cert_path: Path = DEFAULT_CERT_PATH,
    key_path: Path = DEFAULT_KEY_PATH,
) -> x509.Certificate:
    """Validate and install a CA-issued (enterprise or public) cert + key pair,
    replacing whatever was previously at cert_path/key_path. The full chain
    (all PEM blocks in cert_bytes) is preserved as-is."""
    leaf_cert = validate_cert_key_pair(cert_bytes, key_bytes)

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)

    if os.name != "nt":
        os.chmod(key_path, 0o600)

    logger.info(f"Imported TLS certificate for '{leaf_cert.subject.rfc4514_string()}' into {cert_path}")
    return leaf_cert


def cert_info(cert_path: Path = DEFAULT_CERT_PATH) -> Optional[dict]:
    """Return display metadata for the currently installed certificate, or
    None if no certificate exists yet."""
    if not cert_path.exists():
        return None
    cert = x509.load_pem_x509_certificate(cert_path.read_bytes().split(b"-----END CERTIFICATE-----")[0] + b"-----END CERTIFICATE-----")
    subject = cert.subject.rfc4514_string()
    issuer = cert.issuer.rfc4514_string()
    return {
        "subject": subject,
        "issuer": issuer,
        "self_signed": subject == issuer and _SELF_SIGNED_CN in subject,
        "not_valid_before": cert.not_valid_before_utc.isoformat(),
        "not_valid_after": cert.not_valid_after_utc.isoformat(),
        "expired": cert.not_valid_after_utc < datetime.now(timezone.utc),
    }
