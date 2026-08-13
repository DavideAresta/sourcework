# Project Boomerang: Returns Portal

> **Status** `draft` &nbsp;|&nbsp; **Version** 0.1.0 &nbsp;|&nbsp; **Generated** 2026-08-13 16:31 UTC by SourceWork
>
> Synthesised from 5 source(s) and 170 evidence item(s). Requirements tagged `derived` were inferred, not stated.

## Summary

Project Boomerang is a customer self-service returns portal for Germany, Austria and the Netherlands, live and stable by 1 December so the January return peak - more than double today's roughly 8,200 returns a month - is served by software rather than the contact centre, where refund-status enquiries are four in ten contacts and each return costs about EUR 7.50 to handle. Customers will request and track returns, see refund initiation and settlement as two separate states with an expected settlement date, receive a DHL or GLS label within about fifteen minutes, and cancel until the carrier scans the parcel. It is built on existing platform commitments - SAP ECC as system of record with no phase-1 writes, Adyen, Azure AD B2C, PostgreSQL, EU/Frankfurt residency - and every automated decision must be reproducible for seven years. Approval of this PRD is blocked on three conflicts between the architecture constraints and the BRD and five open questions, which the steering group and Legal must decide before build. Two integration gaps are also recorded as pre-build dependencies: the owner of the pre-purchase return-window display (REQ-024), and the channel by which carrier scan events reach the portal (REQ-048, REQ-049).

## Problem statement

Today a customer who wants to return an item has to phone the contact centre and wait: four out of ten customer contacts are refund-status enquiries, and each return costs the company roughly EUR 7.50 in contact-centre time alone, on about 8,200 returns a month - a cost that more than doubles every January when return volume peaks. The customer-facing rules add to the problem: the BRD's free-returns threshold contradicts the newly agreed reason-based fee rule, and the current fourteen-day return window is the single most common reason customers give for not buying. Operations are affected too: about fifteen per cent of phone-raised return requests are abandoned but stay open forever, which distorts the stock forecast; suspected serial returners are only spotted by an agent's memory; and because carrier labels are generated in an overnight batch, a request made at nine in the morning cannot be fulfilled the same day. If nothing changes, next January is expected to be worse than the last.

## Background

Project Boomerang is the company's customer self-service returns portal, consolidated here from the BRD v0.9, the IT architecture constraints note, the kickoff meeting, the 29 March refinement session and the returns-screen wireframe. The requirement set in the table is finalised; this narrative does not restate or renumber it and points at requirements by id. Three conflicts between the architecture constraints and the BRD remain unresolved and block approval, and five open questions remain unanswered; REQ-016 requires the steering group to decide the conflicts before build, and REQ-035 blocks the data model until Legal's retention position lands. The delivery clock is set by platform constraints: live and stable by 1 December (REQ-031), a two-week stabilisation period, then the deployment freeze from 15 December to 6 January (REQ-015, REQ-036). Planning assumes four engineers and one designer (REQ-037) and a four-node scaling ceiling (REQ-012).

## Goals

- Cut the contact-centre cost of returns - roughly EUR 7.50 per return today - by eliminating the refund-status enquiries that make up four in ten of all customer contacts.
- Be live and stable before the deployment freeze so the January return peak, more than double the monthly baseline, is served by the portal instead of the contact centre.
- Make the return window a purchase driver: the window applicable to each customer is visible before purchase and is never shortened afterwards, so return-window uncertainty stops being the top reason cited for not buying.
- Keep every automated accept, reject and fee decision reproducible for seven years and pseudonymised after twenty-four months, with pseudonymisation designed in from the start rather than retrofitted.
- Recover the estimated EUR 40,000 per year attributed to the reason-based handling-fee rule while reducing the handling-fee escalations (61 last quarter).

## Non-goals

- Marketplace returns in phase 1: REQ-029 excludes them until the seller agreement is reviewed, and any later inclusion is subject to the zero-sum rule (REQ-038).
- Writing to SAP or posting credit notes in phase 1 (REQ-001): SAP ECC remains system of record and the portal does not update it.
- Modifying the existing order-history application beyond the entry point (REQ-017), and no real-time SAP stock interface (REQ-002).
- Synchronous carrier label APIs (REQ-005) and any renegotiation of the DHL and GLS contracts within the quarter.
- Poland at launch (REQ-039); targeted for Q3 as a follow-on.
- A new identity store (REQ-006) or any database technology beyond PostgreSQL (REQ-008).
- Automatically refusing returns from suspected serial returners (REQ-028): a human decision is always required.
- Horizontal scaling beyond four nodes to meet capacity (REQ-012).

## Personas

- Customer (online shopper in the launch markets)
- Loyalty programme member
- Returns reviewer (team unresolved: Customer Service or Finance)
- Distribution-centre operative
- Contact-centre agent

## User stories

- **US-01** — As a Customer (online shopper in the launch markets), I want to request a return of one or more items from an order myself, without calling anyone, so that I can start a return whenever I want, on my own, and the company stops paying contact-centre time to collect my request. _(REQ-018, REQ-053, REQ-054, REQ-055, REQ-057, REQ-059)_
- **US-02** — As a Customer (online shopper in the launch markets), I want to see the return window that will apply to me before I complete a purchase, so that the window I am offered is the window I can rely on, and I can factor it into my decision to buy. _(REQ-024, REQ-022, REQ-023, REQ-025)_
- **US-03** — As a Customer (online shopper in the launch markets), I want to see the delivery date and the return-window closing date, with the applicable window length, on the returns screen, so that I know exactly how much time I have left and that the window runs from delivery, not dispatch. _(REQ-044, REQ-045, REQ-058)_
- **US-04** — As a Loyalty programme member, I want the thirty-day window I was offered at purchase, never shortened retroactively, so that my loyalty benefit is real and predictable. _(REQ-022, REQ-023, REQ-025)_
- **US-05** — As a Customer (online shopper in the launch markets), I want each item to show a reason list with its fee implications, and the refund estimate and handling fee to reflect my selection before I submit, so that I understand before submitting whether a EUR 3.90 handling fee applies and why, and I do not escalate afterwards. _(REQ-040, REQ-041, REQ-042, REQ-043, REQ-054, REQ-055, REQ-059)_
- **US-06** — As a Customer (online shopper in the launch markets), I want a real carrier label within about fifteen minutes of my request being approved, and to see that timeline before I submit, so that I can take the parcel to a drop-off point the same afternoon instead of waiting for an overnight batch. _(REQ-005, REQ-026, REQ-027, REQ-056)_
- **US-07** — As a Customer (online shopper in the launch markets), I want refund initiation and refund settlement shown as two separate states, with the expected settlement date, so that I do not call asking where my money is while Adyen settlement is still pending. _(REQ-004, REQ-019, REQ-020, REQ-021)_
- **US-08** — As a Customer (online shopper in the launch markets), I want to cancel my return request myself at any point before the carrier scans the parcel, and have a cancelled request closed out, so that a change of mind does not leave a phantom return distorting the stock forecast. _(REQ-048, REQ-049, REQ-050)_
- **US-09** — As a Returns reviewer (team unresolved: Customer Service or Finance), I want returns from customers suspected of serial returning to arrive in a review queue for a human decision, so that a suspicious case is never auto-refused and a good customer is never blocked by mistake. _(REQ-028)_
- **US-10** — As a Distribution-centre operative, I want to record returned-item condition with a harmonised set of structured codes shared across all four distribution centres, so that Hamburg and Venlo stop recording the same condition differently, and the dropdown can actually be built. _(REQ-051, REQ-052)_
- **US-11** — As a Contact-centre agent, I want customers to be able to answer refund-status questions themselves in the portal, so that my queue is no longer dominated by 'where is my refund' calls. _(REQ-004, REQ-019, REQ-020)_

## Requirements

### Functional

