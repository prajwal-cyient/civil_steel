"""Evaluate IDEA connection checks using the existing joint grader."""

from math import isfinite
from typing import Any, Dict

from config.run_policy import RunPolicy
from orchestration.state import GraderResult


def grade_joint_check_summary(check_summary: Dict[str, Any], run_policy=None) -> GraderResult:
    """Grade valid checks; distinguish exceeded limits from SDK status failures."""
    policy = RunPolicy.model_validate(run_policy or {})
    items = check_summary if isinstance(check_summary, list) else check_summary.get("summary", [])
    failures, fix_types, advisories = [], [], []
    maxima = {"max_plate_utilization": 0.0, "max_deformation_utilization": 0.0, "max_bolt": 0.0, "max_weld": 0.0}
    active_checks = 0
    unresolved = False
    for item in items:
        if item.get("skipped") is True:
            continue
        name = str(item.get("name", "")).lower()
        val, status = item.get("check_value"), item.get("check_status")
        if not isinstance(status, bool) or isinstance(val, bool) or not isinstance(val, (int, float)) or not isfinite(val):
            failures.append(f"Incomplete or nonfinite IDEA check: {name}")
            unresolved = True
            continue
        if "deformation" in name or "local" in name or "loc." in name:
            key, limit, fix = "max_deformation_utilization", policy.max_deformation_utilization_percent, "plate_thickness"
        elif "plate" in name or "strain" in name:
            key, limit, fix = "max_plate_utilization", policy.max_plate_utilization_percent, "plate_thickness"
        elif "bolt" in name:
            key, limit, fix = "max_bolt", policy.max_bolt_utilization_percent, "bolt_dia"
        elif "weld" in name:
            key, limit, fix = "max_weld", policy.max_weld_utilization_percent, "weld_size"
        else:
            if status is not True:
                failures.append(f"IDEA {name} reports check_status=False (value={val}); inspect calculation details.")
                unresolved = True
            continue
        active_checks += 1
        if val < 0:
            failures.append(f"Invalid negative result for {name}: {val}")
            unresolved = True
            continue
        maxima[key] = max(maxima[key], val)
        if val > limit:
            failures.append(f"{item['name']} value {val}% exceeds {limit}% rubric limit")
            fix_types.append(fix)
        elif status is False:
            failures.append(f"IDEA {name} reports check_status=False despite utilization {val}% within {limit}%; {item.get('message') or 'inspect calculation details before correction.'}")
            unresolved = True
    if not active_checks:
        failures.append("No active connection component checks returned.")
        unresolved = True
    if 0 < maxima["max_bolt"] < 50 and maxima["max_plate_utilization"] < 20:
        advisories.append("Joint underutilized; downsizing is not permitted by this rubric.")
    fix_type = fix_types[0] if fix_types and not unresolved else None
    return GraderResult(
        passed=not failures, score=0.0 if failures else 1.0,
        summary="; ".join(failures) if failures else "All returned connection checks passed the configured rubric.",
        details={**maxima, "failures": failures, "fix_type": fix_type,
                 "advisories": advisories, "downsizing_permitted": False},
        corrective_action_needed=fix_type is not None,
        corrective_action={"fix_type": fix_type} if fix_type else None,
    )

from copy import deepcopy
from orchestration.state import WorkflowState
from .idea_runtime import IdeaStaticaJointError


def _validate_template_limits(setup: dict) -> dict:
    """Check physical limits independently of summary utilization percentages."""
    prefix = "calculationCommon/Checks/Shared/"
    physical_limits = {"plastic_strain": setup.get(prefix + "LimitPlasticStrain"),
                       "local_deformation": setup.get(prefix + "LocalDeformationLimit")}
    for name, maximum in (("plastic_strain", 0.05), ("local_deformation", 0.03)):
        value = physical_limits[name]
        if type(value) not in (int, float) or not isfinite(value) or value <= 0 or value > maximum:
            raise IdeaStaticaJointError(f"Template {name} limit is missing or exceeds the configured rubric.")
    if setup.get("calculationCommon/Analysis/AnalysisGeneral/Shared/LocalDeformationCheck") is not True:
        raise IdeaStaticaJointError("Template local-deformation check is disabled.")
    return physical_limits


def validate_calculation_check_coverage(items):
    """The supported bolted/welded templates require every enabled check family."""
    names = [str(item.get("name", "")).lower() for item in items if item.get("skipped") is not True]
    required = {
        "analysis": lambda name: name == "analysis",
        "plates": lambda name: "plate" in name and "local" not in name,
        "local deformation": lambda name: "deformation" in name,
        "bolts": lambda name: "bolt" in name,
        "welds": lambda name: "weld" in name,
    }
    missing = [category for category, matches in required.items() if not any(matches(name) for name in names)]
    if missing:
        raise IdeaStaticaJointError(f"Missing/skipped required IDEA checks for the supported template: {missing}")



def _grade_a5_result(state: WorkflowState, result: dict) -> GraderResult:
    grader = grade_joint_check_summary(result.get("check_summary", {}), state.run_policy)
    if result.get("passed") is not True and grader.passed:
        return GraderResult(passed=False, score=0, summary="IDEA did not report overall calculation success; inspect calculation details.")
    return grader


def evaluate_a5(state: WorkflowState, context: dict) -> WorkflowState:
    try:
        __import__('agents.A5_idea_statica.A5A_build_json', fromlist=['_check_a5_request'])._check_a5_request(state, context)
        joint_group, result = context["joint_group"], context["result"]
        grader = _grade_a5_result(state, result)
        state.joint_grader_results[joint_group] = grader
        context["grader"] = grader
        context["evaluated_result"] = deepcopy(result)
        context["evaluated_grader"] = grader.model_copy(deep=True)
        from utils.audit import evaluation
        evaluation(state, 'IDEA_StatiCa', grader, joint_group=joint_group)
        return state
    except Exception as exc:
        return __import__('agents.A5_idea_statica.A5A_build_json', fromlist=['_fail_a5_step'])._fail_a5_step(state, context, exc)


