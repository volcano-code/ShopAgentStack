"""Load only the repository's reviewed synthetic catalog; never execute the legacy SQL importer."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import re

from .state import no_symlinks, sha


def bundle(root: Path, state: Path) -> dict:
    files = ("catalog/products.tsv", "knowledge/catalog-v2.json", "knowledge/authoring/policies.tsv",
             "knowledge/authoring/staff-policies.tsv")
    raw = {}
    for name in files:
        path = root / name
        no_symlinks(path, root)
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("synthetic input exceeds the import limit")
        raw[name] = path.read_bytes()
    products = []
    categories = set()
    images = {}
    for row in csv.DictReader(io.StringIO(raw[files[0]].decode("utf-8-sig")), delimiter="|"):
        if None in row or any(not isinstance(v, str) or not v.strip() for v in row.values()):
            raise ValueError("invalid product source row")
        slug = row["slug"]
        if not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", slug) or not re.fullmatch(r"\d+\.\d{2}", row["price"]):
            raise ValueError("invalid product identifier or price")
        for key in ("stock", "weight_g"):
            if not row[key].isdigit() or not 0 < int(row[key]) <= 1000000:
                raise ValueError("invalid inventory or weight")
        image = state / "runtime/apps/web/dist/products/catalog-v1" / (slug + ".webp")
        no_symlinks(image, state)
        if not image.is_file():
            raise ValueError("demo snapshot lacks a catalog image; rebuild and initialize a new state")
        images[slug] = sha(image)
        categories.add(row["category"])
        products.append({"slug": slug, "category": row["category"], "name": row["name"], "price": row["price"],
                         "stock": int(row["stock"]), "weightGrams": int(row["weight_g"]),
                         **{k: row[k] for k in ("material", "specification", "description", "care")}})
    if (len(products) != 100 or len(images) != 100 or len({p["name"] for p in products}) != 100 or len(categories) != 10):
        raise ValueError("expected 100 distinct products in 10 categories")
    catalog = json.loads(raw[files[1]])
    if catalog.get("source") != "shop_agent_stack-original-synthetic" or catalog.get("documents") != 96:
        raise ValueError("unsupported synthetic policy catalog")
    rows = {}
    for name in files[2:]:
        for row in csv.DictReader(io.StringIO(raw[name].decode("utf-8-sig")), delimiter="\t"):
            if None in row or not row.get("id") or row["id"] in rows:
                raise ValueError("duplicate/invalid policy source")
            rows[row["id"]] = row
    policies = []
    for item in catalog["policies"]:
        source_id = item["id"]
        row = rows.pop(source_id, None)
        visibility = "STAFF" if source_id.startswith("SOP-") else "CUSTOMER"
        if (row is None or not re.fullmatch(r"(?:SOP|POL)-\d{3}", source_id) or item["title"] != row["title"]
                or item["visibility"] != visibility or item["publication_status"] != "DRAFT"):
            raise ValueError("stale policy catalog or visibility mismatch")
        clauses = [row[f"clause_{n}"] for n in (1, 2, 3)]
        if len(item["clauses"]) != 3 or any(not c or hashlib.sha256(c.encode()).hexdigest() != meta["sha256"]
                                         for c, meta in zip(clauses, item["clauses"])):
            raise ValueError("policy clause digest mismatch; review sources")
        policies.append({"sourceId": source_id, "title": item["title"], "content": "\n".join(clauses), "visibility": visibility})
    if rows or len(policies) != 96 or sum(p["visibility"] == "STAFF" for p in policies) != 16:
        raise ValueError("incomplete policy source inventory")
    hashes = {k: hashlib.sha256(v).hexdigest() for k, v in raw.items()}
    source_hash = hashlib.sha256(json.dumps({"files": hashes, "images": images}, sort_keys=True).encode()).hexdigest()
    return {"sourceSha256": source_hash, "products": products, "policies": policies}
