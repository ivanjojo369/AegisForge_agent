# JSON Response Contract

When a task requires JSON output, the response should satisfy these rules:

- Return valid JSON only when the benchmark expects strict machine-readable output.
- Include all required fields.
- Use stable, descriptive keys.
- Avoid comments, markdown fences, or explanatory text outside the JSON object.
- Prefer explicit nulls over omitted keys when the contract expects field presence.
- Ensure nested structures remain syntactically valid.

## Final checklist

- Is the JSON parseable?
- Are required fields present?
- Are values aligned with the task evidence?
- Is the object complete enough to be scored?
