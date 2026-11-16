"""Branch box certificates and the uninstall safety check (no database or network needed)."""
from __future__ import annotations

import datetime as dt
import ipaddress
import json
import socket
import ssl
import sys
import threading
import uuid

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "branch-edge"))
from motion_edge import __main__ as box  # noqa: E402
from motion_edge.store import Store  # noqa: E402

NAMES = ["BACKOFFICE-PC", "backoffice-pc", "localhost"]


def _handshake(chain, key, cafile, server_hostname: str) -> str:
    """Real TLS handshake on loopback; the client trusts only the box's trust certificate."""
    srv = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    srv.load_cert_chain(str(chain), str(key))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve():
        conn, _ = listener.accept()
        try:
            srv.wrap_socket(conn, server_side=True).close()
        except (ssl.SSLError, OSError):
            pass

    threading.Thread(target=serve, daemon=True).start()
    client = ssl.create_default_context(cafile=str(cafile))
    try:
        with socket.create_connection(listener.getsockname()) as s, client.wrap_socket(s, server_hostname=server_hostname):
            return "trusted"
    except ssl.SSLCertVerificationError as exc:
        return f"refused: {exc.verify_message}"
    finally:
        listener.close()


def test_address_change_keeps_the_trust_the_tills_installed(tmp_path):
    chain1, key1, ca, fp1 = box.ensure_certificate(tmp_path, NAMES, ["127.0.0.1", "192.168.1.10"])
    first_leaf = chain1.read_bytes()
    assert _handshake(chain1, key1, ca, "BACKOFFICE-PC") == "trusted"
    assert _handshake(chain1, key1, ca, "192.168.1.10") == "trusted"
    # Same addresses: nothing is reissued.
    assert box.ensure_certificate(tmp_path, NAMES, ["127.0.0.1", "192.168.1.10"])[3] == fp1
    assert chain1.read_bytes() == first_leaf
    # DHCP hands the PC a new address: the server certificate is reissued, the trust certificate is not.
    chain2, key2, ca2, fp2 = box.ensure_certificate(tmp_path, NAMES, ["127.0.0.1", "192.168.1.77"])
    assert fp2 == fp1 and ca2 == ca
    assert chain2.read_bytes() != first_leaf
    assert _handshake(chain2, key2, ca, "BACKOFFICE-PC") == "trusted"
    assert _handshake(chain2, key2, ca, "192.168.1.77") == "trusted"
    # Public addresses are never put in the server certificate.
    chain3, _, _, _ = box.ensure_certificate(tmp_path, NAMES, ["127.0.0.1", "192.168.1.77", "41.210.1.5"])
    assert b"41.210.1.5" not in chain3.read_bytes()


def test_stolen_box_key_cannot_impersonate_public_sites(tmp_path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    _, _, ca_path, _ = box.ensure_certificate(tmp_path, NAMES, ["127.0.0.1", "10.0.0.5"])
    # The root's private key is never stored; a thief gets at most the (name-constrained) issuer key.
    assert not list(tmp_path.glob("box-ca-key*"))
    ca = x509.load_pem_x509_certificate((tmp_path / "box-issuer.pem").read_bytes())
    ca_key = serialization.load_pem_private_key((tmp_path / "box-issuer-key.pem").read_bytes(), password=None)

    def forge(san: x509.GeneralName):
        key = ec.generate_private_key(ec.SECP256R1())
        now = dt.datetime.now(dt.timezone.utc)
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "forged")]))
                .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=30))
                .add_extension(x509.SubjectAlternativeName([san]), critical=False)
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .sign(ca_key, hashes.SHA256()))
        chain, keyf = tmp_path / f"forged-{uuid.uuid4().hex}.pem", tmp_path / f"forged-key-{uuid.uuid4().hex}.pem"
        chain.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + ca.public_bytes(serialization.Encoding.PEM))  # leaf + issuer
        box._write_key(keyf, key)
        return chain, keyf

    chain, keyf = forge(x509.DNSName("bank.example"))
    assert "refused" in _handshake(chain, keyf, ca_path, "bank.example")
    chain, keyf = forge(x509.IPAddress(ipaddress.ip_address("8.8.8.8")))
    assert "refused" in _handshake(chain, keyf, ca_path, "8.8.8.8")
    # ... while the box's own name still works with a properly issued certificate.
    chain, keyf = forge(x509.DNSName("backoffice-pc"))
    assert _handshake(chain, keyf, ca_path, "backoffice-pc") == "trusted"


def _store_with(tmp_path, statuses: list[str]) -> None:
    store = Store(tmp_path / "box.sqlite3")
    with store.write() as c:
        for i, status in enumerate(statuses):
            c.execute("""INSERT INTO outbox (event_id, created_at, register_id, cashier, local_receipt_no, payload, status, kind)
                         VALUES (?,?,?,?,?,?,?,'sale')""",
                      (str(uuid.uuid4()), dt.datetime.now(dt.timezone.utc).isoformat(), 1, "cash.x", f"T1-B{i:06d}",
                       json.dumps({"lines": []}), status))


@pytest.mark.parametrize(("statuses", "code"), [
    ([], 0),                              # box never sold anything
    (["acked", "acked"], 0),              # everything accepted by head office
    (["acked", "pending"], 3),            # still waiting to upload
    (["acked", "rejected"], 3),           # head office refused it: awaiting review
])
def test_outbox_status_reports_what_is_not_safe_to_delete(tmp_path, monkeypatch, statuses, code):
    monkeypatch.delenv("MOTION_EDGE_DB", raising=False)
    _store_with(tmp_path, statuses)
    got, info = box.outbox_status(tmp_path)
    assert got == code
    assert info["pending"] == statuses.count("pending") and info["rejected"] == statuses.count("rejected")


def test_outbox_status_missing_and_unreadable(tmp_path, monkeypatch):
    monkeypatch.delenv("MOTION_EDGE_DB", raising=False)
    assert box.outbox_status(tmp_path)[0] == 0  # no database: nothing to lose
    (tmp_path / "box.sqlite3").write_bytes(b"not a database" * 100)
    code, info = box.outbox_status(tmp_path)
    assert code == 2 and "error" in info  # unknown: the uninstaller must keep the data
