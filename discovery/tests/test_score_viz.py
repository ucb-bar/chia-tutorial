"""XY candidate scores from existing CHIA logs, without changing a live loop."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

from chia_dma.score_viz import copy_score_history, load_scores, render_scores, score_table


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
    assert "Elapsed time (h:mm)" in html
    green = svg.findall('.//s:g[@class="candidate valid best-candidate"]', ns)
    assert [p.attrib["data-id"] for p in green] == ["candidate-123456"]
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


def test_axes_use_half_hour_and_half_point_ticks_without_rounding_scores(tmp_path):
    run = tmp_path / "small"
    write_log(run, [event("a", 1, 1), event("b", 1.00002, 4000)])
    svg = svg_from(render_scores(run).read_text())
    x_ticks = svg.findall('.//{*}text[@class="tick x-tick"]')
    assert [t.text for t in x_ticks] == ["0:00", "0:30", "1:00", "1:30"]
    assert [float(t.attrib["data-value"]) for t in x_ticks] == [0, 1800, 3600, 5400]
    y_ticks = svg.findall('.//{*}text[@class="tick y-tick"]')
    assert [float(t.text) for t in y_ticks] == [0, 0.5, 1, 1.5]
    assert [p.attrib["data-score"] for p in svg.findall('.//{*}g')] == ["1", "1.00002"]


def test_best_dot_marks_valid_ties_but_not_failed_attempts(tmp_path):
    run = tmp_path / "ties"
    write_log(run, [event("a", 1.5, 1), event("b", 1.5, 2), event("failed", 1.5, 3, valid=0)])
    svg = svg_from(render_scores(run).read_text())
    green = svg.findall('.//{*}g[@class="candidate valid best-candidate"]')
    assert [p.attrib["data-id"] for p in green] == ["a", "b"]


def test_checkpoint_score_history_excludes_future_and_pending_results(tmp_path):
    origin, target = tmp_path / "source", tmp_path / "fork"
    points = [event("a", 1, 120), event("a", 1, 121, series="best"),
              event("pending", 2, 130), event("future", 3, 140),
              event("failed-generation", 0, 145, valid=0),
              event("after-checkpoint", 0, 200, valid=0)]
    points[1]["extra"]["evaluation_id"] = "a"
    points[4]["extra"]["stage"] = points[5]["extra"]["stage"] = "generation"
    future_best = event("future", 3, 149, series="best")
    future_best["extra"]["evaluation_id"] = "a"
    points.append(future_best)
    events = [dict(type="dispatch", func="build", ts=100)]
    for index, point in enumerate(points):
        identity = str(index)
        events.append(dict(type="local_start", func="score_point", call_id=identity, ts=point["ts"] - 0.1))
        point["call_id"] = identity
        events.append(point)
    write_log(origin, events)
    copy_score_history(origin, target, {
        "a": {"evaluated": True}, "pending": {"evaluated": False},
        "failed-generation": {"evaluated": False}, "after-checkpoint": {"evaluated": False},
    }, cutoff=150)
    saved = load_scores(target / "artifacts/profile/ChiaProfileCollector.log")
    assert [p["candidate_id"] for p in saved["points"]] == ["a", "failed-generation"]
    assert saved["start"] == 100
    assert saved["best"]["score"] == 1
    assert render_scores(target).exists()  # No resumed loop, source tree, or database required.


@pytest.mark.parametrize("output_format", [None, "html", "table"])
def test_scores_cli_does_not_start_or_lock_a_running_loop(sample_run, monkeypatch, capsys, output_format):
    import sys
    import loop

    options = ["--format", output_format] if output_format else []
    monkeypatch.setattr(sys, "argv", ["loop.py", "scores", "--run", sample_run.name, *options])
    monkeypatch.setattr(loop, "load", lambda *args: {"storage": str(sample_run.parent)})
    def unexpected(*args):
        pytest.fail("Plotting must not attach to, restart, or lock the discovery run")
    monkeypatch.setattr(loop, "RunLock", unexpected)
    monkeypatch.setattr(loop, "run_experiment", unexpected)
    loop.main()
    output = capsys.readouterr().out
    html_path = sample_run / "artifacts/profile/scores.html"
    if output_format == "table":
        assert "Candidate ID" in output
        assert output.index("candidate-123456") < output.index("candidate-abcdef") < output.index("failed-987654")
        assert not html_path.exists()
    else:
        assert str(html_path) in output and html_path.exists()


def test_score_table_deduplicates_and_sorts_numerically_with_full_ids(tmp_path):
    run = tmp_path / "table"
    write_log(run, [event("candidate-abcdef", 2, 2), event("candidate-123456", 10, 4),
                    event("candidate-abcdef", 2, 2), event("failed-987654", 0, 3, valid=0),
                    event("candidate-000001", 2.00002, 1),
                    event("candidate-123456", 10, 5, series="best")])
    table = score_table(run)
    rows = [line.split() for line in table.splitlines()[2:]]
    assert [row[1] for row in rows] == [
        "candidate-123456", "candidate-000001", "candidate-abcdef", "failed-987654",
    ]
    assert [row[2] for row in rows] == ["10", "2.00002", "2", "0"]
    assert [row[3] for row in rows] == ["valid", "valid", "valid", "failed"]
    write_log(run, [])
    assert score_table(run) == "No completed candidate scores yet."


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


@pytest.mark.parametrize("mode", ["modern", "fallback", "denied"])
def test_copyable_tooltip_and_keyboard_selection(sample_run, tmp_path, mode):
    engine = shutil.which("node") or shutil.which("gjs")
    if not engine:
        pytest.skip("Node.js or GJS is required to execute the selection handlers")
    html = render_scores(sample_run).read_text()
    code = re.search(r"<script>(.*?)</script>", html, re.S)[1]
    script = tmp_path / "selection.mjs"
    script.write_text("const mode=" + json.dumps(mode) + ";\n" + """
