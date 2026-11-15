"""Barcode registration and scanning rules."""
from __future__ import annotations

from conftest import pid, q1
from motion_api.services.catalog import barcode_candidates, gtin_valid, normalize_barcode


def test_gtin_check_digits():
    assert gtin_valid("4006381333931")          # EAN-13
    assert gtin_valid("036000291452")           # UPC-A
    assert gtin_valid("96385074")               # EAN-8
    assert not gtin_valid("4006381333932")      # wrong check digit
    assert normalize_barcode(" 036000291452 ") == "0036000291452"  # UPC-A stored as EAN-13
    assert barcode_candidates("036000291452") == ["036000291452", "0036000291452"]


def test_register_scan_and_remove_barcodes(login, ids):
    inv = login("inv.ruth")
    product = pid(ids, "Margarine")
    bad = inv.post(f"/api/catalog/products/{product}/barcodes", {"code": "4006381333932"})
    assert bad.status_code == 422 and "check digit" in bad.json()["message"]
    ok = inv.post(f"/api/catalog/products/{product}/barcodes", {"code": "036000291452"})  # UPC-A form
    assert ok.status_code == 201, ok.text
    assert "0036000291452" in ok.json()["barcodes"]
    # A second code (e.g. the outer carton) on the same product.
    assert inv.post(f"/api/catalog/products/{product}/barcodes", {"code": "4006381333931"}).status_code == 201
    # Both the 12-digit and 13-digit scans resolve to the product at the till.
    cashier = login("cash.cen1")
    for scanned in ("036000291452", "0036000291452", "4006381333931"):
        r = cashier.get("/api/catalog/lookup", code=scanned)
        assert r.status_code == 200 and r.json()["id"] == product, scanned
    # The same code cannot be put on another product.
    dup = inv.post(f"/api/catalog/products/{pid(ids, 'Butter')}/barcodes", {"code": "0036000291452"})
    assert dup.status_code == 409 and "Margarine" in dup.json()["message"]
    # Cashiers cannot register barcodes.
    assert cashier.post(f"/api/catalog/products/{product}/barcodes", {"code": "96385074"}).status_code == 403
    # Removing works and the code stops scanning.
    assert inv.post(f"/api/catalog/products/{product}/barcodes/remove", {"code": "036000291452"}).status_code == 200
    assert cashier.get("/api/catalog/lookup", code="036000291452").status_code == 404
    assert q1("SELECT count(*) AS n FROM motion.audit_event WHERE action LIKE 'catalog.barcode.%%' AND target_id=%s",
              (str(product),))["n"] == 3


def test_unknown_barcode_message_is_actionable(login):
    r = login("cash.cen1").get("/api/catalog/lookup", code="5012345678900")
    assert r.status_code == 404 and "register" in r.json()["message"]
