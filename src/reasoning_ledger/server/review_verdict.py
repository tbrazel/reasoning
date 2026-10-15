"""Extract one validated structured review, independently of progress prose.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

import json


def submissions(db, run_id):
    rows = db.execute("SELECT name,result_json FROM research_tool_receipts WHERE run_id=? AND status='completed' "
                      "AND (name='review_submit' OR (name='tool_call' AND json_extract(arguments_json,'$.name')='review_submit')) "
                      "ORDER BY sequence", (run_id,)).fetchall()
    return [json.loads(row["result_json"])["result"] if row["name"] == "tool_call" else json.loads(row["result_json"])
            for row in rows]


def extract(text):
    decoder = json.JSONDecoder()
    candidates = []
    # A JSON verdict is structured data, not an inferred positive sentiment.
    # Multiline/fenced JSON and progress before the report are accepted, but
    # ambiguous multiple verdicts never promote a ledger entry.
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and "verdict" in value and "obligations" in value:
            candidates.append((value, index, index + consumed))
    if len(candidates) != 1:
        raise ValueError("exactly one structured verdict is required")
    verdict, start, end = candidates[0]
    report = (text[:start] + text[end:]).replace("```json", "").replace("```", "").strip()
    return verdict, report
