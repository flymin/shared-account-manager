"""Parse HTML into nodes for form/config lookup and visible email body text.

Shared by mailbox backends and email templates; no network or provider rules.
This is a parsing helper, not an HTML sanitizer or renderer.
"""

from html.parser import HTMLParser


class Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []

    def nodes(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.nodes()

    def text(self):
        return "".join(
            child.text() if isinstance(child, Node) else child
            for child in self.children
        )


class HTML(HTMLParser):
    def __init__(self, value):
        super().__init__(convert_charrefs=True)
        self.root = self.current = Node()
        self.feed(value)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.current)
        self.current.children.append(node)
        if tag not in {
            "area",
            "base",
            "br",
            "col",
            "embed",
            "hr",
            "img",
            "input",
            "link",
            "meta",
            "param",
            "source",
            "track",
            "wbr",
        }:
            self.current = node

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, value):
        self.current.children.append(value)


def visible_text(node):
    if node.tag in {"script", "style", "head"}:
        return ""
    return " ".join(
        visible_text(c) if isinstance(c, Node) else c for c in node.children
    )
