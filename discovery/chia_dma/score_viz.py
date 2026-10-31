"""Score tables and XY charts from the loop's existing native CHIA profile log."""

from html import escape
from collections import defaultdict, deque
import json
import math
from pathlib import Path


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        value = float(value)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _events(path):
    with Path(path).open() as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event


def copy_score_history(origin, target, requests, cutoff):
    """Retain small native score-event pairs visible at a fork's checkpoint.

    Build/simulation spans and logs are omitted. The timestamp boundary also
    excludes generation failures that finished after the chosen checkpoint.
    Missing old profile logs cannot supply historical completion timestamps.
    """
    source = Path(origin) / "artifacts/profile/ChiaProfileCollector.log"
    if not source.is_file():
        return
    pending, selected = defaultdict(deque), []
    first = cutoff
    for event in _events(source):
        timestamp = _number(event.get("ts"))
        if timestamp is None or timestamp > cutoff:
            continue
        first = min(first, timestamp)
        if event.get("func") != "score_point":
            continue
        key = (event.get("call_id"), event.get("func"))
        if event.get("type") in ("local_start", "dispatch"):
            pending[key].append(event)
        elif event.get("type") in ("local_end", "complete") and pending[key]:
            start = pending[key].popleft()
            extra = event.get("extra", {})
            if not isinstance(extra, dict):
                continue
            if extra.get("metric_series") not in ("candidates", "best"):
                continue
            candidate = extra.get("evaluation_id", extra.get("candidate_id"))
            flags = requests.get(candidate)
            if flags is None or (not flags["evaluated"] and extra.get("stage") != "generation"):
                continue
            if extra["metric_series"] == "best" and not requests.get(extra.get("candidate_id"), {}).get("evaluated"):
                continue
            selected.extend((start, event))
    destination = Path(target) / "artifacts/profile/ChiaProfileCollector.log"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w") as stream:
        stream.write(json.dumps({"type": "score_history_start", "ts": first}) + "\n")
        for event in selected:
            stream.write(json.dumps(event) + "\n")


def load_scores(path):
    """Read completed score samples, deduplicating proposals by candidate ID.

    The original collector log supplies both candidate samples and recorded
    bests, including best scores inherited from a restored checkpoint. A live
    writer's incomplete final line is ignored. No collector or database is used.
    """
    candidates, recorded_bests = {}, []
    start = end = None
    for event in _events(path):
        timestamp = _number(event.get("ts"))
        if timestamp is None:
            continue
        start = timestamp if start is None else min(start, timestamp)
        end = timestamp if end is None else max(end, timestamp)
        if event.get("type") not in ("complete", "local_end"):
            continue
        extra = event.get("extra", {})
        if not isinstance(extra, dict):
            continue
        score = _number(extra.get("combined_score"))
        if score is None:
            continue
        candidate = extra.get("candidate_id", "")
        candidate = candidate if isinstance(candidate, str) else ""
        point = dict(candidate_id=candidate, score=score, timestamp=timestamp,
                     valid=extra.get("validity", 1) == 1)
        if extra.get("metric_series") == "candidates" and candidate:
            previous = candidates.get(candidate)
            if previous is None or timestamp >= previous["timestamp"]:
                candidates[candidate] = point
        elif extra.get("metric_series") == "best" and point["valid"]:
            recorded_bests.append(point)
    points = sorted(candidates.values(), key=lambda p: (p["timestamp"], p["candidate_id"]))
    valid = recorded_bests + [p for p in points if p["valid"]]
    return dict(points=points, best=max(valid, key=lambda p: p["score"]) if valid else None,
                start=start or 0, duration=max(1, (end or 0) - (start or 0)))


