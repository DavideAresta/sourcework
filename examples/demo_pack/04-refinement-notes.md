# Refinement session - returns portal

**Date:** 29 March
**Present:** Tomasz Nowak (Customer Service), Marco Bianchi (Commercial Ops),
Anja Weber (Finance), Lena Fischer (Engineering, first half only)
**Notes by:** Tomasz Nowak. Not minuted formally - corrections welcome.

## Purpose

Follow-up to Tuesday's kickoff. Two items were pushed here: the free-returns
threshold, and how the fee interacts with return reasons. We also picked up a
couple of things that came out of the service desk data.

## Free returns and the handling fee

The BRD says returns are free above EUR 50 and that we deduct EUR 3.90 below
that. Anja pulled the numbers and the threshold does not behave the way we
assumed. Two thirds of returns are already above EUR 50, so the fee applies to
a minority of cases but generates a disproportionate share of complaints -
Tomasz counted 61 escalations last quarter that were specifically about the
handling fee.

Marco's proposal, which we agreed in the room: keep the EUR 50 threshold, but
make the fee depend on the **reason** rather than only the order value. Where
the fault is ours - damaged in transit, wrong item received, delivered late -
the return is free regardless of order value. Where the customer simply changed
their mind, the EUR 3.90 fee applies **regardless of order value**, including on
orders above EUR 50.

That last part is a change from the BRD and Marco will get it re-approved. Anja
estimates it is roughly EUR 40,000 a year in recovered handling cost. Note this
contradicts BR-05 as currently written.

## Return window

Confirmed from Tuesday: 30 days for loyalty programme members, 14 days for
everyone else. Anja asked whether the 30 days runs from delivery or from
dispatch. From **delivery** - same as today. Worth writing down because the
order-history screen currently shows the dispatch date more prominently.

## Retention

Sofia's written position came through Thursday afternoon. Summary: audit
records covering the accept/reject/fee decision are kept for 7 years, but they
must be **pseudonymised after 24 months** - the decision, amounts and timestamps
are retained, the customer identifiers are replaced with a stable surrogate key.
That satisfies both obligations. Sofia's note says this needs to be designed in
from the start rather than retrofitted.

## Cancelling a return

New requirement, came from the service desk data rather than from anyone's
document. About 15% of return requests raised by phone today are subsequently
abandoned - the customer changes their mind and never sends the parcel. Right
now that leaves a return sitting open forever and the stock forecast is wrong.

The customer must be able to cancel a return request themselves, at any point
before the parcel has been scanned by the carrier. After the scan it is too
late and it has to complete as a normal return.

## Condition on arrival

Lena asked what the warehouse actually records. Today it is a free-text note in
a spreadsheet. Tomasz to get the list of condition codes the DCs actually use -
there are apparently about six but they differ slightly between Hamburg and
Venlo, which is going to be a problem.

## Poland

Marco confirmed: **not** in the launch wave. Target is Q3, treated as a
follow-on. He was explicit that he did not want this reopened in the steering
group.

## Still open

- Marketplace returns. Priya is taking a scope proposal to steering. Nothing
  moved on this here.
- Whether the review queue for suspected serial returners is staffed by
  Customer Service or by Finance. Anja thinks Finance, Tomasz disagrees. Not
  resolved.
- Condition codes need harmonising across the four DCs before anyone can build
  a dropdown for them.
