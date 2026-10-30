"""Standalone XY score chart from the loop's existing native CHIA profile log."""

from html import escape
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


def load_scores(path):
    """Read completed score samples, deduplicating proposals by candidate ID.

    The original collector log supplies both candidate samples and recorded
    bests, including best scores inherited from a restored checkpoint. A live
    writer's incomplete final line is ignored. No collector or database is used.
    """
    candidates, recorded_bests = {}, []
    start = end = None
    for line in Path(path).read_text().splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
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


def render_scores(run):
    """Write artifacts/profile/scores.html without changing a running experiment."""
    run = Path(run)
    profile = run / "artifacts/profile"
    scores = load_scores(profile / "ChiaProfileCollector.log")
    points, best = scores["points"], scores["best"]
    left, right, top, bottom = 88, 958, 32, 500
    values = [p["score"] for p in points] or [0]
    low, high = min(values), max(values)
    padding = (high - low or abs(high) or 1) * 0.12
    low, high = low - padding, high + padding
    if min(values) >= 0:
        low = max(0, low)
    precision = min(15, max(4, math.ceil(math.log10(
        max(abs(low), abs(high)) / ((high - low) / 5))) + 2))
    duration = scores["duration"]
    unit, unit_name = (3600, "hours") if duration >= 7200 else (60, "minutes") if duration >= 120 else (1, "seconds")
    svg = [f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}"/>',
           f'<line class="axis" x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}"/>']
    for i in range(6):
        x = left + (right - left) * i / 5
        y = bottom - (bottom - top) * i / 5
        value = low + (high - low) * i / 5
        svg.extend([
            f'<line class="grid" x1="{left}" y1="{y}" x2="{right}" y2="{y}"/>',
            f'<text class="tick" x="{left - 12}" y="{y + 4}" text-anchor="end">{value:.{precision}g}</text>',
            f'<text class="tick" x="{x}" y="{bottom + 25}" text-anchor="middle">{duration * i / 5 / unit:.4g}</text>',
        ])
    svg.extend([
        f'<text class="axis-title" x="{(left + right) / 2}" y="556" text-anchor="middle">Elapsed time ({unit_name})</text>',
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
        svg.append(
            f'<g class="candidate {verdict}" tabindex="0" role="button" '
            f'aria-label="{identifier}: score {score:.15g}, {verdict}" '
            f'data-id="{identifier}" data-score="{score:.15g}" data-time="{seconds:.3f}" data-valid="{verdict}">'
            f'<title>{escape(candidate)}\nScore: {score:.15g}\nElapsed: {seconds:.3f}s\n{verdict}</title>'
            f'<circle cx="{x:.3f}" cy="{y:.3f}" r="5"/>'
            f'<text class="candidate-label" x="{label_x:.3f}" y="{label_y:.3f}">{escape(label)}</text></g>'
        )
    if not points:
        svg.append('<text x="520" y="260" text-anchor="middle">No completed candidate scores yet.</text>')
    best_value = format(best["score"], ".15g") if best else "—"
    best_id = escape(best["candidate_id"]) if best else "No valid score recorded"
    page = (_PAGE.replace("{{RUN}}", escape(run.name)).replace("{{BEST}}", best_value)
            .replace("{{BEST_ID}}", best_id).replace("{{COUNT}}", str(len(points)))
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
figure { margin: 24px 0 0; padding: 12px; border: 1px solid #2d3b53; border-radius: 10px; background: #141d2e; }
svg { display: block; width: 100%; height: auto; }
text { fill: #b8c5d9; font: 12px ui-monospace,monospace; }
.axis { stroke: #75849d; }
.grid { stroke: #2b3850; stroke-dasharray: 3 5; }
.axis-title { fill: #e0e6f2; font: 15px system-ui,sans-serif; }
.candidate { cursor: pointer; outline: none; }
.candidate circle { fill: #80b8ff; stroke: #15243d; stroke-width: 2px; }
.candidate.failed circle { fill: #f18d9e; }
.candidate-label { font-size: 11px; fill: #d4deed; paint-order: stroke; stroke: #141d2e; stroke-width: 3px; }
.candidate:hover circle, .candidate:focus circle, .candidate.selected circle { stroke: white; stroke-width: 3px; }
figcaption { color: #aab8ce; padding: 0 16px 12px; font-size: 13px; }
#selection { min-height: 25px; overflow-wrap: anywhere; }
.note { font-size: 12px; }
@media (max-width: 600px) { body { padding: 14px; } }
</style></head><body><main>
<header><div><h1>Candidate scores over time</h1><p>{{RUN}} · {{COUNT}} candidates · labels show the last 6 ID characters</p></div>
<div class="best"><small>Best valid score</small><strong id="best-score">{{BEST}}</strong><span id="best-id">{{BEST_ID}}</span></div></header>
<figure><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 580" aria-label="Candidate score versus elapsed time">
{{SVG}}
</svg><figcaption>Each dot is one completed candidate attempt. Pink dots are failed attempts. Hover or select a dot for its full ID.</figcaption></figure>
<p id="selection" aria-live="polite">Select a candidate to inspect its score and completion time.</p>
<p class="note">Snapshot of the native CHIA profile log. Rerun the scores command and reload this page for newer results. Best score includes any recorded best inherited from a checkpoint.</p>
</main><script>
document.querySelectorAll('.candidate').forEach(point => {
  const select = () => {
    document.querySelectorAll('.candidate.selected').forEach(other => other.classList.remove('selected'));
    point.classList.add('selected');
    document.getElementById('selection').textContent = point.dataset.id + ' · score ' + point.dataset.score
      + ' · ' + point.dataset.time + 's elapsed · ' + point.dataset.valid;
  };
  point.addEventListener('click', select);
  point.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(); }
  });
});
</script></body></html>
"""
