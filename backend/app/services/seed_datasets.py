"""Canonical definition of the four demo datasets (Section 24)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

FileFormat = Literal["csv", "tsv", "sql"]


@dataclass(slots=True)
class DatasetSpec:
    system_key: str          # A | B | C | D
    label: str
    description: str
    table_name: str
    columns: list[str]
    rows: list[dict[str, Any]]
    filename: str
    formats: tuple[FileFormat, ...] = ("csv", "sql")
    default_format: FileFormat = "csv"
    expected_mapping: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Database A — HR system
# email + full_name + mobile_number
# --------------------------------------------------------------------------- #
HR_ROWS: list[dict[str, Any]] = [
    {"employee_id": "E-1001", "email": "john@example.com", "full_name": "John Smith",
     "mobile_number": "+1-555-010-0100", "department": "Engineering", "hire_date": "2019-04-01"},
    {"employee_id": "E-1002", "email": "jane.doe@example.com", "full_name": "Jane Doe",
     "mobile_number": "+1-555-010-0101", "department": "Finance", "hire_date": "2020-01-15"},
    {"employee_id": "E-1003", "email": "  MARCUS@Example.com ", "full_name": "Marcus Lee",
     "mobile_number": "(555) 010-0102", "department": "Support", "hire_date": "2018-09-23"},
    {"employee_id": "E-1004", "email": "n/a", "full_name": "Priya Raman",
     "mobile_number": "555-010-0103", "department": "Design", "hire_date": "2021-07-30"},
    {"employee_id": "E-1005", "email": "marcus.lee@example.com", "full_name": "Marcus Lee",
     "mobile_number": "+1 555 010 0102", "department": "Support", "hire_date": "2022-02-01"},
    {"employee_id": "E-1006", "email": "sofia.k@example.com", "full_name": "Sofia Kovac",
     "mobile_number": "+44 20 7946 0104", "department": "Legal", "hire_date": "2020-11-11"},
    {"employee_id": "E-1007", "email": "li.wei@example.com", "full_name": "Li Wei",
     "mobile_number": "+86 138 0010 0105", "department": "Engineering", "hire_date": "2017-05-08"},
    # malformed rows: exercise the cleaning engine
    {"employee_id": "E-1008", "email": "not-an-email", "full_name": "Broken Record",
     "mobile_number": "12", "department": "QA", "hire_date": "2023-03-14"},
    {"employee_id": "E-1009", "email": "", "full_name": "",
     "mobile_number": "", "department": "Interns", "hire_date": "2024-06-01"},
]

# --------------------------------------------------------------------------- #
# Database B — Staff Directory
# email_id + name + contact_no + address
# --------------------------------------------------------------------------- #
DIRECTORY_ROWS: list[dict[str, Any]] = [
    {"email_id": "john@example.com", "name": "J. Smith", "contact_no": "555-010-0100",
     "address": "742 Evergreen Terrace, Springfield, IL 62704", "title": "Staff Engineer"},
    {"email_id": "jane.doe@example.com", "name": "Jane Doe", "contact_no": "555.010.0101",
     "address": "1600 Pennsylvania Ave, Washington, DC 20500", "title": "Controller"},
    {"email_id": "Marcus.Lee@Example.com", "name": "Marcus Lee", "contact_no": "1-555-010-0102",
     "address": "1 Infinite Loop, Cupertino, CA 95014", "title": "Support Lead"},
    {"email_id": "sofia.k@example.com", "name": "Sofia Kovac", "contact_no": "+442079460104",
     "address": "10 Downing Street, London SW1A 2AA", "title": "Counsel"},
    {"email_id": "li.wei@example.com", "name": "Wei Li", "contact_no": "+861380010105",
     "address": "1 Tiananmen Sq, Beijing 100000", "title": "Engineer"},
    # deliberate near-miss: same name as John, unrelated identity
    {"email_id": "john.smith.jr@othermail.org", "name": "John Smith", "contact_no": "555-010-0999",
     "address": "9 Elm Street, Newark, NJ 07102", "title": "Intern"},
    {"email_id": "priya.raman@example.com", "name": "Priya Raman", "contact_no": "5550100103",
     "address": "42 Sunset Blvd, Los Angeles, CA 90026", "title": "Designer"},
    # nulls / placeholders
    {"email_id": "null", "name": "N/A", "contact_no": "N/A", "address": "Unknown", "title": "—"},
]

# --------------------------------------------------------------------------- #
# Database C — Startup / Community
# username + phone + company
# --------------------------------------------------------------------------- #
STARTUP_ROWS: list[dict[str, Any]] = [
    {"username": "jdoe_startup", "phone": "001-555-010-0100", "company": "Acme Rocket Labs",
     "role": "CTO", "signup_year": 2021},
    {"username": "marcus_l", "phone": "+1 (555) 010-0102", "company": "Northwind Support",
     "role": "Head of Support", "signup_year": 2020},
    {"username": "skovac", "phone": "020 7946 0104", "company": "Kovac Legal LLP",
     "role": "Partner", "signup_year": 2019},
    {"username": "wei_li", "phone": "861380010105", "company": "Dragon Systems",
     "role": "Principal Engineer", "signup_year": 2022},
    {"username": "priya", "phone": "555.010.0103", "company": "Pixel Forge",
     "role": "Design Lead", "signup_year": 2023},
    # same company as John, different person -> must NOT merge with John
    {"username": "ravi_k", "phone": "555-010-0110", "company": "Acme Rocket Labs",
     "role": "Backend Engineer", "signup_year": 2023},
    {"username": "erin_w", "phone": "555-010-0111", "company": "Acme Rocket Labs",
     "role": "Growth", "signup_year": 2024},
    # handles with formatting noise
    {"username": " @Jane.Doe ", "phone": "555.010.0101", "company": "Doe & Associates",
     "role": "Founder", "signup_year": 2018},
]

# --------------------------------------------------------------------------- #
# Database D — Members Club
# member_id + email + username
# --------------------------------------------------------------------------- #
MEMBERS_ROWS: list[dict[str, Any]] = [
    {"member_id": "MB-9001", "email": "john@example.com", "username": "jdoe_startup",
     "tier": "Platinum", "points": 8420, "joined": "2021-03-15"},
    {"member_id": "MB-9002", "email": "jane.doe@example.com", "username": "Jane.Doe",
     "tier": "Gold", "points": 3110, "joined": "2018-11-02"},
    {"member_id": "MB-9003", "email": "marcus.lee@example.com", "username": "marcus_l",
     "tier": "Silver", "points": 1520, "joined": "2020-08-19"},
    {"member_id": "MB-9004", "email": "sofia.k@example.com", "username": "skovac",
     "tier": "Gold", "points": 2980, "joined": "2019-01-07"},
    {"member_id": "MB-9005", "email": "li.wei@example.com", "username": "wei_li",
     "tier": "Bronze", "points": 640, "joined": "2022-02-28"},
    {"member_id": "MB-9006", "email": "ravi.kumar@example.com", "username": "ravi_k",
     "tier": "Silver", "points": 1890, "joined": "2023-04-17"},
    {"member_id": "MB-9007", "email": "erin.white@example.com", "username": "erin_w",
     "tier": "Bronze", "points": 410, "joined": "2024-01-09"},
    {"member_id": "MB-9008", "email": "priya.raman@example.com", "username": "priya",
     "tier": "Gold", "points": 2760, "joined": "2021-10-05"},
    # entity reachable ONLY via the username from C, not by email
    {"member_id": "MB-9009", "email": "former.alias@outlook.com", "username": "jdoe_startup",
     "tier": "Platinum", "points": 9010, "joined": "2020-06-30"},
    {"member_id": "MB-9010", "email": "broken@@example", "username": "",
     "tier": "Bronze", "points": 0, "joined": ""},
]


DATASETS: list[DatasetSpec] = [
    DatasetSpec(
        system_key="A",
        label="Database A — HR System",
        description="Employee records: email, full name, mobile number.",
        table_name="hr_employees",
        columns=["employee_id", "email", "full_name", "mobile_number", "department", "hire_date"],
        rows=HR_ROWS,
        filename="database_a_hr.csv",
        expected_mapping={
            "email": "email",
            "phone": "mobile_number",
            "name": "full_name",
            "member_id": "employee_id",
        },
    ),
    DatasetSpec(
        system_key="B",
        label="Database B — Staff Directory",
        description="Directory records: email_id, name, contact_no, address.",
        table_name="staff_directory",
        columns=["email_id", "name", "contact_no", "address", "title"],
        rows=DIRECTORY_ROWS,
        filename="database_b_directory.csv",
        expected_mapping={
            "email": "email_id",
            "phone": "contact_no",
            "name": "name",
            "address": "address",
        },
    ),
    DatasetSpec(
        system_key="C",
        label="Database C — Startup Directory",
        description="Community/startup records: username, phone, company.",
        table_name="startup_members",
        columns=["username", "phone", "company", "role", "signup_year"],
        rows=STARTUP_ROWS,
        filename="database_c_startup.csv",
        formats=("csv", "tsv", "sql"),
        expected_mapping={
            "username": "username",
            "phone": "phone",
            "company": "company",
        },
    ),
    DatasetSpec(
        system_key="D",
        label="Database D — Members Club",
        description="Membership records: member_id, email, username.",
        table_name="club_members",
        columns=["member_id", "email", "username", "tier", "points", "joined"],
        rows=MEMBERS_ROWS,
        filename="database_d_members.csv",
        expected_mapping={
            "member_id": "member_id",
            "email": "email",
            "username": "username",
        },
    ),
]

#: the query the demo is built around
DEMO_QUERY = "john@example.com"

#: extra single-file demo, shipped as a MySQL dump to exercise SQL parsing
SQL_DUMP_SPEC = DatasetSpec(
    system_key="E",
    label="Database E — Legacy CRM (SQL dump)",
    description="MySQL dump used to demonstrate SQL dump parsing.",
    table_name="crm_contacts",
    columns=["id", "email", "full_name", "mobile_number", "company", "address"],
    rows=[
        {"id": 1, "email": "john@example.com", "full_name": "Jonathan Smith",
         "mobile_number": "555-010-0100", "company": "Acme Rocket Labs",
         "address": "742 Evergreen Terrace, Springfield"},
        {"id": 2, "email": "jane.doe@example.com", "full_name": "J. Doe",
         "mobile_number": "555-0101", "company": "Doe & Associates",
         "address": "1600 Pennsylvania Ave, Washington"},
        {"id": 3, "email": "o'brien@example.com", "full_name": "Sean O'Brien",
         "mobile_number": "555-0120", "company": "Quinn, Hale & Co.",
         "address": "88 Queen St, Dublin"},
        {"id": 4, "email": "chen.wei@example.com", "full_name": "Chen Wei",
         "mobile_number": "861380010106", "company": "Dragon Systems",
         "address": "1 Tiananmen Sq, Beijing"},
    ],
    filename="database_e_crm.sql",
    formats=("sql",),
    default_format="sql",
    expected_mapping={
        "email": "email",
        "name": "full_name",
        "phone": "mobile_number",
        "company": "company",
        "address": "address",
        "member_id": "id",
    },
)

ALL_SEED_SPECS: list[DatasetSpec] = [*DATASETS, SQL_DUMP_SPEC]
