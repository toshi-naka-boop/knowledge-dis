"""Seed data generator for knowledge-discovery (Milestone 2).

Follows design.md §3 and seed-spec.md:
- Setting: Meridian Care Partners Group (~400 employees).
- 4 fully implemented personas registered in agents collection (active=True):
  1. Rachel Kim (Senior Account Manager, Healthcare Staffing Division)
  2. Marcus Delgado (Commercial Broker, Healthcare Real Estate Division)
  3. Elena Vasquez (Transition Advisor, Practice Transition Support) - with private item
  4. Tom Whitfield (Senior Accountant, Corporate Services) - designed to drop
- 396 synthetic employee profiles matching department distribution:
  - Healthcare Staffing: 40% (~158)
  - Real Estate: 15% (~59)
  - Transition Advisory: 10% (~40)
  - Corporate Services (Accounting, HR, IT, Legal): 25% (~99)
  - Executive / Operations: 10% (~40)
- Includes intentional overlaps for Demo Questions 1 & 2 to showcase the Screen Funnel (top 20).
- Fully idempotent with deterministic employee_id and agent_id keys.
- Supports --dry-run for inspection without network/Firestore calls.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import os
import sys
from typing import Any

from knowledge_discovery.matching import DeterministicEmbedder, Embedder
from knowledge_discovery.models import (
    Agent,
    MailSeed,
    Profile,
    ProfileItem,
    Schedule,
    Task,
    utc_now_iso,
)
from knowledge_discovery.store import Store


# -----------------------------------------------------------------------------
# 4 Fully Implemented Personas (Fixed Seeds)
# -----------------------------------------------------------------------------

FIXED_PERSONAS_DATA = [
    {
        "agent_id": "agent_rachel_kim",
        "employee_id": "emp_rachel_kim",
        "display_name": "Rachel Kim",
        "role": "Senior Account Manager, Healthcare Staffing Division",
        "supported_intents": ["connect_ask", "connect_ask_private", "no_connection"],
        "endpoint": "agent://rachel_kim",
        "items": [
            {
                "key": "current_work",
                "body": (
                    "Manages staffing accounts for 30+ hospital and clinic clients across the metro area. "
                    "Handles nurse and allied-health placement contracts, client escalations, and renewal negotiations. "
                    "Longest-tenured account manager on the medical corporation side; keeps informal notes on each "
                    "client's decision-makers and hiring history."
                ),
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
            {
                "key": "expertise",
                "body": (
                    "Client relationship history: which medical groups changed hiring policy after ownership changes, "
                    "which facilities had early-turnover disputes, how each director of nursing prefers candidates presented."
                ),
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
            {
                "key": "background",
                "body": "8 years in healthcare staffing; started as a recruiter before moving to account management.",
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
        ],
    },
    {
        "agent_id": "agent_marcus_delgado",
        "employee_id": "emp_marcus_delgado",
        "display_name": "Marcus Delgado",
        "role": "Commercial Broker, Healthcare Real Estate Division",
        "supported_intents": ["connect_ask", "connect_ask_private", "no_connection"],
        "endpoint": "agent://marcus_delgado",
        "items": [
            {
                "key": "current_work",
                "body": (
                    "Brokers medical office buildings and clinic sites. Currently working two ambulatory-surgery-center "
                    "site searches; tracks zoning, parking-ratio, and ADA requirements that medical tenants hit during "
                    "relocation. Maintains a private list of off-market properties suitable for healthcare use."
                ),
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
            {
                "key": "expertise",
                "body": (
                    "Knows which sites can physically and legally host a clinic: zoning categories, conversion costs "
                    "from retail to medical use, landlord attitudes toward medical tenants."
                ),
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
            {
                "key": "background",
                "body": "Former hospital facilities coordinator; moved to brokerage 6 years ago.",
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
        ],
    },
    {
        "agent_id": "agent_elena_vasquez",
        "employee_id": "emp_elena_vasquez",
        "display_name": "Elena Vasquez",
        "role": "Transition Advisor, Practice Transition (M&A) Support",
        "supported_intents": ["connect_ask", "connect_ask_private", "no_connection"],
        "endpoint": "agent://elena_vasquez",
        "items": [
            {
                "key": "current_work",
                "body": (
                    "Advises independent physician practices on succession planning: valuation prep, buyer search, "
                    "and post-transition staffing continuity."
                ),
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
            {
                "key": "expertise",
                "body": (
                    "Practice succession patterns: when owners start considering exit, what kills deals late, "
                    "how staffing contracts transfer."
                ),
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
            {
                "key": "transition_pipeline",
                "body": (
                    "Currently advising two unannounced clinic succession deals, including one whose owner is also "
                    "exploring relocation before sale. Details under NDA."
                ),
                "source": "seed_fixed",
                "visibility": "private",  # Strictly private knowledge item
                "reviewed": True,
            },
            {
                "key": "background",
                "body": "CPA background; 5 years in healthcare M&A advisory.",
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
        ],
    },
    {
        "agent_id": "agent_tom_whitfield",
        "employee_id": "emp_tom_whitfield",
        "display_name": "Tom Whitfield",
        "role": "Senior Accountant, Corporate Services",
        "supported_intents": ["connect_ask", "connect_ask_private", "no_connection"],
        "endpoint": "agent://tom_whitfield",
        "items": [
            {
                "key": "current_work",
                "body": (
                    "Prepares consolidated monthly closes, quarterly financial statements, and tax filings for "
                    "the group's entities. Coordinates the annual external audit."
                ),
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
            {
                "key": "expertise",
                "body": "GAAP reporting, intercompany reconciliation, audit documentation.",
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
            {
                "key": "background",
                "body": "12 years in corporate accounting.",
                "source": "seed_fixed",
                "visibility": "public",
                "reviewed": True,
            },
        ],
    },
]


def build_fixed_personas() -> tuple[list[Agent], list[Profile]]:
    """Build the 4 fixed agents and their corresponding profiles."""
    agents: list[Agent] = []
    profiles: list[Profile] = []

    for item in FIXED_PERSONAS_DATA:
        agent = Agent(
            agent_id=item["agent_id"],
            employee_id=item["employee_id"],
            display_name=item["display_name"],
            supported_intents=list(item["supported_intents"]),
            endpoint=item["endpoint"],
            active=True,
        )
        agents.append(agent)

        profile_items = [
            ProfileItem(
                key=pi["key"],
                body=pi["body"],
                source=pi["source"],
                visibility=pi["visibility"],
                reviewed=pi["reviewed"],
            )
            for pi in item["items"]
        ]

        profile = Profile(
            employee_id=item["employee_id"],
            name=item["display_name"],
            role=item["role"],
            items=profile_items,
        )
        profiles.append(profile)

    return agents, profiles


# -----------------------------------------------------------------------------
# Synthetic 396 Profiles Generator
# -----------------------------------------------------------------------------

FIRST_NAMES = [
    "Alexander", "Sophia", "Liam", "Olivia", "Noah", "Emma", "Ethan", "Ava",
    "Mason", "Isabella", "Lucas", "Mia", "Oliver", "Harper", "Elijah", "Evelyn",
    "Aiden", "Abigail", "James", "Emily", "Benjamin", "Charlotte", "Sebastian", "Amelia",
    "Jackson", "Ella", "Daniel", "Madison", "Matthew", "Scarlett", "Henry", "Victoria",
    "Joseph", "Aria", "Samuel", "Grace", "David", "Chloe", "Carter", "Penelope",
    "Wyatt", "Riley", "Jayden", "Zoey", "John", "Nora", "Owen", "Lily",
    "Dylan", "Eleanor", "Luke", "Hannah", "Gabriel", "Lillian", "Anthony", "Addison",
    "Isaac", "Aubrey", "Grayson", "Ellie", "Jack", "Stella", "Julian", "Natalie",
    "Levi", "Zoe", "Christopher", "Leah", "Joshua", "Hazel", "Andrew", "Violet",
    "Lincoln", "Aurora", "Mateo", "Savannah", "Ryan", "Audrey", "Jaxon", "Brooklyn",
    "Nathan", "Bella", "Aaron", "Claire", "Isaiah", "Skylar", "Thomas", "Lucy",
    "Charles", "Paisley", "Caleb", "Everly", "Josiah", "Anna", "Christian", "Caroline",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas",
    "Taylor", "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White",
    "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young",
    "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Gomez", "Phillips", "Evans", "Turner", "Diaz", "Parker",
    "Cruz", "Edwards", "Collins", "Reyes", "Stewart", "Morris", "Morales", "Murphy",
    "Cook", "Rogers", "Gutierrez", "Ortiz", "Morgan", "Cooper", "Peterson", "Bailey",
    "Reed", "Kelly", "Howard", "Ramos", "Kim", "Cox", "Ward", "Richardson",
    "Watson", "Brooks", "Chavez", "Wood", "James", "Bennett", "Gray", "Mendoza",
    "Ruiz", "Hughes", "Price", "Alvarez", "Castillo", "Sanders", "Patel", "Myers",
]

# Department distribution targets: total 396 synthetic
# Healthcare Staffing: 158 (40%)
# Real Estate: 59 (15%)
# Transition Advisory: 40 (10%)
# Corporate Services: 99 (25%)
# Executive & Operations: 40 (10%)

STAFFING_ROLES = [
    "Nurse Placement Specialist",
    "Allied Health Recruiter",
    "Hospital Staffing Coordinator",
    "Clinical Talent Acquisition Partner",
    "Physician Credentialing Manager",
    "Travel Nurse Program Lead",
    "Healthcare Contract Negotiator",
    "Staffing Operations Specialist",
]

REAL_ESTATE_ROLES = [
    "Healthcare Property Analyst",
    "Medical Facility Leasing Agent",
    "Ambulatory Site Consultant",
    "Clinical Space Acquisition Specialist",
    "Healthcare Real Estate Project Manager",
    "Zoning & Permitting Coordinator",
]

TRANSITION_ROLES = [
    "Practice Valuation Associate",
    "Physician Practice Succession Consultant",
    "Medical Group M&A Analyst",
    "Practice Transition Coordinator",
    "Healthcare Acquisition Due Diligence Specialist",
]

CORPORATE_ROLES = [
    "Financial Analyst",
    "HR Business Partner",
    "Healthcare Compliance Officer",
    "IT Systems Administrator",
    "Legal Counsel - Healthcare Regulatory",
    "Payroll & Benefits Administrator",
    "Internal Audit Specialist",
    "Enterprise Systems Engineer",
]

EXECUTIVE_ROLES = [
    "Director of Regional Staffing",
    "VP of Business Development",
    "Director of Real Estate Advisory",
    "Operations Director",
    "Strategic Growth Manager",
]


def generate_synthetic_profiles(count: int = 396) -> list[Profile]:
    """Generate 396 high quality synthetic employee profiles deterministically."""
    profiles: list[Profile] = []

    # Distribution counts
    staffing_count = 158
    real_estate_count = 59
    transition_count = 40
    corporate_count = 99
    exec_count = count - (staffing_count + real_estate_count + transition_count + corporate_count)  # 40

    dept_assignments: list[tuple[str, list[str]]] = (
        [("staffing", STAFFING_ROLES)] * staffing_count
        + [("real_estate", REAL_ESTATE_ROLES)] * real_estate_count
        + [("transition", TRANSITION_ROLES)] * transition_count
        + [("corporate", CORPORATE_ROLES)] * corporate_count
        + [("executive", EXECUTIVE_ROLES)] * exec_count
    )

    dept_local_counters: dict[str, int] = {}
    for idx, (dept, role_list) in enumerate(dept_assignments, start=1):
        emp_id = f"emp_synth_{idx:03d}"
        f_name = FIRST_NAMES[(idx * 7) % len(FIRST_NAMES)]
        l_name = LAST_NAMES[(idx * 13) % len(LAST_NAMES)]
        full_name = f"{f_name} {l_name}"
        role = role_list[idx % len(role_list)]
        # E-3: overlap branches must key on the department-LOCAL index — the
        # global idx never satisfies `<= 5` for departments that start at 159+
        local_idx = dept_local_counters.get(dept, 0) + 1
        dept_local_counters[dept] = local_idx

        # Intentional overlap design:
        # First 5 in real_estate: strong overlap with Demo 1 (medical clinic relocation & zoning)
        # First 5 in transition: strong overlap with Demo 2 (practice retirement & succession)
        if dept == "real_estate" and local_idx <= 5:
            current_work = (
                f"Coordinates site acquisition and municipal zoning clearances and permits for outpatient medical facilities. "
                f"Evaluates parking ratio compliance, ADA access, and plumbing infrastructure conversions for clinic tenants. "
                f"Assists regional health networks with complex clinic relocation planning."
            )
            expertise = "Medical zoning codes, retail-to-clinic adaptive reuse, healthcare facility tenant representation."
            background = f"Experienced real estate specialist with {4 + (idx % 6)} years in commercial healthcare properties."
        elif dept == "transition" and local_idx <= 5:
            current_work = (
                f"Assists retiring physicians with practice valuations, succession timelines, and buyer matchmaking. "
                f"Structures goodwill asset transfers and patient chart continuity for private clinical practices. "
                f"Coordinates transition staffing retainers during practice leadership handovers."
            )
            expertise = "Physician retirement succession, practice valuation metrics, medical practice asset sales."
            background = f"Former healthcare practice business manager with {5 + (idx % 5)} years in medical M&A advisory."
        elif dept == "staffing":
            current_work = (
                f"Manages clinical staffing placements and scheduling for regional healthcare networks. "
                f"Liaises with hospital department heads to fulfill urgent nursing and allied health staffing requests. "
                f"Monitors contract compliance and clinician credentialing timelines."
            )
            expertise = "Allied health placement, nursing registry management, acute care staffing contracts."
            background = f"Healthcare staffing professional with {3 + (idx % 8)} years of industry experience."
        elif dept == "real_estate":
            current_work = (
                f"Analyzes commercial real estate lease agreements and property listings for healthcare tenants. "
                f"Prepares market comparison reports for medical office buildings and clinical suites. "
                f"Coordinates site visits and lease negotiations between healthcare groups and property owners."
            )
            expertise = "Commercial lease negotiation, medical office market analysis, tenant improvement allowances."
            background = f"Commercial real estate advisor with {2 + (idx % 7)} years specializing in healthcare assets."
        elif dept == "transition":
            current_work = (
                f"Supports financial modeling and due diligence reviews for medical practice acquisitions. "
                f"Coordinates confidential information memorandums and buyer NDA tracking for clinic owners. "
                f"Analyzes practice operational metrics to prepare seller readiness assessments."
            )
            expertise = "Practice financial valuation, confidential seller representation, clinical M&A due diligence."
            background = f"Financial analyst with {3 + (idx % 6)} years in healthcare transaction advisory."
        elif dept == "corporate":
            current_work = (
                f"Administers corporate business operations, compliance audits, and internal workflows. "
                f"Maintains enterprise systems and ensures adherence to state and federal healthcare regulations. "
                f"Prepares documentation for cross-department reporting and organizational oversight."
            )
            expertise = "Enterprise administrative workflows, healthcare regulatory compliance, organizational operations."
            background = f"Corporate operations professional with {4 + (idx % 10)} years in healthcare administration."
        else:  # executive
            current_work = (
                f"Directs group-wide strategic initiatives and cross-divisional partnership development. "
                f"Oversees operational performance metrics across staffing, real estate, and advisory units. "
                f"Aligns group resources to support enterprise growth across metropolitan healthcare markets."
            )
            expertise = "Healthcare enterprise strategy, executive leadership, inter-departmental synergy development."
            background = f"Senior healthcare executive with {10 + (idx % 8)} years in healthcare management."

        # E-3 (clone mitigation): weave the role and a rotating focus phrase
        # into the generic body so profiles are not verbatim department clones
        _focus = [
            "Primary focus: multi-site client portfolios.",
            "Primary focus: rural and community providers.",
            "Primary focus: large metropolitan health systems.",
            "Primary focus: fast-turnaround urgent engagements.",
        ][local_idx % 4]
        current_work = f"Serves as {role}. " + current_work + " " + _focus

        items = [
            ProfileItem(
                key="current_work",
                body=current_work,
                source="seed_synth",
                visibility="public",
                reviewed=False,
            ),
            ProfileItem(
                key="expertise",
                body=expertise,
                source="seed_synth",
                visibility="public",
                reviewed=False,
            ),
            ProfileItem(
                key="background",
                body=background,
                source="seed_synth",
                visibility="public",
                reviewed=False,
            ),
        ]

        prof = Profile(
            employee_id=emp_id,
            name=full_name,
            role=role,
            items=items,
        )
        profiles.append(prof)

    return profiles


def generate_all_seeds(
    embedder: Embedder | None = None,
) -> tuple[list[Agent], list[Profile]]:
    """Generate all 4 fixed agents and 400 total employee profiles with embeddings."""
    fixed_agents, fixed_profiles = build_fixed_personas()
    synthetic_profiles = generate_synthetic_profiles(count=396)

    all_profiles = fixed_profiles + synthetic_profiles
    emb = embedder or DeterministicEmbedder()

    # Precompute vector embeddings (both full and public-only) for all 400 profiles (§14.4)
    for profile in all_profiles:
        item_text = " ".join(item.body for item in profile.items)
        profile.embedding = emb.embed(item_text)

        public_items = [it for it in profile.items if it.visibility == "public"]
        public_item_text = " ".join(it.body for it in public_items)
        profile.embedding_public = emb.embed(public_item_text)

    return fixed_agents, all_profiles


def get_base_today(today_str: str | None = None) -> date:
    """Resolve base date for relative seed calculation (§14.7)."""
    raw = today_str or os.environ.get("DEMO_TODAY")
    if raw:
        try:
            if "T" in raw:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
            return date.fromisoformat(raw.strip())
        except (ValueError, TypeError):
            pass
    return datetime.now(timezone.utc).date()


def build_m3_tasks(today_str: str | None = None) -> list[Task]:
    """Generate M3 seed tasks for requester persona Jordan Lee (§14.2, §14.3)."""
    ref_today = get_base_today(today_str)

    # 1. Stagnant task: T2 exceeding
    # Overdue 3d, stale 5d, reschedule 2, untouched 1, relative neglect 1 -> Score 17.0 >> T2 (7.0)
    task_stagnant = Task(
        task_id="task_jordan_riverside_clinic",
        owner_employee_id="emp_jordan_lee",
        title="Riverside Clinic Relocation Assessment",
        description="Need to find suitable medical office site with required zoning (C-2/O-M) and parking ratios for Riverside Clinic relocation.",
        status="todo",
        due_date=(ref_today - timedelta(days=3)).isoformat(),
        created_at=(ref_today - timedelta(days=10)).isoformat() + "T09:00:00Z",
        last_updated_at=(ref_today - timedelta(days=5)).isoformat() + "T09:00:00Z",
        reschedule_count=2,
        status_changed_at=(ref_today - timedelta(days=10)).isoformat() + "T09:00:00Z",
    )

    # 2. Active task 1: updated 1 day ago -> triggers relative neglect for stagnant task
    task_active1 = Task(
        task_id="task_jordan_nurse_staffing_renewal",
        owner_employee_id="emp_jordan_lee",
        title="Metro General Nurse Placement Contract Renewal",
        description="Negotiate 2026 contract terms and clinician rate cards.",
        status="in_progress",
        due_date=(ref_today + timedelta(days=5)).isoformat(),
        created_at=(ref_today - timedelta(days=4)).isoformat() + "T10:00:00Z",
        last_updated_at=(ref_today - timedelta(days=1)).isoformat() + "T14:00:00Z",
        reschedule_count=0,
        status_changed_at=(ref_today - timedelta(days=3)).isoformat() + "T10:00:00Z",
    )

    # 3. Active task 2
    task_active2 = Task(
        task_id="task_jordan_credentialing_audit",
        owner_employee_id="emp_jordan_lee",
        title="Allied Health Clinician Credentialing Verification",
        description="Audit clinician files for upcoming joint commission review.",
        status="todo",
        due_date=(ref_today + timedelta(days=2)).isoformat(),
        created_at=(ref_today - timedelta(days=2)).isoformat() + "T08:00:00Z",
        last_updated_at=(ref_today - timedelta(days=2)).isoformat() + "T08:00:00Z",
        reschedule_count=0,
        status_changed_at=(ref_today - timedelta(days=2)).isoformat() + "T08:00:00Z",
    )

    return [task_stagnant, task_active1, task_active2]


def build_m3_schedules(today_str: str | None = None) -> list[Schedule]:
    """Generate M3 schedule reminders covering overdue, today, tomorrow, upcoming (§14.2)."""
    ref_today = get_base_today(today_str)

    return [
        Schedule(
            item_id="sched_jordan_expense",
            owner_employee_id="emp_jordan_lee",
            kind="expense_deadline",
            title="Submit Monthly Travel & Client Expense Report",
            due_date=(ref_today - timedelta(days=1)).isoformat(),
        ),
        Schedule(
            item_id="sched_jordan_weekly_report",
            owner_employee_id="emp_jordan_lee",
            kind="weekly_report",
            title="Submit Healthcare Staffing Weekly Activity Report",
            due_date=ref_today.isoformat(),
        ),
        Schedule(
            item_id="sched_jordan_meeting_prep",
            owner_employee_id="emp_jordan_lee",
            kind="meeting_prep",
            title="Prepare Discussion Points for Client Executive Review",
            due_date=(ref_today + timedelta(days=1)).isoformat(),
        ),
        Schedule(
            item_id="sched_jordan_journal",
            owner_employee_id="emp_jordan_lee",
            kind="journal",
            title="Complete Weekly Tacit Knowledge & Account Log",
            due_date=(ref_today + timedelta(days=3)).isoformat(),
        ),
    ]


def build_m3_mail_seeds(today_str: str | None = None) -> list[MailSeed]:
    """Generate M3 mail seed for proactive profile diff proposal (§14.5).

    Owner must be one of the 4 registered personas (V-10): profile-diff
    reflection assumes the owning employee already has a profiles/agents
    entry, since the secretary never fabricates a profile for a mail owner
    who isn't registered. emp_marcus_delgado is used because his existing
    current_work item already covers ambulatory-surgery-center site
    searches, matching this email's subject.
    """
    ref_today = get_base_today(today_str)

    return [
        MailSeed(
            mail_id="mail_jordan_clinic_mou",
            owner_employee_id="emp_marcus_delgado",
            subject="Update on ambulatory surgery center partnership discussions",
            body=(
                "Hi Jordan, regarding our discussions with St. Jude ASC, we have finalized the preliminary "
                "staffing protocol for outpatient surgical teams. We are also tracking surgical suite utilization "
                "patterns across our regional network. Please update your records."
            ),
            received_at=(ref_today - timedelta(days=1)).isoformat() + "T11:00:00Z",
            processed=False,
        )
    ]


def populate_store(
    store: Store,
    embedder: Embedder | None = None,
    dry_run: bool = False,
    today: str | None = None,
) -> tuple[int, int]:
    """Populate store with seed agents, profiles, and Milestone 3 secretary records.

    Returns:
        (agent_count, profile_count)
    """
    agents, profiles = generate_all_seeds(embedder=embedder)
    m3_tasks = build_m3_tasks(today_str=today)
    m3_schedules = build_m3_schedules(today_str=today)
    m3_mail_seeds = build_m3_mail_seeds(today_str=today)

    if dry_run:
        print(f"=== DRY RUN: Seed Data Summary ===")
        print(f"Total Agents to register:  {len(agents)}")
        print(f"Total Profiles to store:    {len(profiles)}")
        print(f"Total M3 Tasks to store:    {len(m3_tasks)}")
        print(f"Total M3 Schedules to store:{len(m3_schedules)}")
        print(f"Total M3 MailSeeds to store:{len(m3_mail_seeds)}")
        print("\n--- Registered Agents (4) ---")
        for a in agents:
            print(f"  • {a.agent_id}: {a.display_name} (employee_id={a.employee_id}, active={a.active})")
        print("\n--- Sample Profiles (First 5) ---")
        for p in profiles[:5]:
            priv_keys = [it.key for it in p.items if it.visibility == "private"]
            priv_info = f" [Private items: {priv_keys}]" if priv_keys else ""
            print(f"  • {p.employee_id}: {p.name} - {p.role}{priv_info}")
            for it in p.items:
                print(f"      [{it.key}] ({it.visibility}): {it.body[:65]}...")
        return len(agents), len(profiles)

    # Save agents to store
    for a in agents:
        store.save_agent(a)

    # Save profiles to store
    for p in profiles:
        store.save_profile(p)

    # Save M3 secretary seeds
    for t in m3_tasks:
        store.save_task(t)

    for s in m3_schedules:
        store.save_schedule(s)

    for m in m3_mail_seeds:
        store.save_mail_seed(m)

    print(
        f"Successfully populated store with {len(agents)} agents, {len(profiles)} profiles, "
        f"{len(m3_tasks)} tasks, {len(m3_schedules)} schedules, and {len(m3_mail_seeds)} mail seeds."
    )
    return len(agents), len(profiles)


def main() -> None:
    """CLI entrypoint for seed generation script."""
    parser = argparse.ArgumentParser(description="Generate and load seed data for knowledge-discovery.")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing to Firestore.")
    parser.add_argument("--use-firestore", action="store_true", help="Write to live Firestore instance.")
    parser.add_argument("--project", type=str, default=None, help="Google Cloud project ID.")
    parser.add_argument("--database", type=str, default=None, help="Firestore database ID.")
    parser.add_argument(
        "--today",
        type=str,
        default=None,
        help="Base date for relative seed generation (YYYY-MM-DD). Defaults to DEMO_TODAY or today.",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Delete all collections (agents/profiles/messages/tasks/schedules/mail_seeds/cards) "
        "before seeding. Use for a demo reset (requires --use-firestore).",
    )
    parser.add_argument(
        "--embedder",
        choices=["deterministic", "gemini"],
        default="deterministic",
        help="Embedder used for stored profile embeddings. Use 'gemini' when the server "
        "runs with GeminiEmbedder (embedding dimensions must match at query time).",
    )
    args = parser.parse_args()

    embedder = None
    if args.embedder == "gemini":
        from knowledge_discovery.gemini_adapters import GeminiEmbedder
        embedder = GeminiEmbedder()

    if args.dry_run or not args.use_firestore:
        from knowledge_discovery.store import InMemoryStore
        store = InMemoryStore()
        populate_store(store, dry_run=args.dry_run, embedder=embedder, today=args.today)
    else:
        from knowledge_discovery.firestore_store import FirestoreStore
        store = FirestoreStore(project=args.project, database=args.database)
        if args.clear:
            print("Clearing all collections before seeding (--clear)...")
            store.clear()
        populate_store(store, dry_run=False, embedder=embedder, today=args.today)


if __name__ == "__main__":
    main()
