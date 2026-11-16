"""Run the branch box on the back-office PC:  ``python -m motion_edge`` (or MotionBranchBox.exe).

* Settings come from ``config.json`` in the data folder (written by the installer), overridable
  by environment variables: central URL, device token, ports.
* Certificates: on first start the box creates a **trust root** (20 years) that the tills trust once,
  whose private key is discarded immediately, and a name-constrained **issuer** under it that signs
  the **server certificate** for the PC's name and current LAN addresses. When the address changes
  (DHCP) or renewal is due, only the server certificate is reissued; the root and its fingerprint stay
  the same, so the tills keep working without being set up again. A stolen box yields only the issuer
  key, which is constrained to this PC's names and private network ranges (enforced by Windows, Chrome
  and OpenSSL on intermediates), so it cannot impersonate public sites.
* Tills reach the till at ``https://<PC name>:8443/``. A plain-HTTP helper on port 8080 serves only
  the public trust certificate, its fingerprint and the PC name (for ``setup-till.ps1``).
* ``MotionBranchBox.exe --outbox-status`` reads the saved events read-only (works while the box is
  stopped) and reports what has not reached head office; ``uninstall-box.ps1`` relies on it.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import logging
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_DATA = Path(os.environ.get("ProgramData", str(Path.home()))) / "MotionBranchBox"


def data_dir() -> Path:
    return Path(os.environ.get("MOTION_EDGE_DATA", str(DEFAULT_DATA)))


def load_config() -> dict:
    cfg_file = data_dir() / "config.json"
    cfg = json.loads(cfg_file.read_text(encoding="utf-8")) if cfg_file.exists() else {}
    env = {"central_url": "MOTION_EDGE_CENTRAL_URL", "device_token": "MOTION_EDGE_TOKEN",
           "https_port": "MOTION_EDGE_HTTPS_PORT", "http_port": "MOTION_EDGE_HTTP_PORT"}
    for key, var in env.items():
        if os.environ.get(var):
            cfg[key] = os.environ[var]
    missing = [k for k in ("central_url", "device_token") if not cfg.get(k)]
    if missing:
        raise SystemExit(f"Branch box is not configured: missing {', '.join(missing)} in {cfg_file}. "
                         "Run install-box.ps1 again.")
    # The tills reach the box over the shop LAN, so it listens on the LAN; the installer opens
    # these ports in Windows Firewall for private networks only.
    cfg.setdefault("bind", "0.0.0.0")  # noqa: S104
    cfg.setdefault("https_port", 8443)
    cfg.setdefault("http_port", 8080)
    return cfg


def local_names() -> tuple[list[str], list[str]]:
    host = socket.gethostname()
    fqdn = socket.getfqdn()
    names = sorted({host, host.lower(), "localhost", *([fqdn, fqdn.lower()] if fqdn and "." in fqdn else [])})
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(host, None, family=socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return names, sorted(ips)


# Private address ranges the trust certificate may vouch for (the box's LAN address can change).
PRIVATE_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
LEAF_DAYS, RENEW_BEFORE_DAYS = 825, 30


def _write_key(path: Path, key) -> None:
    from cryptography.hazmat.primitives import serialization

    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))


def _ca_cert(subject, issuer, public_key, signing_key, names: list[str], days: int, ski_key=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    now = dt.datetime.now(dt.timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(public_key)
               .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
               .not_valid_after(now + dt.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=True, path_length=0 if ski_key else 1), critical=True)
               .add_extension(x509.KeyUsage(digital_signature=False, key_cert_sign=True, crl_sign=True,
                                            content_commitment=False, key_encipherment=False, data_encipherment=False,
                                            key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
               # Only this PC's names and private network addresses: never a public site.
               .add_extension(x509.NameConstraints(
                   permitted_subtrees=[x509.DNSName(n) for n in names]
                   + [x509.IPAddress(ipaddress.ip_network(n)) for n in PRIVATE_NETWORKS],
                   excluded_subtrees=None), critical=True)
               .add_extension(x509.SubjectKeyIdentifier.from_public_key(public_key), critical=False))
    if ski_key is not None:
        builder = builder.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ski_key), critical=False)
    return builder.sign(signing_key, hashes.SHA256())


def _ensure_ca(folder: Path, names: list[str]):
    """Trust hierarchy, created once and never replaced automatically.

    * ``box-ca.pem``: the root the tills trust. Its private key is discarded right after it signs the
      issuer, so nobody (not even someone who steals this PC) can sign anything else with it.
    * ``box-issuer.pem`` + key: the issuing certificate the box keeps to renew its server certificate.
      It carries the name constraints, which every verifier (Windows CryptoAPI, Chrome, OpenSSL)
      enforces on an intermediate, so a stolen issuer key cannot impersonate any public site.
    Returns (issuer certificate, issuer key)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    ca_path, issuer_path, issuer_key_path = folder / "box-ca.pem", folder / "box-issuer.pem", folder / "box-issuer-key.pem"
    if ca_path.exists() and issuer_path.exists() and issuer_key_path.exists():
        return (x509.load_pem_x509_certificate(issuer_path.read_bytes()),
                serialization.load_pem_private_key(issuer_key_path.read_bytes(), password=None))
    org = x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Motion Imprints")
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Motion branch box trust {names[0]}"), org])
    root = _ca_cert(root_name, root_name, root_key.public_key(), root_key, names, 365 * 20)
    issuer_key = ec.generate_private_key(ec.SECP256R1())
    issuer_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Motion branch box issuer {names[0]}"), org])
    issuer = _ca_cert(issuer_name, root_name, issuer_key.public_key(), root_key, names, 365 * 20 - 1,
                      ski_key=root_key.public_key())
    del root_key  # never written to disk
    folder.mkdir(parents=True, exist_ok=True)
    for stale in ("box-ca-key.pem", "box-server-chain.pem", "box-server-key.pem"):
        (folder / stale).unlink(missing_ok=True)
    _write_key(issuer_key_path, issuer_key)
    issuer_path.write_bytes(issuer.public_bytes(serialization.Encoding.PEM))
    ca_path.write_bytes(root.public_bytes(serialization.Encoding.PEM))
    return issuer, issuer_key


