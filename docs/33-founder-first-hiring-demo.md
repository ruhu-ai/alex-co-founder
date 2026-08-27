# 33 — Founder-first Ruhu FDE demo runbook

This is the honest product-demo path for one synthetic Forward Deployment
Engineer role at Ruhu. It demonstrates a durable operation, an external event
resuming that operation, and carefully bounded internal receipts. It does not
enable live hiring, generic inbox processing, candidate outreach, or H4.

## One-time local setup

1. In **Connectors**, select **Alex's Calendar** and choose **Connect**. The
   current tab opens Google consent; sign in only as `alex@ruhu.ai`. This is a
   distinct role-calendar connection with the Calendar scope needed for the
   exact internal-demo invitation. If a founder sign-in is required first, the
   app returns to this consent flow automatically after sign-in; it does not
   open a separate browser window. The equivalent local route is
   `/api/integrations/google/connect?connector=alex_calendar`.
2. Re-consent the founder Calendar connector if it does not already include
   OpenID identity scope: `/api/integrations/google/connect?connector=calendar`.
   Sign in only as `ijidai@ruhu.ai`.
3. Obtain opaque subject hashes locally; never copy an access or refresh token:

   ```bash
   .venv/bin/python -c 'from dotenv import load_dotenv; load_dotenv(".env"); from services.google_oauth import account_subject_hash; print(account_subject_hash("alex")); print(account_subject_hash("founder"))'
   ```

4. In `.env`, set only these values, with the actual two hash outputs:

   ```text
   HIRING_ENABLE_SYNTHETIC_DEMO=1
   HIRING_SYNTHETIC_FIXTURE_IDS=fixture_ruhu_fde_walkthrough
   HIRING_ENABLE_INTERNAL_CONTROLLED_DEMO=1
   HIRING_INTERNAL_DEMO_ALEX_SUBJECT_SHA256=sha256:...
   HIRING_INTERNAL_DEMO_FOUNDER_SUBJECT_SHA256=sha256:...
   HIRING_INTERNAL_DEMO_APPLICATION_PDF_SHA256=sha256:af240b750f2b41ae558866a584662549cca96022de4c778eaf0999697e1ac69b
   ```

   Keep all H4S variables unset. Restart the local server after changing
   configuration.

## Recording sequence

1. In Co-Founder chat, submit:

   ```text
   /hiring Forward Deployment Engineer for Ruhu, Inc. Full-time employee, based in Nigeria and working remotely.
   ```

   This creates a durable draft role and immutable proposed role policy. It
   does not post a job or send mail.
2. Open **Hiring operations**. Review the role brief, scorecard, interview
   plan, and exact post. Select **Approve role brief and job-post draft**.
   The server binds approval to that policy version and hash.
3. Manually publish the synthetic post only in the contained Browser panel.
   Record the actual public HTTPS URL in **Record a manual job post**. The
   app records a receipt; it never publishes on the founder's behalf.
4. Select **Begin controlled demo**. The UI will state that no inbox read has
   happened yet.
5. From `ijidai.lassa@gmail.com`, send a single email to `alex@ruhu.ai` with
   `ijidai@ruhu.ai` copied:

   - Subject: `Demo application - Amira Okafor - Forward Deployment Engineer`
   - Body includes exactly: `[[COFOUNDER_INTERNAL_DEMO:ruhu_fde_v1]]`
   - Attachment: `output/pdf/amira-okafor-fde-demo-application.pdf`

6. In Hiring operations select **Import marked CV email**. The only permitted
   query requires that sender, Alex recipient, exact subject, attachment, and
   body marker; the attachment hash must match the deployment pin. A successful
   import creates the actual synthetic candidate case and Evidence Passport,
   then the candidate board awaits a human decision.
7. Open the candidate board and Evidence Passport. Record an attributed human
   `ADVANCE` or `HOLD` decision. Alex did not choose the candidate.
8. In **Live internal demo**, select **Review recap email** or **Review
   calendar invite**. The exact immutable recipient, content, start/end time,
   and provider-account pins are shown before the second confirmation. The
   receipts arrive only at the named Ruhu accounts.
9. For reset, select **Review calendar cancellation** if an invite was created,
   approve it, then select **Reset demo**. The calendar event is cancelled; the
   provider email is intentionally retained and the UI says so.

## Proof and safety assertions

- Repeating an effect execution returns the existing receipt rather than
  sending a second email or invitation.
- An ambiguous Calendar result becomes `UNCERTAIN`; select **Reconcile**. The
  app looks up only the deterministic event ID and never retries automatically.
- An ambiguous Gmail send is not guessed or resent; it remains visibly
  uncertain for manual inspection.
- The import is one controlled fixture path, not a Gmail watch or general
  mailbox search. It stores bounded derived evidence, not the raw email/PDF.
- The entire role and candidate record remain fixture-marked synthetic data.
  H4/H4S live hiring inputs remain disabled.

## Verification commands

```bash
.venv/bin/python scripts/preflight_hiring_internal_demo.py
.venv/bin/python -m pytest -q tests/unit/test_app_server.py tests/unit/test_hiring_sandbox.py tests/unit/test_internal_controlled_demo.py
.venv/bin/python -m ruff check app/main.py app/hiring_routes.py services/internal_controlled_demo.py services/internal_controlled_demo_effects.py services/internal_controlled_demo_google.py services/internal_controlled_demo_intake.py
```

The historical broad H0–H3 test run can crash in the current local Python's
CFFI/OpenSSL extension during vault encryption. Treat that as an environment
repair item, not a passing end-to-end result; rerun it in the pinned container
or repaired virtual environment before recording the final demo.
