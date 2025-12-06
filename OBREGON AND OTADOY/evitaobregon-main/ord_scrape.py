import argparse
import csv
import io
import json
import re
import sys
import tempfile
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup


BROWSE_URL = "https://open-reaction-database.org/browse"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def get_html(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def extract_next_data(html: str) -> Optional[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return None
    try:
        return json.loads(script.text)
    except Exception:
        return None


def find_links(html: str, patterns: List[str]) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if any(p in href for p in patterns):
            if href.startswith("/"):
                href = f"https://open-reaction-database.org{href}"
            links.append(href)
    return list(dict.fromkeys(links))


def find_dataset_page_links(html: str) -> List[str]:
    return find_links(html, ["/dataset", "/datasets", "/data/"])


def find_dataset_download_links(html: str) -> List[str]:
    return find_links(html, [".pb.gz", ".pbtxt", ".json", "raw=1"])


def extract_dataset_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"(ord_dataset-[a-f0-9]+)", url)
    if m:
        return m.group(1)
    return None


def build_github_raw_urls(dataset_id: str) -> List[str]:
    base_paths = [
        "https://raw.githubusercontent.com/Open-Reaction-Database/ord-data/main/data",
        "https://raw.githubusercontent.com/open-reaction-database/ord-data/main/data",
    ]
    urls = []
    for bp in base_paths:
        urls.append(f"{bp}/{dataset_id}/{dataset_id}.pb.gz")
        urls.append(f"{bp}/{dataset_id}/{dataset_id}.pbtxt")
        urls.append(f"{bp}/{dataset_id}/{dataset_id}.json")
    return urls


def list_github_dataset_files(dataset_id: str) -> List[str]:
    api_urls = [
        f"https://api.github.com/repos/Open-Reaction-Database/ord-data/contents/data/{dataset_id}",
        f"https://api.github.com/repos/open-reaction-database/ord-data/contents/data/{dataset_id}",
    ]
    files: List[str] = []
    for api in api_urls:
        try:
            r = requests.get(api, headers={"Accept": "application/vnd.github+json", **HEADERS}, timeout=30)
            if r.status_code == 200:
                items = r.json()
                for it in items:
                    name = it.get("name")
                    if isinstance(name, str):
                        files.append(name)
                break
        except Exception:
            continue
    return files


def list_all_github_datasets(limit: Optional[int] = None) -> List[str]:
    api_urls = [
        "https://api.github.com/repos/Open-Reaction-Database/ord-data/contents/data",
        "https://api.github.com/repos/open-reaction-database/ord-data/contents/data",
    ]
    ids: List[str] = []
    for api in api_urls:
        try:
            r = requests.get(api, headers={"Accept": "application/vnd.github+json", **HEADERS}, timeout=30)
            if r.status_code == 200:
                items = r.json()
                for it in items:
                    if it.get("type") == "dir":
                        name = it.get("name")
                        if isinstance(name, str) and name.startswith("ord_dataset-"):
                            ids.append(name)
                break
        except Exception:
            continue
    if limit:
        return ids[:limit]
    return ids


def search_github_dataset_path(dataset_id: str) -> Optional[str]:
    api_urls = [
        f"https://api.github.com/search/code?q={dataset_id}+repo:Open-Reaction-Database/ord-data",
        f"https://api.github.com/search/code?q={dataset_id}+repo:open-reaction-database/ord-data",
    ]
    for api in api_urls:
        try:
            r = requests.get(api, headers={"Accept": "application/vnd.github.text-match+json", **HEADERS}, timeout=30)
            if r.status_code == 200:
                js = r.json()
                items = js.get("items") or []
                for it in items:
                    path = it.get("path")
                    if isinstance(path, str) and dataset_id in path and path.endswith(".pb.gz"):
                        return path
        except Exception:
            continue
    return None


def safe_message_to_dict(message: Any) -> Dict[str, Any]:
    try:
        from google.protobuf.json_format import MessageToDict
    except Exception:
        return {}
    try:
        return MessageToDict(message, preserving_proto_field_name=True)
    except Exception:
        return {}


def download_bytes(url: str) -> bytes:
    r = requests.get(url, headers=HEADERS, timeout=60)
    r.raise_for_status()
    return r.content


def load_dataset_from_pb_gz_bytes(data: bytes) -> Optional[Any]:
    try:
        from ord_schema.message_helpers import load_message
        from ord_schema.proto import dataset_pb2
    except Exception:
        return None
    with tempfile.NamedTemporaryFile(suffix=".pb.gz", delete=True) as tmp:
        tmp.write(data)
        tmp.flush()
        return load_message(tmp.name, dataset_pb2.Dataset)


def fetch_dataset_by_id(dataset_id: str) -> Optional[Any]:
    try:
        from ord_schema.message_helpers import fetch_dataset
        return fetch_dataset(dataset_id)
    except Exception:
        return None


def pick_identifier(identifiers: List[Dict[str, Any]], kind: str) -> Optional[str]:
    for ident in identifiers:
        t = ident.get("type")
        v = ident.get("value")
        if not v:
            continue
        if isinstance(t, str) and t.upper() == kind.upper():
            return v
    if kind.lower() == "name":
        for ident in identifiers:
            v = ident.get("value")
            if v and re.search(r"[A-Za-z]", v):
                return v
    return None


CATEGORY_KEYS = {
    "base": ["base"],
    "solvent": ["solvent"],
    "amine": ["amine", "aniline"],
    "aryl_halide": ["aryl halide", "bromide", "chloride", "iodide", "aryl"],
    "metal": ["metal", "catalyst", "palladium", "nickel", "pd", "ni", "cu", "rhodium"],
    "ligand": ["ligand"],
    "carboxylic_acid": ["carboxylic acid", "acid", "carboxylate"],
    "additive": ["additive"],
    "activation_agent": ["activation agent", "activating agent", "coupling reagent"],
}


def label_to_categories(label: str) -> List[str]:
    ll = label.lower()
    hits = []
    for cat, keys in CATEGORY_KEYS.items():
        for k in keys:
            if k in ll:
                hits.append(cat)
                break
    return hits


def role_to_categories(role: Optional[str]) -> List[str]:
    if not role:
        return []
    r = str(role).lower()
    hits = []
    if "solvent" in r:
        hits.append("solvent")
    if "ligand" in r:
        hits.append("ligand")
    if "catalyst" in r or "metal" in r:
        hits.append("metal")
    if "base" in r:
        hits.append("base")
    return hits


def extract_components_from_reaction_dict(rxn: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {
        "base": [],
        "solvent": [],
        "amine": [],
        "aryl_halide": [],
        "metal": [],
        "ligand": [],
        "carboxylic_acid": [],
        "additive": [],
        "activation_agent": [],
    }
    inputs = rxn.get("inputs", {})
    for label, inp in inputs.items():
        components = inp.get("components", [])
        cats = label_to_categories(label)
        for comp in components:
            ids = comp.get("identifiers", [])
            name = pick_identifier(ids, "NAME")
            smiles = pick_identifier(ids, "SMILES")
            role = comp.get("reaction_role")
            rcats = role_to_categories(role)
            merged = set(cats) | set(rcats)
            if not merged and label.lower() in ("substrate", "electrophile"):
                merged.add("aryl_halide")
            if not merged and name and re.search(r"bromide|chloride|iodide", name.lower()):
                merged.add("aryl_halide")
            if not merged and name and re.search(r"amine|aniline", name.lower()):
                merged.add("amine")
            if not merged and name and re.search(r"phosphine|xphos|bippy|segphos|dppf|binap|bidentate|ligand", name.lower()):
                merged.add("ligand")
            if not merged and name and re.search(r"palladium|nickel|pd\b|ni\b|cu\b|copper|rhodium", name.lower()):
                merged.add("metal")
            if not merged and name and re.search(r"base|carbonate|phosphate|oxide|tert-butoxide|tBuOK|KOtBu|DBU|TEA|DIPEA|\bNaOH\b|\bKOH\b", name.lower()):
                merged.add("base")
            if not merged and name and re.search(r"solvent|dioxane|toluene|dme|dmf|dma|dcm|meoh|etoh|methanol|ethanol|acetonitrile|meCN|THF|isopropanol|IPA|xylene|ethyl acetate", name.lower()):
                merged.add("solvent")
            if not merged and name and re.search(r"acid|carboxylate", name.lower()):
                merged.add("carboxylic_acid")
            comp_obj: Dict[str, Any] = {
                "reaction_role": role,
                "identifiers": [{"type": i.get("type"), "value": i.get("value")} for i in ids],
                "amount": comp.get("amount"),
                "name": name,
                "smiles": smiles,
                "input_key": label,
            }
            for cat in merged:
                out[cat].append(comp_obj)
    for k in out:
        uniq = []
        seen = set()
        for item in out[k]:
            key = (item.get("name") or "", item.get("smiles") or "", item.get("reaction_role") or "")
            if key in seen:
                continue
            seen.add(key)
            uniq.append(item)
        out[k] = uniq
    return out


def extract_reactions_from_dataset(dataset: Any) -> List[Dict[str, Any]]:
    reactions = []
    try:
        ds_dict = safe_message_to_dict(dataset)
        ds_name = ds_dict.get("name")
        ds_id = ds_dict.get("dataset_id")
        for rxn_msg in getattr(dataset, "reactions", []):
            rxn_dict = safe_message_to_dict(rxn_msg)
            rid = rxn_dict.get("reaction_id")
            comps = extract_components_from_reaction_dict(rxn_dict)
            reactions.append({"dataset_id": ds_id, "dataset_name": ds_name, "reaction_id": rid, **comps})
    except Exception:
        pass
    return reactions


def build_raw(reactions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for r in reactions:
        rid = r.get("reaction_id")
        for cat_key in [
            "base",
            "solvent",
            "amine",
            "aryl_halide",
            "metal",
            "ligand",
            "carboxylic_acid",
            "additive",
            "activation_agent",
        ]:
            items = r.get(cat_key) or []
            for item in items:
                role = item.get("reaction_role")
                input_key = item.get("input_key")
                identifiers = item.get("identifiers") or []
                for ident in identifiers:
                    id_type = ident.get("type")
                    id_val = ident.get("value")
                    if not id_type or id_val is None:
                        continue
                    entries.append({
                        "reaction_id": rid,
                        "input_key": input_key,
                        "reaction_role": role,
                        "identifier_type": id_type,
                        "value": id_val,
                    })
    return entries


def scrape(browse_url: str, max_datasets: Optional[int] = None) -> List[Dict[str, Any]]:
    html = get_html(browse_url)
    dataset_pages = find_dataset_page_links(html)
    downloads = find_dataset_download_links(html)
    results: List[Dict[str, Any]] = []
    for page_url in dataset_pages:
        try:
            ph = get_html(page_url)
        except Exception:
            continue
        dl_links = find_dataset_download_links(ph)
        for dl in dl_links:
            if dl.endswith(".pb.gz") or ("raw=1" in dl and dl.endswith(".pb.gz")):
                try:
                    data = download_bytes(dl)
                    dataset = load_dataset_from_pb_gz_bytes(data)
                    if dataset is None:
                        continue
                    reactions = extract_reactions_from_dataset(dataset)
                    results.extend(reactions)
                except Exception:
                    continue
        if max_datasets and len(results) > 0:
            if len(results) >= max_datasets:
                break
    for dl in downloads:
        if dl.endswith(".pb.gz") or ("raw=1" in dl and dl.endswith(".pb.gz")):
            try:
                data = download_bytes(dl)
                dataset = load_dataset_from_pb_gz_bytes(data)
                if dataset is None:
                    continue
                reactions = extract_reactions_from_dataset(dataset)
                results.extend(reactions)
            except Exception:
                continue
    if (max_datasets is None or len(results) < max_datasets):
        ids = list(dict.fromkeys(re.findall(r"ord_dataset-[a-f0-9]{32}", html)))
        for ds_id in ids:
            names = list_github_dataset_files(ds_id)
            candidate = None
            for nm in names:
                if nm.endswith(".pb.gz"):
                    candidate = nm
                    break
            if candidate:
                for bp in [
                    "https://raw.githubusercontent.com/Open-Reaction-Database/ord-data/main/data",
                    "https://raw.githubusercontent.com/open-reaction-database/ord-data/main/data",
                ]:
                    raw_url = f"{bp}/{ds_id}/{candidate}"
                    try:
                        data = download_bytes(raw_url)
                        dataset = load_dataset_from_pb_gz_bytes(data)
                        if dataset is None:
                            continue
                        reactions = extract_reactions_from_dataset(dataset)
                        results.extend(reactions)
                        break
                    except Exception:
                        continue
            if not candidate:
                path = search_github_dataset_path(ds_id)
                if path:
                    for bp in [
                        "https://raw.githubusercontent.com/Open-Reaction-Database/ord-data/main",
                        "https://raw.githubusercontent.com/open-reaction-database/ord-data/main",
                    ]:
                        raw_url = f"{bp}/{path}"
                        try:
                            data = download_bytes(raw_url)
                            dataset = load_dataset_from_pb_gz_bytes(data)
                            if dataset is None:
                                continue
                            reactions = extract_reactions_from_dataset(dataset)
                            results.extend(reactions)
                            break
                        except Exception:
                            continue
            if max_datasets and len(results) >= max_datasets:
                break
    if not results:
        ds_id = extract_dataset_id_from_url(browse_url)
        if ds_id:
            dataset = fetch_dataset_by_id(ds_id)
            if dataset is not None:
                reactions = extract_reactions_from_dataset(dataset)
                results.extend(reactions)
            names = list_github_dataset_files(ds_id)
            candidate = None
            for nm in names:
                if nm.endswith(".pb.gz"):
                    candidate = nm
                    break
            if candidate:
                for bp in [
                    "https://raw.githubusercontent.com/Open-Reaction-Database/ord-data/main/data",
                    "https://raw.githubusercontent.com/open-reaction-database/ord-data/main/data",
                ]:
                    raw_url = f"{bp}/{ds_id}/{candidate}"
                    try:
                        data = download_bytes(raw_url)
                        dataset = load_dataset_from_pb_gz_bytes(data)
                        if dataset is None:
                            continue
                        reactions = extract_reactions_from_dataset(dataset)
                        results.extend(reactions)
                        break
                    except Exception:
                        continue
            if not results:
                path = search_github_dataset_path(ds_id)
                if path:
                    for bp in [
                        "https://raw.githubusercontent.com/Open-Reaction-Database/ord-data/main",
                        "https://raw.githubusercontent.com/open-reaction-database/ord-data/main",
                    ]:
                        raw_url = f"{bp}/{path}"
                        try:
                            data = download_bytes(raw_url)
                            dataset = load_dataset_from_pb_gz_bytes(data)
                            if dataset is None:
                                continue
                            reactions = extract_reactions_from_dataset(dataset)
                            results.extend(reactions)
                            break
                        except Exception:
                            continue
    if not results:
        ids = list_all_github_datasets(limit=max_datasets)
        for ds_id in ids:
            names = list_github_dataset_files(ds_id)
            candidate = None
            for nm in names:
                if nm.endswith(".pb.gz"):
                    candidate = nm
                    break
            if not candidate:
                continue
            for bp in [
                "https://raw.githubusercontent.com/Open-Reaction-Database/ord-data/main/data",
                "https://raw.githubusercontent.com/open-reaction-database/ord-data/main/data",
            ]:
                raw_url = f"{bp}/{ds_id}/{candidate}"
                try:
                    data = download_bytes(raw_url)
                    dataset = load_dataset_from_pb_gz_bytes(data)
                    if dataset is None:
                        continue
                    reactions = extract_reactions_from_dataset(dataset)
                    results.extend(reactions)
                    break
                except Exception:
                    continue
            if max_datasets and len(results) >= max_datasets:
                break
    return results


def write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_csv(path: str, entries: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["reaction_id", "input_key", "reaction_role", "identifier_type", "value"])
        writer.writeheader()
        for e in entries:
            writer.writerow({
                "reaction_id": e.get("reaction_id"),
                "input_key": e.get("input_key"),
                "reaction_role": e.get("reaction_role"),
                "identifier_type": e.get("identifier_type"),
                "value": e.get("value"),
            })


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("urls", nargs="*", help="One or more browse/dataset/id URLs")
    p.add_argument("--browse", default=BROWSE_URL)
    p.add_argument("--out-json", default="ord_reactions.json")
    p.add_argument("--out-csv", default="ord_reactions.csv")
    p.add_argument("--max-datasets", type=int, default=None)
    args = p.parse_args()
    targets = args.urls if args.urls else [args.browse]
    results: List[Dict[str, Any]] = []
    for url in targets:
        results.extend(scrape(url, args.max_datasets))
    raw = build_raw(results)
    write_json(args.out_json, raw)
    write_csv(args.out_csv, raw)
    print(f"Wrote entries ({len(raw)}) to {args.out_json} and {args.out_csv}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(1)

