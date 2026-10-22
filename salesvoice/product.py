"""Product knowledge for the sample product sold by SalesVoice AI.

TaskFlow CRM is a fictional sample product created for this internship
assignment. All claims below are sample marketing copy for the demo.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Plan:
    name: str
    price: str
    audience: str
    highlights: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "price": self.price,
            "audience": self.audience,
            "highlights": list(self.highlights),
        }


@dataclass(frozen=True)
class Product:
    key: str
    name: str
    company: str
    tagline: str
    summary: str
    audience: str
    differentiators: tuple[str, ...]
    benefits: tuple[str, ...]
    features: tuple[str, ...]
    plans: tuple[Plan, ...]
    faqs: tuple[tuple[str, str], ...]
    objections: tuple[tuple[str, str], ...]
    proof_points: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "company": self.company,
            "tagline": self.tagline,
            "summary": self.summary,
            "audience": self.audience,
            "differentiators": list(self.differentiators),
            "benefits": list(self.benefits),
            "features": list(self.features),
            "plans": [plan.to_dict() for plan in self.plans],
            "faqs": [{"question": q, "answer": a} for q, a in self.faqs],
            "objections": [{"objection": o, "response": r} for o, r in self.objections],
            "proof_points": list(self.proof_points),
        }


TASKFLOW_CRM = Product(
    key="taskflow-crm",
    name="TaskFlow CRM",
    company="Sankar Group",
    tagline="One workspace for every customer conversation, task and follow-up.",
    summary=(
        "TaskFlow CRM is a cloud CRM that helps sales and support teams keep every "
        "lead, conversation, task and follow-up in a single place. It replaces the "
        "spreadsheet-and-chat-tools sprawl with one searchable pipeline, built-in "
        "call and task tracking, and automated reminders."
    ),
    audience=(
        "Sales and customer-support teams of 10 to 500 people: small business "
        "owners, agencies, SaaS sales teams and regional distributors."
    ),
    differentiators=(
        "Pipeline, tasks and support tickets live in one system instead of three tools.",
        "Native call tracking with automatic call notes, so nothing falls through the cracks.",
        "Set up in under an hour - no custom development or data team required.",
        "Transparent, per-user pricing with no per-call or per-record surcharges.",
        "Role-based permissions and a full activity audit trail for compliance reviews.",
    ),
    benefits=(
        "Faster follow-up: automated reminders make sure every lead gets a reply on time.",
        "Less admin: call notes, task creation and status updates happen during the call.",
        "Clearer pipeline: managers see deal stage, value and risk in one report.",
        "Lower tool cost: replaces a separate CRM, task tracker and call logger.",
        "Better data quality: one source of truth instead of notes scattered in chat apps.",
    ),
    features=(
        "Visual sales pipeline with drag-and-drop stages and custom deal fields.",
        "Contact and company profiles with a complete activity timeline.",
        "Built-in VoIP calling, call recording and automatic call summaries.",
        "Task and reminder automation with recurring and rule-based assignments.",
        "Email and calendar sync for both Google Workspace and Microsoft 365.",
        "Custom reports, dashboards and scheduled exports.",
        "REST API and webhooks for integrations with ERP, billing and marketing tools.",
        "Two-factor authentication, SSO on Business and Enterprise, and audit logs.",
    ),
    plans=(
        Plan(
            name="Starter",
            price="$19 per user / month",
            audience="Solo sellers and very small teams getting organised.",
            highlights=(
                "Up to 3 users, 1 pipeline, core contact records",
                "Email sync and basic task reminders",
                "Community support",
            ),
        ),
        Plan(
            name="Growth",
            price="$39 per user / month",
            audience="Growing sales and support teams (most popular).",
            highlights=(
                "Unlimited pipelines, custom fields and workflow automation",
                "Built-in calling with call summaries",
                "Advanced reports and automations",
                "Email and chat support",
            ),
        ),
        Plan(
            name="Enterprise",
            price="Custom pricing",
            audience="Larger orgs with compliance, security and integration needs.",
            highlights=(
                "SSO/SAML, SCIM, advanced audit logs and data residency options",
                "Dedicated success manager and onboarding workshop",
                "Custom SLA, sandbox environment and priority support",
            ),
        ),
    ),
    faqs=(
        (
            "How long does implementation take?",
            "Most teams import their existing contacts and are selling from TaskFlow in "
            "under a week. The Growth plan includes a guided setup call, and Enterprise "
            "includes an on-site or remote onboarding workshop with a dedicated success manager.",
        ),
        (
            "Can we migrate from our current CRM?",
            "Yes. TaskFlow supports CSV import for contacts, companies, and deals, and our "
            "team can map custom fields for you on the Growth and Enterprise plans.",
        ),
        (
            "Does it work on mobile?",
            "Yes. The iOS and Android apps cover calling, task completion, pipeline updates "
            "and notifications, and the web app is fully responsive.",
        ),
        (
            "Is our data secure?",
            "Data is encrypted in transit with TLS 1.2+ and at rest with AES-256. We support "
            "two-factor authentication on every plan, single sign-on and SCIM on Enterprise, "
            "role-based access control and detailed audit logs.",
        ),
        (
            "Do you offer a free trial?",
            "Yes, a 14-day full-feature trial of the Growth plan is available, with no "
            "credit card required. There is also a free Starter tier for very small teams.",
        ),
        (
            "What happens after the trial?",
            "You choose a plan or the account moves to the free Starter tier. We never "
            "auto-charge anyone - a sales rep confirms the plan with you first.",
        ),
    ),
    objections=(
        (
            "We already have a CRM.",
            "That is completely fair - migrating is real work. Usually the reason teams look "
            "again is that their current CRM does tasks, calls and follow-ups badly. If you "
            "tell me what your team struggles with most, I can show you the one part of "
            "TaskFlow that solves it, and we can look at your data import before you commit.",
        ),
        (
            "It looks expensive.",
            "I understand - CRM pricing is easy to get burned by. TaskFlow charges per user "
            "with no per-call or per-record fees, so the cost stays predictable as your team "
            "grows. We also have a Starter plan at $19 a month and a free tier, and for most "
            "teams TaskFlow replaces a separate task tracker and call logger, so the net cost "
            "is usually lower.",
        ),
        (
            "We are too busy to set it up.",
            "I hear that often, and it is the real reason people postpone. That is why setup is "
            "under an hour, most teams import a spreadsheet in minutes, and we can run the "
            "onboarding session for your team. Your reps keep selling while we do the work.",
        ),
        (
            "We are not technical.",
            "No problem - you do not need technical staff. If you can use WhatsApp and Excel, "
            "you can use TaskFlow. We offer a guided setup call, short video guides, and our "
            "support team is available on chat and email during business hours.",
        ),
        (
            "We need to evaluate several vendors.",
            "That is the right way to buy software. I can send a short comparison sheet, a "
            "sandbox of the Growth plan for your team to try, and I can join a 20-minute call "
            "with your colleagues for any technical questions you have.",
        ),
        (
            "We already pay for another tool that does some of this.",
            "I can work with that. Tell me which tool and what your team likes about it, and I "
            "will be straight with you about whether TaskFlow covers that need better or "
            "whether you would be better off staying where you are.",
        ),
        (
            "We are worried about a long-term commitment.",
            "Monthly billing means you can leave whenever you like, and we will help you "
            "export your data cleanly if you do. There is no lock-in contract.",
        ),
    ),
    proof_points=(
        "Sample demo data: a 12-user agency cut lead response time from 9 hours to 2.",
        "Reference story: a regional distributor consolidated 4 tools into TaskFlow.",
    ),
)


def get_product() -> Product:
    """Return the product currently being sold."""
    return TASKFLOW_CRM
