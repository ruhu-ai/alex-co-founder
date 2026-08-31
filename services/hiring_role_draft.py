"""Closed builder for founder-authored, internal-only Hiring role drafts."""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta
from typing import Any

from services.hiring_contracts import RoleContract, jurisdiction_policy_id

_PROHIBITED = re.compile(
    r"\b(?:culture\s*fit|personality|race|ethnicity|religion|gender|sex|age|"
    r"disability|marital|pregnan|nationality|school prestige|employer prestige)\b",
    re.I,
)
_PROHIBITED_COPY_PHRASE = re.compile(
    r"\b(?:culture\s*fit|school prestige|employer prestige|preferred nationality|"
    r"must be under age|must be over age)\b", re.I)

_DEFAULT_HIRING_PROCESS = [
    "Application review against the role's job-related criteria",
    "Structured interviews using the reviewed scorecard and interview plan",
    "A final human decision by the Founder",
]
_REQUIRED_DESCRIPTION_FIELDS = {
    "purpose": "role purpose",
    "responsibilities": "responsibilities",
    "required_qualifications": "required qualifications",
    "relevant_experience": "relevant experience",
    "location": "location",
    "work_arrangement": "work arrangement",
    "employment_type": "employment type",
    "hiring_process": "hiring process",
    "application_instructions": "application instructions",
}
_COMPENSATION_NOT_SUPPLIED = (
    "Not supplied — discuss with the Founder before publication")


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error": True, "error_code": code,
            "message": message, **extra}


def _clean_list(values: list[str] | None, *, limit: int = 12) -> list[str]:
    """Return bounded, whitespace-normalized, de-duplicated draft facts."""
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = re.sub(r"\s+", " ", str(value or "")).strip()
        if not item or item.casefold() in seen:
            continue
        seen.add(item.casefold())
        cleaned.append(item[:500])
        if len(cleaned) == limit:
            break
    return cleaned


def derive_role_title(description: str, *, company_name: str) -> str:
    """Extract the Founder-supplied title without borrowing demo-role facts."""
    normalized = re.sub(r"\s+", " ", str(description or "")).strip()
    # Natural chat commands often introduce the title with a creation request.
    # Remove only the exact company-bound preamble; the following text remains
    # Founder-authored and is still validated by the normal title contract.
    normalized = re.sub(
        rf"^(?:/hiring\s+)?(?:please\s+)?create\s+(?:a\s+)?(?:new\s+)?"
        rf"role\s+(?:for|at)\s+{re.escape(company_name)}"
        rf"(?:\s*,?\s*inc\.?)?\s*:\s*",
        "", normalized, flags=re.I).strip()
    title = re.split(r"[,.;:]", normalized, maxsplit=1)[0].strip()
    title = re.sub(
        r"^(?:/hiring\s+|hire\s+|hiring\s+|we need\s+|we are hiring\s+|"
        r"looking for\s+|a\s+|an\s+)", "", title, flags=re.I).strip()
    title = re.sub(
        rf"\s+for\s+{re.escape(company_name)}(?:\s*,?\s*inc\.?)?$",
        "", title, flags=re.I).strip()
    # Preserve the Founder's title while normalizing one common compound form.
    title = re.sub(r"\bfull-stack\b", "Full-Stack", title, flags=re.I)
    return title


def role_description_missing_fields(description: dict[str, Any] | None) -> list[str]:
    """Return candidate-facing fields that must be complete before approval.

    Optional compensation, benefits and fair-process wording are omitted from
    the public projection when absent. Required fields never receive invented
    fallback copy.
    """
    source = dict(description or {})
    missing: list[str] = []
    list_fields = {
        "responsibilities", "required_qualifications",
        "preferred_qualifications", "relevant_experience", "hiring_process",
    }
    for key, label in _REQUIRED_DESCRIPTION_FIELDS.items():
        value = source.get(key)
        if key in list_fields:
            present = bool(_clean_list(value if isinstance(value, list) else []))
        else:
            present = bool(str(value or "").strip())
        if not present:
            missing.append(label)
    return missing


