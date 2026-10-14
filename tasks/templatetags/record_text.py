"""Render an allowlist of note styles while escaping every user-provided character."""
import re
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()
STYLES = {
    "**": ("bold", "strong", ""), "[u]": ("u", "span", "note-underline"),
    **{f"[color={name}]": ("color", "span", f"note-color-{name}") for name in ("default", "blue", "green", "rose")},
    **{f"[size={name}]": ("size", "span", f"note-size-{name}") for name in ("small", "normal", "large")},
}
TOKEN = re.compile(r"\*\*|\[\/?(?:u|color|size)(?:=[a-z]+)?\]")
CLOSE = {"[/u]": "u", "[/color]": "color", "[/size]": "size"}


def parse_note(text):
    root = {"children": []}
    stack, offset = [root], 0

    def append_text(value):
        if not value:
            return
        children = stack[-1]["children"]
        if children and isinstance(children[-1], str):
            children[-1] += value
        else:
            children.append(value)

    for match in TOKEN.finditer(text):
        append_text(text[offset:match.start()])
        mark, top = match.group(), stack[-1]
        closing = "bold" if mark == "**" and top.get("kind") == "bold" else CLOSE.get(mark)
        if closing and top.get("kind") == closing:
            stack.pop()
            if top["children"]:
                stack[-1]["children"].append(top)
            else:
                append_text(top["open"] + mark)
        elif mark in STYLES:
            if len(stack) > 16:
                return [text]
            kind, tag, classname = STYLES[mark]
            stack.append({"kind": kind, "tag": tag, "class": classname, "open": mark, "children": []})
        else:
            append_text(mark)
        offset = match.end()
    append_text(text[offset:])
    while len(stack) > 1:
        node = stack.pop()
        append_text(node["open"])
        for child in node["children"]:
            if isinstance(child, str):
                append_text(child)
            else:
                stack[-1]["children"].append(child)
    return root["children"]


@register.filter
def bold_note(value):
    def render(children):
        pieces = []
        for child in children:
            if isinstance(child, str):
                pieces.append(str(escape(child)))
            else:
                # Tags and classes are selected exclusively from STYLES, never from user HTML.
                attribute = f' class="{child["class"]}"' if child["class"] else ""
                pieces.append(f'<{child["tag"]}{attribute}>' + render(child["children"]) + f'</{child["tag"]}>')
        return "".join(pieces)
    return mark_safe(render(parse_note(str(value or ""))).replace("\r\n", "\n").replace("\n", "<br>"))
