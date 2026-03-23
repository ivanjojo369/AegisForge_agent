# Orchestration Flow

## Goal
The orchestration layer is responsible for turning a single task into a clean, auditable episode.

## Episode Lifecycle

1. **Receive task input**
   - load raw task request
   - normalize benchmark-specific fields into a common context

2. **Build `TaskContext`**
   - attach track hint
   - attach input constraints
   - attach artifact expectations

3. **Classify and plan**
   - run task classifier
   - build an execution plan
   - initialize budget state

4. **Route the episode**
   - choose adapter
   - choose prompt profile
   - choose strategy profile

5. **Execute**
   - run the selected adapter
   - collect intermediate events
   - update budget and trace state

6. **Self-check**
   - validate output shape
   - validate required evidence
   - validate policy/risk expectations

7. **Emit outputs**
   - final response
   - trace summary
   - scorecard metadata
   - artifact references if applicable

## Why This Matters
A visible orchestration flow makes the system easier to debug, benchmark, and explain. It also strengthens the story around reproducibility, traceability, and clean-state execution.

## Future Extensions
- retry and recovery policies
- context reset and checkpoint logic
- failure taxonomy integration
- per-track orchestration overrides