def validate_role_description(
        description: dict[str, Any] | None,
        contract: RoleContract) -> dict[str, Any]:
    """Validate that a structured draft is complete and bound to its contract."""
    source = dict(description or {})
    missing = role_description_missing_fields(source)
    if missing:
        return {"status": "needs_information", "error": True,
                "error_code": "role_description_incomplete",
                "message": "Complete the job description before approval.",
                "missing_fields": missing}
    if str(source.get("candidate_facing_job_post") or "").strip() != \
            contract.public_job_description:
        return _error(
            "role_description_mismatch",
            "The candidate-facing draft does not match the exact policy content.")
    return {"status": "success", "role_description": source}


def _bullet_section(title: str, values: list[str]) -> str:
    return f"## {title}\n" + "\n".join(f"- {value}" for value in values)


def _render_candidate_post(
        *, company_name: str, role_title: str, role_summary: str,
        success_outcomes: list[str], responsibilities: list[str],
        required_criteria: list[str], preferred_criteria: list[str],
        relevant_experience: list[str], location: str,
        work_arrangement: str, employment_type: str,
        compensation_envelope: str, benefits: list[str],
        hiring_process: list[str], founder_post_notes: str,
        application_instructions: str,
        equal_opportunity_statement: str,
        accessibility_statement: str) -> str:
    """Build the exact candidate-facing copy from supplied, reviewable facts."""
    sections = [
        f"# {role_title} at {company_name}",
        f"## Role overview\n{role_summary}",
    ]
    if founder_post_notes and founder_post_notes.casefold() != role_summary.casefold():
        sections.append(f"## Additional role context\n{founder_post_notes}")
    sections.extend([
        _bullet_section("What success looks like", success_outcomes),
        _bullet_section("Responsibilities", responsibilities),
        _bullet_section("Must-have qualifications", required_criteria),
    ])
    if preferred_criteria:
        sections.append(_bullet_section(
            "Preferred qualifications", preferred_criteria))
    sections.append(_bullet_section(
        "Relevant experience", relevant_experience))
    sections.append(
        "## Working model\n"
        f"- Location: {location}\n"
        f"- Work arrangement: {work_arrangement}\n"
        f"- Employment type: {employment_type}")
    compensation_lines = []
    if compensation_envelope:
        compensation_lines.append(f"Compensation: {compensation_envelope}")
    compensation_lines.extend(benefits)
    if compensation_lines:
        sections.append(_bullet_section(
            "Compensation and benefits", compensation_lines))
    sections.append(_bullet_section("Hiring process", hiring_process))
    sections.append(
        "## How to apply\n" + (
            application_instructions
            or "Application instructions will be added by the Founder before publication."))
    process_commitments = [
        statement for statement in (
            equal_opportunity_statement, accessibility_statement)
        if statement
    ]
    if process_commitments:
        sections.append(
            "## Fair and accessible process\n" + "\n\n".join(process_commitments))
    return "\n\n".join(sections).strip()


