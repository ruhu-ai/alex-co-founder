"""Provision the initial synthetic-hiring workspace owner via ADC.

Required: HIRING_OWNER_SUBJECT (verified Firebase ``sub``). Optional:
HIRING_WORKSPACE_ID, HIRING_OWNER_ACTOR_ID. This provisions authority only; it
does not enable real candidate processing or any external effect.
"""

from __future__ import annotations

import asyncio
import os

from services.actor_identity import WorkspaceRole, create_membership


async def main() -> None:
    subject = os.environ.get("HIRING_OWNER_SUBJECT", "")
    if not subject:
        raise SystemExit("HIRING_OWNER_SUBJECT is required")
    result = await create_membership(
        actor_id=os.environ.get("HIRING_OWNER_ACTOR_ID", "member_owner"),
        workspace_id=os.environ.get("HIRING_WORKSPACE_ID", "workspace_founder"),
        auth_subject=subject, role=WorkspaceRole.OWNER,
        created_by="provisioner:seed_hiring_membership", synthetic=False)
    if result.get("error"):
        raise SystemExit(result.get("message", "membership provisioning failed"))
    print("Hiring workspace owner provisioned; real candidate processing remains disabled.")


if __name__ == "__main__":
    asyncio.run(main())
