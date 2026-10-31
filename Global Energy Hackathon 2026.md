# Global Energy Hackathon 2026

## Project: AegisGrid

**Category / proposed track:** Digital Solutions, Apps & AI/ML Tools · Non-residential / healthcare  
**Theme:** Energy availability and affordability  
**Event:** October 30–November 1, 2026  
**Organizer:** Anneru Solutions  
**Official event:** https://hackathon.anneru.com/

> **Submission working draft — not evidence of submission.** Verify the local build, repository contents, demonstration video, submission process, and eligibility before uploading. The published event website does not specify an exact submission cutoff time in the information reviewed.

## Project overview

**AegisGrid** is a scenario-based decision-support prototype for the energy resilience of a community clinic. It examines how estimated electricity demand, critical loads, grid outages, solar generation, a usable battery, and energy tariffs influence service continuity and electricity purchases.

The prototype is built as an independent product using shared **AegisForge Nexus / Colossus** infrastructure for local evaluation and evidence tracking. It does **not** control a real battery, inverter, clinic, or grid connection.

## Problem

Community clinics depend on uninterrupted energy for essential functions. Outages can disrupt operations, while limited budgets make the choice between grid supply, local solar and storage difficult. AegisGrid provides a reproducible *hypothetical* comparison of policies rather than making unsupported guarantees about a real installation.

## Demonstrated prototype capabilities

- Editable clinic scenario: estimated daily demand, daily critical demand, outage hours, daily solar output, usable battery energy, currency and energy tariff.
- Initial 24-hour scenario with 30-minute intervals, as demonstrated in the Colossus interface.
- Three-day comparison (144 half-hour intervals per strategy) of four policies: **grid only**, **balanced solar**, **critical reserve**, and **grid recharge**.
- Reports of critical and total unserved energy, grid electricity cost, and final battery state for each strategy.
- Comparison criterion prioritizing critical unserved energy, then other reported outcomes. **This is a best-of-tested-policies comparison, not global mathematical optimization.**
- Local checks of mathematical consistency and integration with Colossus **Laboratory**, **ProofGraph** and **Mission Control** for recorded evidence and provenance summaries.
- JSON export of analyses/evidence where supported by the installed interface.

The figures previously shown in the prototype are **synthetic demonstration results**, not field measurements or externally authenticated validation.

## Example scenario (illustrative defaults)

| Input | Example |
|---|---:|
| Installation | Community clinic |
| Estimated daily demand | 120 kWh |
| Critical daily demand | 30 kWh |
| Outage duration | 4 hours/day |
| Daily solar generation | 30 kWh |
| Usable battery capacity | 20 kWh |
| Tariff | 3.5 MXN/kWh |

These values should be changed to suit the demonstration and **must not** be treated as engineering specifications for a real clinic.

## What makes the approach useful

1. Explicitly distinguishes **critical** from **total** energy not supplied.
2. Compares simple, interpretable dispatch policies under identical input assumptions.
3. Preserves inspectable summaries and evaluation evidence through the existing Colossus infrastructure.
4. Keeps simulation and engineering advice separate from physical control of energy systems.

## Reproduction guide — to verify on the submission machine

1. Clone or open the repository containing the **AegisGrid** package and shared Nexus modules.
2. Install the dependencies declared by that repository and start the **unified Colossus web interface** using its documented entry point.
3. Select **AegisGrid** and enter the example clinic scenario above.
4. Run the **24-hour simulation** and inspect the critical-energy and balance results.
5. Run **comparison of strategies** for three days and inspect all four policies.
6. Export the analysis JSON and open the corresponding evidence in **Mission Control** if enabled.

**Before publication:** insert the tested exact installation and startup commands here. They cannot be confirmed from the screenshots alone; do not invent them.

## Limitations and responsible interpretation

- Weather, solar yield, load profiles, outage timing, degradation, inverter capacity, losses and battery behavior are modeled with simplifying assumptions.
- Cost comparison does not establish full life-cycle economics; CAPEX, maintenance and replacement are not fully assessed.
- The selected policy is not proof of global optimality, field performance or regulatory compliance.
- SHA-256 consistency checks and archived provenance do not by themselves prove external authenticity or physical accuracy.
- **Not for live critical-infrastructure control or clinical operating decisions.**

## Final submission checklist

- [ ] Confirm participation/eligibility and registration through the official organizer.
- [ ] Confirm the project-specific submission link and exact cutoff shown to the registered participant.
- [ ] Publish the AegisGrid product, shared required modules, and assets to an accessible repository/branch (without credentials or unrelated local files).
- [ ] Add exact tested setup/run steps and record the commit or release being submitted.
- [ ] Record an honest demonstration video of the scenario, comparison and evidence inspection.
- [ ] Add repository URL, video URL and concise project description to the official submission.
- [ ] Verify that the organizer shows the entry as actually submitted.

**Repository intended for publication:** https://github.com/ivanjojo369/AegisForge_agent  
**AegisGrid submission branch/release:** TO BE CONFIRMED  
**Demo video:** TO BE ADDED  
**Submission status:** NOT VERIFIED

## Source

Global Energy Hackathon official information: https://hackathon.anneru.com/
