"""One replaceable human reaction per canonical response."""

SCHEMA = (
    """DELETE FROM chat_reactions WHERE EXISTS (
        SELECT 1 FROM chat_reactions newer
        WHERE newer.organization_id=chat_reactions.organization_id
          AND newer.target_key=chat_reactions.target_key AND newer.user_id=chat_reactions.user_id
          AND (newer.created_at>chat_reactions.created_at
            OR (newer.created_at=chat_reactions.created_at AND newer.emoji>chat_reactions.emoji))
    )""",
    "CREATE UNIQUE INDEX chat_reaction_person ON chat_reactions(organization_id,target_key,user_id)",
)