| ID | Pri | Requirement | Acceptance criteria | Sources |
|---|---|---|---|---|
| REQ-004 | MUST | **Distinct refund initiation and settlement states, never conflated in logic or communications**<br>The portal must model refund initiation and refund settlement as two distinct states and show both to the customer; its logic and customer-facing communications must never state or imply that funds have been received before settlement completes (Adyen settles at T+7). | • The portal tracks refund initiation and settlement states separately for each refund.<br>• Customer-facing messages do not state or imply that funds have been received before settlement completes.<br>• Communications reflect that the refund amount appears on the customer's statement only after settlement.<br>• The portal shows refund initiation and refund settlement as two separate, distinguishable statuses.<br>• A customer whose refund has been initiated but not yet settled sees a status that makes clear the money has not yet reached their account. | 02-it-architecture-constraints.pdf; 02-it-architecture-constraints.pdf; 02-it-architecture-constraints.pdf; 02-it-architecture-constraints.pdf; 03-kickoff-meeting.vtt @ 00:02:41 Priya Raman; 03-kickoff-meeting.vtt @ 00:02:05 Lena Fischer; 03-kickoff-meeting.vtt @ 00:02:26 Tomasz Nowak; 03-kickoff-meeting.vtt @ 00:02:26 Tomasz Nowak |
| REQ-011 | MUST | **Capacity target: 19,000 returns/month at 400 concurrent users**<br>The system must sustain 19,000 return requests per month with a peak of 400 concurrent users. | • The portal remains operational during a load test with 400 concurrent users.<br>• The portal sustains a simulated monthly volume of 19,000 return requests. | 02-it-architecture-constraints.pdf |
| REQ-016 | MUST | **Steering-group decisions on flagged BRD conflicts before build**<br>Before build starts, the steering group must make an explicit, recorded decision on each conflict the architecture note flags against the BRD: BR-04 label timing, BR-06 refund timing, and the section 6 retention overlap. | • A written decision record exists for each of the three flagged conflicts (BR-04, BR-06, section 6 retention).<br>• No build work that depends on those decisions begins before they are recorded. | 02-it-architecture-constraints.pdf; 02-it-architecture-constraints.pdf |
| REQ-018 | MUST | **Self-service returns portal**<br>The system must provide a customer-facing self-service returns portal for initiating and tracking returns of purchases made from the company. | • A customer can initiate a return through the portal without calling the contact centre.<br>• A customer can track an existing return through the portal without calling the contact centre. | 03-kickoff-meeting.vtt @ 00:00:04 Priya Raman |
| REQ-019 | MUST | **Customer-facing return status view**<br>The system must give customers a status view of the full journey of their return, from request through refund settlement. | • A customer can see the current status of each of their returns.<br>• A customer can answer 'where is my refund' from the status view without contacting support. | 03-kickoff-meeting.vtt @ 00:00:54 Priya Raman; 03-kickoff-meeting.vtt @ 00:00:40 Tomasz Nowak; 03-kickoff-meeting.vtt @ 00:00:40 Tomasz Nowak; 03-kickoff-meeting.vtt @ 00:02:26 Tomasz Nowak |
| REQ-020 | MUST | **Expected settlement date shown to customer**<br>The system must display the expected refund settlement date to the customer. | • Every initiated refund shows an expected settlement date in the status view. | 03-kickoff-meeting.vtt @ 00:02:41 Priya Raman; 03-kickoff-meeting.vtt @ 00:02:05 Lena Fischer; 03-kickoff-meeting.vtt @ 00:02:05 Lena Fischer |
| REQ-021 | MUST | **Refund initiation within five business days**<br>The system must initiate the refund within five business days of return approval; the five-business-day target applies to initiation only, not settlement. | • For every approved return, the time from approval to refund initiation is at most five business days.<br>• Settlement timing is governed by the expected settlement date, not by the five-business-day target. | 03-kickoff-meeting.vtt @ 00:02:41 Priya Raman; 03-kickoff-meeting.vtt @ 00:02:05 Lena Fischer; 03-kickoff-meeting.vtt @ 00:02:05 Lena Fischer |
| REQ-022 | MUST | **Return window: thirty days for loyalty members, fourteen days for others**<br>The system must apply a thirty-day return window to loyalty programme members and a fourteen-day return window to all other customers. | • An order belonging to a loyalty member is eligible for return up to thirty days.<br>• An order belonging to a non-member is eligible for return up to fourteen days.<br>• No order is ever eligible for a window shorter than fourteen days.<br>• A loyalty member is shown a 30-day return window.<br>• A non-member is shown a 14-day return window. | 03-kickoff-meeting.vtt @ 00:01:46 Priya Raman; 03-kickoff-meeting.vtt @ 00:01:10 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:01:10 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:00:54 Priya Raman; 03-kickoff-meeting.vtt @ 00:01:10 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:01:10 Marco Bianchi; 04-refinement-notes.md @ Return window; 05-returns-screen-wireframe.png @ image |
| REQ-024 | MUST | **Return window visible before purchase**<br>The system must display the return window applicable to the customer before the purchase is completed. | • Before checkout completes, the customer sees the return window that will apply to the order.<br>• A logged-in loyalty member is shown the thirty-day window before purchase. | 03-kickoff-meeting.vtt @ 00:01:46 Priya Raman; 03-kickoff-meeting.vtt @ 00:01:31 Sofia Greco; 03-kickoff-meeting.vtt @ 00:01:10 Marco Bianchi |
| REQ-026 | MUST | **Return label within fifteen minutes of approval**<br>The system must make the return label available within fifteen minutes of the return request being approved, using a pre-drawn pool of carrier labels. | • A return label request approved at any time of day yields a usable label within fifteen minutes.<br>• A label requested at 09:00 is available by 09:15 the same day, not the next day.<br>• Label availability does not depend on the 02:00 carrier batch run. | 03-kickoff-meeting.vtt @ 00:03:50 Priya Raman; 03-kickoff-meeting.vtt @ 00:03:50 Priya Raman; 03-kickoff-meeting.vtt @ 00:04:04 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:03:29 Lena Fischer; 03-kickoff-meeting.vtt @ 00:03:29 Lena Fischer; 03-kickoff-meeting.vtt @ 00:02:58 Lena Fischer; 03-kickoff-meeting.vtt @ 00:02:58 Lena Fischer; 03-kickoff-meeting.vtt @ 00:03:16 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:02:58 Lena Fischer |
| REQ-027 | MUST | **DHL and GLS carrier support**<br>The system must support return label generation for both DHL and GLS. | • A return can be completed with a label from either DHL or GLS. | 03-kickoff-meeting.vtt @ 00:02:58 Lena Fischer; 03-kickoff-meeting.vtt @ 00:03:29 Lena Fischer |
| REQ-028 | MUST | **Review queue for suspicious returns (BR-08)**<br>The system must route returns from customers suspected of serial returning to a review queue for a human decision, and must never automatically refuse a return. | • A return flagged as suspicious is neither automatically refunded nor automatically refused.<br>• Suspicious returns appear in a review queue where a human decision-maker can act.<br>• The outcome of the human decision is recorded. | 03-kickoff-meeting.vtt @ 00:04:57 Priya Raman; 03-kickoff-meeting.vtt @ 00:04:57 Priya Raman; 03-kickoff-meeting.vtt @ 00:04:38 Tomasz Nowak; 03-kickoff-meeting.vtt @ 00:04:38 Tomasz Nowak; 03-kickoff-meeting.vtt @ 00:04:38 Tomasz Nowak |
| REQ-039 | MUST | **Poland excluded from launch wave**<br>Poland must not be part of the launch wave; the Poland launch is targeted for Q3 and treated as a follow-on. | • Launch-wave scope contains no Poland-specific functionality.<br>• Poland appears in planning only as a Q3 follow-on. | 04-refinement-notes.md @ Poland; 03-kickoff-meeting.vtt @ 00:07:04 Priya Raman; 04-refinement-notes.md @ Poland |
| REQ-040 | MUST | **Handling fee determined by return reason**<br>When a customer requests a return, the handling fee must be determined by the return reason in combination with order value, not by order value alone. | • Two returns with the same order value but different reasons can yield different fees.<br>• Fee calculation logic references the return reason. | 04-refinement-notes.md @ Free returns and the handling fee; 03-kickoff-meeting.vtt @ 00:07:28 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:07:35 Priya Raman; 03-kickoff-meeting.vtt @ 00:07:35 Priya Raman; 04-refinement-notes.md @ Purpose; 04-refinement-notes.md @ Free returns and the handling fee |
| REQ-041 | MUST | **Company-fault returns are always free**<br>Where the return reason is 'damaged in transit', 'wrong item received', or 'delivered late', the return must be free of the handling fee regardless of order value. | • Each of the three company-fault reasons is displayed as 'free return'.<br>• No handling fee is charged for these reasons on any order value. | 04-refinement-notes.md @ Free returns and the handling fee; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image |
| REQ-042 | MUST | **Change-of-mind returns incur fee regardless of order value**<br>Where the return reason is 'changed mind', the EUR 3.90 handling fee must apply regardless of order value, including orders above EUR 50. | • A change-of-mind return on an order above EUR 50 shows a EUR 3.90 handling fee.<br>• The EUR 50 threshold does not waive the fee for this reason. | 04-refinement-notes.md @ Free returns and the handling fee; 05-returns-screen-wireframe.png @ image; 04-refinement-notes.md @ Free returns and the handling fee |
| REQ-043 | SHOULD | **Does-not-fit returns free above EUR 50**<br>Where the return reason is 'does not fit', the return must be free when the order value exceeds EUR 50. | • The 'Does not fit' reason is displayed as 'free return over EUR 50'. | 05-returns-screen-wireframe.png @ image; 04-refinement-notes.md @ Free returns and the handling fee |
| REQ-044 | MUST | **Return window measured from delivery**<br>The return window must run from the delivery date, not the dispatch date. | • For an order delivered 3 April, a loyalty member's window closes 3 May.<br>• Window close dates are computed from the delivery date in all cases. | 04-refinement-notes.md @ Return window; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image |
| REQ-045 | SHOULD | **Delivery date as salient window basis** `derived`<br>The portal must present the delivery date as the salient basis for the return window, at least as prominently as the dispatch date. | • On the returns screen the delivery date and window-close date are both displayed.<br>• The delivery date is not visually subordinate to the dispatch date. | 04-refinement-notes.md @ Return window; 04-refinement-notes.md @ Return window; 05-returns-screen-wireframe.png @ image |
| REQ-048 | MUST | **Customer self-service cancellation**<br>The customer must be able to cancel their own return request at any point before the parcel has been scanned by the carrier. | • A cancel affordance is available while the parcel is unscanned.<br>• A customer can cancel without staff involvement.<br>• The screen states the cancellation cut-off. | 04-refinement-notes.md @ Cancelling a return; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image; 04-refinement-notes.md @ Cancelling a return |
| REQ-049 | MUST | **Cancellation cut-off at carrier scan**<br>Once the parcel has been scanned by the carrier, cancellation must no longer be possible and the return must complete as a normal return. | • Cancellation is unavailable after carrier scan.<br>• A scanned return follows the normal return completion flow. | 04-refinement-notes.md @ Cancelling a return; 05-returns-screen-wireframe.png @ image |
| REQ-050 | SHOULD | **Cancelled requests closed out** `derived`<br>A cancelled return request must be closed in the system rather than left open, so that it no longer distorts the stock forecast. | • After cancellation, the request reaches a terminal state.<br>• Cancelled requests are excluded from the stock forecast. | 04-refinement-notes.md @ Cancelling a return; 04-refinement-notes.md @ Cancelling a return; 04-refinement-notes.md @ Cancelling a return |
| REQ-052 | SHOULD | **Structured condition capture** `derived`<br>The portal must support recording the condition of returned items using structured codes rather than free-text notes. | • Returned-item condition can be recorded against a code list.<br>• Condition data is comparable across DCs. | 04-refinement-notes.md @ Condition on arrival; 04-refinement-notes.md @ Still open |
| REQ-053 | MUST | **Per-item return selection rows**<br>The returns screen must show each order item as a row containing a checkbox, product image placeholder, product name, unit price, quantity, and a 'Reason:' dropdown defaulting to '-- select --'. | • Each item row renders all listed fields.<br>• The reason dropdown defaults to '-- select --'. | 05-returns-screen-wireframe.png @ image |
| REQ-054 | MUST | **Reason options shown with fee implications**<br>The returns screen must list the return reason options together with their fee implications: Damaged in transit (free return), Wrong item received (free return), Does not fit (free return over EUR 50), Changed mind (EUR 3.90 handling fee applies), Delivered late (free return). | • All five reasons are visible with the stated fee implications.<br>• Fee implications shown on screen match the fee-engine rules. | 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image |
| REQ-055 | MUST | **Refund estimate and handling fee footer** `derived`<br>The returns screen must display a 'Refund estimate' and a 'Handling fee' in the footer, reflecting the selected items and their reasons. | • Selecting the EUR 89.00 jumper with a free reason shows Refund estimate EUR 89.00 and Handling fee EUR 0.00.<br>• Changing the selection or reason updates both values. | 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image |
| REQ-056 | SHOULD | **Timeline expectations displayed**<br>The returns screen must display the expected timelines: return label ready in approximately 15 minutes, refund initiated within 5 business days, and settlement to the customer's card within 7 further days. | • The footer displays the three timeline statements. | 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image |
| REQ-057 | SHOULD | **Returns page framing**<br>The returns page must be titled 'Return items from this order' and show the breadcrumb 'NORDWIND \| My orders > Order <order id> > Return items'. | • Page title matches the spec.<br>• Breadcrumb renders with the order id substituted. | 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image |
| REQ-058 | MUST | **Delivery and window-close dates displayed**<br>The returns screen must display the order's delivery date and the date the return window closes, including the applicable window length (e.g. 'loyalty: 30 days'). | • For a loyalty member's order delivered 3 April, the screen shows 'Order delivered 3 April.' and 'Return window closes 3 May (loyalty: 30 days).' | 05-returns-screen-wireframe.png @ image; 05-returns-screen-wireframe.png @ image |
| REQ-059 | MUST | **Submit return request**<br>The customer must be able to submit the return request from the returns screen via a primary 'Request return' action. | • A primary 'Request return' button is present.<br>• Activating it submits the return request. | 05-returns-screen-wireframe.png @ image |

