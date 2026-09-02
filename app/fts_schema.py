"""Schéma de l'index FTS5 et de ses déclencheurs.

Module volontairement sans dépendance (ni SQLModel, ni crypto) : `app.database`
et `tests/conftest.py` l'importent tous les deux, ce qui garantit que les tests
tournent sur le schéma réellement déployé plutôt que sur une copie qui dérive.

Les déclencheurs recalculent eux-mêmes la colonne `tags` depuis `link_tags`.
C'est essentiel : la version précédente réinsérait `tags = ''` à chaque UPDATE
sur `links`, si bien qu'un simple déplacement de lien, une extraction lecteur ou
une mise à jour de vignette effaçait les étiquettes de l'index. La correction ne
pouvait pas tenir dans la discipline d'appeler `refresh_link_fts` partout : neuf
chemins d'écriture l'oubliaient.
"""

# Sous-requête partagée par les deux déclencheurs d'écriture.
_TAGS_EXPR = (
    "COALESCE((SELECT GROUP_CONCAT(t.name, ' ') FROM link_tags lt"
    " JOIN tags t ON t.id = lt.tag_id WHERE lt.link_id = new.id), '')"
)

# Marqueur de version du schéma des déclencheurs : sa présence dans le SQL
# stocké par SQLite indique que la migration vers les déclencheurs autonomes a
# déjà eu lieu. Voir `app.database.init_db`.
TRIGGER_MARKER = "link_tags"

FTS_TABLE = """CREATE VIRTUAL TABLE IF NOT EXISTS fts_links USING fts5(
        title,
        description,
        note,
        url,
        tags,
        tokenize='unicode61 remove_diacritics 1'
    )"""

TRIGGER_NAMES = ("links_ai", "links_au", "links_ad")

FTS_TRIGGERS = [
    f"""CREATE TRIGGER IF NOT EXISTS links_ai AFTER INSERT ON links BEGIN
        INSERT INTO fts_links(rowid, title, description, note, url, tags)
        VALUES (new.id, new.title, new.description, new.note, new.url, {_TAGS_EXPR});
    END""",
    f"""CREATE TRIGGER IF NOT EXISTS links_au AFTER UPDATE ON links BEGIN
        DELETE FROM fts_links WHERE rowid = old.id;
        INSERT INTO fts_links(rowid, title, description, note, url, tags)
        VALUES (new.id, new.title, new.description, new.note, new.url, {_TAGS_EXPR});
    END""",
    """CREATE TRIGGER IF NOT EXISTS links_ad AFTER DELETE ON links BEGIN
        DELETE FROM fts_links WHERE rowid = old.id;
    END""",
]

FTS_SETUP = [FTS_TABLE, *FTS_TRIGGERS]

# Reconstruction complète de l'index depuis les tables sources. Sert à la
# migration (les lignes écrites par l'ancien déclencheur ont perdu leurs
# étiquettes) et au bouton « reconstruire l'index » des paramètres.
FTS_REBUILD_SELECT = """
    SELECT l.id, l.title, l.description, l.note, l.url,
           COALESCE(GROUP_CONCAT(t.name, ' '), '')
    FROM links l
    LEFT JOIN link_tags lt ON lt.link_id = l.id
    LEFT JOIN tags t ON t.id = lt.tag_id
    GROUP BY l.id
"""
