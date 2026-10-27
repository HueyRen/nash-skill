# Interactive report contract

The report answers the decision on first render. It is not a scenario-entry product and never opens on an empty form.

## First view

Show the decision question, evidence date, stability classification, recommendation, rationale, next concrete move, best alternative, reversal trigger, and human gate before asking for interaction. State that a qualitative stability result is not a mathematical Nash proof.

## Core sections

- **Decision brief:** preferred move, best alternative, downside, reversal trigger, and human gate.
- **Assumption lab:** two to four controls initialized from the sourced baseline.
- **Strategy stability:** recommended route, alternatives, likely response, conditions, and tradeoffs.
- **Move → response path:** main branch, strongest adverse branch, and reassessment point.
- **Players:** visible and hidden actors, objective, BATNA, constraints, leverage, and likely move.
- **Evidence:** separate facts, inferences, and unknowns with useful source/date labels.
- **Risk and psychology:** failure modes and process safeguards, not personality diagnosis.

Use progressive disclosure for source detail. Keep exact private values out of the report unless essential.

## Interaction

The first render is complete without touching controls. Assumption controls run only deterministic sensitivity calculations over the embedded report data. Mark the 0–100 result as relative scenario fit, not success probability, utility, or a new model inference.

When a user changes an assumption:

- update strategy ranking and selected detail locally;
- preserve the original evidence unchanged;
- save only the small parameter map and selected strategy in widget state;
- do not claim to have reread files or performed new LLM reasoning.

The follow-up action sends the decision question, changed parameter values, and selected strategy back to the current thread via `window.openai.sendFollowUpMessage`. It must not include full source excerpts or sensitive local data. The next `/nash` turn rereads affected context and creates a new report.

## Equilibrium labels

- `candidate` → **Candidate stable outcome — qualitative**
- `none` → **No robust stable outcome identified**
- `proven` → **Formal Nash equilibrium — verified**
- `not-assessed` → **Stability not assessed**

Only `proven` may use formal-equilibrium language. All other states must remain explicitly qualitative.

## Delivery

Use the current `visualize` skill and its exact output contract. Render one self-contained HTML fragment with embedded analysis data, no `fetch`, XHR, WebSocket, local server, external fonts, or private source dumps. The root must be unique. Verify responsive layout, keyboard access, reduced motion, and the primary slider/strategy/follow-up interactions.

If rendering fails, return a concise Markdown brief from the validated JSON. Analysis must remain usable even when the visual surface is unavailable.