def ensure_certificate(folder: Path, names: list[str] | None = None, ips: list[str] | None = None) -> tuple[Path, Path, Path, str]:
    """Server certificate (reissued when the PC's addresses change or renewal is due) under a stable trust root.

    Returns (chain file for the server, server key, trust root certificate, trust root fingerprint)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    if names is None or ips is None:
        names, ips = local_names()
    ips = [i for i in ips if any(ipaddress.ip_address(i) in ipaddress.ip_network(n) for n in PRIVATE_NETWORKS)]
    issuer, issuer_key = _ensure_ca(folder, names)
    ca_path = folder / "box-ca.pem"
    chain_path, key_path = folder / "box-server-chain.pem", folder / "box-server-key.pem"
    now = dt.datetime.now(dt.timezone.utc)
    if chain_path.exists() and key_path.exists():
        leaf = x509.load_pem_x509_certificate(chain_path.read_bytes())
        san = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_ips = {str(x) for x in san.get_values_for_type(x509.IPAddress)}
        have_names = set(san.get_values_for_type(x509.DNSName))
        if (leaf.issuer == issuer.subject and set(ips) <= have_ips and set(names) <= have_names
                and leaf.not_valid_after_utc > now + dt.timedelta(days=RENEW_BEFORE_DAYS)):
            return chain_path, key_path, ca_path, fingerprint(ca_path)
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
            .issuer_name(issuer.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=LEAF_DAYS))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
            .sign(issuer_key, hashes.SHA256()))
    _write_key(key_path, key)
    chain_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + issuer.public_bytes(serialization.Encoding.PEM))
    return chain_path, key_path, ca_path, fingerprint(ca_path)


def outbox_status(folder: Path) -> tuple[int, dict]:
    """Read-only count of saved events head office has not accepted. Exit codes: 0 nothing at risk,
    3 events pending upload or awaiting review, 2 the data could not be read (treat as at risk)."""
    import sqlite3

    db = Path(os.environ.get("MOTION_EDGE_DB") or folder / "box.sqlite3")
    if not db.exists():
        return 0, {"database": str(db), "exists": False, "pending": 0, "rejected": 0}
    try:
        conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        try:
            counts = dict(conn.execute("SELECT status, count(*) FROM outbox GROUP BY status").fetchall())
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return 2, {"database": str(db), "exists": True, "error": str(exc)}
    out = {"database": str(db), "exists": True, "pending": counts.get("pending", 0), "rejected": counts.get("rejected", 0),
           "acked": counts.get("acked", 0)}
    return (3 if out["pending"] or out["rejected"] else 0), out


def fingerprint(cert_path: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    der = x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(serialization.Encoding.DER)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def serve_certificate_helper(bind: str, port: int, https_port: int, ca_path: Path, fp: str) -> None:
    pem = ca_path.read_bytes()
    box_name = socket.gethostname()

    class Helper(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            return

        def do_GET(self):  # noqa: N802
            if self.path == "/cert":
                self._send(200, pem, "application/x-pem-file")
            elif self.path == "/fingerprint":
                self._send(200, fp.encode(), "text/plain")
            elif self.path == "/name":
                self._send(200, box_name.encode(), "text/plain")
            else:
                host = (self.headers.get("Host") or "localhost").split(":")[0]
                self.send_response(302)
                self.send_header("Location", f"https://{host}:{https_port}/")
                self.end_headers()

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((bind, port), Helper)
    threading.Thread(target=server.serve_forever, daemon=True, name="cert-helper").start()


def main() -> None:
    ap = argparse.ArgumentParser(prog="MotionBranchBox")
    ap.add_argument("--outbox-status", action="store_true",
                    help="report saved events not yet accepted by head office (read-only) and exit")
    args = ap.parse_args()
    if args.outbox_status:
        code, info = outbox_status(data_dir())
        print(json.dumps(info))
        raise SystemExit(code)
    folder = data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(folder / "box.log", encoding="utf-8"), logging.StreamHandler()])
    # Windows reports every dropped browser connection from asyncio's proactor; it is harmless noise.
    logging.getLogger("asyncio").addFilter(
        lambda record: "_call_connection_lost" not in record.getMessage())
    cfg = load_config()
    os.environ.setdefault("MOTION_EDGE_DB", str(folder / "box.sqlite3"))
    os.environ["MOTION_EDGE_CENTRAL_URL"] = cfg["central_url"]
    os.environ["MOTION_EDGE_TOKEN"] = cfg["device_token"]
    if getattr(sys, "frozen", False):  # packaged exe: the till screen ships next to it
        os.environ.setdefault("MOTION_EDGE_WEB_DIR", str(Path(sys.executable).with_name("web")))
    chain_path, key_path, ca_path, fp = ensure_certificate(folder)
    (folder / "box-fingerprint.txt").write_text(fp, encoding="utf-8")
    serve_certificate_helper(cfg["bind"], int(cfg["http_port"]), int(cfg["https_port"]), ca_path, fp)

    import uvicorn

    from .app import create_app

    app = create_app()
    names, ips = local_names()
    logging.getLogger("motion.edge").info("Branch box ready: https://%s:%s/  (fingerprint %s)", names[0], cfg["https_port"], fp)
    uvicorn.run(app, host=cfg["bind"], port=int(cfg["https_port"]), ssl_certfile=str(chain_path), ssl_keyfile=str(key_path),
                log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
