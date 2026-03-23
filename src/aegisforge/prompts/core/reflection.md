# AegisForge Reflection Prompt

Use reflection sparingly and only when it improves the final answer.

## Reflection goals

- Detect whether the current answer is incomplete.
- Check whether the answer matches the requested format.
- Catch unsupported claims, skipped constraints, or missing evidence.
- Decide whether revision is worth the remaining budget.

## Reflection questions

- Did the answer solve the actual task, not just part of it?
- Did the answer respect the active track's risk posture?
- Did the answer include invented observations or unsupported certainty?
- Is the artifact or schema complete enough to pass grading?
- Would one short revision materially improve correctness?

## Reflection rules

- Do not reflect endlessly.
- If the answer is already strong and budget is tight, finalize.
- If the issue is high-severity, revise before returning.
- Prefer one effective revision over multiple weak revisions.

## Revision posture

Revise only when there is a clear gap in:
- correctness,
- safety,
- completeness,
- or format compliance.
