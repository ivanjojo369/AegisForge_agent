# Impact Narrative — `quipu_lab` Contribution

## One-paragraph version
This contribution expands `quipu_lab` into a more realistic, multi-turn evaluation domain for organization-facing conversational agents. By growing the domain to **33 validated base tasks** and strengthening catalog integrity, smoke coverage, and end-to-end submission checks, the benchmark gains broader pressure on clarification-before-action, policy-vs-preference tradeoffs, wrong-tool penalties, noisy retrieval, multi-step handoffs, memory preservation, and structured planning. The result is a more demanding and practically useful evaluation surface for purple-agent style systems operating under operational and policy constraints.

## Short PR-ready version
This PR improves benchmark coverage by expanding `quipu_lab` to 33 validated base tasks spanning clarification, retrieval under noise, handoffs, wrong-tool penalties, negotiation consistency, memory preservation, and policy-aware action selection. The contribution strengthens the realism of multi-turn evaluation for organization-facing agents and makes the domain more useful for testing tool-use discipline, operational robustness, and decision quality under constraints.

## Expanded version
The main impact of this contribution is not just “more tasks,” but **better evaluation pressure**.

The added `quipu_lab` tasks broaden the benchmark along several important axes:

1. **Clarification before action**
   - Evaluates whether agents gather missing information before acting.
   - Reduces reward for brittle or over-eager action selection.

2. **Policy-aware decision making**
   - Tests whether the agent can balance user requests against organizational constraints.
   - Better reflects real deployment conditions.

3. **Preference vs policy conflicts**
   - Captures cases where user preference alone should not dictate the outcome.
   - Helps distinguish compliant reasoning from shallow accommodation.

4. **Tool-use discipline**
   - Includes wrong-tool penalties and no-hallucinated-tool scenarios.
   - Improves the benchmark’s ability to detect unsafe or sloppy tool behavior.

5. **Retrieval under noise**
   - Adds distractor-heavy and context-rotation style tasks.
   - Makes evaluation less dependent on easy, direct lookup behavior.

6. **Multi-step handoffs and dependencies**
   - Introduces workflows where success depends on ordered reasoning and coordination.
   - Better approximates real operational settings.

7. **Memory preservation across turns**
   - Evaluates whether important state is retained across multi-turn interactions.
   - Important for realistic agent workflows and longer conversations.

8. **Catalog reliability**
   - Stronger metadata normalization, validation, smoke checks, and catalog tests make the contribution easier to trust, reproduce, and integrate.

## Suggested “Why merge this?” line
This contribution improves both **coverage** and **reliability**: it adds realistic multi-turn evaluation cases while also making the `quipu_lab` catalog easier to validate, test, and maintain.
