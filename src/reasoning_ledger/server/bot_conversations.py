"""Bounded conversation discovery intersecting live bot and requester access.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from .research_tools import schema
from .store import StoreError


def discovery_tool():
    identity = {"type": "string", "pattern": "^[a-f0-9]{32}$"}
    return {"name": "topics_search", "description": "Discover authorized topics by title, project or channel, "
            "including empty conversations. Query matches part of the topic title; name filters are exact and "
            "case-insensitive. Results include titles and project/channel names and IDs. Multiple matches "
            "remain separate; choose the intended ID before searching its messages.",
            "input_schema": schema({
                "query": {"type": "string", "maxLength": 200},
                "project_name": {"type": "string", "minLength": 1, "maxLength": 200},
                "channel_name": {"type": "string", "minLength": 1, "maxLength": 200},
                "project_id": identity, "channel_id": identity, "topic_id": identity,
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            })}


def topic_metadata(store, db, user, organization, topic):
    _, project, _, channel, row = store._topic_access(db, user, organization, topic)
    return {"organization_id": organization, "project_id": project["id"], "project_name": project["name"],
            "channel_id": channel["id"], "channel_name": channel["name"],
            "topic_id": row["id"], "topic_title": row["name"], "topic_kind": row["kind"]}


class BotConversations:
    def __init__(self, runtime):
        self.runtime, self.store = runtime, runtime.store

    def authorized(self, db, bot, run):
        """Never load names until both live human audiences authorize the topic."""
        result = {}
        for topic in self.runtime.bots.topic_ids(db, bot):
            try:
                self.store._topic_access(db, bot["created_by"], run["organization_id"], topic)
                result[topic] = topic_metadata(self.store, db, run["requested_by"], run["organization_id"], topic)
            except StoreError:
                continue
        return result

    def invoke(self, credential, run_id, lease, name, arguments):
        with self.store.transaction() as db:
            bot, run = self.runtime.leased(db, credential, run_id, lease)
            metadata = self.authorized(db, bot, run)
            if name == "topics_search":
                query = arguments.get("query", "").casefold()
                matches = []
                for topic in metadata.values():
                    if query not in topic["topic_title"].casefold():
                        continue
                    if any(arguments.get(key) and arguments[key] != topic[key]
                           for key in ("topic_id", "channel_id", "project_id")):
                        continue
                    if any(arguments.get(key) and arguments[key].casefold() != topic[key].casefold()
                           for key in ("project_name", "channel_name")):
                        continue
                    matches.append(topic)
                matches.sort(key=lambda item: tuple(item[key].casefold()
                             for key in ("project_name", "channel_name", "topic_title", "topic_id")))
                return matches[:arguments.get("limit", 10)]
            granted = [topic for topic in metadata if not arguments.get("topic_id") or arguments["topic_id"] == topic]
            if not granted:
                return []
            escaped = arguments["query"].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            rows = db.execute("SELECT m.*,u.name AS sender_name FROM messages m JOIN users u ON u.id=m.author_id "
                "WHERE m.organization_id=? AND m.topic_id IN (" + ",".join("?" for _ in granted) + ") "
                "AND m.content LIKE ? ESCAPE '\\' ORDER BY m.sequence DESC LIMIT ?",
                (run["organization_id"], *granted, "%" + escaped + "%", arguments.get("limit", 10))).fetchall()
            return [{**self.store._message(row, db), **metadata[row["topic_id"]]} for row in reversed(rows)]
