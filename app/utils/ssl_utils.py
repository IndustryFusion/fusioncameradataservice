"""
SSL / TLS Utilities
--------------------
Generates a self-signed X.509 certificate + private key if no certificate
files are present on disk.  The generated certificate is written to the paths
defined in Config and re-used on subsequent starts.

Usage
-----
    from app.utils.ssl_utils import ensure_ssl_context
    ssl_context = ensure_ssl_context(config)  # returns ssl.SSLContext or None
"""

import ipaddress
import logging
import os
import socket
import ssl
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)


def _generate_self_signed(cert_path: str, key_path: str, days: int, device_id: str):
    """Generate a self-signed RSA certificate and write it to disk."""
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
    except ImportError as exc:
        raise RuntimeError(
            "The 'cryptography' package is required to generate SSL certificates. "
            "Install it with: pip install cryptography"
        ) from exc

    hostname = socket.gethostname()

    # ── Private key ──────────────────────────────────────────────────────
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # ── Subject / Issuer ─────────────────────────────────────────────────
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "XX"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "FusionCameraDataService"),
            x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, device_id),
            x509.NameAttribute(NameOID.COMMON_NAME, hostname),
        ]
    )

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(hostname),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    # ── Write to disk ────────────────────────────────────────────────────
    Path(cert_path).parent.mkdir(parents=True, exist_ok=True)

    with open(key_path, "wb") as fh:
        fh.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    # Restrict key file permissions to owner-read-only
    os.chmod(key_path, 0o600)

    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))

    logger.info(
        "Self-signed TLS certificate generated → %s  (valid %d days)", cert_path, days
    )


def ensure_ssl_context(cfg) -> "ssl.SSLContext | None":
    """
    Return a configured ssl.SSLContext, or *None* when SSL is disabled.

    If ``SSL_SELF_SIGNED`` is True and the cert/key files do not yet exist,
    they are created automatically.
    """
    if not cfg.SSL_ENABLED:
        logger.warning("SSL is DISABLED — traffic will be plain HTTP")
        return None

    cert_missing = not Path(cfg.SSL_CERT_PATH).is_file()
    key_missing = not Path(cfg.SSL_KEY_PATH).is_file()

    if cert_missing or key_missing:
        if cfg.SSL_SELF_SIGNED:
            logger.info("SSL certificate not found — generating self-signed cert")
            _generate_self_signed(
                cfg.SSL_CERT_PATH,
                cfg.SSL_KEY_PATH,
                cfg.SSL_CERT_DAYS,
                cfg.DEVICE_ID,
            )
        else:
            raise FileNotFoundError(
                f"SSL cert/key not found at {cfg.SSL_CERT_PATH} / {cfg.SSL_KEY_PATH}. "
                "Either provide the files or set SSL_SELF_SIGNED=true."
            )

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile=cfg.SSL_CERT_PATH, keyfile=cfg.SSL_KEY_PATH)
    # Disable weak protocols
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    logger.info("TLS context ready (cert: %s)", cfg.SSL_CERT_PATH)
    return ctx
