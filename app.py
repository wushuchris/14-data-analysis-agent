"""Deterministic public demo; every record comes from the bundled generator."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st
from service_analysis.analysis import Period, ComparisonResult
from service_analysis.demo_data import SCENARIOS, MONTHS, load_scenario
from service_analysis.model_adapter import AnalysisRequest, run_assisted_analysis
from service_analysis.reporting import export_report_json, _summary
from service_analysis.charts import render_chart_svg, WARNING_TEXT

QUESTIONS = {
    "drivers": "What could explain the change?",
    "segments": "Which categories and regions changed?",
    "resolution_change": "How did resolution time change?",
}
COMPARISONS = {
    "January → February": ("January", "February"),
    "January → March": ("January", "March"),
    "February → March": ("February", "March"),
}
NOTICE_TEXT = {
    **WARNING_TEXT,
    "DATA_QUALITY_BLOCKED": "An analysis was blocked because its required data was not usable.",
    "EXECUTION_INCOMPLETE": "Some requested analyses could not be completed.",
    "REQUESTED_ANALYSES_INCOMPLETE": "Some analyses required by this question are incomplete.",
    "NO_VERIFIED_FINDINGS": "No verified findings are available for this selection.",
    "NO_CALCULATION_EVIDENCE": "No usable calculation evidence is available.",
    "DEPENDENCY_NOT_COMPLETE": "An analysis was skipped because its prerequisite did not complete.",
}
QUALITY_TEXT = {
    "INVALID_ROW": "A source record failed validation.",
    "EMPTY_TABLE": "A required table is empty.",
    "EXACT_DUPLICATE": "An exact duplicate was removed.",
    "MISSING_CATEGORY": "Some requests have no service category.",
    "MISSING_REGION": "Some requests have no region.",
    "TINY_GROUP": "Some groups have very small samples.",
    "MISSING_OPERATIONS_COVERAGE": "Operations coverage is incomplete.",
    "UNAVAILABLE_STAFFING": "Staffing is missing or zero for an operations day.",
    "MISSING_WORKLOAD_LINK": "Some completed requests lack a matching operations day.",
}


def run_demo(scenario, question, comparison):
    first, second = COMPARISONS[comparison]
    baseline = Period(start=MONTHS[first][0], end=MONTHS[first][1])
    current = Period(start=MONTHS[second][0], end=MONTHS[second][1])
    data = load_scenario(scenario)
    result = run_assisted_analysis(data, AnalysisRequest(question=question,
        baseline=baseline, comparison=current, opening_period=current))
    return data.profile, result


def main():
    st.set_page_config(page_title="Service Operations Analyst", page_icon="📊", layout="wide")
    st.title("Service Operations Analyst")
    st.write("Resolution times changed. See where the difference came from and what to investigate next.")
    st.caption("Harborlight Service Operations is fictional. All records are synthetic.")
    with st.sidebar:
        st.header("Explore the operation")
        with st.form("analysis_controls"):
            scenario = st.selectbox("Scenario", list(SCENARIOS),
                format_func=lambda key: SCENARIOS[key].label, key="scenario")
            question = st.selectbox("Question", list(QUESTIONS),
                format_func=lambda key: QUESTIONS[key], key="question")
            comparison = st.selectbox("Comparison", list(COMPARISONS), key="comparison")
            submitted = st.form_submit_button("Run analysis", key="run_analysis")
        st.caption("Deterministic mode · no live model calls.")
        st.write("Resolution statistics select completed requests by resolution date. "
                 "Workload analysis uses opening dates during the comparison month.")
    if submitted or "_demo_result" not in st.session_state:
        # Clear stale output before attempting a different submitted selection.
        st.session_state.pop("_demo_result", None)
        try:
            profile, result = run_demo(scenario, question, comparison)
        except Exception:
            st.error("The analysis could not be produced. Choose another synthetic scenario and run again.")
            return
        st.session_state["_demo_result"] = result
        st.session_state["_demo_profile"] = profile
        st.session_state["_demo_selection"] = (scenario, question, comparison)
    result = st.session_state["_demo_result"]
    profile = st.session_state["_demo_profile"]
    scenario, question, comparison = st.session_state["_demo_selection"]
    report = result.report
    st.caption(f"Displayed analysis: {SCENARIOS[scenario].label} · {comparison} · {QUESTIONS[question]}")
    st.info(SCENARIOS[scenario].description)
    if report.status == "complete":
        st.success("Requested analysis completed. Findings retain their data and interpretation limitations.")
    elif report.status == "partial":
        st.warning("Partial analysis: usable results are retained, but some requested conclusions are unavailable.")
    else:
        st.error("Analysis unavailable: the data does not support the requested calculations.")
    overall = next((s.result for s in result.execution.outcomes
                    if isinstance(s.result, ComparisonResult) and s.result.baseline.grouping == "overall"), None)
    if overall is not None:
        first, second = overall.baseline.rows[0], overall.comparison.rows[0]
        change = overall.rows[0].mean_change_hours
        cols = st.columns(3)
        cols[0].metric("Baseline mean", "Unavailable" if first.mean_hours is None else f"{first.mean_hours:.2f} hours")
        cols[1].metric("Comparison mean", "Unavailable" if second.mean_hours is None else f"{second.mean_hours:.2f} hours")
        cols[2].metric("Mean change", "Unavailable" if change is None else f"{change:+.2f} hours")
        st.caption(f"Completed requests: baseline {first.sample_size}; comparison {second.sample_size}. "
                   "Durations are elapsed calendar hours.")
        if change is not None:
            direction = "increased" if change > 1e-9 else "decreased" if change < -1e-9 else "was unchanged"
            st.write("Average resolution time " + direction +
                     (f" by {abs(change):.2f} hours." if direction != "was unchanged" else "."))
    tabs = st.tabs(["Findings", "Charts", "Data quality", "Execution evidence"])
    with tabs[0]:
        st.subheader("What the evidence supports")
        preferred = {"mean_change_hours", "mix_contribution_hours", "within_category_contribution_hours", "pearson_r"}
        highlighted = [f for f in report.findings.accepted
                       if f.evidence.reference.group == "All" and f.evidence.reference.metric in preferred]
        if not highlighted:
            highlighted = list(report.findings.accepted[:5])
        for finding in highlighted:
            st.write(_summary(finding).text)
            st.caption("Samples: " + ", ".join(map(str, finding.evidence.sample_sizes)) + " " +
                       finding.evidence.sample_unit.replace("_", " ") + ".")
        if not highlighted:
            st.write("No verified numerical findings are available.")
        st.subheader("What to investigate")
        st.write("These are investigation topics, not established causes.")
        if overall is not None:
            st.write("Review request composition and handling practices within categories. "
                     "Compare regional patterns before changing operations.")
        if any(c.kind == "correlation" and c.status != "unavailable" for c in report.charts):
            st.write("Review staffing and demand alongside category mix and timing. "
                     "The observed workload relationship does not establish causation.")
        st.subheader("Limitations to keep in view")
        for code in report.notices:
            st.write("• " + NOTICE_TEXT.get(code, "Additional limitation: " + code.lower().replace("_", " ") + "."))
        with st.expander("All verified metrics and their scope"):
            st.dataframe([{"Metric": f.evidence.reference.metric.replace("_", " "),
                "Group": f.evidence.reference.group, "Value": f.evidence.value, "Units": f.evidence.units,
                "Samples": str(f.evidence.sample_sizes), "Population": str(f.evidence.population_sizes),
                "Classification exclusions": str(f.evidence.excluded_classification_counts),
                "Source status": f.evidence.result_status} for f in report.findings.accepted],
                hide_index=True, width="stretch")
    with tabs[1]:
        if not report.charts:
            st.write("No charts are available because calculations were blocked.")
        for index, chart in enumerate(report.charts):
            svg = render_chart_svg(chart)
            st.image(svg, width="stretch")
            st.download_button("Download chart SVG", svg, file_name=f"service-chart-{index+1}.svg",
                               mime="image/svg+xml", key=f"chart_download_{index}")
    with tabs[2]:
        st.subheader("Three months of synthetic records")
        st.dataframe([{"Table": label, **counts.model_dump()}
                      for label, counts in (("Completed requests", profile.requests), ("Daily operations", profile.operations))],
                     hide_index=True, width="stretch")
        st.write("Input counts describe the whole demonstration dataset; report samples describe selected periods.")
        st.dataframe([{"Analysis": scope.capitalize(), "Ready": getattr(profile, scope + "_ready")}
                      for scope in ("resolution", "category", "region", "workload")],
                     hide_index=True, width="stretch")
        if profile.issues:
            st.dataframe([{"Issue": QUALITY_TEXT.get(i.code, i.code.lower().replace("_", " ")),
                           "Severity": i.severity, "Occurrences": i.count, "Affected analyses": ", ".join(i.scopes)}
                          for i in profile.issues], hide_index=True, width="stretch")
        else:
            st.write("No preparation issues were found.")
    with tabs[3]:
        st.subheader("What actually ran")
        st.write(f"{result.execution.tool_calls} tool calls · {result.execution.reserved_steps} reserved steps "
                 f"· {result.model_calls} model calls")
        st.dataframe([s.model_dump() for s in report.steps], hide_index=True, width="stretch")
        st.dataframe([e.model_dump() for e in report.events], hide_index=True, width="stretch")
        with st.expander("Calculation and report identifiers"):
            st.json({"run_id": report.run_id, "report_id": report.report_id, "dataset_id": report.dataset_id,
                     "planning_source": result.planning_source, "findings_source": result.findings_source,
                     "requested_work_status": result.requested_work_status})
    st.download_button("Download analysis JSON", export_report_json(report),
        file_name="service-analysis-report.json", mime="application/json", key="report_download")
    st.caption("Built to demonstrate validated analytical tools, evidence checks, and controlled execution.")


if __name__ == "__main__":
    main()
