"""Annotator: marking existing code with references to requirements."""

import re
from pathlib import Path

from src.mcp.parsers.go import parse_go
from src.mcp.parsers.java import parse_java
from src.mcp.parsers.kotlin import parse_kotlin
from src.mcp.parsers.python import CodeAnnotation, parse_python
from src.mcp.parsers.rust import parse_rust
from src.mcp.parsers.typescript import parse_typescript
from src.storage.adapter import StorageAdapter

# ---------------------------------------------------------------------------
# Word-level matching for symbol ↔ requirement title
# ---------------------------------------------------------------------------

# Threshold: fraction of title words found in the symbol name
_OVERLAP_THRESHOLD = 0.5
# Minimum word length for inclusion in scoring
_MIN_WORD_LEN = 3

# Splits a string into words: camelCase, PascalCase, snake_case, kebab-case, spaces
_SPLIT_RE = re.compile(r"[ _\-]+")
_CAMEL_RE = re.compile(
    r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|(?<=\d)(?=[A-Za-z])|(?<=[A-Za-z])(?=\d)"
)


def split_words(text: str) -> set[str]:
    """Split an identifier or title into a set of words.

    Handles: camelCase, PascalCase, snake_case, kebab-case, spaces.
    Converts to lowercase.
    """
    if not text:
        return set()
    # First split by delimiters (space, _, -)
    parts = _SPLIT_RE.split(text)
    result: set[str] = set()
    for part in parts:
        if not part:
            continue
        # Split CamelCase/PascalCase
        sub_parts = _CAMEL_RE.split(part)
        for sp in sub_parts:
            low = sp.lower()
            if low:
                result.add(low)
    return result


# ---------------------------------------------------------------------------
# Porter stemming for word form normalisation
# ---------------------------------------------------------------------------


def _porter_stem(word: str) -> str:
    """Simple Porter stemmer — reduces word to its root form.

    Handles common suffixes: -ing, -ed, -s, -es, -tion, -er, -or, -ment, etc.
    This is a simplified version (not full Porter algorithm) optimised for
    identifier matching in codebases.
    """
    w = word.lower()
    if len(w) <= 3:
        return w

    # Step 1a: plurals and past participles
    if w.endswith("sses"):
        w = w[:-2]  # stresses → stress
    elif w.endswith("ies"):
        w = w[:-3] + "y"  # ponies → pony
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]  # cats → cat, users → user

    # Step 1b: -ed and -ing
    if w.endswith("eed"):
        if len(w) > 4:
            w = w[:-1]  # agreed → agree
    elif w.endswith("ed") and any(v in w[:-2] for v in "aeiou"):
        w = w[:-2]  # created → create
    elif w.endswith("ing") and any(v in w[:-3] for v in "aeiou"):
        w = w[:-3]  # creating → create
        # Double consonant after short vowel: running → run
        if len(w) >= 3 and w[-1] == w[-2] and w[-1] not in "aeiou":
            w = w[:-1]

    # Step 2: -ational → -ate, -tion → -e, -izer → -ize, etc.
    if w.endswith("ational"):
        w = w[:-5] + "e"  # relational → relate
    elif w.endswith("tion"):
        w = w[:-4] + "e"  # creation → create
    elif w.endswith("enci"):
        w = w[:-1] + "e"  # dependency → dependence
    elif w.endswith("anci"):
        w = w[:-1] + "e"  # reliance → reliance
    elif w.endswith("izer"):
        w = w[:-1]  # modernizer → modernize
    elif w.endswith("abli"):
        w = w[:-1] + "e"  # conformabli → conformable
    elif w.endswith("alli"):
        w = w[:-2]  # formalli → formal
    elif w.endswith("entli"):
        w = w[:-2]  # differentli → different
    elif w.endswith("eli"):
        w = w[:-2]  # likeli → like
    elif w.endswith("ousli"):
        w = w[:-2]  # analogusli → analogous
    elif w.endswith("ization"):
        w = w[:-5] + "e"  # modernization → modernize
    elif w.endswith("ator"):
        w = w[:-3] + "e"  # operator → operate
    elif w.endswith("alism"):
        w = w[:-3]  # formalism → formal
    elif w.endswith("iveness"):
        w = w[:-4]  # decisiveness → decisive
    elif w.endswith("fulness"):
        w = w[:-4]  # hopefulness → hopeful
    elif w.endswith("ousness"):
        w = w[:-4]  # callousness → callous
    elif w.endswith("aliti"):
        w = w[:-3]  # formality → formal
    elif w.endswith("iviti"):
        w = w[:-3] + "e"  # sensitivity → sensitive
    elif w.endswith("biliti"):
        w = w[:-5] + "le"  # sensibility → sensible

    # Step 3: -icate → -ic, -ful → "", -ness → "", etc.
    if w.endswith("icate"):
        w = w[:-3]  # triplicate → triplic
    elif w.endswith("ative"):
        w = w[:-5]  # formative → form
    elif w.endswith("alize"):
        w = w[:-3]  # formalize → formal
    elif w.endswith("iciti"):
        w = w[:-3]  # electricity → electric
    elif w.endswith("ical"):
        w = w[:-2]  # electrical → electric
    elif w.endswith("ful"):
        w = w[:-3]  # hopeful → hope
    elif w.endswith("ness"):
        w = w[:-4]  # goodness → good
    elif w.endswith("ment"):
        w = w[:-4]  # management → manage
    elif w.endswith("er") and len(w) > 4:
        w = w[:-2]  # worker → work, validator → validat → then step 4
    elif w.endswith("or") and len(w) > 4:
        w = w[:-2]  # actor → act, authenticator → authenticat

    # Step 4: remove final -e after consonant
    if w.endswith("e") and len(w) > 3:
        if w[-2] not in "aeiou" and not w.endswith("ee"):
            w = w[:-1]

    return w


