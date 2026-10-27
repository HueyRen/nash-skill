#!/usr/bin/env python3
"""Validate the small, stable contract used by Nash reports."""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
from pathlib import Path


REQUIRED_TOP = {
    "version": str,
    "meta": dict,
    "context": dict,
    "decision": dict,
    "parameters": list,
    "players": list,
    "strategies": list,
    "evidence": dict,
    "risks": list,
    "response_tree": list,
    "psychology": list,
}


def require(obj: dict, keys: tuple[str, ...], where: str, errors: list[str]) -> None:
    for key in keys:
        if key not in obj:
            errors.append(f"{where}: missing {key!r}")


def iter_strings(value: object, path: str = "root"):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from iter_strings(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from iter_strings(item, f"{path}[{index}]")


def validate_formal_model(formal: object, errors: list[str]) -> None:
    if not isinstance(formal, dict):
        errors.append("meta.formal_model is required when equilibrium_status is 'proven'")
        return

    require(
        formal,
        (
            "model_type",
            "players",
            "strategies",
            "payoffs",
            "equilibrium_profile",
            "best_response_checks",
            "solver",
            "verified",
        ),
        "meta.formal_model",
        errors,
    )
    if formal.get("model_type") != "pure-strategy-normal-form":
        errors.append("meta.formal_model.model_type: only pure-strategy-normal-form is currently verified")

    players = formal.get("players", [])
    strategies = formal.get("strategies", {})
    valid_players = (
        isinstance(players, list)
        and len(players) >= 2
        and all(isinstance(player, str) and player for player in players)
        and len(players) == len(set(players))
    )
    if not valid_players:
        errors.append("meta.formal_model.players: expected at least two unique player ids")
        return

    valid_strategies = isinstance(strategies, dict)
    if valid_strategies:
        for player in players:
            options = strategies.get(player)
            if (
                not isinstance(options, list)
                or not options
                or not all(isinstance(option, str) and option for option in options)
                or len(options) != len(set(options))
            ):
                valid_strategies = False
                break
    if not valid_strategies:
        errors.append("meta.formal_model.strategies: enumerate a non-empty unique strategy set for every player")
        return

    expected_profiles = list(itertools.product(*(strategies[player] for player in players)))
    if len(expected_profiles) > 256:
        errors.append("meta.formal_model: more than 256 pure profiles; use a dedicated reviewed solver")
        return

    payoff_rows = formal.get("payoffs")
    payoff_map: dict[tuple[str, ...], dict[str, float]] = {}
    if not isinstance(payoff_rows, list):
        errors.append("meta.formal_model.payoffs: expected a complete list of payoff profiles")
        payoff_rows = []
    for index, row in enumerate(payoff_rows):
        where = f"meta.formal_model.payoffs[{index}]"
        if not isinstance(row, dict):
            errors.append(f"{where}: expected object")
            continue
        profile = row.get("profile")
        values = row.get("values")
        if not isinstance(profile, list) or len(profile) != len(players):
            errors.append(f"{where}.profile: expected one strategy per player in player order")
            continue
        profile_tuple = tuple(profile)
        if any(profile_tuple[player_index] not in strategies[player] for player_index, player in enumerate(players)):
            errors.append(f"{where}.profile: contains a strategy outside the enumerated set")
            continue
        if profile_tuple in payoff_map:
            errors.append(f"{where}.profile: duplicate payoff profile")
            continue
        if not isinstance(values, dict) or set(values) != set(players):
            errors.append(f"{where}.values: provide exactly one payoff for every player")
            continue
        numeric_values: dict[str, float] = {}
        invalid_value = False
        for player in players:
            value = values[player]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                errors.append(f"{where}.values.{player}: expected a finite numeric payoff")
                invalid_value = True
                break
            numeric_values[player] = float(value)
        if not invalid_value:
            payoff_map[profile_tuple] = numeric_values

    expected_set = set(expected_profiles)
    actual_set = set(payoff_map)
    if expected_set != actual_set:
        missing = len(expected_set - actual_set)
        extra = len(actual_set - expected_set)
        errors.append(f"meta.formal_model.payoffs: incomplete payoff table ({missing} missing, {extra} extra profiles)")

    equilibrium = formal.get("equilibrium_profile")
    equilibrium_tuple = tuple(equilibrium) if isinstance(equilibrium, list) else ()
    if equilibrium_tuple not in expected_set:
        errors.append("meta.formal_model.equilibrium_profile: expected an enumerated pure strategy profile")
    elif equilibrium_tuple in payoff_map and expected_set == actual_set:
        for player_index, player in enumerate(players):
            equilibrium_payoff = payoff_map[equilibrium_tuple][player]
            profitable = []
            for alternative in strategies[player]:
                if alternative == equilibrium_tuple[player_index]:
                    continue
                deviated = list(equilibrium_tuple)
                deviated[player_index] = alternative
                deviated_tuple = tuple(deviated)
                if payoff_map[deviated_tuple][player] > equilibrium_payoff:
                    profitable.append(alternative)
            if profitable:
                errors.append(
                    f"meta.formal_model.equilibrium_profile: {player} has profitable unilateral deviation(s) {profitable}"
                )

    checks = formal.get("best_response_checks")
    checked_players: set[str] = set()
    if isinstance(checks, list):
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                errors.append(f"meta.formal_model.best_response_checks[{index}]: expected object")
                continue
            actor = check.get("actor_id")
            profile = check.get("profile")
            if actor in players and profile == list(equilibrium_tuple) and check.get("profitable_deviation") == "none":
                checked_players.add(actor)
    if set(players) != checked_players:
        errors.append("meta.formal_model.best_response_checks: every player needs a no-deviation check at the equilibrium profile")

    if not isinstance(formal.get("solver"), str) or not formal["solver"].strip():
        errors.append("meta.formal_model.solver: identify the solver or exhaustive method used")
    if formal.get("verified") is not True:
        errors.append("meta.formal_model.verified: must be true after solver verification")


def validate(data: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["root must be an object"]

    for key, expected in REQUIRED_TOP.items():
        if key not in data:
            errors.append(f"root: missing {key!r}")
        elif not isinstance(data[key], expected):
            errors.append(f"root.{key}: expected {expected.__name__}")

    if errors:
        return errors

    if data["version"] != "1.0":
        errors.append("version: only schema version '1.0' is supported")

    require(data["meta"], ("slug", "title", "question", "as_of", "confidence", "equilibrium_status"), "meta", errors)
    require(
        data["context"],
        ("thread_intent", "roots_considered", "sources_used", "omissions", "stale_warnings", "redactions"),
        "context",
        errors,
    )
    require(
        data["decision"],
        (
            "recommended_strategy_id",
            "recommendation",
            "rationale",
            "next_step",
            "best_alternative",
            "main_downside",
            "reversal_trigger",
            "stop_conditions",
            "human_gate",
        ),
        "decision",
        errors,
    )
    require(data["evidence"], ("facts", "inferences", "unknowns"), "evidence", errors)

    parameter_ids: set[str] = set()
    for index, item in enumerate(data["parameters"]):
        where = f"parameters[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected object")
            continue
        require(item, ("id", "label", "value", "min", "max", "step", "low_label", "high_label"), where, errors)
        pid = item.get("id")
        if isinstance(pid, str):
            if pid in parameter_ids:
                errors.append(f"{where}: duplicate id {pid!r}")
            parameter_ids.add(pid)
        value, minimum, maximum = item.get("value"), item.get("min"), item.get("max")
        if all(isinstance(v, (int, float)) for v in (value, minimum, maximum)) and not minimum <= value <= maximum:
            errors.append(f"{where}: value must be within min/max")
    if not 2 <= len(data["parameters"]) <= 4:
        errors.append("parameters: expected 2 to 4 decision-relevant controls")

    strategy_ids: set[str] = set()
    for index, item in enumerate(data["strategies"]):
        where = f"strategies[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected object")
            continue
        require(
            item,
            ("id", "name", "summary", "base_fit", "sensitivity", "tradeoffs", "conditions", "next_response", "reversibility"),
            where,
            errors,
        )
        sid = item.get("id")
        if isinstance(sid, str):
            if sid in strategy_ids:
                errors.append(f"{where}: duplicate id {sid!r}")
            strategy_ids.add(sid)
        if not isinstance(item.get("sensitivity", {}), dict):
            errors.append(f"{where}.sensitivity: expected object")
        else:
            unknown = set(item["sensitivity"]) - parameter_ids
            if unknown:
                errors.append(f"{where}.sensitivity: unknown parameter ids {sorted(unknown)}")
            for parameter_id, coefficient in item["sensitivity"].items():
                if not isinstance(coefficient, (int, float)):
                    errors.append(f"{where}.sensitivity.{parameter_id}: expected number")
                elif abs(coefficient) > 2:
                    errors.append(f"{where}.sensitivity.{parameter_id}: implausibly large display coefficient")
        if isinstance(item.get("base_fit"), (int, float)) and not 0 <= item["base_fit"] <= 100:
            errors.append(f"{where}.base_fit: expected 0..100")

    if len(data["strategies"]) < 2:
        errors.append("strategies: expected at least a preferred option and an alternative")

    selected = data["decision"].get("recommended_strategy_id")
    if selected not in strategy_ids:
        errors.append("decision.recommended_strategy_id must reference a strategy")

    for kind in ("facts", "inferences", "unknowns"):
        if not isinstance(data["evidence"].get(kind), list):
            errors.append(f"evidence.{kind}: expected list")

    for index, item in enumerate(data["evidence"].get("facts", [])):
        where = f"evidence.facts[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected object")
            continue
        require(item, ("id", "claim", "source_path", "as_of", "freshness"), where, errors)
        if item.get("freshness") not in {"current", "dated", "unknown"}:
            errors.append(f"{where}.freshness: unsupported value")

    for index, item in enumerate(data["players"]):
        where = f"players[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected object")
            continue
        require(item, ("id", "name", "role", "hidden", "objective", "constraints", "leverage", "batna", "likely_move"), where, errors)
    if len(data["players"]) < 2:
        errors.append("players: Nash analysis requires at least two materially distinct actors")

    for index, item in enumerate(data["risks"]):
        where = f"risks[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected object")
            continue
        require(item, ("name", "severity", "likelihood", "mitigation"), where, errors)

    for index, item in enumerate(data["response_tree"]):
        where = f"response_tree[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected object")
            continue
        require(item, ("if", "then", "owner", "checkpoint"), where, errors)

    for index, item in enumerate(data["psychology"]):
        where = f"psychology[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: expected object")
            continue
        require(item, ("pattern", "status", "signal", "countermove"), where, errors)

    status = data["meta"].get("equilibrium_status")
    if data["meta"].get("confidence") not in {"low", "medium", "high"}:
        errors.append("meta.confidence: expected low, medium, or high")
    if status not in {"candidate", "none", "proven", "not-assessed"}:
        errors.append("meta.equilibrium_status: unsupported value")
    if status == "proven":
        validate_formal_model(data["meta"].get("formal_model"), errors)

    serialized = json.dumps(data, ensure_ascii=False)
    secret_patterns = (
        r"\bAKIA[0-9A-Z]{16}\b",
        r"\b(?:sk|ghp)-[A-Za-z0-9_\-]{20,}\b",
        r"(?i)\b(?:api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\s,}\]]{8,}",
    )
    if any(re.search(pattern, serialized) for pattern in secret_patterns):
        errors.append("report payload appears to contain a credential or secret")

    privacy_patterns = (
        (re.compile(r"(?:^|\s)/(?:Users|home)/[^/\s]+"), "absolute home path; present it as ~/…"),
        (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "email address"),
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN-like identifier"),
        (re.compile(r"\b\d{9,18}\b"), "raw long account or tax identifier"),
    )
    reported_privacy_errors: set[str] = set()
    for path, value in iter_strings(data):
        for pattern, label in privacy_patterns:
            if pattern.search(value) and label not in reported_privacy_errors:
                errors.append(f"{path}: report payload contains {label}")
                reported_privacy_errors.add(label)

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("analysis", type=Path)
    args = parser.parse_args()
    data = json.loads(args.analysis.read_text(encoding="utf-8"))
    errors = validate(data)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print("Analysis is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
