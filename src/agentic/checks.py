"""Checks shared by planning and live execution, before any model is modified."""

from agentic.config import settings


def preview_joint_groups(candidate: dict) -> dict:
    # This helper only builds dictionaries; no OpenSTAAD methods are called.
    from radiator_support.staad_integration import _create_joint_groups
    nodes, members, connectivity, endpoints, supports = {}, {}, {}, {}, set()
    for member_id, source in enumerate(candidate["members_split_beams"], 1):
        member = dict(source, id=member_id)
        node_ids = []
        for end, point in enumerate((member["p1"], member["p2"])):
            key = tuple(round(c, 3) for c in point)
            node_id = nodes.setdefault(key, len(nodes) + 1)
            node_ids.append(node_id)
            connectivity.setdefault(node_id, []).append((member_id, end))
        members[member_id] = member
        endpoints[member_id] = tuple(node_ids)
        if member.get("type") == "column":
            supports.add(node_ids[0])
    return _create_joint_groups(connectivity, members, nodes, endpoints, supports)


def template_coverage(candidate: dict) -> dict:
    groups = preview_joint_groups(candidate)
    available = {path.stem for path in settings().templates.glob("*.ideaCon")}
    return {"required": sorted(groups), "available": sorted(available),
            "missing": sorted(set(groups) - available), "joint_groups": groups}
