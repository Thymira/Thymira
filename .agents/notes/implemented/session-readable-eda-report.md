# Publish a source-checked EDA narrative

## Problem

Conversation summaries could expose a structured profile as JSON or concatenate execution output,
while profile images had no readable explanation of measured dataset facts. A report must not turn
an uninspected image into a finding or imply that missing values were already treated.

## Decision

Render EDA deterministically from active profile, schema, dataset, and completed task evidence. State
the profiled shape, registered column types, observed missing columns, declared target, completed
work, and limitations in four report sections. Link image and source artifacts, but make no claim
about image pixels. Say that missing columns need a treatment decision before modeling; when none
are recorded, limit the statement to the profiled rows. Keep structured task output and console
content in run evidence instead of presenting JSON as prose.

Publish the Markdown as a REPORT with artifact lineage. MIRA recomputes profile statistics from the
registered dataset bytes and schema, and makes those numbers available to A18 only after successful
verification and only to a report that names the verified profile in its lineage. No metric sidecar
or extra model call is used. Preserve existing evaluation and training reports.

A18 reports partial evidence as not applicable when any ordinary report has no linked profile
or metrics evidence; one verified report cannot cover an unrelated report. Coding-agent claims
are explicitly attributed to that agent. Pure numeric-claim matching was extracted into
`checks/report_claims.py`, reducing the previously oversized audit module below its size limit.

## Alternatives considered

- Echoing DataProfile or CodeResult JSON preserves bytes but does not answer a user's question in
  readable language.
- Asking a language model to narrate the profile adds cost and can introduce unsupported findings.
- Trusting numbers copied into the Markdown or a renderer-authored sidecar would not independently
  establish that they match the source dataset.
- Reading or describing PNG contents without an image-analysis result would invent evidence.

## Consequences

The report is deterministic and auditable; A18 checks profile-derived numerical claims against
source-recomputed facts. A changed profile statistic fails verification. Profile conclusions remain
limited to the rows and fields recorded in the profile; this narrative does not certify other
preprocessing or interpretation of generated plots. Offline tests use scripted providers and make no
real provider calls.