def build_contract(
        *, company_name: str, role_title: str, role_summary: str,
        headcount_target: int, target_date: str, location: str,
        work_arrangement: str, compensation_envelope: str,
        required_criteria: list[str], public_job_description: str,
        employment_type: str,
        responsibilities: list[str] | None = None,
        success_outcomes: list[str] | None = None,
        preferred_criteria: list[str] | None = None,
        relevant_experience: list[str] | None = None,
        benefits: list[str] | None = None,
        hiring_process: list[str] | None = None,
        application_instructions: str = "",
        equal_opportunity_statement: str = "",
        accessibility_statement: str = "") -> dict[str, Any]:
    """Validate founder facts and deterministically build review materials."""
    strings = {
        "company": str(company_name or "").strip(),
        "role": str(role_title or "").strip(),
        "summary": str(role_summary or "").strip(),
        "date": str(target_date or "").strip(),
        "location": str(location or "").strip(),
        "work arrangement": str(work_arrangement or "").strip(),
        "employment type": str(employment_type or "").strip(),
        "compensation": str(compensation_envelope or "").strip(),
        "job post notes": str(public_job_description or "").strip(),
        "application instructions": str(application_instructions or "").strip(),
        "equal opportunity statement": str(
            equal_opportunity_statement or "").strip(),
        "accessibility statement": str(accessibility_statement or "").strip(),
    }
    required_string_labels = (
        "company", "role", "summary", "date", "location",
        "work arrangement", "employment type")
    missing = [label for label in required_string_labels if not strings[label]]
    criteria = _clean_list(required_criteria)
    responsibility_lines = _clean_list(responsibilities)
    outcome_lines = _clean_list(success_outcomes)
    preferred_lines = _clean_list(preferred_criteria)
    experience_lines = _clean_list(relevant_experience)
    benefit_lines = _clean_list(benefits)
    process_lines = _clean_list(hiring_process) or list(_DEFAULT_HIRING_PROCESS)
    if not criteria:
        missing.append("job-related evidence criteria")
    if not responsibility_lines:
        missing.append("candidate-facing responsibilities")
    if not outcome_lines:
        missing.append("role success outcomes")
    if not experience_lines:
        missing.append("relevant experience expectations")
    if not strings["application instructions"]:
        missing.append("application instructions")
    # The strict protected-attribute guard applies to selection material. A
    # whole-post word scan rejects valid technical/accessibility prose such as
    # "race condition", "age-gating", or "users with a disability".
    selection_text = " ".join((*criteria, *preferred_lines, *experience_lines))
    if _PROHIBITED.search(selection_text):
        return _error(
            "prohibited_hiring_criterion",
            "The role brief contains a prohibited attribute or proxy. Use only "
            "job-related evidence criteria.")
    candidate_copy = " ".join((
        strings["summary"], strings["job post notes"],
        *responsibility_lines, *outcome_lines, *process_lines))
    if _PROHIBITED_COPY_PHRASE.search(candidate_copy):
        return _error(
            "prohibited_hiring_criterion",
            "The role brief contains prohibited hiring language. Use only "
            "job-related evidence criteria.")
    if missing:
        return {"status": "needs_information", "missing_fields": missing,
                "created": False}
    try:
        parsed_target = date.fromisoformat(strings["date"])
    except ValueError:
        return _error("role_brief_invalid", "Target date must be YYYY-MM-DD.")
    if parsed_target < date.today():
        return _error("role_brief_invalid", "Target date cannot be in the past.")
    if not isinstance(headcount_target, int) or not 1 <= headcount_target <= 50:
        return _error("role_brief_invalid", "Headcount must be between 1 and 50.")
    criterion_rows = []
    question_rows = []
    for index, label in enumerate(criteria, start=1):
        digest = hashlib.sha256(label.casefold().encode()).hexdigest()[:12]
        criterion_id = f"criterion_{index}_{digest}"
        question_id = f"question_{index}_{digest}"
        criterion_rows.append({
            "criterion_id": criterion_id,
            "label": label[:160],
            "description": (
                f"Job-related evidence demonstrating {label.rstrip('.')}.")[:1000],
            "evidence_examples": [
                "A concrete example, the person's direct contribution, and the outcome"],
            "approved_question_ids": [question_id],
        })
        question_rows.append({
            "question_id": question_id,
            "criterion_id": criterion_id,
            "text": f"Describe a concrete example that demonstrates {label.rstrip('.')}.",
            "rubric": ["Concrete scope", "Direct contribution", "Outcome evidence"],
        })

    candidate_post = _render_candidate_post(
        company_name=strings["company"], role_title=strings["role"],
        role_summary=strings["summary"], success_outcomes=outcome_lines,
        responsibilities=responsibility_lines, required_criteria=criteria,
        preferred_criteria=preferred_lines,
        relevant_experience=experience_lines, location=strings["location"],
        work_arrangement=strings["work arrangement"],
        employment_type=strings["employment type"],
        compensation_envelope=strings["compensation"], benefits=benefit_lines,
        hiring_process=process_lines,
        founder_post_notes=strings["job post notes"],
        application_instructions=strings["application instructions"],
        equal_opportunity_statement=strings["equal opportunity statement"],
        accessibility_statement=strings["accessibility statement"],
    )
    try:
        contract = RoleContract.model_validate({
            "schema_version": 1,
            "company_name": strings["company"],
            "role_title": strings["role"],
            "role_summary": strings["summary"][:3000],
            "headcount_target": headcount_target,
            "target_date": strings["date"],
            "location_envelope": [strings["location"], strings["work arrangement"]],
            "compensation_envelope": (
                strings["compensation"] or _COMPENSATION_NOT_SUPPLIED),
            "criteria": criterion_rows,
            "interview_plan": question_rows,
            "public_job_description": candidate_post,
            "approved_reason_codes": [
                "CRITERION_EVIDENCE_SUFFICIENT",
                "MORE_JOB_EVIDENCE_REQUIRED",
            ],
            "prohibited_criteria": [
                "Protected characteristics", "Culture fit", "Prestige proxies"],
            "notice_policy_id": "notice_founder_draft_v1",
            "retention_policy_id": "retention_unconfigured_draft_v1",
            "jurisdiction_policy_id": jurisdiction_policy_id(strings["location"]),
        })
    except ValueError as exc:
        return _error("role_brief_invalid", str(exc)[:400])
    return {
        "status": "success",
        "contract": contract,
        "role_description": {
            "purpose": strings["summary"],
            "responsibilities": responsibility_lines,
            "required_qualifications": criteria,
            "preferred_qualifications": preferred_lines,
            "relevant_experience": experience_lines,
            "success_outcomes": outcome_lines,
            "location": strings["location"],
            "work_arrangement": strings["work arrangement"],
            "employment_type": strings["employment type"],
            "compensation": strings["compensation"],
            "benefits": benefit_lines,
            "hiring_process": process_lines,
            "application_instructions": strings["application instructions"],
            "equal_opportunity_statement": strings[
                "equal opportunity statement"],
            "accessibility_statement": strings["accessibility statement"],
            "candidate_facing_job_post": candidate_post,
            "honest_unknowns": [
                label for label, value in (
                    ("Compensation", strings["compensation"]),
                    ("Benefits", benefit_lines),
                    ("Application instructions", strings[
                        "application instructions"]),
                    ("Equal-opportunity wording", strings[
                        "equal opportunity statement"]),
                    ("Accessibility wording", strings[
                        "accessibility statement"]),
                ) if not value
            ],
        },
    }


