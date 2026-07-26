# Translating Excerpta

Excerpta's interface can be translated into any language, and contributions are
welcome. You do not need to know Python: a translation is a single text file.

This document is written in English because it is addressed to translators of
every language. The rest of the project's internal documentation is in French.

## Current state

| Language | Status |
|---|---|
| English | source language, always complete |
| French | complete |

Your language is not listed? That is exactly what this document is for.

## How it works, in one paragraph

Interface strings are stored in gettext catalogues. The source strings are in
English, so translating into Spanish means translating **from English**, never
through French. Each language has one `.po` file under
`app/translations/<code>/LC_MESSAGES/messages.po`. That file is the only thing
a translation contribution needs to touch.

## Where to send it

Open a pull request against the **`main` branch on GitHub**
(<https://github.com/notarobot63/excerpta>). Your pull request should contain
the `.po` file and nothing else.

## Adding a new language

You need Python and the project's development dependencies:

```bash
git clone https://github.com/notarobot63/excerpta.git
cd excerpta
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
```

Create the catalogue for your language, using its
[ISO 639-1 code](https://en.wikipedia.org/wiki/List_of_ISO_639_language_codes)
(`it` for Italian, `pt_BR` for Brazilian Portuguese):

```bash
pybabel init -i app/messages.pot -d app/translations -l it
```

Then edit `app/translations/it/LC_MESSAGES/messages.po`. Any text editor works;
[Poedit](https://poedit.net/) is a good dedicated tool and is free.

## Updating an existing language

When new strings appear in the application, refresh the catalogues:

```bash
# Re-extract source strings from the code
pybabel extract -F babel.cfg -o app/messages.pot --project=Excerpta .

# Merge them into your language, keeping existing translations
pybabel update -i app/messages.pot -d app/translations -l it
```

New entries appear with an empty `msgstr`. Entries whose source text changed are
marked `#, fuzzy`: review them and remove the `fuzzy` marker once corrected.

## Things that will bite you

These are not style preferences. Each one causes a real, visible failure.

**Keep placeholder names exactly as they are.** `%(num)d` must stay
`%(num)d`. Renaming it to `%(nombre)d` raises an error and breaks the whole
page, not just that string. You may reorder placeholders freely, and you may
drop one if your language does not need it.

```po
msgid "Rebuild the search index for %(num)d links?"
msgstr "Reconstruire l'index de recherche pour %(num)d liens ?"
```

**Keep the `{n}` token too.** A few strings use `{n}` instead, because the
number is filled in by the browser. If it disappears from your translation, the
user sees a sentence with no number in it.

**Double any literal percent sign.** Write `100%%`, not `100%`, otherwise the
page fails to render.

**Fill in every plural form.** Your language may have one, two, three or more.
The `.po` header declares how many, and each `msgstr[0]`, `msgstr[1]`… must be
filled. Do not leave one empty because it "looks the same" in your language.

**Do not translate the interface into markup.** If a string has no HTML in it,
your translation should not add any.

## Checking your work

```bash
# Compile the catalogues (the generated .mo files are not committed)
pybabel compile -d app/translations

# Validate catalogue integrity
pytest tests/test_i18n.py
```

`pytest` checks that no translation renamed a placeholder or lost a `{n}`
token. It runs in CI too, so a pull request that breaks a catalogue is caught
before merge.

To see your translation in a running instance, start the app and pick your
language from the selector, available on the sign-in page and in the sidebar.

## What is deliberately not translated

- **Your own content**: link titles, descriptions, notes, folder and tag names.
  Those are your data.
- **JSON API responses**, which are consumed by programs, including the Android
  app. Translating them would break the API contract.
- **CSS theme names** and **server logs**.

## Notes for a good translation

Excerpta addresses the user directly and stays sober: no exclamation marks, no
enthusiasm the original does not have. Where your language distinguishes formal
and informal address, prefer whichever a small self-hosted tool would use with
someone who installed it themselves.

Interface strings are short by necessity. A sidebar item that is one word in
English should not become five in your language; find the shorter phrasing even
if it is slightly less literal.

## Credit

Translators are credited in the `.po` file header (`Last-Translator`). Fill it
in with the name you want to be known by. If you would rather not appear, leave
it blank; the contribution is just as welcome.

## Anything unclear?

Open an issue at <https://github.com/notarobot63/excerpta/issues>. A question
about this document is a bug in this document.

The technical decisions behind the setup, and the reasoning for them, are in
[docs/i18n.md](docs/i18n.md).
