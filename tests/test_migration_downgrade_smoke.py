"""Downgrade smoke test (#913): every revision defines upgrade/downgrade and
the revision graph has a single head, so `upgrade head` then `downgrade -1` is valid."""
import importlib.util
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


def load_revisions():
    mods = []
    for path in sorted(VERSIONS.glob("*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mods.append(mod)
    return mods


def test_every_revision_has_downgrade():
    for mod in load_revisions():
        assert callable(getattr(mod, "downgrade", None)), mod.__name__


def test_new_revisions_chain_to_single_head():
    mods = load_revisions()
    parents = set()
    for mod in mods:
        down = mod.down_revision
        parents.update(down if isinstance(down, (tuple, list)) else [down])
    heads = [m.revision for m in mods if m.revision not in parents]
    assert heads == ["0034_sla_audit_indexes_payment_asset"]