let copied='', selected=false;
const elements=new Map();
function element() {
  return {value:'',textContent:'',hidden:true,style:{},listeners:{},
    addEventListener(type,fn) { this.listeners[type]=fn; },
    getBoundingClientRect: () => ({left:100,top:100,width:200,height:100}),
    select() { selected=true; }, contains: () => true,
  };
}
const point = {...element(),
  dataset: {id:'candidate-123456',score:'1.4',time:'50.000',valid:'valid'},
  classList: {add() {},remove() {}},
};
const document = {
  querySelectorAll: () => [point],
  getElementById(id) { if (!elements.has(id)) elements.set(id,element()); return elements.get(id); },
  addEventListener() {},
  execCommand(command) {
    if (command!=='copy') throw new Error('Expected clipboard copy');
    copied=document.getElementById('tooltip-id').value;
    return mode!=='denied';
  },
};
const navigator=mode==='fallback' ? {} : {clipboard:{writeText: async text => {
  if (mode==='denied') throw new Error('Clipboard blocked');
  copied=text;
}}};
const window={innerWidth:1200,innerHeight:800};
""" + code + """
point.listeners.mouseenter({clientX:200,clientY:200});
if (elements.get('tooltip').hidden || elements.get('tooltip-id').value!=='candidate-123456')
  throw new Error('Hover must show a copyable full ID');
point.listeners.click();
if (!elements.get('selection').textContent.includes('candidate-123456'))
  throw new Error('Click must select the full candidate');
await elements.get('copy-id').listeners.click();
if (copied!=='candidate-123456') throw new Error('Must copy the full ID, not its suffix');
if (mode==='modern' && selected) throw new Error('Use clipboard API when available');
if (mode!=='modern' && !selected) throw new Error('Fallback must select the copyable field');
if (mode==='denied' && !elements.get('copy-status').textContent.includes('Ctrl/Cmd+C'))
  throw new Error('Manual copy must remain available if clipboard access is denied');
if (mode!=='denied' && elements.get('copy-status').textContent!=='Copied')
  throw new Error('Copy success must be reported');
elements.get('selection').textContent='';
point.listeners.keydown({key:'Enter',preventDefault() {}});
if (!elements.get('selection').textContent.includes('50.000s')) throw new Error('Keyboard selection must work');
elements.get('tooltip-close').listeners.click();
if (!elements.get('tooltip').hidden) throw new Error('Tooltip must close');
""")
    command = [engine, "-m", str(script)] if Path(engine).name == "gjs" else [engine, str(script)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
