"""Session-only derived XY display data; never used for machining or posting.

Motion/ToolPoint/Point are frozen value objects. Tuple snapshots therefore
detect even same-length in-place edits to a published motion list without
re-flattening arcs or rebuilding numeric signatures on every pan.
"""
from __future__ import annotations


class PreparedDisplayPath:
    def __init__(self, path, signature, operation_signature, condense):
        self.path = path  # Keep identity alive; never trust a recycled id().
        self.signature = signature
        self.operation_signature = operation_signature
        self.blocks = condense(path.blocks)
        self._strokes = {}
        self._tabs = {}

    def strokes(self, block, factory):
        key = id(block)
        if key not in self._strokes:
            self._strokes[key] = factory(block)
        return self._strokes[key]

    def tabs(self, block, operation, factory):
        key = id(block)
        if key not in self._tabs:
            self._tabs[key] = factory(block, operation)
        return self._tabs[key]


class PreviewDisplayCache:
    """Only the active document/current path generation is retained.

World-space data is cached. Zoom, viewport, selection color/width, layering,
visibility and canvas clipping are still evaluated by the normal renderer.
"""
    def __init__(self):
        self.context = None
        self.paths = {}

    def begin(self, context, previews):
        if context != self.context:
            self.paths.clear()
            self.context = context
        self.paths = {key: value for key, value in self.paths.items()
                      if previews.get(key) is value.path}

    def prepare(self, operation_id, path, operation, condense):
        signature = tuple(
            (id(block), block.operation_id, block.tool_station,
             repr(block.metadata), tuple(block.motions))
            for block in path.blocks
        )
        operation_signature = repr(operation)
        previous = self.paths.get(operation_id)
        if (previous is not None and previous.path is path
                and previous.signature == signature
                and previous.operation_signature == operation_signature):
            return previous
        # Failed preparation must not leave the superseded data available.
        self.paths.pop(operation_id, None)
        prepared = PreparedDisplayPath(path, signature, operation_signature, condense)
        self.paths[operation_id] = prepared
        return prepared
