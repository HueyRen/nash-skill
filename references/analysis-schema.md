# Nash report data contract

Write UTF-8 JSON. Keep it concise, sourced, and safe to embed in a self-contained visual report. The validator enforces the required shape; this reference defines the semantics.

## Top-level object

```json
{
  "version": "1.0",
  "meta": {},
  "context": {},
  "decision": {},
  "parameters": [],
  "players": [],
  "strategies": [],
  "evidence": {},
  "risks": [],
  "response_tree": [],
  "psychology": []
}
```

## Metadata and context

```json
{
  "meta": {
    "slug": "short-ascii-slug",
    "title": "short decision title",
    "question": "the decision being analyzed",
    "as_of": "YYYY-MM-DD or timestamp",
    "confidence": "low | medium | high",
    "equilibrium_status": "candidate | none | proven | not-assessed",
    "formal_model": null
  },
  "context": {
    "thread_intent": "concise scope inferred from the thread",
    "roots_considered": ["current thread", "~/cortex", "active workspace"],
    "sources_used": ["short display paths"],
    "omissions": ["skipped or out-of-budget category"],
    "stale_warnings": ["dated source warning"],
    "redactions": ["category removed from presentation"]
  }
}
```

Use `proven` only for a defined game with explicit strategy sets and payoffs plus complete best-response checks. When it is `proven`, `formal_model` must briefly identify the payoff model and verification. All ordinary real-world strategic cases use `candidate`, `none`, or `not-assessed`.

For `proven`, use this shape:

```json
{
  "formal_model": {
    "model_type": "pure-strategy-normal-form",
    "players": ["A1", "A2"],
    "strategies": {"A1": ["S1", "S2"], "A2": ["S3", "S4"]},
    "payoffs": [
      {"profile": ["S1", "S3"], "values": {"A1": 2, "A2": 1}},
      {"profile": ["S1", "S4"], "values": {"A1": 0, "A2": 0}},
      {"profile": ["S2", "S3"], "values": {"A1": 1, "A2": 0}},
      {"profile": ["S2", "S4"], "values": {"A1": 0, "A2": 2}}
    ],
    "equilibrium_profile": ["S1", "S3"],
    "best_response_checks": [
      {"actor_id": "A1", "profile": ["S1", "S3"], "profitable_deviation": "none"},
      {"actor_id": "A2", "profile": ["S1", "S3"], "profitable_deviation": "none"}
    ],
    "solver": "named method or script and version",
    "verified": true
  }
}
```

List every Cartesian strategy profile exactly once and give every player a finite numeric payoff. The validator independently recomputes every pure unilateral deviation at `equilibrium_profile`; a self-asserted `verified: true` is not sufficient. Mixed-strategy proofs are not accepted by this renderer's validator and require a separately reviewed solver and report path.

Present home-directory paths as `~/…`; do not embed raw account identifiers, tax IDs, secrets, email addresses, balances, or unrelated personal detail.

## Decision

```json
{
  "decision": {
    "recommended_strategy_id": "S1",
    "recommendation": "one-sentence direction",
    "rationale": "why this is preferred under current assumptions",
    "next_step": "smallest concrete, reversible move",
    "best_alternative": "best viable fallback",
    "main_downside": "most important cost",
    "reversal_trigger": "new fact that would change the answer",
    "stop_conditions": ["condition that pauses the strategy"],
    "human_gate": "decision or professional review that remains with a human"
  }
}
```

The preferred move must be feasible. If evidence is incomplete, prefer reversible information-gaining moves. High-stakes legal, tax, medical, immigration, employment, financial, and externally binding actions always need a human gate.

## Sensitivity parameters

Use two to four parameters that can materially change the answer. They are post-analysis exploration controls, not intake fields.

```json
{
  "id": "deadline_pressure",
  "label": "Deadline pressure",
  "value": 80,
  "min": 0,
  "max": 100,
  "step": 5,
  "low_label": "Low",
  "high_label": "High"
}
```

