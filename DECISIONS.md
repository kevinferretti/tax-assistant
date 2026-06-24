# DECISIONS

The route from the four pillars to a downloadable 1040 was mine to design. Here are
the consequential choices and *why* — including a couple that changed once they met
reality.

## The one principle everything hangs on
**The LLM converses and extracts; it never does arithmetic and never owns a number.**
Every dollar on the form comes from a deterministic Python engine; the model's only
powers are talking and calling typed tools. This single rule is what makes the harness
*real* (not "it's in the prompt") and is also the cheapest path to accuracy and trust:
there is no hallucinated math to defend against because the model is structurally
incapable of producing a tax figure.

## Language / framework — Python + FastAPI, vanilla JS, one container
Python has the right libraries (pypdf/pypdfium2 for the real IRS form, pydantic for
schema guardrails) and first-class OpenAI support. One FastAPI service serves both the
API and a hand-built vanilla HTML/CSS/JS UI — no Node build step, a tiny image, and
total control over a UI that doesn't look auto-generated. Frameworks would have added
weight without buying anything the spec rewards.

## Model / provider — OpenAI, two-tier, fast by default
OpenAI (per available credits). Because the hard logic is deterministic, the chat model
only has to converse and route to tools, so a **fast** model is the right default there;
a more **capable** model is used only for the one-shot W-2 vision read where accuracy
matters. Exact model ids are **verified against the account at startup** (`GET /v1/models`)
rather than hard-coded — GPT-4o/4.1 were retired Feb 2026 and GPT-5-family ids vary by
account. Both tiers are env-overridable.

## Obtaining & filling the 1040 — the genuine IRS form, stamped and flattened
We use the **official IRS `f1040.pdf`** (2025). I mapped every field by rendering the
form's own widget rectangles and reading each box. The plan was to fill AcroForm fields,
but the IRS form turned out to be an **XFA hybrid**, which renders inconsistently across
viewers when you inject AcroForm values. So we **stamp the computed values onto the real
page at the mapped coordinates and flatten** — the genuine government form, guaranteed to
render identically in any browser, Acrobat, or Preview. `generate_1040_pdf` refuses to run
unless the independent verification pass succeeds, so a downloaded return can only ever
contain engine-verified numbers.

## W-2 input — upload + vision, with a one-click sample
The user drops a photo/PDF of a W-2; OpenAI vision extracts every box into a validated
schema **with per-field confidence**. High-confidence boxes are trusted silently;
low-confidence ones get one gentle confirmation — accuracy without spending the question
budget. A "Try a sample W-2" button feeds a realistic fake through the *exact same*
pipeline, so a judge can prove the real path in one click. PDFs are rendered to an image
(pypdfium2) so the same vision path handles either format.

## Tax computation — deterministic and genuinely accurate
A from-scratch 2025 engine: all four filing statuses, CTC/ODC with the correct
$200k/$400k phaseout, and **EITC** (2025 Rev. Proc. parameters, investment-income cliff,
$50-band rounding to match the EIC table). It uses the IRS **Tax Table** method (tax the
midpoint of the $50 band) below $100k — which the IRS *requires* and which a naive bracket
formula gets subtly wrong — and the worksheet above. Figures reflect the OBBBA 2025 updates
(standard deduction $15,750 / $31,500 / $23,625; CTC $2,200, $1,700 refundable). Every line
carries human-readable **provenance**.

## Guardrails — enforced in code, not prose
- **5-question budget:** the only way to ask the user something is the `ask_user` tool;
  past five it is refused and the agent is told to finish with safe defaults. Counted from
  the event log and shown live in the UI.
- **PII boundary (the security ask):** the vision model is the *only* thing that sees the
  W-2 image; the SSN is split off and **never reaches the conversational model**, is masked
  (`***-**-1234`) everywhere in the trace and logs, and is written to the PDF only by code.
  Extracted document text is treated as data, not instructions (prompt-injection guard via
  schema-constrained extraction).
- **Verification gate + scope guard:** an independent "second accountant" re-derives the tax
  a different way and asserts invariants/statutory limits before any PDF; off-scope asks
  (state tax, prior years, crypto, representation) are detected and declined; a not-tax-advice
  disclaimer is always present.

## State & sessions — event-sourced, in-memory, no PII on disk
State is `fold(events)` over an append-only log. One mechanism, three payoffs: clean
**mid-conversation corrections** (just append), a **live refund** that updates as answers
land, and the log *is* the observation trail surfaced in the UI and at `/api/trace`.
Sessions live in memory only (prototype-appropriate; no real PII, nothing persisted).

## Conversation design — anticipatory, ≤5 questions
The W-2 already gives wages, withholding, name, and address, so questions are spent only on
what it can't show: filing status, dependents, and (if relevant) other income — branching
naturally (e.g. "married" → jointly or separately). The tone is warm and human by design;
the hard limits rarely get hit because the flow is built not to need them.

## Hosting & testing
Deployed as a single container on an OVH VPS behind a shared Caddy edge proxy, auto-deploying
on push to `main` (GitHub Actions → SSH). Proven by a deterministic `pytest` suite (engine vs
hand-computed values, EITC, verification tamper-detection, PDF round-trip, guardrails) plus an
**opt-in eval runner** (golden 1040s, simulated conversations, red-team) that is excluded from
CI so it never runs automatically.
