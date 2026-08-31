import json

import pytest

from app.services.sla.contract_spec import (
    ContractSpecMismatchError,
    verify_sla_contract_spec,
    verify_sla_contract_spec_file,
)


VALID_SPEC = {
    "functions": [{
        "name": "calculate_sla",
        "inputs": [
            {"name": "outage_id", "type": "string"},
            {"name": "severity", "type": "string"},
            {"name": "mttr_minutes", "type": "u32"},
            {"name": "policy_version", "type": "string"},
            {"name": "threshold_source", "type": "string"},
        ],
    }],
}


def test_sla_contract_spec_accepts_matching_function():
    verify_sla_contract_spec(VALID_SPEC)


@pytest.mark.parametrize("spec", [
    {"functions": []},
    {"functions": [{"name": "calculate_sla", "inputs": [{"type": "u32"}]}]},
])
def test_sla_contract_spec_rejects_missing_or_incompatible_function(spec):
    with pytest.raises(ContractSpecMismatchError):
        verify_sla_contract_spec(spec)


def test_sla_contract_spec_file_rejects_invalid_json(tmp_path):
    spec_file = tmp_path / "sla-abi.json"
    spec_file.write_text("not json", encoding="utf-8")

    with pytest.raises(ContractSpecMismatchError):
        verify_sla_contract_spec_file(str(spec_file))


def test_sla_contract_spec_file_reads_json(tmp_path):
    spec_file = tmp_path / "sla-abi.json"
    spec_file.write_text(json.dumps(VALID_SPEC), encoding="utf-8")

    verify_sla_contract_spec_file(str(spec_file))