### Non Functional

| ID | Pri | Requirement | Acceptance criteria | Sources |
|---|---|---|---|---|
| REQ-032 | SHOULD | **Peak return-volume capacity** `derived`<br>The system must handle at least twice the baseline monthly return volume (approximately 16,400 returns in a peak month). | • The system operates normally while processing at least twice the baseline monthly return volume.<br>• The January peak does not require support or operations workarounds caused by volume. | 03-kickoff-meeting.vtt @ 00:00:21 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:00:21 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:06:06 Lena Fischer |

### Constraint

| ID | Pri | Requirement | Acceptance criteria | Sources |
|---|---|---|---|---|
| REQ-001 | MUST | **SAP ECC remains system of record; no phase 1 SAP writes**<br>The returns portal must treat SAP ECC as the system of record for stock and for the financial posting of credit notes, and must not write to SAP during phase 1. | • No portal component creates, updates or deletes data in SAP ECC during phase 1.<br>• Stock values and credit-note postings acted upon by the portal originate from SAP ECC. | 02-it-architecture-constraints.pdf @ p.1; 02-it-architecture-constraints.pdf @ p.1 |
| REQ-002 | MUST | **Stock and goods-receipt data via nightly IDoc feed only**<br>The portal must obtain stock and goods-receipt data exclusively through the nightly IDoc feed (completing at 23:30 CET) and must not depend on a real-time interface for that data. | • The phase 1 design contains no synchronous stock or goods-receipt integration.<br>• Portal behaviour is correct when stock data is up to one nightly feed old. | 02-it-architecture-constraints.pdf @ p.1; 02-it-architecture-constraints.pdf |
| REQ-003 | MUST | **Adyen as sole payment service provider**<br>All consumer payment and refund processing must use Adyen, the designated payment service provider for all consumer channels, with refunds initiated through the Adyen API. | • Every refund is initiated via the Adyen API.<br>• No alternative payment service provider integration exists in the portal. | 02-it-architecture-constraints.pdf @ p.1; 02-it-architecture-constraints.pdf |
| REQ-005 | MUST | **Return label generation via nightly carrier batch**<br>Return labels for DHL and GLS must be generated through the existing batch job that runs at 02:00 CET; the portal must not rely on a synchronous label API from either carrier. | • No synchronous DHL or GLS label API call exists in the phase 1 design.<br>• A label requested at 09:00 CET is made available no earlier than the morning after the next 02:00 CET batch run. | 02-it-architecture-constraints.pdf @ p.1; 02-it-architecture-constraints.pdf; 02-it-architecture-constraints.pdf |
| REQ-006 | MUST | **Authentication via existing Azure AD B2C tenant**<br>Customer authentication must use the existing Azure AD B2C tenant, and no new identity store may be introduced. | • Every customer login flow authenticates against the existing Azure AD B2C tenant.<br>• No new identity store or directory is provisioned for the portal. | 02-it-architecture-constraints.pdf @ p.1; 02-it-architecture-constraints.pdf @ p.1 |
| REQ-007 | MUST | **EU data residency in Frankfurt region**<br>All portal data must be stored and processed in the EU, in the Frankfurt region. | • All storage and processing resources for the portal are located in the EU Frankfurt region.<br>• No portal data is stored or processed outside the EU. | 02-it-architecture-constraints.pdf @ p.1 |
| REQ-008 | MUST | **PostgreSQL as sole relational database**<br>The portal must use PostgreSQL as its only relational database, and no new database technology may be provisioned for the project. | • All relational persistence in the portal uses PostgreSQL.<br>• No other database engine is provisioned or introduced for the project. | 02-it-architecture-constraints.pdf @ p.1; 02-it-architecture-constraints.pdf @ p.1 |
| REQ-009 | MUST | **Security Review Board clearance for new public endpoints**<br>Every new public endpoint must pass the Security Review Board, which requires a completed threat model and a 10-working-day lead time. | • Every new public endpoint has a completed threat model and SRB approval before it is exposed to production traffic.<br>• SRB review requests are submitted at least 10 working days before the planned exposure date. | 02-it-architecture-constraints.pdf @ p.1 |
| REQ-010 | MUST | **Customer-facing traffic routed through existing WAF**<br>All customer-facing traffic must route through the existing web application firewall. | • All customer-facing traffic passes through the existing WAF.<br>• No public route into the portal bypasses the WAF. | 02-it-architecture-constraints.pdf @ p.1 |
| REQ-012 | MUST | **Scaling ceiling of four nodes**<br>The system must meet the stated capacity (19,000 return requests per month, peak 400 concurrent users) without horizontal scaling beyond four nodes. | • The capacity targets are met on a deployment of at most four nodes. | 02-it-architecture-constraints.pdf |
| REQ-013 | MUST | **7-year audit reproducibility of automated decisions**<br>Every automated accept, reject or fee decision must be reproducible on demand for 7 years, independently of the marketing-consent lifecycle. | • For any automated accept, reject or fee decision made within the last 7 years, the original inputs can be re-evaluated and the original outcome reproduced on demand.<br>• Reproducibility of a decision does not depend on marketing-consent data still being held. | 02-it-architecture-constraints.pdf @ p.1; 02-it-architecture-constraints.pdf |
| REQ-014 | MUST | **OpenTelemetry tracing for all services**<br>All portal services must emit traces to the existing OpenTelemetry collector. | • Every service in the portal emits traces that are consumed by the existing OpenTelemetry collector.<br>• No portal service is trace-silent. | 02-it-architecture-constraints.pdf @ p.1 |
| REQ-015 | MUST | **Deployment freeze during peak trading**<br>Deployments must not occur between 15 December and 6 January (peak trading freeze). | • The release calendar contains no production deployment in the window from 15 December through 6 January.<br>• No production release occurs during the freeze window. | 02-it-architecture-constraints.pdf @ p.1; 03-kickoff-meeting.vtt @ 00:06:06 Lena Fischer; 03-kickoff-meeting.vtt @ 00:06:06 Lena Fischer |
| REQ-017 | SHOULD | **Phase 1 scope boundary** `derived`<br>The portal must be delivered as a new service without modifying the existing order-history application beyond adding an entry point, and must not address marketplace orders (which are held in a separate schema). | • The existing order-history application is changed only by the addition of the new entry point.<br>• No marketplace-order data or functionality appears in the phase 1 portal scope. | 02-it-architecture-constraints.pdf; 02-it-architecture-constraints.pdf @ p.2 |
| REQ-023 | MUST | **Statutory minimum return window of fourteen days**<br>The system must never apply a return window shorter than fourteen days. | • No customer is ever presented or applied a return window below fourteen days. | 03-kickoff-meeting.vtt @ 00:01:31 Sofia Greco; 03-kickoff-meeting.vtt @ 00:01:31 Sofia Greco |
| REQ-025 | MUST | **No retroactive shortening of an offered return window**<br>The system must honour the return window offered at the time of purchase and must not apply a shorter window retroactively. | • A customer to whom a thirty-day window was presented at purchase can return within thirty days, even if their loyalty status later changes. | 03-kickoff-meeting.vtt @ 00:01:31 Sofia Greco |
| REQ-029 | MUST | **Marketplace returns excluded pending seller-agreement review**<br>The system must not provide a marketplace return flow until the seller agreement has been reviewed and it is confirmed that accepting such returns does not create obligations for the company. | • No marketplace return entry point exists in the portal until the seller-agreement review is complete and signed off. | 03-kickoff-meeting.vtt @ 00:05:28 Sofia Greco; 03-kickoff-meeting.vtt @ 00:05:28 Sofia Greco; 03-kickoff-meeting.vtt @ 00:05:43 Priya Raman |
| REQ-030 | MUST | **Launch countries: Germany, Austria, Netherlands**<br>The system must be available to customers in Germany, Austria, and the Netherlands at launch. | • Customers in Germany, Austria, and the Netherlands can initiate and track returns at launch. | 03-kickoff-meeting.vtt @ 00:05:54 Marco Bianchi |
| REQ-031 | MUST | **Live and stable by 1 December**<br>The portal must be live and stable no later than 1 December. | • The portal is live in the launch countries on or before 1 December.<br>• The portal remains stable through the January return peak. | 03-kickoff-meeting.vtt @ 00:06:24 Priya Raman; 03-kickoff-meeting.vtt @ 00:06:06 Lena Fischer; 03-kickoff-meeting.vtt @ 00:00:21 Marco Bianchi; 03-kickoff-meeting.vtt @ 00:00:21 Marco Bianchi |
| REQ-033 | MUST | **Personal data retention: twenty-four months**<br>The system must apply a twenty-four-month retention period for personal data. | • Personal data is not retained beyond the twenty-four-month retention period. | 03-kickoff-meeting.vtt @ 00:04:11 Sofia Greco; 03-kickoff-meeting.vtt @ 00:04:11 Sofia Greco |
| REQ-034 | MUST | **Seven-year audit-record retention**<br>Audit records covering each return's accept/reject/fee decision must be retained for seven years and remain available and queryable throughout that period; records include the accept/reject decision and the fee outcome. | • Audit records remain available and queryable for seven years.<br>• Audit records remain retrievable for 7 years after the decision.<br>• Records include the accept/reject decision and the fee outcome. | 03-kickoff-meeting.vtt @ 00:04:11 Sofia Greco; 03-kickoff-meeting.vtt @ 00:04:11 Sofia Greco; 04-refinement-notes.md @ Retention |
| REQ-035 | MUST | **Data model design blocked until retention position**<br>The team must not design the portal's data model until the written retention position is delivered. | • No data-model artefacts are produced or approved before the retention position document is delivered. | 03-kickoff-meeting.vtt @ 00:04:30 Priya Raman; 03-kickoff-meeting.vtt @ 00:04:30 Priya Raman; 03-kickoff-meeting.vtt @ 00:04:11 Sofia Greco |
| REQ-036 | MUST | **Completion and stabilisation before deployment freeze**<br>The delivery plan must treat 1 December as the date by which all planned scope is complete, and must include a two-week stabilisation period between 1 December and the deployment freeze. | • No planned scope item is scheduled for completion after 1 December.<br>• The plan shows a continuous two-week stabilisation window between 1 December and the deployment freeze.<br>• New scope is only accepted if it fits before 1 December. | 03-kickoff-meeting.vtt @ 00:06:24 Priya Raman; 03-kickoff-meeting.vtt @ 00:06:24 Priya Raman |
| REQ-037 | MUST | **Planning capacity assumption**<br>Effort estimates for the quarter must assume a team of four engineers and one designer. | • All plan estimates are consistent with a capacity of four engineers and one designer.<br>• No estimate assumes additional engineering or design capacity. | 03-kickoff-meeting.vtt @ 00:06:40 Lena Fischer; 03-kickoff-meeting.vtt @ 00:06:40 Lena Fischer |
| REQ-038 | MUST | **Zero-sum phase-one scope**<br>If marketplace returns are added to phase one, an item of equivalent size must be removed from phase-one scope. | • Any proposal adding marketplace returns to phase one names the scope item being removed.<br>• Phase-one scope as recorded at the kickoff contains no marketplace returns. | 03-kickoff-meeting.vtt @ 00:06:40 Lena Fischer; 03-kickoff-meeting.vtt @ 00:06:51 Priya Raman |
| REQ-046 | MUST | **Pseudonymisation after 24 months**<br>Audit records must be pseudonymised after 24 months: the decision, amounts, and timestamps are retained while customer identifiers are replaced with a stable surrogate key. | • After 24 months, audit records contain no customer identifiers.<br>• The surrogate key is stable, so records belonging to the same customer remain linkable. | 04-refinement-notes.md @ Retention |
| REQ-047 | MUST | **Pseudonymisation by design**<br>Pseudonymisation must be designed into the returns portal from the start rather than retrofitted. | • The data model supports the 24-month pseudonymisation without re-architecture.<br>• Pseudonymisation is part of the initial design review, not a later retrofit task. | 04-refinement-notes.md @ Retention; 04-refinement-notes.md @ Retention |
| REQ-051 | MUST | **Harmonised condition codes before dropdown**<br>Before a condition-code dropdown is built, the condition codes must be harmonised across the four distribution centres. | • A single canonical condition-code set agreed across all four DCs exists before dropdown implementation starts.<br>• The Hamburg/Venlo differences are resolved in that set. | 04-refinement-notes.md @ Still open; 04-refinement-notes.md @ Condition on arrival; 04-refinement-notes.md @ Condition on arrival |

