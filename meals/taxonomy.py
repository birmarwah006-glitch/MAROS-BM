"""
Python concept taxonomy.

The existing MAROS taxonomy (prep_mode.CONCEPTS) is twelve hardcoded Operating
Systems ids whose tagger has no "none" escape and hard-defaults to
`processes`. It would mis-tag every Python Meal, so Python gets its own.

Deliberately NOT a full knowledge graph. It is large enough to place a Meal in
a learning order and to answer "what should I watch next", and no larger. It
grows when real content needs a slot that does not exist.

Ids are stable strings — they end up in Meal documents and, later, in mastery
rows — so they may be ADDED to but not renamed.
"""

from __future__ import annotations

# id -> (display name, gloss for the tagger, prerequisite ids)
CONCEPTS: dict[str, tuple[str, str, list[str]]] = {
    # ── Basics ──────────────────────────────────────────────────────────
    "python.basics.print": (
        "print()",
        "Sending output to the terminal; arguments, sep and end.",
        [],
    ),
    "python.basics.input_output": (
        "input() and print()",
        "Reading from the user and writing back. input() always returns str.",
        ["python.basics.print"],
    ),
    "python.basics.variables": (
        "Variables",
        "Names bound to values, rebinding, and what a name actually refers to.",
        [],
    ),
    "python.basics.data_types": (
        "Data types",
        "int, float, str, bool, None and how to tell them apart.",
        ["python.basics.variables"],
    ),
    "python.basics.type_conversion": (
        "Type conversion",
        "int(), float(), str() and the errors raised by bad conversions.",
        ["python.basics.data_types"],
    ),
    "python.basics.strings": (
        "Strings",
        "String literals, f-strings, concatenation, common string methods.",
        ["python.basics.data_types"],
    ),
    "python.basics.operators": (
        "Operators",
        "Arithmetic, comparison and boolean operators, and precedence.",
        ["python.basics.data_types"],
    ),

    # ── Control flow ────────────────────────────────────────────────────
    "python.control.if": (
        "if / elif / else",
        "Branching on a condition; truthiness; nesting.",
        ["python.basics.operators"],
    ),
    "python.control.for": (
        "for loops",
        "Iterating over a sequence; the loop variable; what Python does each pass.",
        ["python.basics.variables"],
    ),
    "python.control.while": (
        "while loops",
        "Looping until a condition changes; termination; infinite loops.",
        ["python.control.if"],
    ),
    "python.control.range": (
        "range()",
        "Generating a sequence of integers; start, stop, step; it is lazy.",
        ["python.control.for"],
    ),
    "python.control.break_continue": (
        "break and continue",
        "Leaving a loop early, and skipping the rest of one pass.",
        ["python.control.for"],
    ),

    # ── Data structures ─────────────────────────────────────────────────
    "python.data.lists": (
        "Lists",
        "Ordered mutable sequences; append, indexing, length.",
        ["python.basics.data_types"],
    ),
    "python.data.indexing": (
        "Indexing and slicing",
        "Zero-based indexing, negative indices, slice notation.",
        ["python.data.lists"],
    ),
    "python.data.dicts": (
        "Dictionaries",
        "Key-value mapping; lookup, insertion, missing keys, .get().",
        ["python.data.lists"],
    ),
    "python.data.tuples": (
        "Tuples",
        "Immutable sequences; unpacking; when to prefer them over lists.",
        ["python.data.lists"],
    ),
    "python.data.sets": (
        "Sets",
        "Unordered unique collections; membership tests; set operations.",
        ["python.data.lists"],
    ),
    "python.data.comprehensions": (
        "Comprehensions",
        "Building a list or dict from an expression over an iterable.",
        ["python.data.lists", "python.control.for"],
    ),

    # ── Functions ───────────────────────────────────────────────────────
    "python.functions.define": (
        "Defining functions",
        "def, the body, calling, and why you would factor code out at all.",
        ["python.basics.variables"],
    ),
    "python.functions.parameters": (
        "Parameters and arguments",
        "Positional, keyword and default arguments.",
        ["python.functions.define"],
    ),
    "python.functions.return": (
        "return",
        "Handing a value back; the difference between returning and printing.",
        ["python.functions.define"],
    ),
    "python.functions.scope": (
        "Scope",
        "Local versus global names; what a function can and cannot see.",
        ["python.functions.define"],
    ),
    "python.functions.recursion": (
        "Recursion",
        "A function calling itself; base case; the call stack.",
        ["python.functions.return"],
    ),

    # ── Errors ──────────────────────────────────────────────────────────
    "python.errors.exceptions": (
        "Exceptions",
        "What an exception is, reading a traceback, common built-in errors.",
        ["python.basics.type_conversion"],
    ),
    "python.errors.try_except": (
        "try / except",
        "Catching an exception, handling it, and not swallowing it silently.",
        ["python.errors.exceptions"],
    ),
    "python.errors.debugging": (
        "Debugging",
        "Locating a fault: reading the error, printing state, narrowing down.",
        ["python.errors.exceptions"],
    ),

    # ── Modules and IO ──────────────────────────────────────────────────
    "python.modules.import": (
        "Modules and imports",
        "import, from-import, the standard library, installing packages.",
        ["python.functions.define"],
    ),
    "python.io.files": (
        "Files",
        "Opening, reading and writing files; the with statement.",
        ["python.modules.import"],
    ),
    "python.io.json": (
        "JSON",
        "Serialising and parsing JSON; mapping it onto dicts and lists.",
        ["python.data.dicts", "python.io.files"],
    ),
    "python.io.http": (
        "HTTP requests",
        "Sending a request and handling the response; status codes.",
        ["python.modules.import"],
    ),

    # ── OOP ─────────────────────────────────────────────────────────────
    "python.oop.classes": (
        "Classes and objects",
        "Defining a class, creating instances, what an object is for.",
        ["python.functions.define"],
    ),
    "python.oop.init": (
        "__init__",
        "The initialiser, self, and setting up instance attributes.",
        ["python.oop.classes"],
    ),
    "python.oop.methods": (
        "Methods",
        "Functions bound to an object, and how they differ from plain functions.",
        ["python.oop.init"],
    ),
    "python.oop.inheritance": (
        "Inheritance",
        "Deriving one class from another; overriding; super().",
        ["python.oop.methods"],
    ),

    # ── Project-shaped concepts ─────────────────────────────────────────
    # A lecture that BUILDS something teaches things that are not language
    # features. Without slots for these, a project's most useful Meals have
    # nowhere to go and get mis-tagged onto a syntax concept.
    "python.project.overview": (
        "What we are building",
        "The goal of the project, its shape, and why it is worth building.",
        [],
    ),
    "python.project.setup": (
        "Project setup",
        "Environment, dependencies, and the file layout a project starts from.",
        ["python.modules.import"],
    ),
    "python.project.design": (
        "Design decisions",
        "Choosing an approach and the trade-off behind it.",
        [],
    ),
    "python.project.pipeline": (
        "Putting it together",
        "How the pieces built so far combine into a working whole.",
        [],
    ),
    "python.project.pitfalls": (
        "Common mistakes",
        "The errors people actually hit here, and how to avoid them.",
        [],
    ),
}

