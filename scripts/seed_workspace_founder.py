"""Provision the sole interactive workspace founder through platform IAM.

Required: WORKSPACE_FOUNDER_SUBJECT, the verified Firebase/OIDC ``sub`` claim.
Optional: WORKSPACE_ID and WORKSPACE_FOUNDER_ACTOR_ID. Existing memberships are
never silently changed; revocation and other changes remain versioned/audited.
"""

from __future__ import annotations

import asyncio
import os

from services.actor_identity import WorkspaceRole, create_membership


async def main() -> None:
    subject = os.environ.get("WORKSPACE_FOUNDER_SUBJECT", "")
    if not subject:
        raise SystemExit("WORKSPACE_FOUNDER_SUBJECT is required")
    workspace_id = os.environ.get(
        "WORKSPACE_ID", os.environ.get("FOUNDER_ID", "founder"))
    result = await create_membership(
        actor_id=os.environ.get("WORKSPACE_FOUNDER_ACTOR_ID", "member_founder"),
        workspace_id=workspace_id, auth_subject=subject,
        role=WorkspaceRole.FOUNDER,
        created_by="provisioner:seed_workspace_founder",
        synthetic=False, local_only=False)
    if result.get("error_code") == "version_conflict":
        print("Workspace founder already exists; no authority was changed.")
        return
    if result.get("error"):
        raise SystemExit(result.get("message", "membership provisioning failed"))
    print(f"Workspace founder provisioned for {workspace_id}.")


if __name__ == "__main__":
    asyncio.run(main())