def ruhu_fde_package() -> dict[str, Any]:
    """Optional reviewed synthetic demo fixture; never normal `/hiring` input."""
    result = build_contract(
        company_name="Ruhu",
        role_title="Forward Deployment Engineer",
        role_summary=(
            "Lead secure customer deployments from discovery through durable "
            "production outcomes, including incident learning."),
        headcount_target=1,
        target_date="2027-01-30",
        location="Nigeria",
        work_arrangement="Remote",
        employment_type="Full-time employee",
        # No compensation fact is supplied by the command itself. Keep the
        # internal contract honest and omit compensation from candidate copy
        # until the Founder provides a reviewed value.
        compensation_envelope="",
        required_criteria=[
            "Customer deployment delivery",
            "Constructive production incident response",
        ],
        preferred_criteria=[
            "Experience working directly with enterprise or regulated customers",
            "Experience improving deployment playbooks or operational tooling",
        ],
        relevant_experience=[
            "Hands-on ownership of at least one customer-facing production deployment",
            "Direct participation in incident response and post-incident follow-through",
        ],
        responsibilities=[
            "Own customer deployment delivery from discovery through launch",
            "Translate customer goals and constraints into a bounded deployment plan",
            "Coordinate secure integration, validation, launch, and handover",
            "Communicate delivery risks, dependencies, and decisions clearly",
            "Coordinate incident response and document learning",
            "Turn repeat deployment work into reusable internal playbooks",
        ],
        success_outcomes=[
            "Customer deployments reach production with agreed scope and accountable owners",
            "Launch risks and incidents are surfaced early and followed through visibly",
            "Deployment learning improves the next customer implementation",
        ],
        benefits=[],
        hiring_process=[
            "Application review against the job-related criteria in this post",
            "A structured conversation about deployment and incident examples",
            "A practical role discussion using the same reviewed scorecard",
            "A final human decision by the Founder",
        ],
        application_instructions=(
            "Apply through the role-specific application form or role email "
            "shown on this page after the Founder publishes the approved role."),
        accessibility_statement=(
            "Candidates may request an adjustment for any stage of the hiring "
            "process; the request will not be used as a hiring criterion."),
        public_job_description=(
            "Help customers move from a well-defined need to a secure, durable "
            "production deployment while improving how Ruhu delivers the next one."),
    )
    if result.get("error") or result.get("status") != "success":
        raise RuntimeError(str(result.get("message") or "invalid FDE draft"))
    return result


