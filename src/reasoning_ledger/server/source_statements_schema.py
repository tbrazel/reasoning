"""Keep source-attributed claim identities independent of their bibliography references."""

SCHEMA = (
    "UPDATE mathematical_records SET object_id=claim_id WHERE record_type IN ('claim-head','claim-revision') AND claim_id IS NOT NULL",
)
