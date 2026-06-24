# Fake W-2 test data

A set of **fabricated** Form W-2s for testing the tax-filing assistant. All data
is fake — fake names, fake employers, fake EINs, and SSNs in the reserved
`555-xx-xxxx` example range. **No real PII. Not valid for any real filing.**

Everything here is self-contained in `testdata/` and does not touch the app code.

## What's here

| File / dir | Purpose |
|---|---|
| `generate_w2s.py` | Generator. Re-run to recreate all artifacts. |
| `w2_pdfs/*.pdf` | One W-2 per profile (Copy B), for the upload/OCR path. |
| `w2_images/*.png` | PNG render of each W-2, for image-upload flows. |
| `manifest.json` | Ground-truth box values + expected filing scenario per case. Use for evals/unit tests. |

Regenerate:

```bash
.venv/Scripts/python testdata/generate_w2s.py
```

## The profiles

All center on the challenge's ~$40k W-2 earner, with deliberate variety so
different code paths and filing statuses get exercised.

| id | What it tests |
|---|---|
| `01_single_40k_baseline` | Happy path: single, ~$40k, withholding → small refund. |
| `02_single_38k_no_state_tax` | Texas employer, no state income tax (boxes 15–17 empty). |
| `03_mfj_42k_primary_earner` | Married filing jointly; agent should ask about a spouse. |
| `04_single_45k_401k_deferral` | $3,600 pre-tax 401(k) (box 12 **D**); box 1 < boxes 3/5; box 13 checked. Agent must use box 1 for AGI, not gross. |
| `05_hoh_36k_dependent_care` | Head of household; box 10 dependent-care benefits implies a dependent. |
| `06_single_41k_pretax_health` | Section-125 pre-tax health; boxes 1, 3 **and** 5 all reduced. |
| `07_single_39k_underwithheld` | Under-withheld → balance **due** instead of a refund. |
| `08_single_messy_partial` | Robustness: box 2 + control number missing. Tests recovery / follow-up questions. |

## Internal consistency

Each W-2 is derived from a gross-pay model so the boxes agree the way a real
W-2 would:

- **Box 1** = gross − pre-tax 401(k) − pre-tax (section-125) health.
- **Boxes 3 & 5** (SS / Medicare wages) = gross − pre-tax health (401(k) does
  *not* reduce these), capped at the 2025 SS wage base ($176,100).
- **Box 4** = 6.2% of box 3. **Box 6** = 1.45% of box 5.
- **Box 2** (federal withholding) is set to a realistic amount for the wage
  level, generally landing near the true 2025 liability so most cases produce a
  small refund (and `07` a small balance due).

`manifest.json` carries the exact expected values for each box, so a test can
compare what the agent extracted against ground truth.