## Conflicts to resolve

- **REQ-005 / REQ-026** — D-005 requires return labels to be generated only through the existing 02:00 CET nightly carrier batch with no synchronous carrier API (a 09:00 request is available no earlier than the morning after the next batch), while D-027 requires the label within fifteen minutes of approval from a pre-drawn pool and explicitly states availability must not depend on the 02:00 batch (a 09:00 request available by 09:15). The two availability criteria are incompatible on the same scenario; this is the BR-04 label-timing conflict.
  - _Suggested resolution:_ This is exactly the BR-04 conflict that D-016 requires the steering group to decide and record before build. The likely reconciliation is that the pre-drawn pool is replenished by the 02:00 batch (preserving the 'no synchronous carrier API' constraint) and D-005's next-morning availability criterion is amended or dropped in favour of the 15-minute SLA; the decision must be recorded before any dependent build work starts.
- **REQ-011 / REQ-032** — D-011 sets the capacity target at 19,000 return requests per month with a 400-concurrent-user peak, while D-034 requires handling at least twice the baseline monthly volume, i.e. approximately 16,400 returns in a peak month. The same quantity (sustained monthly return volume) carries two different figures.
  - _Suggested resolution:_ Reconcile into a single capacity requirement instead of silently picking one figure: if 19,000/month already exceeds 2x the baseline (~16,400), both can be read as floors and 19,000/month at 400 concurrent users governs; confirm with the BRD and the capacity decision which figure is authoritative, and keep D-012's four-node ceiling attached to whichever figure is adopted.
- **REQ-033 / REQ-046** — D-035 states personal data is not retained beyond the twenty-four-month retention period, while D-050 requires audit records to keep a stable surrogate key for seven years so records remain linkable to the same customer. A stable, linkable surrogate key may itself constitute personal data under GDPR, putting the two requirements in tension.
  - _Suggested resolution:_ Resolution belongs to the written retention position and the section 6 retention decision that D-016 requires from the steering group: it must state whether the stable surrogate satisfies the 24-month personal-data purge (including key derivation and reversibility controls), and Legal's approval of the pseudonymisation approach must be recorded before the data model is designed (D-037).

## Open questions

| Question | Why it matters | Blocking |
|---|---|---|
| Will Legal approve the pseudonymisation approach - customer identifiers in audit records replaced after 24 months by a stable surrogate key while the decision, amounts and timestamps remain for 7 years - and will the written retention position (BRD section 6) confirm how the twenty-four-month personal-data retention and the seven-year audit-record retention interact? | The data model cannot be designed until the written retention position is delivered (D-037), and the section 6 retention conflict cannot be closed by the steering group without it. | yes |
| Are marketplace returns in scope for the portal, and in which phase? Specifically: will steering approve phase-one inclusion, and if so, which equal-sized scope item is removed under the zero-sum rule (D-040)? | Marketplace orders are about 11% of order volume and their returns are contractually the seller's responsibility, yet customers complain to the company anyway. The phase-one baseline excludes marketplace returns (D-017, D-040), and no marketplace return flow may exist until the seller-agreement review is signed off (D-030). If steering approves inclusion, phase-one deliverables change and an offsetting removal must be named. | yes |
| Which condition codes do the four distribution centres actually use, and what is the agreed harmonised canonical set? | The harmonisation constraint (D-055) requires a canonical condition-code set agreed across all four DCs before the condition dropdown can be built, and the action item to obtain the DCs' lists is unresolved. | yes |
| Which department staffs the review queue for suspected serial returners - Customer Service or Finance? | Staffing determines workflow design, SLAs and access controls for the review queue (D-029), wherever it sits in scope. | yes |
| Is the five-reason return-reason taxonomy (including 'Does not fit') complete and approved, and what handling fee applies to 'Does not fit' returns below EUR 50? | The fee engine needs a complete, approved taxonomy with unambiguous fee rules (D-042, D-058); only the above-EUR-50 case for 'Does not fit' is currently specified (D-045), leaving the below-threshold fee undefined. | yes |

## Success metrics

| Metric | Definition | Baseline | Target |
|---|---|---|---|
| Monthly return requests through the portal | Number of return requests submitted via the portal in a calendar month, grouped by launch country (DE/AT/NL). | 8,200 returns/month handled today (all channels) | — |
| Refund-status contact share | Monthly share of all customer contacts whose recorded reason is a refund-status enquiry ('where is my refund'), taken from contact-centre categorisation data. | 4 in 10 (40%) | — |
| Contact-centre cost per return | Contact-centre cost attributable to returns handling in a month, divided by the number of returns handled in that month. | EUR 7.50 | — |
| Label time-to-availability | Elapsed minutes between the return-approval timestamp and the timestamp at which the return label is first available to the customer in the portal, per request; report median and p95. | next-day for 09:00 requests (02:00 CET batch) | 15 minutes or less (REQ-026) |
| Refund initiation latency | Business days between return approval and the refund-initiation call to the Adyen API, per return. | — | 5 business days or fewer (REQ-021) |
| Abandoned return-request rate | Return requests raised via the phone channel that are subsequently abandoned, as a share of all phone-raised return requests in a month. | 15% (phone channel today) | — |
| Handling-fee escalations | Number of customer escalations specifically about the handling fee in a quarter. | 61 (last quarter) | — |
| Review-queue routing rate | Share of portal return requests routed to the serial-returner review queue in a month. | approx. 1 in 40 (current, agent-identified) | — |

