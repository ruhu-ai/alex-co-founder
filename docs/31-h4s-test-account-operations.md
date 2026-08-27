# 31 — H4S Test-Account Operations

H4S is a synthetic sandbox, not a hiring pilot. Do not configure it with the
ordinary Alex role mailbox, the founder mailbox, or a primary Calendar.

## Required test identities

Provision four separate, team-controlled identities:

1. sandbox Gmail sender;
2. synthetic candidate mailbox;
3. founder test-recipient mailbox; and
4. sandbox Calendar.

Store only the sender and Calendar refresh tokens as named Secret Manager
secrets. Record the provider account subjects as SHA-256 hashes in deployment
configuration. Configure the three test addresses separately as
`HIRING_H4S_TEST_CANDIDATE_ADDRESS`, `HIRING_H4S_TEST_FOUNDER_ADDRESS`, and
`HIRING_H4S_TEST_CALENDAR_ATTENDEE_ADDRESS`. The founder UI supplies only a
destination *kind*; the server derives the actual address and probe receipt.
Provisioning creates durable H4S bindings referencing secret names; refresh
tokens, raw email, and full account identity never enter Firestore, prompts,
logs, or approvals.

## Enablement proof

Before enabling HIRING_ENABLE_H4_SANDBOX:

- H0 remains SYNTHETIC_ONLY and all ordinary hiring effects remain false.
- Positive account probe proves each configured account is the intended test
  identity; a negative probe proves the ordinary Alex/founder account is refused.
- Every recipient is provisioned from the deployment-owned test destination
  configuration; no free-text address is accepted by the UI or effect route.
- The test Gmail watch uses its own Pub/Sub topic and route-specific workload
  identity.
- The test-reply sender preserves the outbound `In-Reply-To` Message-ID and
  supplies the exact `X-CoFounder-H4S-Causal-Token` through the test Gmail API
  path. A normal human-looking reply without both values is intentionally
  quarantined, not correlated.

## Recording proof

Record these results before a demo:

1. non-allowlisted destination refused before provider call;
2. exact approved test email produces one receipt;
3. provider timeout becomes UNCERTAIN and reconciles from provider evidence;
4. verified test reply causally wakes one CandidateRun after restart;
5. exact approved Calendar invite creates one receipt;
6. test Calendar reschedule/cancellation names the original sandbox action,
   preserves the same candidate/test attendees, receives a separate exact
   approval, and reconciles from the provider event state; and
7. sandbox closure revokes binding/destinations and rejects late event/retry.

No real candidate, public job-board, or ordinary company connector may appear
in the recording.
