"""Verification of the exported Soroban contract ABI used by SLA math."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


class ContractSpecMismatchError(RuntimeError):
    """Raised when the deployed SLA contract ABI differs from this service."""


EXPECTED_SLA_FUNCTIONS: dict[str, tuple[str, ...]] = {
    "calculate_sla": ("string", "string", "u32", "string", "string"),
}


def _normalize_type(value: Any) -> str:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, Mapping):
        if len(value) != 1:
            raise ContractSpecMismatchError(f"Unsupported Soroban ABI type: {value!r}")
        return str(next(iter(value))).lower()
    raise ContractSpecMismatchError(f"Unsupported Soroban ABI type: {value!r}")


def verify_sla_contract_spec(spec: Mapping[str, Any]) -> None:
    """Raise when the exported Soroban ABI cannot support local SLA calls."""
    functions = spec.get("functions", spec.get("funcs"))
    if not isinstance(functions, list):
        raise ContractSpecMismatchError("Soroban ABI must contain a functions array.")

    actual: dict[str, tuple[str, ...]] = {}
    for function in functions:
        if not isinstance(function, Mapping) or not isinstance(function.get("name"), str):
            continue
        inputs = function.get("inputs", function.get("params", []))
        if not isinstance(inputs, list):
            raise ContractSpecMismatchError(
                f"Soroban ABI inputs for {function['name']} must be an array."
            )
        actual[function["name"]] = tuple(
            _normalize_type(parameter.get("type"))
            if isinstance(parameter, Mapping)
            else _normalize_type(parameter)
            for parameter in inputs
        )

    for name, expected_types in EXPECTED_SLA_FUNCTIONS.items():
        if name not in actual:
            raise ContractSpecMismatchError(f"Soroban ABI is missing required function: {name}.")
        if actual[name] != expected_types:
            raise ContractSpecMismatchError(
                f"Soroban ABI signature mismatch for {name}: "
                f"expected {expected_types}, got {actual[name]}."
            )


def verify_sla_contract_spec_file(path: str) -> None:
    """Load a Soroban CLI JSON ABI export and verify its SLA entrypoint."""
    try:
        with Path(path).open(encoding="utf-8") as spec_file:
            spec = json.load(spec_file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractSpecMismatchError(f"Unable to load Soroban ABI spec at {path}: {exc}") from exc
    if not isinstance(spec, Mapping):
        raise ContractSpecMismatchError("Soroban ABI document must be a JSON object.")
    verify_sla_contract_spec(spec)