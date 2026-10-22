"""Describes editable settings so the editor can build forms for faces and actions automatically."""

from dataclasses import dataclass, field


@dataclass
class Field:
    key: str
    label: str
    kind: str                       # text, multiline, template, int, bool, choice, color, file, hotkey,
                                    # page, app, variable, time
    default: object = None
    choices: list = field(default_factory=list)   # [(value, label)] for "choice"
    minimum: int = 0
    maximum: int = 100
    help: str = ""
    show_if: tuple | None = None    # (other_key, allowed_values) - only shown when other_key's value is in the set
    file_filter: str = ""


def defaults(fields: list[Field]) -> dict:
    return {f.key: f.default for f in fields}


def fill_defaults(values: dict | None, fields: list[Field]) -> dict:
    out = dict(values or {})
    for f in fields:
        out.setdefault(f.key, f.default)
    return out


def visible(f: Field, values: dict) -> bool:
    if not f.show_if:
        return True
    other, allowed = f.show_if
    return values.get(other) in allowed