def score_table(run):
    """Format one row per candidate, ordered by descending unrounded score."""
    scores = load_scores(Path(run) / "artifacts/profile/ChiaProfileCollector.log")
    points = sorted(scores["points"], key=lambda p: (-p["score"], p["timestamp"], p["candidate_id"]))
    if not points:
        return "No completed candidate scores yet."
    headers = ("Rank", "Candidate ID", "Score", "Status")
    rows = [(str(index), point["candidate_id"], format(point["score"], ".15g"),
             "valid" if point["valid"] else "failed")
            for index, point in enumerate(points, 1)]
    widths = [max(len(headers[i]), max(len(row[i]) for row in rows)) for i in range(len(headers))]

    def line(row):
        return "  ".join(value.rjust(width) if i in (0, 2) else value.ljust(width)
                         for i, (value, width) in enumerate(zip(row, widths))).rstrip()

    return "\n".join([line(headers), line(tuple("-" * width for width in widths)),
                      *(line(row) for row in rows)])


def render_scores(run):
    """Write artifacts/profile/scores.html without changing a running experiment."""
    run = Path(run)
    profile = run / "artifacts/profile"
    scores = load_scores(profile / "ChiaProfileCollector.log")
    points, best = scores["points"], scores["best"]
    values = [p["score"] for p in points] or [0]
    score_step, time_step = 0.5, 1800
    low = min(0, math.floor(min(values) / score_step) * score_step)
    high = max(low + score_step, (math.floor(max(values) / score_step) + 1) * score_step)
    duration = max(time_step, math.ceil(scores["duration"] / time_step) * time_step)
    plot_width = max(1000, 130 + round(duration / time_step) * 56)
    left, right, top, bottom = 88, plot_width - 42, 32, 500
    svg = [f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}"/>',
           f'<line class="axis" x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}"/>']
    for i in range(round((high - low) / score_step) + 1):
        value = low + i * score_step
        y = bottom - (bottom - top) * (value - low) / (high - low)
        svg.extend([
            f'<line class="grid" x1="{left}" y1="{y}" x2="{right}" y2="{y}"/>',
            f'<text class="tick y-tick" data-value="{value:g}" x="{left - 12}" y="{y + 4}" text-anchor="end">{value:g}</text>',
        ])
    for i in range(round(duration / time_step) + 1):
        seconds = i * time_step
        x = left + (right - left) * seconds / duration
        hours, minutes = divmod(seconds // 60, 60)
        svg.extend([
            f'<line class="grid" x1="{x}" y1="{top}" x2="{x}" y2="{bottom}"/>',
            f'<text class="tick x-tick" data-value="{seconds}" x="{x}" y="{bottom + 25}" text-anchor="middle">{hours}:{minutes:02}</text>',
        ])
    svg.extend([
        f'<text class="axis-title" x="{(left + right) / 2}" y="556" text-anchor="middle">Elapsed time (h:mm)</text>',
        '<text class="axis-title" transform="translate(22,266) rotate(-90)" text-anchor="middle">Score</text>',
    ])
    labels = []
    for point in points:
        candidate, score = point["candidate_id"], point["score"]
        seconds = point["timestamp"] - scores["start"]
        x = left + seconds / duration * (right - left)
        y = bottom - (score - low) / (high - low) * (bottom - top)
        label = candidate[-6:]
        label_width = max(1, len(label)) * 7
        label_x = max(left, min(x + 8, right - label_width))
        # Stagger nearby labels while retaining a visible label for every dot.
        positions = [min(bottom - 3, max(top + 12, y + offset))
                     for offset in (-12, 22, -27, 37, -42, 52)]
        def overlaps(label_y):
            return sum(max(0, min(label_x + label_width, old_x + old_width) - max(label_x, old_x))
                       * max(0, 14 - abs(label_y - old_y))
                       for old_x, old_y, old_width in labels)
        label_y = min(positions, key=overlaps)
        labels.append((label_x, label_y, label_width))
        identifier = escape(candidate, quote=True)
        verdict = "valid" if point["valid"] else "failed"
        highlight = " best-candidate" if best and point["valid"] and score == best["score"] else ""
        svg.append(
            f'<g class="candidate {verdict}{highlight}" tabindex="0" role="button" '
            f'aria-label="{identifier}: score {score:.15g}, {verdict}" '
            f'data-id="{identifier}" data-score="{score:.15g}" data-time="{seconds:.3f}" data-valid="{verdict}">'
            f'<desc>{escape(candidate)}; score {score:.15g}; elapsed {seconds:.3f}s; {verdict}</desc>'
            f'<circle cx="{x:.3f}" cy="{y:.3f}" r="5"/>'
            f'<text class="candidate-label" x="{label_x:.3f}" y="{label_y:.3f}">{escape(label)}</text></g>'
        )
    if not points:
        svg.append('<text x="520" y="260" text-anchor="middle">No completed candidate scores yet.</text>')
    best_value = format(best["score"], ".15g") if best else "—"
    best_id = escape(best["candidate_id"]) if best else "No valid score recorded"
    page = (_PAGE.replace("{{RUN}}", escape(run.name)).replace("{{BEST}}", best_value)
            .replace("{{BEST_ID}}", best_id).replace("{{COUNT}}", str(len(points)))
            .replace("{{PLOT_WIDTH}}", str(plot_width))
            .replace("{{SVG}}", "\n".join(svg)))
    output = profile / "scores.html"
    output.write_text(page)
    return output


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Candidate scores — {{RUN}}</title>
<style>
* { box-sizing: border-box; }
body { margin: 0; padding: 30px; background: #101522; color: #e0e6f2; font: 15px system-ui,sans-serif; }
main { max-width: 1320px; margin: auto; }
header { display: flex; justify-content: space-between; align-items: center; gap: 24px; flex-wrap: wrap; }
h1 { margin: 0 0 6px; font-size: 26px; font-weight: 600; }
p { color: #aab8ce; }
header p { margin: 0; }
.best { max-width: 470px; padding: 16px 22px; border: 1px solid #33445e; border-radius: 10px; background: #172337; }
.best small { display: block; color: #aab8ce; }
#best-score { font-size: 32px; font-weight: 650; color: #80e2bd; }
#best-id { display: block; margin-top: 4px; font: 12px monospace; overflow-wrap: anywhere; }
figure { margin: 24px 0 0; padding: 12px; border: 1px solid #2d3b53; border-radius: 10px; background: #141d2e; overflow-x: auto; }
svg { display: block; width: 100%; min-width: {{PLOT_WIDTH}}px; height: auto; }
text { fill: #b8c5d9; font: 12px ui-monospace,monospace; }
.axis { stroke: #75849d; }
.grid { stroke: #2b3850; stroke-dasharray: 3 5; }
.axis-title { fill: #e0e6f2; font: 15px system-ui,sans-serif; }
.candidate { cursor: pointer; outline: none; }
.candidate circle { fill: #80b8ff; stroke: #15243d; stroke-width: 2px; }
.candidate.failed circle { fill: #f18d9e; }
.candidate.best-candidate circle { fill: #80e2bd; }
.candidate-label { font-size: 11px; fill: #d4deed; paint-order: stroke; stroke: #141d2e; stroke-width: 3px; }
.candidate:hover circle, .candidate:focus circle, .candidate.selected circle { stroke: white; stroke-width: 3px; }
figcaption { color: #aab8ce; padding: 0 16px 12px; font-size: 13px; }
#selection { min-height: 25px; overflow-wrap: anywhere; }
.note { font-size: 12px; }
#tooltip { position: fixed; z-index: 10; width: min(430px,calc(100vw - 24px)); padding: 16px;
  color: #e0e6f2; background: #1b2940; border: 1px solid #5a7398; border-radius: 9px;
  box-shadow: 0 8px 28px #0008; }
#tooltip[hidden] { display: none; }
#tooltip label { display: block; font-size: 12px; color: #aab8ce; margin-bottom: 7px; }
#tooltip-id { width: 100%; padding: 8px; color: #e0e6f2; background: #101522;
  border: 1px solid #486080; border-radius: 4px; font: 12px monospace; user-select: text; }
#tooltip button { padding: 6px 10px; color: #e0e6f2; background: #294363;
  border: 1px solid #5a7398; border-radius: 4px; cursor: pointer; }
#tooltip-close { float: right; }
#tooltip-detail { margin: 10px 0; font-size: 13px; }
#copy-status { margin-left: 8px; font-size: 12px; }
@media (max-width: 600px) { body { padding: 14px; } }
</style></head><body><main>
<header><div><h1>Candidate scores over time</h1><p>{{RUN}} · {{COUNT}} candidates · labels show the last 6 ID characters</p></div>
<div class="best"><small>Best valid score</small><strong id="best-score">{{BEST}}</strong><span id="best-id">{{BEST_ID}}</span></div></header>
<figure><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {{PLOT_WIDTH}} 580" aria-label="Candidate score versus elapsed time">
{{SVG}}
</svg><figcaption>Each dot is one completed candidate attempt. Green marks the best valid score; pink marks failures. Hover or select a dot to copy its full ID.</figcaption></figure>
<p id="selection" aria-live="polite">Select a candidate to inspect its score and completion time.</p>
<p class="note">Snapshot of the native CHIA profile log. Rerun the scores command and reload this page for newer results. Best score includes any recorded best inherited from a checkpoint.</p>
</main>
<div id="tooltip" role="dialog" aria-label="Candidate details" hidden>
  <button id="tooltip-close" type="button" aria-label="Close candidate details">×</button>
  <label for="tooltip-id">Candidate ID</label>
  <input id="tooltip-id" readonly spellcheck="false" aria-label="Full candidate ID">
  <p id="tooltip-detail"></p>
  <button id="copy-id" type="button">Copy ID</button><span id="copy-status" role="status"></span>
</div><script>
const tooltip = document.getElementById('tooltip');
const idField = document.getElementById('tooltip-id');
const copyStatus = document.getElementById('copy-status');
document.querySelectorAll('.candidate').forEach(point => {
  const show = event => {
    idField.value = point.dataset.id;
    copyStatus.textContent = '';
    document.getElementById('tooltip-detail').textContent = 'Score ' + point.dataset.score
      + ' · ' + point.dataset.time + 's elapsed · ' + point.dataset.valid;
    tooltip.hidden = false;
    const pointBox = point.getBoundingClientRect(), box = tooltip.getBoundingClientRect();
    const x = event?.clientX ?? pointBox.left + pointBox.width / 2;
    const y = event?.clientY ?? pointBox.top + pointBox.height / 2;
    tooltip.style.left = Math.max(8, Math.min(x + 12, window.innerWidth - box.width - 12)) + 'px';
    tooltip.style.top = Math.max(8, Math.min(y + 12, window.innerHeight - box.height - 12)) + 'px';
  };
  const select = event => {
    document.querySelectorAll('.candidate.selected').forEach(other => other.classList.remove('selected'));
    point.classList.add('selected');
    document.getElementById('selection').textContent = point.dataset.id + ' · score ' + point.dataset.score
      + ' · ' + point.dataset.time + 's elapsed · ' + point.dataset.valid;
    show(event);
  };
  point.addEventListener('mouseenter', show);
  point.addEventListener('focus', show);
  point.addEventListener('click', select);
  point.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(event); }
  });
});
document.getElementById('copy-id').addEventListener('click', async () => {
  let copied = false;
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      await navigator.clipboard.writeText(idField.value);
      copied = true;
    }
  } catch (_) { /* Local HTML files may not have clipboard API permission. */ }
  if (!copied) {
    idField.select();
    try { copied = document.execCommand('copy'); } catch (_) { /* Keep ID selected. */ }
  }
  copyStatus.textContent = copied ? 'Copied' : 'ID selected — press Ctrl/Cmd+C';
});
document.getElementById('tooltip-close').addEventListener('click', () => { tooltip.hidden = true; });
document.addEventListener('click', event => {
  if (!tooltip.contains(event.target) && !event.target.closest('.candidate')) tooltip.hidden = true;
});
document.addEventListener('keydown', event => { if (event.key === 'Escape') tooltip.hidden = true; });
</script></body></html>
"""