## Risks

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| The label-timing conflict is unresolved: REQ-005 restricts label generation to the 02:00 CET carrier batch with no synchronous carrier API, while REQ-026 requires a label within fifteen minutes of approval from a pre-drawn pool. On the same scenario the two MUST requirements give next-morning and same-quarter-hour availability; building to the wrong model wastes most of the quarter. | high | high | Obtain the steering decision on BR-04 label timing (REQ-016) before any label work starts; do not let either requirement be silently dropped or silently assumed. |
| REQ-011 (19,000 requests/month, 400 concurrent users) and REQ-032 (at least twice baseline, approx. 16,400 in a peak month) state two different figures for the same quantity. Performance work sized to the wrong figure either fails at the January peak or overspends. | medium | medium | Ask the steering group to reconcile the two figures into one sustained design target with one load-test target before performance testing starts. |
| The retention position from Legal has not landed. REQ-035 blocks the data model, and if the written position arrives late or rejects the stable-surrogate-key design, the 1 December date is at risk. | high | medium | Sofia Greco to deliver the written position before data-model design begins; build pseudonymisation in from the start (REQ-047) so either Legal outcome is constructible without rework. |
| No channel is specified for carrier scan events, so the cancellation cut-off (REQ-048, REQ-049) cannot be enforced in a timely manner. If scan events arrive only via a nightly batch, customers could cancel after the physical scan but before the system registers it, leaving refunds and stock wrong for that window. | high | medium | Specify the scan-event mechanism and latency (carrier tracking feed, IDoc extension or polling) before the cancellation flow is built; if nightly-only, define the acceptable overlap window and the reconciliation behaviour. |
| Launch is in Germany, Austria and the Netherlands (REQ-030), but no requirement covers language support and the wireframe copy (REQ-053 through REQ-059) is English. Defaulting to an English-only build risks rework and poor customer experience in two of three launch markets. | medium | high | Decide launch languages before UI build; treat wireframe strings as source-language placeholders and externalise all customer-facing copy, including fee labels, timeline messages and status text. |
| REQ-024 requires the return window to be shown before purchase, but the portal is a post-purchase service (REQ-017) with no pre-purchase context. Without a named owner the requirement may go unimplemented. | medium | medium | Name the checkout platform as the owning system for REQ-024 and record a cross-team dependency; limit the portal's obligation to the post-purchase display (REQ-058). |
| Every new public endpoint needs SRB clearance with a completed threat model and a 10-working-day lead (REQ-009). Endpoints discovered late will miss the 1 December date (REQ-031). | medium | medium | Inventory all public endpoints during design, batch SRB submissions early, and put the 10-working-day lead time into the delivery plan. |
| Condition codes differ between distribution centres (Hamburg and Venlo named) and harmonisation is not done; REQ-051 blocks the dropdown, and structured condition capture (REQ-052) slips if the canonical set arrives late. | medium | medium | Tomasz Nowak to obtain the codes the four DCs actually use; agree the harmonised set in the M1 gate before any dropdown work starts. |
| The review queue for suspected serial returners (REQ-028) has no agreed staffing team (Customer Service vs Finance). A queue with no one working it would defeat BR-08. | medium | medium | Decide staffing before go-live; build the queue role-agnostic so either team can staff it without rework. |
| Marketplace scope could be added mid-phase without an equal-size removal, overloading a four-engineer, one-designer team (REQ-037) and breaking the zero-sum rule (REQ-038). | medium | medium | Enforce REQ-038 at the steering gate: Priya Raman's scope proposal must name the equal-size phase-1 removal if marketplace returns are added. |
| January volume more than doubles the monthly baseline. If the portal is not stable by the deployment freeze (15 December to 6 January, REQ-015), it misses the peak it exists to serve (REQ-031, REQ-036). | high | medium | Treat 1 December as the hard delivery date; protect the two-week stabilisation period and refuse scope additions that would consume it. |

## Milestones

| Milestone | Requirements | Target |
|---|---|---|
| **M1: Decisions, dependencies and approval gate** — Steering decisions on the three conflicts (REQ-016); Legal's written retention position before data-model design (REQ-035); condition-code harmonisation (REQ-051); marketplace scope with zero-sum trade (REQ-029, REQ-038); ownership of the pre-purchase return-window display (REQ-024); the carrier scan-event channel for REQ-048 and REQ-049. Endpoint inventory and threat models start here for the SRB (REQ-009); plan against four engineers and one designer (REQ-037). | REQ-016, REQ-035, REQ-051, REQ-029, REQ-038, REQ-024, REQ-009, REQ-037 | — |
| **M2: Platform foundations** — Integration with existing infrastructure: SAP ECC as system of record with the nightly IDoc feed (REQ-001, REQ-002), Adyen as sole PSP (REQ-003), Azure AD B2C (REQ-006), EU/Frankfurt residency and PostgreSQL (REQ-007, REQ-008), WAF routing (REQ-010), OpenTelemetry tracing (REQ-014). | REQ-001, REQ-002, REQ-003, REQ-006, REQ-007, REQ-008, REQ-010, REQ-014 | — |
| **M3: Core returns flow and UI** — Self-service request and tracking: full-journey status view with the two-state refund (REQ-018, REQ-019, REQ-020, REQ-004), five-business-day initiation target (REQ-021), reason-based fee logic (REQ-040, REQ-041, REQ-042, REQ-043), return windows measured from delivery (REQ-022, REQ-023, REQ-025, REQ-044, REQ-045), the returns screen per the wireframe (REQ-053 through REQ-059), structured condition capture (REQ-052), and the fifteen-minute label from a pre-drawn pool generated via the carrier batch (REQ-026, REQ-027, REQ-005, REQ-056). | REQ-018, REQ-019, REQ-020, REQ-004, REQ-021, REQ-040, REQ-041, REQ-042, REQ-043, REQ-022, REQ-023, REQ-025, REQ-044, REQ-045, REQ-052, REQ-053, REQ-054, REQ-055, REQ-056, REQ-057, REQ-058, REQ-059, REQ-026, REQ-027, REQ-005 | — |
| **M4: Cancellation and review queue** — Self-service cancellation with the cut-off at carrier scan, using the scan-event channel secured in M1, and closure of cancelled requests (REQ-048, REQ-049, REQ-050); review queue for suspected serial returners with no automatic refusal (REQ-028). | REQ-048, REQ-049, REQ-050, REQ-028 | — |
| **M5: Audit, retention and pseudonymisation** — Seven-year reproducible automated decisions (REQ-013); twenty-four-month personal-data retention and seven-year audit records (REQ-033, REQ-034); pseudonymisation by design with a stable surrogate key after twenty-four months (REQ-046, REQ-047). Built after the retention position from M1. | REQ-013, REQ-033, REQ-034, REQ-046, REQ-047 | — |
| **M6: Capacity and performance validation** — Load-testing against the reconciled capacity figure (see the conflict between REQ-011 and REQ-032) on at most four nodes (REQ-012). | REQ-011, REQ-012, REQ-032 | — |
| **M7: Launch and stabilisation** — Live in Germany, Austria and the Netherlands (REQ-030), Poland excluded (REQ-039), phase-1 scope boundary respected (REQ-017); all planned scope complete by 1 December with a two-week stabilisation period before the 15 December deployment freeze (REQ-031, REQ-036, REQ-015). | REQ-030, REQ-039, REQ-017, REQ-031, REQ-036, REQ-015 | 1 December (REQ-031) |

## Unresolved conflicts blocking approval

This PRD is not approvable until the three conflicts below are resolved by the steering group, as REQ-016 requires before build starts. Each conflict makes two MUST requirements mutually exclusive on a concrete scenario.

- BR-04 label timing - REQ-005 vs REQ-026. REQ-005 restricts label generation to the existing 02:00 CET carrier batch with no synchronous carrier API; REQ-026 requires the label within fifteen minutes of approval from a pre-drawn pool. For a request approved at 09:00, the first yields next-morning availability and the second 09:15. The kickoff recorded a decision to use a pre-drawn pool, but the architecture note's exclusivity wording has not been revised, so the conflict stands at the requirements level. Required decision: which availability model governs, and whether the architecture constraint is amended or the target renegotiated.
- Capacity figures - REQ-011 vs REQ-032. REQ-011 sets sustained capacity at 19,000 return requests per month with a 400-concurrent-user peak; REQ-032 requires at least twice the baseline monthly volume (approx. 16,400 in a peak month). The same quantity carries two figures. Required decision: confirm whether 19,000 is the sustained design target with 16,400 as the floor, or reconcile to a single figure, and name the load-test target.
- BRD section 6 retention overlap - REQ-033 vs REQ-046. REQ-033 applies a twenty-four-month retention period to personal data; REQ-046 keeps a stable surrogate key for seven years so audit records remain linkable to a customer. A stable, linkable surrogate key may itself be personal data under GDPR. Required decision: Legal's written position (open question 1) on how the two obligations interact; REQ-035 blocks the data model until it lands.

## Open questions and required decisions

Five open questions remain unanswered in the sources. Two stay blocking for approval; three are downgraded to pre-build or pre-launch decisions with owners, on the basis that the team can proceed meanwhile.

1. Retention and pseudonymisation position (blocking). Will Legal approve replacing customer identifiers with a stable surrogate key after 24 months while decisions, amounts and timestamps remain for 7 years, and will the written retention position confirm how the two obligations interact? Owner: Sofia Greco (Legal). Blocks REQ-035 and therefore the data model; cannot be downgraded.
2. Marketplace scope and phase (blocking for scope sign-off). Are marketplace returns in phase 1? Priya Raman is bringing a scope proposal to steering; any phase-1 inclusion must name the equal-size removal under the zero-sum rule (REQ-038). The team can proceed on non-marketplace scope meanwhile because REQ-029 keeps marketplace flows out by default.
3. Harmonised condition codes (downgraded to pre-dropdown). Which codes do the four distribution centres actually use, and what is the agreed canonical set? Owner: Tomasz Nowak to obtain the DC code lists. Blocks only the dropdown (REQ-051); structured capture and all other portal work proceed.
4. Review-queue staffing (downgraded to pre-launch). Customer Service or Finance? Anja says Finance, Tomasz disagrees. The queue (REQ-028) can be built role-agnostic; the decision is needed before go-live, not before build.
5. Return-reason taxonomy and 'does not fit' below EUR 50 (blocking for that fee branch). Is the five-reason taxonomy complete and approved, and what fee applies to 'does not fit' returns below EUR 50? REQ-054 displays 'free return over EUR 50', which implies a fee below the threshold that REQ-040 through REQ-043 do not define. The fee computation for that branch cannot be built until this is decided; everything else proceeds.

A sixth decision follows from the launch geography and is set out in the Localization section below.

## Localization

REQ-030 launches the portal in Germany, Austria and the Netherlands, which implies customer-facing copy in at least German and Dutch. No requirement currently says so, and the wireframe-derived requirements REQ-053 through REQ-059 quote English strings ('Request return', 'Return items from this order', '-- select --', the reason labels and timeline messages) as if they were final copy.

Position recorded in this revision:
- The wireframe strings are source-language placeholders, not approved final copy. They fix layout, field labels and message content; the English wording shown is not committed.
- A steering decision is required before UI build starts: which languages are supported at launch, and who owns the translated copy. If the decision is English-only launch with localization deferred, that decision must be recorded together with the customer-experience risk of an English portal in two of three launch markets.
- Regardless of the decision, all customer-facing strings must be externalised from the first build: refund-status text (REQ-004, REQ-019, REQ-020), fee-implication labels (REQ-054, REQ-055), timeline messages (REQ-056), return-window text (REQ-058) and page framing (REQ-057). This makes the language decision a copy-level change, not a rework.
- The same mechanism covers status text, fee labels and timeline messages, so those strings are settled by the same decision, not separately.

## Cross-team dependencies and integration gaps

Two requirements cannot be implemented as written without decisions that sit outside the portal team; both must be closed in the M1 gate.

