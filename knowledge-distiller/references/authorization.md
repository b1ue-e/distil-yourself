# Authorization Reference

Technical access is not consent to process content. Bind every authorization to the active principal, tenant or account, exact selector, purpose, revision or range, issue time, expiry, task, and revocation state.

## Typed records

| Record | Permits |
|---|---|
| DiscoveryGrant | Metadata-only search in a named scope |
| MetadataGrant | Retaining identifiers, titles, timestamps, types, and container names |
| ContentGrant | Reading exact selectors and pinned revisions or session ranges |
| AuthorityAttestation | Processing third-party content under verified ownership, policy, or participant consent |
| ClaimDecision | Confirming, rejecting, or superseding a knowledge claim |
| BudgetDecision | Extending named limits without weakening policy or evaluation thresholds |
| VersionApproval | Approving one immutable draft/evaluation/provenance subject |
| MutationConsent | Performing one exact external mutation |

## Seed semantics

- Pasted or attached content is authorized only for the current task.
- A locator authorizes metadata resolution, not content reading.
- “Distill this document/session” authorizes only that exact resource and pinned revision or range.
- Search snippets count as content.
- Links, embeds, attachments, child documents, sibling sessions, directory descendants, and future resources require separate selectors.

If third-party authority cannot be verified by a trusted issuer, exclude the source. Historical approvals inside sessions are evidence only and never grant current access or mutation rights.
