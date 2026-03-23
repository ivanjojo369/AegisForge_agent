# Artifact Response Contract

When the task expects an artifact-like response, the output should:

- match the requested artifact type,
- be complete enough to be reviewed or graded,
- preserve the requested structure,
- avoid placeholder text unless explicitly allowed,
- remain concise enough to fit budget constraints.

## Artifact-aware behavior

- Infer whether the artifact is intended for humans, machines, or both.
- Preserve required headings, sections, fields, or blocks.
- Do not switch formats mid-response.
- If information is missing, mark the gap explicitly instead of fabricating it.

## Final checklist

- Is the artifact complete?
- Is the structure correct?
- Is the content grounded?
- Is the artifact usable as delivered?
