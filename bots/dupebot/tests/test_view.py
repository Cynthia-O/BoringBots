from botview import render
from dupebot.cluster import cluster_records, scan
from dupebot.report import build_report
from dupebot.suggest import Suggestion, SuggestResult
from dupebot.view import results_page


def two_cluster_report(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    for name in (
        "Alpha plan_v1.docx",
        "Alpha plan_v2.docx",
        "Zebra notes_v1.pdf",
        "Zebra notes_v2.pdf",
    ):
        (root / name).write_bytes(b"x" * 300_000)
    s = scan(root)
    return build_report(root, s, cluster_records(s.records, 0.6), 0.6)


def answer(cluster):
    files = [{"path": f["name"], "role": "member"} for f in cluster["files"]]
    group = {
        "files": files,
        "reason": "r",
        "relationship": "unrelated",
        "check_before_deleting": "",
    }
    return {"cluster_id": cluster["cluster_id"], "groups": [group]}


def test_unanswered_clusters_wait_in_scan_order(tmp_path):
    report = two_cluster_report(tmp_path)
    first, second = report["clusters"]
    html = render(
        results_page(
            report,
            {first["cluster_id"]: answer(first)},
            "m",
            "e",
            agent_note="Waiting for the analysis to finish.",
        )
    )
    assert html.index(first["cluster_id"]) < html.index(second["cluster_id"])
    assert html.count("waiting for the model") == 1
    assert "Clusters answered</dt><dd>1 of 2" in html


def test_the_agents_picks_move_to_the_top_and_the_rest_follow(tmp_path):
    report = two_cluster_report(tmp_path)
    first, second = report["clusters"]
    answers = {c["cluster_id"]: answer(c) for c in (first, second)}
    pick = Suggestion(
        second["cluster_id"],
        second["files"][0]["name"],
        second["files"][1]["name"],
        "Same bytes.",
        True,
    )
    result = SuggestResult([pick], "Found one.", cap=1, candidates=1)
    html = render(results_page(report, answers, "m", "e", suggestions=result))
    picked, other, rest = (
        html.index(second["cluster_id"]),
        html.index("Other clusters"),
        html.index(first["cluster_id"]),
    )
    assert picked < other < rest
    assert "priority 1" in html and "Found one." in html