- Pre-purchase return-window display - REQ-024. REQ-017 delivers the portal as a new service that does not modify the existing order-history application beyond an entry point, and the portal has no pre-purchase context. The pre-purchase display required by REQ-024 must be owned by the e-commerce checkout platform, not the returns portal, and recorded as a cross-team dependency with a named owner. The portal's own obligation is the post-purchase display (REQ-058), and it is limited to that.
- Carrier scan events - REQ-048, REQ-049. The cancellation cut-off is the physical carrier scan, but the specified inbound integrations - the nightly IDoc feed (REQ-002) and the nightly carrier label batch (REQ-005) - are not described as carrying scan events, and no synchronous carrier API exists. Before the cancellation flow is built, one of the following must be specified: a carrier tracking feed, an IDoc extension, or a polling mechanism, together with its latency. If scan events can only arrive nightly, the specification must define the window during which a customer could cancel after the physical scan but before the system registers it, and the reconciliation behaviour: a cancellation received in that window is refused with an explanation and the return completes as a normal return (REQ-049).
- Existing platform consumed as-is: SAP ECC via the nightly IDoc feed (REQ-001, REQ-002), Adyen as sole PSP (REQ-003, REQ-004), the DHL/GLS 02:00 CET batch (REQ-005), Azure AD B2C (REQ-006), the existing WAF (REQ-010) and the existing OpenTelemetry collector (REQ-014). None of these is changed by this project.

## Assumptions

- The team is four engineers and one designer (REQ-037); the quarter's estimates assume this and no more.
- The portal is a new service (REQ-017); the existing order-history application changes only by an entry point.
- No real-time SAP interface for stock and goods-receipt data exists or is planned before 2027, per the architecture note.
- Renegotiating the DHL and GLS carrier contracts to change the batch schedule is not achievable within the quarter, per the architecture note.
- The five-reason taxonomy, the fee labels and the wireframe copy are provisional pending the decisions listed above; nothing in the wireframe overrides the steering decisions still to be recorded.
- January return volume is more than double the monthly baseline, and the portal must serve it (REQ-032).

## Glossary

- **BR-05** — Requirement in the BRD governing the free-returns threshold and handling fee; contradicted by the agreed reason-based fee rule.
- **BRD** — Business Requirements Document; its fee rule states returns are free above EUR 50 and EUR 3.90 is deducted below.
- **Carrier batch job** — The current overnight (02:00) carrier integration that generates labels in batch; changing its schedule requires renegotiating the DHL and GLS contracts.
- **Carrier scan** — The moment the carrier scans the return parcel; the cut-off after which a return can no longer be cancelled.
- **Condition codes** — Codes used by DCs to record the condition of returned items; roughly six per DC and currently inconsistent between Hamburg and Venlo.
- **Credit note** — The financial document that records a refund owed to a customer; posted in SAP ECC.
- **Deployment freeze** — The date after which no further deployments occur; preceded by a two-week stabilisation period starting 1 December.
- **Distribution centre** — One of the company's four fulfilment warehouses; Poznań is one.
- **Distribution centre (DC)** — One of the company's four fulfilment warehouses; Poznań, Hamburg and Venlo are among them.
- **Free-returns threshold** — The EUR 50 order-value threshold from the BRD above which returns were free; retained only for selected reasons under the new rule.
- **Handling fee** — The EUR 3.90 deduction applied to a refund when a return is not free.
- **IDoc** — SAP's intermediate document format used to transfer stock and goods-receipt data to downstream systems via a nightly batch feed completing at 23:30 CET.
- **Launch wave** — The set of countries in the initial launch; Poland is excluded and targeted for Q3.
- **Loyalty programme member** — A customer enrolled in the loyalty programme, entitled to the 30-day return window.
- **Marketplace returns** — Returns of goods sold through marketplace partners; phase-one inclusion is pending a steering decision.
- **Marketplace seller** — A third-party seller on the company's platform (about 11% of order volume); marketplace returns are contractually the seller's responsibility.
- **PSP** — Payment service provider; Adyen is the designated provider for all consumer channels.
- **Peak trading freeze** — The period 15 December through 6 January during which production deployments are prohibited; the freeze is preceded by a two-week stabilisation period starting 1 December.
- **Phase 1** — The first delivery phase of Project Boomerang, during which the portal must not write to SAP; its scope is subject to the zero-sum rule if marketplace returns are added.
- **Phase one** — The initial release scope; subject to the zero-sum rule if marketplace returns are added.
- **Pre-drawn label pool** — A stock of pre-generated DHL/GLS return labels assigned on demand so a customer gets a real label within roughly fifteen minutes instead of waiting for the carrier batch job.
- **Project Boomerang** — Internal project name for the customer self-service returns portal.
- **Pseudonymisation** — Replacement of customer identifiers with a stable surrogate key after 24 months, while retaining the decision, amounts, and timestamps.
- **Refund initiation** — The moment the refund is submitted to the payment provider (Adyen API); must occur within five business days.
- **Refund settlement** — The moment the refunded money appears on the customer's bank statement; Adyen settles at T+7.
- **Return window** — The period in which an item may be returned: 30 days for loyalty programme members and 14 days for everyone else, measured from delivery.
- **SRB** — Security Review Board; must approve every new public endpoint, requiring a completed threat model and a 10-working-day lead time.
- **Serial returner** — A customer suspected of returning items with unusual frequency (about one return in forty involves one); their cases are routed to a review queue whose staffing is unresolved.
- **Serial returners** — Customers suspected of repeatedly returning goods; their cases are routed to a review queue whose staffing is unresolved.
- **Stabilisation period** — The two weeks between 1 December and the deployment freeze during which scope is complete and stabilised.
- **Statutory minimum return window** — Fourteen days, per the EU Consumer Rights Directive; the company may not offer less.
- **Steering group** — Governance body that must issue explicit decisions on the conflicts between the architecture constraints and the BRD before build starts.
- **Stock forecast** — Prediction of future inventory affected by pending returns; currently distorted by abandoned return requests left open.
- **System of record** — The authoritative source of truth for a data domain; here SAP ECC for stock and credit-note financial postings.
- **T+7** — Adyen settlement terms: refund funds settle seven days after the refund is initiated; the amount is not visible on the customer's statement until settlement completes.
- **WAF** — Web application firewall; the existing one through which all customer-facing traffic must route.

## Sources

| ID | Title | Type | Location |
|---|---|---|---|
| `src-af40245e7fd2` | 01-business-requirements.pdf | document | `file:///mnt/mx500/projects/prd-forge/examples/demo_pack/01-business-requirements.pdf` |
| `src-1f9fd3afcaa3` | 02-it-architecture-constraints.pdf | document | `file:///mnt/mx500/projects/prd-forge/examples/demo_pack/02-it-architecture-constraints.pdf` |
| `src-467a83ee9f97` | 03-kickoff-meeting.vtt | transcript | `file:///mnt/mx500/projects/prd-forge/examples/demo_pack/03-kickoff-meeting.vtt` |
| `src-03015e5aef51` | 04-refinement-notes.md | document | `file:///mnt/mx500/projects/prd-forge/examples/demo_pack/04-refinement-notes.md` |
| `src-27fdd49dce84` | 05-returns-screen-wireframe.png | image | `file:///mnt/mx500/projects/prd-forge/examples/demo_pack/05-returns-screen-wireframe.png` |

## Traceability

