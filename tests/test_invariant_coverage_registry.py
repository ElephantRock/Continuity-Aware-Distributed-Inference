import json
from pathlib import Path
import re


def canonical_invariant_ids():
    catalogue = Path('spec/03-invariants.md').read_text()
    return set(re.findall(r'^# \d+\. ([A-F]\d+) — ', catalogue, re.MULTILINE))


def load_registry():
    return json.loads(Path('spec/invariant-coverage.json').read_text())


def test_registry_covers_every_canonical_invariant_exactly_once():
    registry = load_registry()
    assert registry['schema'] == 'cadi.invariant-coverage.v1'
    assert registry['catalogue'] == 'spec/03-invariants.md'
    expected = canonical_invariant_ids()
    assert expected
    assert set(registry['invariants']) == expected
    assert len(registry['invariants']) == len(expected)


def test_every_invariant_maps_to_existing_named_pytest_function():
    registry = load_registry()
    for invariant_id, entry in registry['invariants'].items():
        assert entry['title'].strip(), invariant_id
        assert entry['tests'], invariant_id
        for node_id in entry['tests']:
            path_text, separator, function_name = node_id.partition('::')
            assert separator == '::', (invariant_id, node_id)
            path = Path(path_text)
            assert path.is_file(), (invariant_id, node_id)
            source = path.read_text()
            pattern = rf'^def\s+{re.escape(function_name)}\s*\('
            assert re.search(pattern, source, re.MULTILINE), (invariant_id, node_id)


def test_registry_only_points_into_test_tree():
    registry = load_registry()
    for entry in registry['invariants'].values():
        for node_id in entry['tests']:
            assert node_id.startswith('tests/')