The initial `value` is the sourced baseline. Keep labels understandable without reading a formula.

## Players

```json
{
  "id": "A1",
  "name": "Decision owner",
  "role": "decision-owner | counterparty | influencer | gatekeeper | institution | hidden-actor",
  "hidden": false,
  "objective": "what this actor wants",
  "constraints": ["hard or soft constraint"],
  "leverage": ["credible source of influence"],
  "batna": "best actually available fallback",
  "likely_move": "observable likely action",
  "evidence_ids": ["F1", "I1"]
}
```

Do not label a wish or threat as a BATNA. Hidden actors include gatekeepers, regulators, platform owners, data custodians, and institutions that can change permissions or payoffs.

## Strategies

```json
{
  "id": "S1",
  "name": "Short action label",
  "summary": "specific move, timing, and intended effect",
  "base_fit": 76,
  "sensitivity": {
    "deadline_pressure": 0.25,
    "counterparty_receptiveness": 0.15
  },
  "conditions": ["condition required for this strategy"],
  "tradeoffs": ["upside or downside"],
  "next_response": "most likely counterpart response",
  "reversibility": "reversible | partly-reversible | irreversible",
  "evidence_ids": ["F1", "I2"]
}
```

`base_fit` is a heuristic display score from 0–100 at the baseline assumptions. A sensitivity coefficient is the number of fit points added for one unit of parameter change. Use small, transparent coefficients. These values are neither success probabilities nor payoffs and must not be cited as facts.

Include at least the recommended strategy and the best alternative. Include a do-nothing or wait option when it is feasible and materially different.

## Evidence ledger

```json
{
  "evidence": {
    "facts": [
      {
        "id": "F1",
        "claim": "directly supported statement",
        "source_path": "~/cortex/project.md · lines 12–18",
        "as_of": "YYYY-MM-DD or unknown",
        "freshness": "current | dated | unknown"
      }
    ],
    "inferences": [
      {
        "id": "I1",
        "claim": "derived conclusion",
        "basis": "F1 + F2",
        "confidence": "low | medium | high",
        "falsifier": "observable fact that would weaken it"
      }
    ],
    "unknowns": [
      {
        "id": "U1",
        "question": "missing or disputed information",
        "impact": "low | medium | high — how it changes the decision",
        "resolution": "fastest safe way to learn it"
      }
    ]
  }
}
```

A fact always has a source. A dated fact remains a fact about a past state, not the present. An inference cites fact IDs. A material unknown appears in the reversal trigger, risk list, or sensitivity controls.

## Risks, response path, and psychological filter

```json
{
  "risks": [
    {
      "name": "failure mode",
      "severity": "low | medium | high",
      "likelihood": "low | medium | high | unknown",
      "mitigation": "specific safeguard",
      "evidence_ids": ["F2", "I3"]
    }
  ],
  "response_tree": [
    {
      "if": "decision owner takes a move or a condition occurs",
      "then": "likely response and resulting state",
      "owner": "actor responsible for the next action",
      "checkpoint": "when to reassess"
    }
  ],
  "psychology": [
    {
      "pattern": "possible bias, identity pressure, urgency, or power dynamic",
      "status": "observed | inferred | unknown",
      "signal": "what evidence makes it relevant",
      "countermove": "process safeguard, not manipulation"
    }
  ]
}
```

Represent the strongest plausible adverse response. Psychological notes describe decision risk and safeguards; never diagnose a person or claim access to hidden motives.

## Final checks

- The recommendation references a strategy that exists.
- Facts, inferences, and unknowns remain distinguishable.
- The best alternative, main downside, reversal trigger, stop conditions, and human gate are present.
- Hidden actors and the strongest adverse response are represented.
- `proven` is never used without an explicit formal model.
- Report paths and content are minimally disclosed and free of credentials or raw identifiers.
