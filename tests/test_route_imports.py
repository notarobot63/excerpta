"""Non-régression : aucun nom référencé par le code ne doit être introuvable.

`POST /settings/purge-freshrss` référençait `FreshRSSConfig` sans l'importer.
La route rendait donc un 500 (NameError) dès qu'un utilisateur cliquait le
bouton correspondant, et rien ne le signalait : aucun test ne la couvrait, et le
projet n'embarquait pas de linter.

Le test ne retient que la catégorie « undefined name » de pyflakes, celle qui
casse à l'exécution. Les avertissements d'imports inutilisés sont ignorés :
`app/routes/links/__init__.py` réexporte volontairement, et `app/main.py`
importe `models` pour son effet de bord sur les métadonnées SQLModel.
"""
from pathlib import Path

from pyflakes.api import checkPath
from pyflakes.messages import UndefinedExport, UndefinedLocal, UndefinedName
from pyflakes.reporter import Reporter

APP_DIR = Path(__file__).resolve().parent.parent / "app"

_FATAL = (UndefinedName, UndefinedLocal, UndefinedExport)


class _Collector(Reporter):
    """Retient les seuls messages qui traduisent un nom non résolu."""

    def __init__(self):
        super().__init__(None, None)
        self.fatal: list[str] = []
        self.syntax: list[str] = []

    def flake(self, message):
        if isinstance(message, _FATAL):
            self.fatal.append(str(message))

    def unexpectedError(self, filename, msg):
        self.syntax.append(f"{filename}: {msg}")

    def syntaxError(self, filename, msg, lineno, offset, text):
        self.syntax.append(f"{filename}:{lineno}: {msg}")


def test_no_undefined_names_in_app():
    collector = _Collector()
    files = sorted(APP_DIR.rglob("*.py"))
    assert files, "aucun module analysé, le chemin de l'application a dû changer"
    for path in files:
        checkPath(str(path), collector)

    assert not collector.syntax, "erreur de syntaxe : " + "\n".join(collector.syntax)
    assert not collector.fatal, "noms non résolus :\n" + "\n".join(collector.fatal)
