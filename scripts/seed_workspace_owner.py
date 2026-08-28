"""Provision the initial interactive workspace owner through the platform IAM.

Required: WORKSPACE_OWNER_SUBJECT, the verified Firebase/OIDC ``sub`` claim.
Optional: WORKSPACE_ID and WORKSPACE_OWNER_ACTOR_ID.  This command is
idempotent only in the safe direction: an existing membership is left for the
versioned membership API rather than silently changing its role or subject.
"""

from __future__ import annotations

import asyncio
import os

from services.actor_identity import WorkspaceRole, create_membership


async def main() -> None:
    subject = os.environ.get("WORKSPACE_OWNER_SUBJECT", "")
    if not subject:
        raise SystemExit("WORKSPACE_OWNER_SUBJECT is required")
    workspace_id = os.environ.get(
        "WORKSPACE_ID", os.environ.get("FOUNDER_ID", "founder"))
    result = await create_membership(
        actor_id=os.environ.get("WORKSPACE_OWNER_ACTOR_ID", "member_owner"),
        workspace_id=workspace_id, auth_subject=subject,
        role=WorkspaceRole.OWNER,
        created_by="provisioner:seed_workspace_owner",
        synthetic=False, local_only=False)
    if result.get("error_code") == "version_conflict":
        print("Workspace owner already exists; no authority was changed.")
        return
    if result.get("error"):
        raise SystemExit(result.get("message", "membership provisioning failed"))
    print(f"Workspace owner provisioned for {workspace_id}.")


if __name__ == "__main__":
    asyncio.run(main())
