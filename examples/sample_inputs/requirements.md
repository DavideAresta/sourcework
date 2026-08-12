# Invoice reconciliation - draft notes from Finance Ops

## Context

Four legal entities (IT, DE, NL, UK) each run their own month-end close.
Reconciliation is currently a shared Excel workbook with VLOOKUPs against a CSV
export from SAP. It breaks about once a quarter.

## What we need it to do

- Ingest supplier invoices from the AP inbox and from the EDI feed.
- Match each invoice to one or more purchase order lines. Partial deliveries
  mean a single invoice can span several PO lines.
- Apply a matching tolerance. Anything inside tolerance is auto-matched.
- Route everything else to an exception queue for a human.
- Let the reviewer see invoice and PO side by side, with the differences
  highlighted.
- Record a reason code on every rejection.
- Produce a month-end reconciliation report per entity.

## Non-functional

- The nightly matching run must complete within two hours for 15,000 invoices.
- The exception queue must load in under two seconds with 500 open items.
- Audit records are immutable and retained for seven years.
- Multi-currency: EUR, GBP, CHF, USD. FX rate used must be stored with the match.

## Known constraints

- SAP is the system of record for purchase orders. We do not write back to it
  in the first release.
- Access is restricted by entity: a UK reviewer must not see DE invoices.
- The team is three engineers and one designer, for one quarter.

## Open

- Do we need to support credit notes in v1? Finance says probably yes.
- Nobody has confirmed whether the EDI feed includes the PO reference reliably.