def _stem_set(words: set[str]) -> set[str]:
    """Apply stemming to every word in a set."""
    return {_porter_stem(w) for w in words}


def words_overlap_score(
    title_words: set[str],
    symbol_words: set[str],
    use_stemming: bool = True,
) -> float:
    """Fraction of title words found in the symbol name (0.0 – 1.0).

    Words shorter than _MIN_WORD_LEN are excluded from the calculation.
    If use_stemming=True (default), both title and symbol words are stemmed
    before comparison, enabling matching of different word forms.
    """
    title_filtered = {w for w in title_words if len(w) >= _MIN_WORD_LEN}
    if not title_filtered:
        return 0.0

    if use_stemming:
        title_stems = _stem_set(title_filtered)
        symbol_stems = _stem_set(symbol_words)
        overlap = title_stems & symbol_stems
        return len(overlap) / len(title_stems)

    overlap = title_filtered & symbol_words
    return len(overlap) / len(title_filtered)


def find_matches(symbol_name: str, title_index: dict[str, str]) -> list[str]:
    """Find req_id of requirements whose titles overlap with the symbol name.

    Uses word-level matching with a threshold of _OVERLAP_THRESHOLD.
    """
    sym_words = split_words(symbol_name)
    if not sym_words:
        return []

    matches: list[str] = []
    for key, req_id in title_index.items():
        # Key is already lowercase
        key_words = split_words(key)
        score = words_overlap_score(key_words, sym_words)
        if score >= _OVERLAP_THRESHOLD:
            matches.append(req_id)
    return matches


def annotate_code(
    storage: StorageAdapter,
    code_dir: Path,
    dry_run: bool = True,
) -> dict:
    """Scans code without @implements, finds matches with requirements,
    adds annotations.

    Returns:
        {'files_checked': N, 'annotated': N, 'changes': [...]}
    """
    result = {"files_checked": 0, "annotated": 0, "changes": []}

    if not code_dir.is_dir():
        return result

    # Build index: title/id → req_id
    all_reqs = storage.list_all()
    title_index: dict[str, str] = {}
    for summary in all_reqs:
        title_index[summary.title.lower()] = summary.id
        title_index[summary.id.lower()] = summary.id

    for py_file in code_dir.rglob("*.py"):
        result["files_checked"] += 1
        annotations, symbols = parse_python(py_file)
        existing = {a.req_id for a in annotations}

        if existing:
            continue  # already annotated

        code = py_file.read_text(encoding="utf-8")
        new_annotations = []

        for sym in symbols:
            # Find symbol name match with requirement (word-level)
            matched_ids = find_matches(sym.name, title_index)
            for req_id in matched_ids:
                if req_id not in existing:
                    new_annotations.append((sym, req_id))
                    existing.add(req_id)

        if new_annotations and not dry_run:
            # Add @implements before each symbol
            for sym, req_id in new_annotations:
                annotation = f'@implements("{req_id}")'
                pattern = rf"^(?P<indent>\s*)(def|class)\s+{re.escape(sym.name)}\b"
                code = re.sub(
                    pattern,
                    rf"\g<indent>{annotation}\n\g<indent>\g<2> {sym.name}",
                    code,
                    flags=re.MULTILINE,
                    count=1,
                )

            # Add import if not present
            if "from src.tracing import implements" not in code:
                # Insert after last import or at the beginning
                last_import = None
                for m in re.finditer(r"^(import |from src\.)", code, re.MULTILINE):
                    last_import = m
                if last_import:
                    pos = last_import.end()
                    code = (
                        code[:pos] + "\nfrom src.tracing import implements" + code[pos:]
                    )
                else:
                    code = "from src.tracing import implements\n" + code

            py_file.write_text(code, encoding="utf-8")

        result["changes"].extend(
            {"file": str(py_file), "symbol": sym.name, "req_id": req_id}
            for sym, req_id in new_annotations
        )

        if new_annotations:
            result["annotated"] += 1

    return result
