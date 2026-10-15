"""Authenticated personal model favorites, independent of organization membership.

Copyright Benjamin Antieau and Deven Manam. SPDX-License-Identifier: Apache-2.0
"""

from .store import StoreError


def favorites(store, user, *, model_id=None, favorite=None):
    if model_id is not None and (not isinstance(model_id, str) or not 1 <= len(model_id) <= 200
                                 or any(char.isspace() or ord(char) < 32 for char in model_id)):
        raise StoreError(400, "favorite requires a model identifier")
    if model_id is not None and type(favorite) is not bool:
        raise StoreError(400, "choose whether the model is a favorite")
    with store.transaction(write=model_id is not None) as db:
        if model_id is not None:
            if favorite:
                count = db.execute("SELECT COUNT(*) FROM user_model_favorites WHERE user_id=?", (user,)).fetchone()[0]
                exists = db.execute("SELECT 1 FROM user_model_favorites WHERE user_id=? AND model_id=?",
                                    (user, model_id)).fetchone()
                if count >= 100 and not exists:
                    raise StoreError(409, "a person can save up to 100 model favorites")
                db.execute("INSERT INTO user_model_favorites(user_id,model_id) VALUES(?,?) "
                           "ON CONFLICT(user_id,model_id) DO NOTHING", (user, model_id))
            else:
                db.execute("DELETE FROM user_model_favorites WHERE user_id=? AND model_id=?", (user, model_id))
        return {"user_id": user, "models": [row[0] for row in db.execute(
            "SELECT model_id FROM user_model_favorites WHERE user_id=? ORDER BY model_id", (user,))]}
