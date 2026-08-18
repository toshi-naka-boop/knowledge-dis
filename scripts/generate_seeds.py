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
import sys
from typing import Any

from knowledge_discovery.matching import DeterministicEmbedder, Embedder
from knowledge_discovery.models import Agent, Profile, ProfileItem
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

    for idx, (dept, role_list) in enumerate(dept_assignments, start=1):
        emp_id = f"emp_synth_{idx:03d}"
        f_name = FIRST_NAMES[(idx * 7) % len(FIRST_NAMES)]
        l_name = LAST_NAMES[(idx * 13) % len(LAST_NAMES)]
        full_name = f"{f_name} {l_name}"
        role = role_list[idx % len(role_list)]

        # Intentional overlap design:
        # Candidate 1..5 in real_estate: strong overlap with Demo 1 (medical clinic relocation & zoning)
        # Candidate 1..5 in transition: strong overlap with Demo 2 (practice retirement & succession)
        if dept == "real_estate" and idx <= 5:
            current_work = (
                f"Coordinates site acquisition and municipal zoning clearances and permits for outpatient medical facilities. "
                f"Evaluates parking ratio compliance, ADA access, and plumbing infrastructure conversions for clinic tenants. "
                f"Assists regional health networks with complex clinic relocation planning."
            )
            expertise = "Medical zoning codes, retail-to-clinic adaptive reuse, healthcare facility tenant representation."
            background = f"Experienced real estate specialist with {4 + (idx % 6)} years in commercial healthcare properties."
        elif dept == "transition" and idx <= 5:
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

    # Precompute vector embeddings for all 400 profiles
    for profile in all_profiles:
        item_text = " ".join(item.body for item in profile.items)
        profile.embedding = emb.embed(item_text)

    return fixed_agents, all_profiles


def populate_store(
    store: Store,
    embedder: Embedder | None = None,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Populate store with seed agents and profiles.

    Returns:
        (agent_count, profile_count)
    """
    agents, profiles = generate_all_seeds(embedder=embedder)

    if dry_run:
        print(f"=== DRY RUN: Seed Data Summary ===")
        print(f"Total Agents to register: {len(agents)}")
        print(f"Total Profiles to store:   {len(profiles)}")
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

    print(f"Successfully populated store with {len(agents)} agents and {len(profiles)} profiles.")
    return len(agents), len(profiles)


def main() -> None:
    """CLI entrypoint for seed generation script."""
    parser = argparse.ArgumentParser(description="Generate and load seed data for knowledge-discovery.")
    parser.add_argument("--dry-run", action="store_true", help="Print summary without writing to Firestore.")
    parser.add_argument("--use-firestore", action="store_true", help="Write to live Firestore instance.")
    parser.add_argument("--project", type=str, default=None, help="Google Cloud project ID.")
    parser.add_argument("--database", type=str, default=None, help="Firestore database ID.")
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
        populate_store(store, dry_run=args.dry_run, embedder=embedder)
    else:
        from knowledge_discovery.firestore_store import FirestoreStore
        store = FirestoreStore(project=args.project, database=args.database)
        populate_store(store, dry_run=False, embedder=embedder)


if __name__ == "__main__":
    main()
