"""Provision the synthetic-hiring workspace founder via ADC.

Required: HIRING_FOUNDER_SUBJECT (verified Firebase ``sub``). Optional:
HIRING_WORKSPACE_ID and HIRING_FOUNDER_ACTOR_ID. This provisions founder
authority only; it does not enable real candidate processing or any effect.
"""

from __future__ import annotations

import asyncio
import os

from services.actor_identity import WorkspaceRole, create_membership


async def main() -> None:
    subject = os.environ.get("HIRING_FOUNDER_SUBJECT", "")
    if not subject:
        raise SystemExit("HIRING_FOUNDER_SUBJECT is required")
    result = await create_membership(
        actor_id=os.environ.get("HIRING_FOUNDER_ACTOR_ID", "member_founder"),
        workspace_id=os.environ.get("HIRING_WORKSPACE_ID", "workspace_founder"),
        auth_subject=subject, role=WorkspaceRole.FOUNDER,
        created_by="provisioner:seed_hiring_founder", synthetic=False)
    if result.get("error"):
        raise SystemExit(result.get("message", "membership provisioning failed"))
    print("Hiring workspace founder provisioned; real candidate processing remains disabled.")


if __name__ == "__main__":
    asyncio.run(main())
