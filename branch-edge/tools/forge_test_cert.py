"""Pilot security check: make a FORGED certificate for a public site with this box's issuer key.

    python forge_test_cert.py "C:\\ProgramData\\MotionBranchBox" forged.pem      (on the back-office PC, as Administrator)

Copy forged.pem to a checkout computer that ran setup-till.ps1, then run
    powershell -ExecutionPolicy Bypass -File verify-cert-windows.ps1 -Installed -Chain forged.pem
It must print INVALID (the box's trust is name-constrained, so a stolen box key cannot impersonate other
sites). Then run it on the box's real chain (box-server-chain.pem): it must print OK. Delete forged.pem after.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def main() -> None:
    data, out = Path(sys.argv[1]), Path(sys.argv[2])
    issuer = x509.load_pem_x509_certificate((data / "box-issuer.pem").read_bytes())
    issuer_key = serialization.load_pem_private_key((data / "box-issuer-key.pem").read_bytes(), password=None)
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "www.example.com")]))
            .issuer_name(issuer.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("www.example.com")]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False)
            .sign(issuer_key, hashes.SHA256()))
    out.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + issuer.public_bytes(serialization.Encoding.PEM))
    print(f"wrote {out} (a forged www.example.com certificate; it must be rejected on the tills)")


if __name__ == "__main__":
    main()
