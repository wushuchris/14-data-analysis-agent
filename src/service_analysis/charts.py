"""Small, dependency-free SVG charts built from the evidence catalog."""
from html import escape
from math import isfinite
from textwrap import wrap
from typing import Literal
from pydantic import Field, model_validator
from service_analysis.schemas import Contract
from service_analysis.analysis import _identity
from service_analysis.findings import EvidenceFact


class ChartPoint(Contract):
    label: str
    evidence: EvidenceFact


class ChartSpec(Contract):
    chart_id: str
    title: str
    kind: Literal["mean_resolution", "mean_change", "decomposition", "correlation"]
    points: tuple[ChartPoint, ...] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def coherent(self):
        first = self.points[0].evidence
        allowed = {
            "mean_resolution": {"mean_hours"},
            "mean_change": {"mean_change_hours"},
            "decomposition": {"mix_contribution_hours", "within_category_contribution_hours",
                              "observed_mean_change_hours"},
            "correlation": {"pearson_r"},
        }[self.kind]
        expected_units = "pearson_coefficient" if self.kind == "correlation" else "elapsed_calendar_hours"
        ids = []
        for point in self.points:
            fact = point.evidence
            if (fact.dataset_id, fact.reference.result_id, fact.units, fact.periods, fact.selection) != (
                first.dataset_id, first.reference.result_id, first.units, first.periods, first.selection
            ) or fact.units != expected_units or fact.reference.metric not in allowed:
                raise ValueError("Chart evidence scope mismatch")
            if fact.value is not None:
                if not isfinite(fact.value) or (self.kind == "correlation" and not -1 <= fact.value <= 1):
                    raise ValueError("Invalid chart value")
            ids.append(fact.evidence_id)
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate chart evidence")
        return self

    @property
    def status(self):
        available = sum(p.evidence.value is not None for p in self.points)
        if not available:
            return "unavailable"
        return "complete" if available == len(self.points) and all(
            p.evidence.result_status == "complete" for p in self.points) else "partial"

    @property
    def warnings(self):
        return tuple(dict.fromkeys(w for p in self.points for w in p.evidence.warnings))

    @property
    def domain(self):
        if self.kind == "correlation":
            return (-1.0, 1.0)
        values = [float(p.evidence.value) for p in self.points if p.evidence.value is not None]
        lower, upper = min([0.0] + values), max([0.0] + values)
        return (lower, upper) if lower != upper else (0.0, 1.0)


def build_charts(catalog: tuple[EvidenceFact, ...]) -> tuple[ChartSpec, ...]:
    """The application selects chart types; models cannot supply plotting code."""
    buckets = {}
    for fact in catalog:
        metric = fact.reference.metric
        if metric == "mean_hours":
            kind = "mean_resolution"
        elif metric == "mean_change_hours":
            kind = "mean_change"
        elif metric == "pearson_r":
            kind = "correlation"
        elif metric in ("mix_contribution_hours", "within_category_contribution_hours",
                        "observed_mean_change_hours") and fact.reference.group == "All":
            kind = "decomposition"
        else:
            continue
        buckets.setdefault((fact.reference.result_id, kind), []).append(fact)
    titles = {"mean_resolution": "Mean resolution time", "mean_change": "Change in mean resolution time",
              "decomposition": "Arithmetic decomposition of mean change",
              "correlation": "Workload association: pooled and regional"}
    labels = {"mix_contribution_hours": "Case mix", "within_category_contribution_hours": "Within category",
              "observed_mean_change_hours": "Observed total"}
    charts = []
    for (result_id, kind), facts in buckets.items():
        points = tuple(ChartPoint(
            label=labels[f.reference.metric] if kind == "decomposition" else f.reference.group,
            evidence=f) for f in facts)
        title = titles[kind]
        if kind in ("mean_resolution", "mean_change"):
            title += " by " + facts[0].grouping
        charts.append(ChartSpec(chart_id=_identity("chart-", {"result_id": result_id, "kind": kind,
            "method": "evidence-bar-v1"}), title=title, kind=kind, points=points))
    return tuple(charts)


def render_chart_svg(chart: ChartSpec) -> str:
    """Render an accessible standalone SVG; null values never acquire bars."""
    chart = ChartSpec.model_validate(chart.model_dump(mode="json"))
    first = chart.points[0].evidence
    lower, upper = chart.domain
    left, plot_width, row_height = 300, 470, 46
    bottom = 118 + len(chart.points) * row_height
    captions = [
        "Periods: " + "; ".join(f"{p.start} to {p.end}" for p in first.periods) +
        (" (baseline; comparison)" if len(first.periods) == 2 else ""),
        "Selection: " + first.selection.replace("_", " ") + "; units: " +
        ("Pearson r" if chart.kind == "correlation" else "elapsed calendar hours") + ".",
        "n follows period order and counts " + first.sample_unit.replace("_", " ") +
        ". Missing values are unavailable, not zero.",
        "Chart status: " + chart.status + ". Caveat codes: " + ", ".join(chart.warnings),
    ]
    lines = [line for caption in captions for line in wrap(caption, width=112)]
    height = bottom + 45 + 18 * len(lines)
    def x(value):
        return left + (float(value) - lower) / (upper - lower) * plot_width
    def text(value, xpos, ypos, extra=""):
        return f'<text x="{xpos}" y="{ypos}" {extra}>{escape(str(value))}</text>'
    elements = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="980" height="{height}" viewBox="0 0 980 {height}" role="img">',
        "<title>" + escape(chart.title) + "</title>",
        "<desc>" + escape(" ".join(captions)) + "</desc>",
        '<rect width="100%" height="100%" fill="white"/>',
        '<g font-family="sans-serif" font-size="13" fill="#172B4D">',
        text(chart.title, 20, 30, 'font-size="20" font-weight="bold"'),
        text("Value", 800, 72), text("Group / component and sample sizes", 20, 72),
        f'<line x1="{x(0):.3f}" x2="{x(0):.3f}" y1="85" y2="{bottom}" stroke="#52606D"/>',
    ]
    for index, point in enumerate(chart.points):
        y = 110 + index * row_height
        fact = point.evidence
        elements.append(text(point.label, 20, y))
        elements.append(text("n=" + ", ".join(map(str, fact.sample_sizes)), 20, y + 16, 'font-size="11"'))
        if fact.value is None:
            elements.append(text("Unavailable", 800, y))
        else:
            start, end = sorted((x(0), x(fact.value)))
            elements.append(f'<rect class="data-bar" x="{start:.3f}" y="{y-16}" width="{end-start:.3f}" '
                            f'height="22" fill="#286A8D" data-evidence-id="{escape(fact.evidence_id, quote=True)}"/>')
            elements.append(text(format(fact.value, ".12g"), 800, y))
    for tick in sorted({lower, 0.0, upper}):
        elements.append(text(format(tick, ".6g"), f"{x(tick):.3f}", bottom + 18, 'text-anchor="middle"'))
    elements.extend(text(line, 20, bottom + 45 + index * 18) for index, line in enumerate(lines))
    elements.extend(["</g>", "</svg>"])
    return "\n".join(elements)
