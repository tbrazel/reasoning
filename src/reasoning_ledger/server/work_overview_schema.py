"""Incrementally maintained branch status counts and overview page indexes."""

INCREMENT = (
    "INSERT INTO work_task_counts(program_id,status,total) VALUES(NEW.program_id,NEW.status,1) "
    "ON CONFLICT(program_id,status) DO UPDATE SET total=work_task_counts.total+1;"
)
DECREMENT = "UPDATE work_task_counts SET total=total-1 WHERE program_id=OLD.program_id AND status=OLD.status;"
TRIGGERS = {
    "insert": ("INSERT", "", INCREMENT),
    "update": ("UPDATE", "WHEN NEW.status<>OLD.status", DECREMENT + INCREMENT),
    "delete": ("DELETE", "", DECREMENT),
}
STATEMENTS = {
    operation: f"CREATE TRIGGER work_task_count_{operation} AFTER {event} ON work_tasks {condition} BEGIN {body} END"
    for operation, (event, condition, body) in TRIGGERS.items()
}
SCHEMA = (
    """CREATE TABLE work_task_counts (
        program_id TEXT NOT NULL REFERENCES work_programs(id), status TEXT NOT NULL,
        total INTEGER NOT NULL CHECK(total>=0), PRIMARY KEY(program_id,status)
    )""",
    "INSERT INTO work_task_counts SELECT program_id,status,COUNT(*) FROM work_tasks GROUP BY program_id,status",
    *STATEMENTS.values(),
    "CREATE INDEX work_overview_tasks ON work_tasks(program_id,id)",
    "CREATE INDEX work_overview_controllers ON work_controllers(program_id,id)",
    "CREATE INDEX work_overview_checks ON work_checks(controller_id,id)",
)


def postgres_trigger(statement):
    operation = next((name for name, sql in STATEMENTS.items() if sql == statement), None)
    if operation is None:
        raise ValueError("unsupported work status counter trigger")
    event, condition, body = TRIGGERS[operation]
    if condition:
        body = f"IF NEW.status<>OLD.status THEN {body} END IF;"
    name = "work_task_count_" + operation
    return [
        f"CREATE FUNCTION {name}_apply() RETURNS trigger LANGUAGE plpgsql AS $reasoning$ "
        f"BEGIN {body} RETURN NULL; END $reasoning$",
        f"CREATE TRIGGER {name} AFTER {event} ON work_tasks FOR EACH ROW EXECUTE FUNCTION {name}_apply()",
    ]
