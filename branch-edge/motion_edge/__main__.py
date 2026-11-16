"""Run the branch box on the back-office PC:  ``python -m motion_edge`` (or MotionBranchBox.exe).

* Settings come from ``config.json`` in the data folder (written by the installer), overridable
  by environment variables: central URL, device token, ports.
* On first start it creates the box's own HTTPS certificate (valid for the PC's name and LAN
  addresses). Tills reach the till at ``https://<box>:8443/``.
* A plain-HTTP helper on port 8080 serves only the public certificate and its fingerprint, so
  ``setup-till.ps1`` can install it on each checkout computer, and redirects everything else.
"""
from __future__ import annotations

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
    names = sorted({host, host.lower(), "localhost"})
    ips = {"127.0.0.1"}
    try:
        for info in socket.getaddrinfo(host, None, family=socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return names, sorted(ips)


def ensure_certificate(folder: Path) -> tuple[Path, Path, str]:
    """Self-signed certificate for the box (10 years), regenerated if the PC's addresses change."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    cert_path, key_path = folder / "box-cert.pem", folder / "box-key.pem"
    names, ips = local_names()
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have = {str(x) for x in san.get_values_for_type(x509.IPAddress)}
        constrained = any(isinstance(e.value, x509.NameConstraints) for e in cert.extensions)
        if constrained and set(ips) <= have and cert.not_valid_after_utc > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30):
            return cert_path, key_path, fingerprint(cert_path)
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Motion branch box {names[0]}"),
                         x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Motion Imprints")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName(
                [x509.DNSName(n) for n in names] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            # The tills trust this certificate as a root, so it may only ever vouch for this box's own
            # names and addresses: a stolen box key cannot impersonate any other site to the tills.
            .add_extension(x509.NameConstraints(
                permitted_subtrees=[x509.DNSName(n) for n in names]
                + [x509.IPAddress(ipaddress.ip_network(f"{i}/32")) for i in ips],
                excluded_subtrees=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=False, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False, key_agreement=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    folder.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path, fingerprint(cert_path)


def fingerprint(cert_path: Path) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    der = x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(serialization.Encoding.DER)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def serve_certificate_helper(bind: str, port: int, https_port: int, cert_path: Path, fp: str) -> None:
    pem = cert_path.read_bytes()

    class Helper(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            return

        def do_GET(self):  # noqa: N802
            if self.path == "/cert":
                self._send(200, pem, "application/x-pem-file")
            elif self.path == "/fingerprint":
                self._send(200, fp.encode(), "text/plain")
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
    cert_path, key_path, fp = ensure_certificate(folder)
    (folder / "box-fingerprint.txt").write_text(fp, encoding="utf-8")
    serve_certificate_helper(cfg["bind"], int(cfg["http_port"]), int(cfg["https_port"]), cert_path, fp)

    import uvicorn

    from .app import create_app

    app = create_app()
    names, ips = local_names()
    logging.getLogger("motion.edge").info("Branch box ready: https://%s:%s/  (fingerprint %s)", names[0], cfg["https_port"], fp)
    uvicorn.run(app, host=cfg["bind"], port=int(cfg["https_port"]), ssl_certfile=str(cert_path), ssl_keyfile=str(key_path),
                log_level="warning", proxy_headers=False)


if __name__ == "__main__":
    main()
