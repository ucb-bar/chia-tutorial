"""XY candidate scores from existing CHIA logs, without changing a live loop."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from chia_dma.score_viz import load_scores, render_scores


def event(candidate, score, timestamp, series="candidates", valid=1, **fields):
    return dict(type="local_end", func="score_point", call_id="reused", ts=timestamp,
                extra=dict(candidate_id=candidate, combined_score=score,
                           metric_series=series, validity=valid), **fields)


def write_log(run, events, partial=""):
    path = run / "artifacts/profile/ChiaProfileCollector.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n" + partial)
    return path


@pytest.fixture
def sample_run(tmp_path):
    run = tmp_path / "DMADiscovery"
    write_log(run, [
        dict(type="dispatch", func="build", call_id="build", ts=100),
        event("candidate-123456", 1.4, 150),
        event("candidate-abcdef", 1.0, 120),
        event("candidate-123456", 1.4, 150),  # Repeated metadata is one proposal.
        event("candidate-abcdef", 1.0, 121, series="best"),
        event("candidate-123456", 1.4, 151, series="best"),
        event("failed-987654", 0, 140, valid=0),
        # Normal evaluate spans also carry scores; they are not extra proposals.
        dict(type="local_end", func="evaluate", call_id="eval", ts=152,
             extra=dict(candidate_id="candidate-123456", combined_score=1.4)),
        dict(type="local_start", func="score_point", call_id="unfinished", ts=160,
             extra=dict(candidate_id="pending", combined_score=999, metric_series="candidates")),
    ], partial='{"type":"local_end"')
    return run


def svg_from(html):
    return ET.fromstring(re.search(r"(<svg\b.*?</svg>)", html, re.S)[1])


def test_one_labeled_dot_per_candidate_in_completion_order(sample_run):
    source = sample_run / "artifacts/profile/ChiaProfileCollector.log"
    original = source.read_bytes()
    scores = load_scores(source)
    assert [p["candidate_id"] for p in scores["points"]] == [
        "candidate-abcdef", "failed-987654", "candidate-123456",
    ]
    assert scores["start"] == 100 and scores["duration"] == 60
    assert scores["best"]["score"] == 1.4
    output = render_scores(sample_run)
    assert output == sample_run / "artifacts/profile/scores.html"
    html = output.read_text()
    svg = svg_from(html)
    ns = {"s": "http://www.w3.org/2000/svg"}
    dots = svg.findall(".//s:circle", ns)
    assert len(dots) == 3
    assert [float(c.attrib["cx"]) for c in dots] == sorted(float(c.attrib["cx"]) for c in dots)
    assert float(dots[1].attrib["cy"]) > float(dots[0].attrib["cy"]) > float(dots[2].attrib["cy"])
    labels = [t.text for t in svg.findall('.//s:text[@class="candidate-label"]', ns)]
    assert labels == ["abcdef", "987654", "123456"]
    assert 'id="best-score">1.4</strong>' in html
    assert 'data-valid="failed"' in html
    assert "Elapsed time (seconds)" in html
    assert not svg.findall(".//s:path", ns) and not svg.findall(".//s:polyline", ns)
    assert source.read_bytes() == original


def test_recorded_checkpoint_best_is_a_number_not_an_extra_dot(tmp_path):
    run = tmp_path / "fork"
    write_log(run, [event("new-111111", 1.2, 20),
                    event("historic-222222", 2.5, 21, series="best")])
    html = render_scores(run).read_text()
    assert 'id="best-score">2.5</strong>' in html
    assert 'id="best-id">historic-222222</span>' in html
    assert len(svg_from(html).findall(".//{*}circle")) == 1


def test_failed_and_nonfinite_values_do_not_become_best_scores(tmp_path):
    run = tmp_path / "failed"
    write_log(run, [event("failed-000000", 8, 2, valid=0),
                    event("nan", float("nan"), 3), event("infinite", float("inf"), 4),
                    event("boolean", True, 5), event("string", "9", 6)])
    scores = load_scores(run / "artifacts/profile/ChiaProfileCollector.log")
    assert len(scores["points"]) == 1 and scores["best"] is None
    html = render_scores(run).read_text()
    assert 'id="best-score">—</strong>' in html


def test_empty_log_and_escaped_candidate_ids(tmp_path):
    run = tmp_path / "empty"
    write_log(run, [])
    assert "No completed candidate scores yet." in render_scores(run).read_text()
    candidate = '</script><script>alert("ID")</script>'
    write_log(run, [event(candidate, 1, 10)])
    html = render_scores(run).read_text()
    assert candidate not in html
    assert svg_from(html).find(".//{*}g").attrib["data-id"] == candidate


def test_small_score_improvements_keep_distinct_axis_labels(tmp_path):
    run = tmp_path / "small"
    write_log(run, [event("a", 1, 1), event("b", 1.00002, 2)])
    svg = svg_from(render_scores(run).read_text())
    labels = [t.text for t in svg.findall('.//{*}text[@text-anchor="end"]')]
    assert len(set(labels)) == 6


def test_scores_cli_does_not_start_or_lock_a_running_loop(sample_run, monkeypatch, capsys):
    import sys
    import loop

    monkeypatch.setattr(sys, "argv", ["loop.py", "scores", "--run", sample_run.name])
    monkeypatch.setattr(loop, "load", lambda *args: {"storage": str(sample_run.parent)})
    def unexpected(*args):
        pytest.fail("Plotting must not attach to, restart, or lock the discovery run")
    monkeypatch.setattr(loop, "RunLock", unexpected)
    monkeypatch.setattr(loop, "run_experiment", unexpected)
    loop.main()
    assert str(sample_run / "artifacts/profile/scores.html") in capsys.readouterr().out


def test_missing_profile_has_a_clear_cli_error(tmp_path, monkeypatch, capsys):
    import sys
    import loop

    monkeypatch.setattr(sys, "argv", ["loop.py", "scores", "--run", "missing"])
    monkeypatch.setattr(loop, "load", lambda *args: {"storage": str(tmp_path)})
    with pytest.raises(SystemExit) as error:
        loop.main()
    assert error.value.code == 2
    assert "No profile log yet" in capsys.readouterr().err
    assert not (tmp_path / "missing").exists()


def test_click_and_keyboard_selection_show_full_candidate_id(sample_run, tmp_path):
    engine = shutil.which("node") or shutil.which("gjs")
    if not engine:
        pytest.skip("Node.js or GJS is required to execute the selection handlers")
    html = render_scores(sample_run).read_text()
    code = re.search(r"<script>(.*?)</script>", html, re.S)[1]
    script = tmp_path / "selection.js"
    script.write_text("""
const point = {
  dataset: {id:'candidate-123456', score:'1.4', time:'50.000', valid:'valid'},
  listeners: {}, classList: {add() {}, remove() {}},
  addEventListener(type, fn) { this.listeners[type]=fn; },
};
const selection = {};
const document = {
  querySelectorAll: () => [point],
  getElementById: () => selection,
};
""" + code + """
point.listeners.click();
if (!selection.textContent.includes('candidate-123456') || !selection.textContent.includes('1.4'))
  throw new Error('Full candidate ID and score must be shown');
selection.textContent='';
point.listeners.keydown({key:'Enter', preventDefault() {}});
if (!selection.textContent.includes('50.000s')) throw new Error('Keyboard selection must work');
""")
    result = subprocess.run([engine, str(script)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
