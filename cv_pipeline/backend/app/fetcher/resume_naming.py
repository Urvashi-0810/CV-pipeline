import re
from pathlib import Path


POSITIONS = [
    "accountant",
    "mechanical engineer",
    "mechanical",
    "civil engineer",
    "civil",
    "electrician",
    "plumber",
    "welder",
    "driver",
    "technician",
    "supervisor",
    "helper",
    "manager",
    "chef",
    "nurse",
    "cook",
    "mason",
    "carpenter",
    "quantity surveyor",
    "safety officer",
    "hse officer",
    "hr officer",
    "storekeeper",
    "warehouse assistant",
    "sales executive",
]


def safe_part(value: str) -> str:
    """Make a filename component safe for Windows."""
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "_", value.strip())
    value = re.sub(r"_+", "_", value)
    return value.strip(" ._")


def detect_position(text: str) -> str:
    """Detect a job title from resume text."""
    normalized = re.sub(r"\s+", " ", text.lower())

    # Prefer the longer job titles before shorter ones.
    for position in sorted(POSITIONS, key=len, reverse=True):
        if re.search(
            r"(?<![a-z])" + re.escape(position) + r"(?![a-z])",
            normalized,
        ):
            return position.title()

    return "Position_Unknown"


def build_resume_filename(
    position: str,
    candidate_name: str,
    passport_number: str,
) -> str:
    """Create Position_Name_Passport.pdf."""
    position = safe_part(position or "Position_Unknown")
    passport_number = safe_part(passport_number or "Passport_Unknown")
    candidate_name = safe_part(candidate_name or "Candidate_Unknown")

    return f"{position}_{passport_number}_{candidate_name}.pdf"