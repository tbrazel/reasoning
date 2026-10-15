"""Immutable inference accounting receipts and recommendation preferences."""

MIGRATION = (
    """CREATE TABLE inference_calls (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
        organization_id TEXT NOT NULL, run_id TEXT NOT NULL, request_id TEXT NOT NULL,
        requested_by TEXT NOT NULL, topic_id TEXT NOT NULL, project_id TEXT NOT NULL, model TEXT NOT NULL,
        parent_call_id TEXT REFERENCES inference_calls(id), purpose TEXT NOT NULL,
        harness_version TEXT NOT NULL, prompt_version TEXT NOT NULL, quote_json TEXT NOT NULL,
        route_json TEXT NOT NULL, context_sha256 TEXT, reserved_microusd INTEGER NOT NULL,
        started_at INTEGER NOT NULL, started_at_ms INTEGER, source TEXT NOT NULL,
        UNIQUE(organization_id,id),
        FOREIGN KEY(organization_id,run_id) REFERENCES llm_runs(organization_id,id)
    )""",
    "CREATE INDEX inference_call_page ON inference_calls(organization_id,sequence)",
    "CREATE INDEX inference_call_run ON inference_calls(run_id,sequence)",
    """CREATE TABLE inference_call_events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, organization_id TEXT NOT NULL, call_id TEXT NOT NULL,
        kind TEXT NOT NULL, data_json TEXT NOT NULL, recorded_at INTEGER NOT NULL,
        FOREIGN KEY(organization_id,call_id) REFERENCES inference_calls(organization_id,id)
    )""",
    "CREATE INDEX inference_call_receipts ON inference_call_events(organization_id,call_id,sequence)",
    """INSERT INTO inference_calls(id,organization_id,run_id,request_id,requested_by,topic_id,project_id,model,
        purpose,harness_version,prompt_version,quote_json,route_json,reserved_microusd,started_at,source)
        SELECT 'history-'||r.id,r.organization_id,r.id,r.request_id,q.requested_by,q.topic_id,c.project_id,r.model,
        CASE WHEN w.inference_run_id IS NULL THEN 'answer' ELSE 'workflow' END,
        'historical-unrecorded','historical-unrecorded',r.quote_json,'{"sort":"price","allow_fallbacks":false}',
        r.reserved_microusd,COALESCE(r.started_at,q.created_at),'historical'
        FROM llm_runs r JOIN llm_requests q ON q.id=r.request_id JOIN topics t ON t.id=q.topic_id
        JOIN channels c ON c.id=t.channel_id LEFT JOIN workflow_node_attempts w ON w.inference_run_id=r.id
        WHERE r.status NOT IN ('queued','running')""",
    """INSERT INTO inference_call_events(organization_id,call_id,kind,data_json,recorded_at)
        SELECT r.organization_id,'history-'||r.id,'historical',json_object('status',r.status,
        'accounting_state',r.accounting_state,'actual_microusd',r.actual_microusd,
        'prompt_tokens',r.prompt_tokens,'completion_tokens',r.completion_tokens,
        'provider_generation_id',r.provider_generation_id,'error_code',r.error_code,
        'finished_at',r.finished_at),COALESCE(r.finished_at,q.created_at)
        FROM llm_runs r JOIN llm_requests q ON q.id=r.request_id
        WHERE r.status NOT IN ('queued','running')""",
    """CREATE TABLE model_recommendation_preferences (
        organization_id TEXT PRIMARY KEY REFERENCES organizations(id), model_ids_json TEXT NOT NULL
    )""",
    *tuple(
        f"""CREATE TRIGGER {table}_immutable_{operation.lower()} BEFORE {operation} ON {table}
        BEGIN SELECT RAISE(ABORT,'inference receipts are immutable'); END"""
        for table in ("inference_calls", "inference_call_events")
        for operation in ("UPDATE", "DELETE")
    ),
)