VALID_IDS = set(CONCEPTS)


def display_name(concept_id: str) -> str:
    entry = CONCEPTS.get(concept_id)
    return entry[0] if entry else concept_id


def prerequisites(concept_id: str) -> list[str]:
    entry = CONCEPTS.get(concept_id)
    return list(entry[2]) if entry else []


def catalogue_for_prompt() -> str:
    """The taxonomy as the planner sees it. Glosses matter — they are what
    keeps the model from inventing ids or reaching for a near-miss."""
    return "\n".join(
        f"- {cid}: {gloss}" for cid, (_name, gloss, _pre) in CONCEPTS.items()
    )


def nearest_valid(concept_id: str) -> str | None:
    """Map a model-suggested id onto a real one, or return None.

    Unlike the Operating Systems tagger this taxonomy replaces, there is a real
    'no match' answer. Silently defaulting is how a Python lecture ends up
    tagged as an OS concept, so returning None is the honest outcome — the
    caller then knows to ask again rather than to trust a near-miss.
    """
    if concept_id in VALID_IDS:
        return concept_id
    if not concept_id:
        return None

    def squash(v: str) -> str:
        return "".join(ch for ch in v.lower() if ch.isalnum())

    needle = squash(concept_id)
    if not needle:
        return None

    # Exact match on the squashed id, its last segment, or the display name.
    # "for_loops" and "for loops" both reach python.control.for this way.
    for cid, (name, _gloss, _pre) in CONCEPTS.items():
        candidates = {squash(cid), squash(cid.split(".")[-1]), squash(name)}
        if needle in candidates:
            return cid

    # Last resort: the needle fully contains a concept's display name, or vice
    # versa, and the match is long enough not to be an accident.
    for cid, (name, _gloss, _pre) in CONCEPTS.items():
        target = squash(name)
        if len(target) >= 4 and (target in needle or needle in target):
            return cid

    return None