def founder_description_package(
        description: str, *, company_name: str, location: str,
        work_arrangement: str, employment_type: str,
        headcount_target: int = 1) -> dict[str, Any]:
    """Compile an explicitly degraded local-only role draft.

    Normal `/hiring` uses the grounded model writer. This conservative compiler
    remains available only behind the explicit local fallback flag and in unit
    tests; it never borrows the synthetic FDE fixture or grants authority.
    """
    normalized = re.sub(r"\s+", " ", str(description or "")).strip()
    if len(normalized) < 12:
        return {"status": "needs_information", "error": True,
                "error_code": "role_description_too_short",
                "message": "Describe the role title and the job-related experience needed."}
    title_fragment = derive_role_title(normalized, company_name=company_name)
    if not title_fragment or len(title_fragment) > 160:
        return {"status": "needs_information", "error": True,
                "error_code": "role_title_missing",
                "message": "Start the description with the exact role title."}

    clauses = [item.strip(" .") for item in re.split(
        r"[,;]|\b(?:and|who)\b", normalized, flags=re.I) if item.strip(" .")]
    required: list[str] = []
    responsibilities: list[str] = []
    for clause in clauses[1:]:
        lowered = clause.casefold()
        if any(marker in lowered for marker in (
                "experience", "strong in", "proficient", "expert", "skilled")):
            required.append(clause[0].upper() + clause[1:])
        if any(marker in lowered for marker in (
                "own ", "work directly", "deliver", "build ", "make ",
                "lead ", "manage ", "design ", "operate ")):
            responsibilities.append(clause[0].upper() + clause[1:])
    required = _clean_list(required) or [
        f"Demonstrated job-related experience for the {title_fragment} role"]
    responsibilities = _clean_list(responsibilities) or [
        f"Own the core responsibilities of the {title_fragment} role from plan to delivery"]
    outcomes = _clean_list([
        ("Deliver " + item[0].lower() + item[1:]).rstrip(".")
        for item in responsibilities[:6]
    ])
    relevant = _clean_list(required)
    target_date = (date.today() + timedelta(days=120)).isoformat()
    result = build_contract(
        company_name=company_name, role_title=title_fragment,
        role_summary=normalized, headcount_target=headcount_target,
        target_date=target_date, location=location,
        work_arrangement=work_arrangement,
        employment_type=employment_type, compensation_envelope="",
        required_criteria=required, preferred_criteria=[],
        relevant_experience=relevant, responsibilities=responsibilities,
        success_outcomes=outcomes,
        application_instructions=(
            "Apply through this role page with a current PDF or DOCX resume."),
        public_job_description=normalized,
    )
    if result.get("status") == "success":
        result["assumptions"] = {
            "company_name": company_name, "location": location,
            "work_arrangement": work_arrangement,
            "employment_type": employment_type,
            "headcount_target": headcount_target, "target_date": target_date,
        }
    return result


def ruhu_fde_contract() -> RoleContract:
    """Compatibility accessor for callers that need only the contract."""
    return ruhu_fde_package()["contract"]