| Requirement | Source | Location | Evidence |
|---|---|---|---|
| REQ-001 | 02-it-architecture-constraints.pdf | p.1 | SAP ECC remains the system of record for stock and for the financial posting of a credit note. |
| REQ-001 | 02-it-architecture-constraints.pdf | p.1 | The portal must not write to SAP in phase 1. |
| REQ-002 | 02-it-architecture-constraints.pdf | p.1 | Stock and goods-receipt data reach downstream systems through a nightly IDoc feed that completes at 23:30 CET. |
| REQ-002 | 02-it-architecture-constraints.pdf |  | There is no real-time interface for stock and goods-receipt data, and none is planned before 2027. |
| REQ-003 | 02-it-architecture-constraints.pdf | p.1 | Adyen is the payment service provider for all consumer channels. |
| REQ-003 | 02-it-architecture-constraints.pdf |  | Refunds are initiated through the Adyen API and settle on a T+7 basis. |
| REQ-004 | 02-it-architecture-constraints.pdf |  | Refunds are initiated through the Adyen API and settle on a T+7 basis. |
| REQ-004 | 02-it-architecture-constraints.pdf |  | The refund amount is not visible on the customer's statement until settlement completes. |
| REQ-004 | 02-it-architecture-constraints.pdf |  | BR-06 conflates two different events: refund initiation and receipt by the customer. |
| REQ-004 | 02-it-architecture-constraints.pdf |  | The refund can be initiated within 5 business days but cannot be received by the customer within that window, because Adyen settles on T+7. |
| REQ-004 | 03-kickoff-meeting.vtt | 00:02:41 Priya Raman | Decision: refund initiated and refund settled are modelled as two distinct states, both visible to the customer. |
| REQ-004 | 03-kickoff-meeting.vtt | 00:02:05 Lena Fischer | Refund initiation and settlement are two different events that the business requirements document treats as one. |
| REQ-004 | 03-kickoff-meeting.vtt | 00:02:26 Tomasz Nowak | Showing two separate refund states in the portal would eliminate that whole category of contact. |
| REQ-004 | 03-kickoff-meeting.vtt | 00:02:26 Tomasz Nowak | The gap between the customer receiving a 'refunded' email and the money appearing in their bank is what generates the 'where is my refund' calls. |
| REQ-005 | 02-it-architecture-constraints.pdf | p.1 | Carrier label generation for DHL and GLS runs as a batch job at 02:00 CET. |
| REQ-005 | 02-it-architecture-constraints.pdf |  | Neither the DHL nor the GLS carrier contract includes access to a synchronous label API. |
| REQ-005 | 02-it-architecture-constraints.pdf |  | Because carrier label generation runs as a batch at 02:00 CET, a label requested at 09:00 is not available until the following morning unless the carrier contract is renegotiated. |
| REQ-006 | 02-it-architecture-constraints.pdf | p.1 | Customer authentication must use the existing Azure AD B2C tenant. |
| REQ-006 | 02-it-architecture-constraints.pdf | p.1 | No new identity store may be introduced. |
| REQ-007 | 02-it-architecture-constraints.pdf | p.1 | All data must be stored and processed in the EU, in the Frankfurt region. |
| REQ-008 | 02-it-architecture-constraints.pdf | p.1 | PostgreSQL is the only approved relational database. |
| REQ-008 | 02-it-architecture-constraints.pdf | p.1 | No new database technology will be provisioned for this project. |
| REQ-009 | 02-it-architecture-constraints.pdf | p.1 | Any new public endpoint must pass the Security Review Board, which requires a 10 working day lead time and a completed threat model. |
| REQ-010 | 02-it-architecture-constraints.pdf | p.1 | All customer-facing traffic must route through the existing web application firewall. |
| REQ-011 | 02-it-architecture-constraints.pdf |  | The system must sustain 19,000 return requests per month with a peak of 400 concurrent users. |
| REQ-012 | 02-it-architecture-constraints.pdf |  | The system must sustain the stated load without horizontal scaling beyond four nodes. |
| REQ-013 | 02-it-architecture-constraints.pdf | p.1 | Every automated accept, reject or fee decision must be reproducible on demand for 7 years. |
| REQ-013 | 02-it-architecture-constraints.pdf |  | The 7-year audit reproducibility requirement is a financial-audit obligation and is independent of the marketing-consent lifecycle. |
| REQ-014 | 02-it-architecture-constraints.pdf | p.1 | All services must emit traces to the existing OpenTelemetry collector. |
| REQ-015 | 02-it-architecture-constraints.pdf | p.1 | Deployments are blocked between 15 December and 6 January, the peak trading freeze. |
| REQ-015 | 03-kickoff-meeting.vtt | 00:06:06 Lena Fischer | There is a deployment freeze from the fifteenth of December to the sixth of January. |
| REQ-015 | 03-kickoff-meeting.vtt | 00:06:06 Lena Fischer | The deployment freeze is not negotiable regardless of scope. |
| REQ-016 | 02-it-architecture-constraints.pdf |  | Each conflict called out in section 5 needs a decision from the steering group before build starts. |
| REQ-016 | 02-it-architecture-constraints.pdf |  | The BRD statements BR-04, BR-06 and the section 6 retention statement cannot be met as written; each needs an explicit decision. |
| REQ-017 | 02-it-architecture-constraints.pdf |  | It is assumed that the portal is a new service and does not modify the existing order-history application beyond adding an entry point. |
| REQ-017 | 02-it-architecture-constraints.pdf | p.2 | Marketplace orders are held in a separate schema and are not addressed by this note. |
| REQ-018 | 03-kickoff-meeting.vtt | 00:00:04 Priya Raman | The project is a self-service returns portal, internally named Project Boomerang. |
| REQ-019 | 03-kickoff-meeting.vtt | 00:00:54 Priya Raman | The status view is the single highest-value piece of the portal and should be treated as such. |
| REQ-019 | 03-kickoff-meeting.vtt | 00:00:40 Tomasz Nowak | A status page would remove most of the 'where is my refund' contacts on its own. |
| REQ-019 | 03-kickoff-meeting.vtt | 00:00:40 Tomasz Nowak | Four out of ten customer contacts are customers asking where their refund is. |
| REQ-019 | 03-kickoff-meeting.vtt | 00:02:26 Tomasz Nowak | The gap between the customer receiving a 'refunded' email and the money appearing in their bank is what generates the 'where is my refund' calls. |
| REQ-020 | 03-kickoff-meeting.vtt | 00:02:41 Priya Raman | Decision: the expected settlement date is shown to the customer. |
| REQ-020 | 03-kickoff-meeting.vtt | 00:02:05 Lena Fischer | The Adyen API can be called within five days, but the money does not appear on the customer's statement for up to seven banking days after that. |
| REQ-020 | 03-kickoff-meeting.vtt | 00:02:05 Lena Fischer | The payment provider Adyen settles refunds at T plus seven. |
| REQ-021 | 03-kickoff-meeting.vtt | 00:02:41 Priya Raman | Decision: the five-business-day target applies to refund initiation only. |
| REQ-021 | 03-kickoff-meeting.vtt | 00:02:05 Lena Fischer | The business requirements document specifies that the refund is issued within five business days. |
| REQ-021 | 03-kickoff-meeting.vtt | 00:02:05 Lena Fischer | The payment provider Adyen settles refunds at T plus seven. |
| REQ-022 | 03-kickoff-meeting.vtt | 00:01:46 Priya Raman | Decision: the return window is thirty days for loyalty members and fourteen days for everyone else. |
| REQ-022 | 03-kickoff-meeting.vtt | 00:01:10 Marco Bianchi | Marco Bianchi proposed a thirty-day return window for loyalty programme members and fourteen days for everyone else. |
| REQ-022 | 03-kickoff-meeting.vtt | 00:01:10 Marco Bianchi | The return window in operation today is fourteen days. |
| REQ-022 | 03-kickoff-meeting.vtt | 00:00:54 Priya Raman | The business requirements document specifies a fourteen-day return window. |
| REQ-022 | 03-kickoff-meeting.vtt | 00:01:10 Marco Bianchi | The company's two biggest competitors both offer thirty-day return windows. |
| REQ-022 | 03-kickoff-meeting.vtt | 00:01:10 Marco Bianchi | The return window is the single most common reason customers cite when explaining why they did not buy from the company. |
| REQ-022 | 04-refinement-notes.md | Return window | The return window is 30 days for loyalty programme members and 14 days for everyone else. |
| REQ-022 | 05-returns-screen-wireframe.png | image | The screen states 'Return window closes 3 May (loyalty: 30 days).' |
| REQ-023 | 03-kickoff-meeting.vtt | 00:01:31 Sofia Greco | Fourteen days is the statutory minimum return window under the EU consumer rights directive. |
| REQ-023 | 03-kickoff-meeting.vtt | 00:01:31 Sofia Greco | Offering a longer return window than the statutory minimum is a commercial decision rather than a legal one, and the company may not offer less. |
| REQ-024 | 03-kickoff-meeting.vtt | 00:01:46 Priya Raman | Decision: the return window rule must be visible to the customer before they buy. |
| REQ-024 | 03-kickoff-meeting.vtt | 00:01:31 Sofia Greco | The company cannot offer thirty days and then quietly withdraw it for some customers. |
| REQ-024 | 03-kickoff-meeting.vtt | 00:01:10 Marco Bianchi | The return window is the single most common reason customers cite when explaining why they did not buy from the company. |
| REQ-025 | 03-kickoff-meeting.vtt | 00:01:31 Sofia Greco | The company cannot offer thirty days and then quietly withdraw it for some customers. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:03:50 Priya Raman | Decision: the return label must be available within fifteen minutes of the return request being approved. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:03:50 Priya Raman | Decision: the fifteen-minute label target is met using a pre-drawn pool of labels. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:04:04 Marco Bianchi | Marco Bianchi confirmed fifteen minutes is commercially acceptable and that next-day label availability was not. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:03:29 Lena Fischer | With a pre-drawn label pool, the customer would get a real label within about fifteen minutes of the request. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:03:29 Lena Fischer | Both carriers support a pre-generated label pool. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:02:58 Lena Fischer | The carrier integration is a batch job that runs at two in the morning. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:02:58 Lena Fischer | Under the current batch job, a label requested at nine in the morning is not available until the next day. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:03:16 Marco Bianchi | If the label arrives the next day, the friction has been moved rather than removed. |
| REQ-026 | 03-kickoff-meeting.vtt | 00:02:58 Lena Fischer | Changing the carrier batch schedule requires renegotiating the DHL and GLS contracts, which is not a one-quarter conversation. |
| REQ-027 | 03-kickoff-meeting.vtt | 00:02:58 Lena Fischer | The carriers are DHL and GLS. |
| REQ-027 | 03-kickoff-meeting.vtt | 00:03:29 Lena Fischer | Both carriers support a pre-generated label pool. |
| REQ-028 | 03-kickoff-meeting.vtt | 00:04:57 Priya Raman | Decision: suspicious returns go to a review queue with a human decision, never an automatic refusal. |
| REQ-028 | 03-kickoff-meeting.vtt | 00:04:57 Priya Raman | The review-queue requirement already exists as BR-08 in the business requirements document. |
| REQ-028 | 03-kickoff-meeting.vtt | 00:04:38 Tomasz Nowak | Roughly one return in forty comes from a customer already suspected of serial returning. |
| REQ-028 | 03-kickoff-meeting.vtt | 00:04:38 Tomasz Nowak | A fully automated flow would refund suspected serial returners without anyone looking. |
| REQ-028 | 03-kickoff-meeting.vtt | 00:04:38 Tomasz Nowak | A review queue is needed rather than a hard block, because the company gets it wrong sometimes and blocking a good customer is expensive. |
| REQ-029 | 03-kickoff-meeting.vtt | 00:05:28 Sofia Greco | The seller agreement should be reviewed before a marketplace return button exists in the portal. |
| REQ-029 | 03-kickoff-meeting.vtt | 00:05:28 Sofia Greco | If the portal accepts a marketplace return request, the company may be taking on an obligation that contractually sits with the seller. |
| REQ-029 | 03-kickoff-meeting.vtt | 00:05:43 Priya Raman | The marketplace returns scope question remains open and was not decided in this meeting. |
| REQ-030 | 03-kickoff-meeting.vtt | 00:05:54 Marco Bianchi | The business requirements document specifies Germany, Austria, and Netherlands as launch countries. |
| REQ-031 | 03-kickoff-meeting.vtt | 00:06:24 Priya Raman | Decision: the real delivery deadline is the first of December, not January. |
| REQ-031 | 03-kickoff-meeting.vtt | 00:06:06 Lena Fischer | If the portal is not live and stable before the freeze, it does not go live until the second week of January, which is after the peak it is being built for. |
| REQ-031 | 03-kickoff-meeting.vtt | 00:00:21 Marco Bianchi | Return volume more than doubles in January. |
| REQ-031 | 03-kickoff-meeting.vtt | 00:00:21 Marco Bianchi | If nothing is done, next January will be worse than the last one. |
| REQ-032 | 03-kickoff-meeting.vtt | 00:00:21 Marco Bianchi | The company handles about 8,200 returns per month. |
| REQ-032 | 03-kickoff-meeting.vtt | 00:00:21 Marco Bianchi | Return volume more than doubles in January. |
| REQ-032 | 03-kickoff-meeting.vtt | 00:06:06 Lena Fischer | If the portal is not live and stable before the freeze, it does not go live until the second week of January, which is after the peak it is being built for. |
| REQ-033 | 03-kickoff-meeting.vtt | 00:04:11 Sofia Greco | The business requirements document specifies personal data is kept for twenty-four months. |
| REQ-033 | 03-kickoff-meeting.vtt | 00:04:11 Sofia Greco | The 24-month personal data retention and the seven-year audit retention are both correct obligations that overlap, and the conflict remains unresolved. |
| REQ-034 | 03-kickoff-meeting.vtt | 00:04:11 Sofia Greco | Lena Fischer's architecture note specifies audit records are kept for seven years. |
| REQ-034 | 03-kickoff-meeting.vtt | 00:04:11 Sofia Greco | The 24-month personal data retention and the seven-year audit retention are both correct obligations that overlap, and the conflict remains unresolved. |
| REQ-034 | 04-refinement-notes.md | Retention | Audit records covering the accept/reject/fee decision are kept for 7 years. |
| REQ-035 | 03-kickoff-meeting.vtt | 00:04:30 Priya Raman | The team must not design the data model until the retention position lands. |
| REQ-035 | 03-kickoff-meeting.vtt | 00:04:30 Priya Raman | Action item: Sofia Greco to deliver a written position on the retention conflict before the end of the month. |
| REQ-035 | 03-kickoff-meeting.vtt | 00:04:11 Sofia Greco | Sofia Greco will come back with a written position on the retention conflict rather than resolving it in the meeting. |
| REQ-036 | 03-kickoff-meeting.vtt | 00:06:24 Priya Raman | Decision: there are two weeks of stabilisation between the first of December and the deployment freeze. |
| REQ-036 | 03-kickoff-meeting.vtt | 00:06:24 Priya Raman | Everyone must plan against the first of December rather than against the peak itself. |
| REQ-037 | 03-kickoff-meeting.vtt | 00:06:40 Lena Fischer | Lena Fischer has four engineers and a designer available for the quarter. |
| REQ-037 | 03-kickoff-meeting.vtt | 00:06:40 Lena Fischer | The estimate in the architecture constraints note assumes four engineers and a designer. |
| REQ-038 | 03-kickoff-meeting.vtt | 00:06:40 Lena Fischer | If marketplace returns are added to phase one, something else must come out of scope. |
| REQ-038 | 03-kickoff-meeting.vtt | 00:06:51 Priya Raman | Nothing is being added to phase one scope today. |
| REQ-039 | 04-refinement-notes.md | Poland | Poland is not in the launch wave; the target is Q3, treated as a follow-on. |
| REQ-039 | 03-kickoff-meeting.vtt | 00:07:04 Priya Raman | Poland is deferred but not formally decided. |
| REQ-039 | 04-refinement-notes.md | Poland | Marco was explicit that he did not want the Poland decision reopened in the steering group. |
| REQ-040 | 04-refinement-notes.md | Free returns and the handling fee | The session agreed to keep the EUR 50 threshold but make the fee depend on the return reason rather than only the order value. |
| REQ-040 | 03-kickoff-meeting.vtt | 00:07:28 Marco Bianchi | The free-returns threshold was missing from the decision list and needs revisiting. |
| REQ-040 | 03-kickoff-meeting.vtt | 00:07:35 Priya Raman | Action item: the free-returns threshold will be revisited in the refinement session on Thursday, with Tomasz Nowak. |
| REQ-040 | 03-kickoff-meeting.vtt | 00:07:35 Priya Raman | The free-returns threshold is a pricing question, not an architecture question. |
| REQ-040 | 04-refinement-notes.md | Purpose | The session was a follow-up to Tuesday's kickoff, covering two items pushed from it: the free-returns threshold and how the handling fee interacts with return reasons. |
| REQ-040 | 04-refinement-notes.md | Free returns and the handling fee | The BRD states that returns are free above EUR 50 and that EUR 3.90 is deducted below that threshold. |
| REQ-041 | 04-refinement-notes.md | Free returns and the handling fee | Where the fault is the company's - damaged in transit, wrong item received, or delivered late - the return is free regardless of order value. |
| REQ-041 | 05-returns-screen-wireframe.png | image | The reason code 'Damaged in transit' is marked as 'free return'. |
| REQ-041 | 05-returns-screen-wireframe.png | image | The reason code 'Wrong item received' is marked as 'free return'. |
| REQ-041 | 05-returns-screen-wireframe.png | image | The reason code 'Delivered late' is marked as 'free return'. |
| REQ-042 | 04-refinement-notes.md | Free returns and the handling fee | Where the customer simply changed their mind, the EUR 3.90 fee applies regardless of order value, including on orders above EUR 50. |
| REQ-042 | 05-returns-screen-wireframe.png | image | The reason code 'Changed mind' has 'EUR 3.90 handling fee applies'. |
| REQ-042 | 04-refinement-notes.md | Free returns and the handling fee | The rule that the change-of-mind fee applies on orders above EUR 50 is a change from the BRD and Marco will get it re-approved. |
| REQ-043 | 05-returns-screen-wireframe.png | image | The reason code 'Does not fit' is marked as 'free return over EUR 50'. |
| REQ-043 | 04-refinement-notes.md | Free returns and the handling fee | The BRD states that returns are free above EUR 50 and that EUR 3.90 is deducted below that threshold. |
| REQ-044 | 04-refinement-notes.md | Return window | The 30-day loyalty return window runs from delivery, not from dispatch - same as today. |
| REQ-044 | 05-returns-screen-wireframe.png | image | The screen states 'Order delivered 3 April.' |
| REQ-044 | 05-returns-screen-wireframe.png | image | The screen states 'Return window closes 3 May (loyalty: 30 days).' |
| REQ-045 | 04-refinement-notes.md | Return window | The 30-day loyalty return window runs from delivery, not from dispatch - same as today. |
| REQ-045 | 04-refinement-notes.md | Return window | The order-history screen currently shows the dispatch date more prominently than the delivery date. |
| REQ-045 | 05-returns-screen-wireframe.png | image | The screen states 'Order delivered 3 April.' |
| REQ-046 | 04-refinement-notes.md | Retention | The retention audit records must be pseudonymised after 24 months: the decision, amounts and timestamps are retained while customer identifiers are replaced with a stable surrogate key. |
| REQ-047 | 04-refinement-notes.md | Retention | Pseudonymisation needs to be designed in from the start rather than retrofitted. |
| REQ-047 | 04-refinement-notes.md | Retention | The retention audit records must be pseudonymised after 24 months: the decision, amounts and timestamps are retained while customer identifiers are replaced with a stable surrogate key. |
| REQ-048 | 04-refinement-notes.md | Cancelling a return | The customer must be able to cancel a return request themselves, at any point before the parcel has been scanned by the carrier. |
| REQ-048 | 05-returns-screen-wireframe.png | image | The footer contains a 'Cancel' button. |
| REQ-048 | 05-returns-screen-wireframe.png | image | The footer states 'You can cancel until the parcel is scanned.' |
| REQ-048 | 04-refinement-notes.md | Cancelling a return | About 15% of return requests raised by phone today are subsequently abandoned. |
| REQ-049 | 04-refinement-notes.md | Cancelling a return | After the parcel has been scanned by the carrier it is too late to cancel, and the return has to complete as a normal return. |
| REQ-049 | 05-returns-screen-wireframe.png | image | The footer states 'You can cancel until the parcel is scanned.' |
| REQ-050 | 04-refinement-notes.md | Cancelling a return | An abandoned return request currently remains open forever, which makes the stock forecast wrong. |
| REQ-050 | 04-refinement-notes.md | Cancelling a return | The customer must be able to cancel a return request themselves, at any point before the parcel has been scanned by the carrier. |
| REQ-050 | 04-refinement-notes.md | Cancelling a return | About 15% of return requests raised by phone today are subsequently abandoned. |
| REQ-051 | 04-refinement-notes.md | Still open | Condition codes need harmonising across the four DCs before anyone can build a dropdown for them. |
| REQ-051 | 04-refinement-notes.md | Condition on arrival | The DCs apparently use about six condition codes, but they differ slightly between Hamburg and Venlo, which is going to be a problem. |
| REQ-051 | 04-refinement-notes.md | Condition on arrival | Today the warehouse records the condition of returned items as a free-text note in a spreadsheet. |
| REQ-052 | 04-refinement-notes.md | Condition on arrival | Today the warehouse records the condition of returned items as a free-text note in a spreadsheet. |
| REQ-052 | 04-refinement-notes.md | Still open | Condition codes need harmonising across the four DCs before anyone can build a dropdown for them. |
| REQ-053 | 05-returns-screen-wireframe.png | image | Each order item row contains a checkbox, a product image placeholder, the product name, its unit price and quantity, and a 'Reason:' dropdown defaulting to '-- select --'. |
| REQ-054 | 05-returns-screen-wireframe.png | image | A 'Reason codes' panel lists five reason options with their fee implications. |
| REQ-054 | 05-returns-screen-wireframe.png | image | The reason code 'Damaged in transit' is marked as 'free return'. |
| REQ-054 | 05-returns-screen-wireframe.png | image | The reason code 'Wrong item received' is marked as 'free return'. |
| REQ-054 | 05-returns-screen-wireframe.png | image | The reason code 'Does not fit' is marked as 'free return over EUR 50'. |
| REQ-054 | 05-returns-screen-wireframe.png | image | The reason code 'Changed mind' has 'EUR 3.90 handling fee applies'. |
| REQ-054 | 05-returns-screen-wireframe.png | image | The reason code 'Delivered late' is marked as 'free return'. |
| REQ-055 | 05-returns-screen-wireframe.png | image | A 'Refund estimate' footer field shows 'EUR 89.00'. |
| REQ-055 | 05-returns-screen-wireframe.png | image | A 'Handling fee' footer field shows 'EUR 0.00'. |
| REQ-055 | 05-returns-screen-wireframe.png | image | Each order item row contains a checkbox, a product image placeholder, the product name, its unit price and quantity, and a 'Reason:' dropdown defaulting to '-- select --'. |
| REQ-056 | 05-returns-screen-wireframe.png | image | The footer states 'Label ready in ~15 min'. |
| REQ-056 | 05-returns-screen-wireframe.png | image | The footer states 'Refund initiated in 5 business days'. |
| REQ-056 | 05-returns-screen-wireframe.png | image | The footer states 'settled to your card within 7 more'. |
| REQ-057 | 05-returns-screen-wireframe.png | image | The breadcrumb path shown is NORDWIND \| My orders > Order 4471-9902 > Return items. |
| REQ-057 | 05-returns-screen-wireframe.png | image | The page is titled 'Return items from this order'. |
| REQ-058 | 05-returns-screen-wireframe.png | image | The screen states 'Order delivered 3 April.' |
| REQ-058 | 05-returns-screen-wireframe.png | image | The screen states 'Return window closes 3 May (loyalty: 30 days).' |
| REQ-059 | 05-returns-screen-wireframe.png | image | The footer contains a primary 'Request return' button. |
