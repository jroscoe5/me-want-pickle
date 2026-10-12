"""Parse an HTML form into the field list the browser would post."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

REDACT = {"__RequestVerificationToken", "RequestData", "Token"}
_FORM_RE = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.S | re.I)
_ATTR_RE = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')
_INPUT_RE = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.I)
_OPTION_RE = re.compile(r'<option\b([^>]*)>(.*?)</option>', re.S | re.I)


@dataclass
class FormSpec:
    action: str
    method: str = "post"
    fields: list[tuple[str, str]] = field(default_factory=list)
    form_id: str | None = None
    flags: dict[str, str] = field(default_factory=dict)

    def get(self, name: str, default: str = "") -> str:
        for k, v in self.fields:
            if k == name:
                return v
        return default

    def has(self, name: str) -> bool:
        return any(k == name for k, _ in self.fields)

    def with_overrides(self, overrides: dict[str, str]) -> list[tuple[str, str]]:
        out, seen = [], set()
        for k, v in self.fields:
            if k in overrides and k not in seen:
                out.append((k, overrides[k]))
                seen.add(k)
            elif k in overrides:
                continue
            else:
                out.append((k, v))
        for k, v in overrides.items():
            if k not in seen:
                out.append((k, v))
        return out


def redacted(fields: list[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(k, f"<{len(v)} chars>" if k in REDACT and v else v) for k, v in fields]


def parse_form(page: str, form_id: str | None = None, action_contains: str | None = None) -> FormSpec | None:
    for m in _FORM_RE.finditer(page):
        attrs = dict(_ATTR_RE.findall(m.group(1)))
        if form_id and attrs.get("id") != form_id:
            continue
        if action_contains and action_contains not in attrs.get("action", ""):
            continue
        spec = FormSpec(action=html.unescape(attrs.get("action", "")), method=attrs.get("method", "post").lower(),
                        form_id=attrs.get("id"))
        body = m.group(2)
        for im in _INPUT_RE.finditer(body):
            tag, a = im.group(1).lower(), dict(_ATTR_RE.findall(im.group(2)))
            name = a.get("name")
            if not name:
                continue
            if tag == "input":
                t = a.get("type", "text").lower()
                if t in ("submit", "button", "image", "file"):
                    continue
                if t in ("checkbox", "radio") and "checked" not in im.group(2).lower():
                    continue
                spec.fields.append((name, html.unescape(a.get("value", "on" if t == "checkbox" else ""))))
            elif tag == "select":
                end = body.find("</select>", im.end())
                opts = _OPTION_RE.findall(body[im.end():end])
                chosen = next((dict(_ATTR_RE.findall(oa)).get("value", txt) for oa, txt in opts if "selected" in oa.lower()), None)
                spec.fields.append((name, html.unescape(chosen or "")))
            else:
                end = body.find("</textarea>", im.end())
                spec.fields.append((name, html.unescape(body[im.end():end].strip())))
        return spec
    return None
