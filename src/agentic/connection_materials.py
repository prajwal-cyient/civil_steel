"""Bind reference IDEA plate grades to the actual standard Tekla details."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re


def reference_plate_material_policy():
    from agentic.reference_procedure import STANDARD_DETAILS
    folder = Path(__file__).resolve().parents[2] / "tekla_connection_attributes"
    attributes, hashes = {}, {}
    for detail in STANDARD_DETAILS.values():
        number, name = detail["component"], detail["attribute_file"]
        path = folder / f"{name}.j{number}"
        content = path.read_bytes()
        materials = {key: value for key, value in re.findall(
            r'^joint_attributes\.(mat\d*)\s+"([^"]+)"', content.decode("utf-8-sig"), re.MULTILINE)}
        if not materials or set(materials.values()) != {"S355J0"}:
            raise ValueError(f"Reference plate grade is not verified S355J0 in {path.name}.")
        attributes[str(number)] = materials
        hashes[path.name] = hashlib.sha256(content).hexdigest()
    return {"idea_material_name": "S 355", "tekla_material_name": "S355J0",
            "component_attributes": attributes, "attribute_file_sha256": hashes,
            "basis": "Plate materials explicitly specified by the senior standard Tekla component files."}


def material_only_plate_evidence(before, after, policy):
    def canonical(value):
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("Nonfinite connection geometry during material verification.")
            return round(value, 9)
        if isinstance(value, dict):
            return {k: canonical(v) for k, v in value.items()}
        if isinstance(value, list):
            return [canonical(v) for v in value]
        return value
    def geometry(value):
        result = deepcopy(value)
        for plate in result.get("plates", []):
            plate.pop("material", None)
        return canonical(result)
    if geometry(before) != geometry(after):
        raise ValueError("Plate material correction changed unrelated geometry, members, welds or topology.")
    original = {p["id"]: p for p in before.get("plates", [])}
    plates = after.get("plates", [])
    if not plates or any(p.get("material") != policy["idea_material_name"] for p in plates):
        raise ValueError("IDEA plates did not retain the declared reference material.")
    changes = [{"id": p["id"], "name": p["name"], "old": original[p["id"]]["material"],
                "new": p["material"]} for p in plates if p["material"] != original[p["id"]]["material"]]
    return {"policy": policy, "geometry_unchanged": True,
            "plates": [{"id": p["id"], "name": p["name"], "material": p["material"]} for p in plates],
            "material_corrections": changes}


def verify_plate_material_evidence(evidence, policy, iom_file):
    if not policy:
        return
    if not evidence or evidence.get("policy") != policy or evidence.get("geometry_unchanged") is not True:
        raise ValueError("Reference plate material evidence is missing or differs from the declared Tekla details.")
    plates = evidence.get("plates", [])
    if not plates or any(p.get("material") != policy["idea_material_name"] for p in plates):
        raise ValueError("Plate material evidence does not cover the reference grade.")
    if not iom_file or not Path(iom_file).is_file():
        raise ValueError("Plate material source IOM file is missing.")
    content = Path(iom_file).read_bytes()
    if hashlib.sha256(content).hexdigest() != evidence.get("iom_sha256"):
        raise ValueError("Plate material source IOM file changed after calculation.")
    actual = [{"id": p["id"], "name": p["name"], "material": p["material"]}
              for p in json.loads(content).get("plates", [])]
    if actual != plates:
        raise ValueError("Plate material evidence differs from the native IOM plates.")
