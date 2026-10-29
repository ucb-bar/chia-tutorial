"""Native CHIA score log partitioning preserves paired events across resumes."""

import json
from types import SimpleNamespace

from chia_dma import profiling


def test_score_log_views_preserve_reused_call_ids(tmp_path, monkeypatch):
    events = []
    # Call IDs can restart in a later job. Match each start/end occurrence.
    for series, score in [('best', 1), ('candidates', 0), ('candidates', 2), ('best', 2)]:
        events.extend([
            {'type': 'local_start', 'func': 'score_point', 'call_id': 'reused', 'ts': len(events)},
            {'type': 'local_end', 'func': 'score_point', 'call_id': 'reused', 'ts': len(events) + 1,
             'extra': {'metric_series': series, 'combined_score': score}},
        ])
    events.append({'type': 'dispatch', 'func': 'build', 'call_id': 'work', 'ts': 0})
    source = tmp_path / 'ChiaProfileCollector.log'
    source.write_text('\n'.join(json.dumps(event) for event in events))
    collector = SimpleNamespace(get_log_path=SimpleNamespace(remote=lambda: str(source)))
    monkeypatch.setattr(profiling, 'get_collector', lambda: collector)
    monkeypatch.setattr(profiling, 'get', lambda value: value)
    profiling.export_profiles(tmp_path)
    full = [json.loads(line) for line in (tmp_path / 'prof_full.log').read_text().splitlines()]
    all_candidates = [json.loads(line) for line in (tmp_path / 'prof_candidates.log').read_text().splitlines()]
    assert len(full) == 5 and len(all_candidates) == 4
    assert [e['extra']['combined_score'] for e in full if 'extra' in e] == [1, 2]
    assert [e['extra']['combined_score'] for e in all_candidates if 'extra' in e] == [0, 2]
    assert source.read_text() == '\n'.join(json.dumps(event) for event in events)
