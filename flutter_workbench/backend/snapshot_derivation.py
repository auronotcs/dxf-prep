"""Replay an explicitly reviewed upstream function batch without unrelated drift."""
import ast
import hashlib
import json
import re


def transplant(base, before, after, names):
    if not names or len(set(names)) != len(names):
        raise ValueError('Choose unique upstream functions for derivation.')
    def parse(text):
        nodes = {}
        lines = text.splitlines(keepends=True)
        for node in ast.parse(text).body:
            if isinstance(node, ast.FunctionDef):
                if node.name in nodes:
                    raise ValueError('Derivation requires unique undecorated functions.')
                nodes[node.name] = (node, ''.join(lines[node.lineno-1:node.end_lineno]))
        return lines, nodes
    lines, current = parse(base)
    _, previous = parse(before)
    _, upstream = parse(after)
    edits, additions = [], []
    for name in names:
        if name not in upstream:
            raise ValueError('Derived function is missing upstream: '+name)
        if any(name in group and group[name][0].decorator_list for group in (current,previous,upstream)):
            raise ValueError('Derivation requires undecorated selected functions.')
        new = upstream[name][1]
        if name in previous:
            if name not in current or current[name][1] != previous[name][1]:
                raise ValueError('Base function diverged before this upstream change: '+name)
            if new == previous[name][1]:
                raise ValueError('Derived function has no upstream change: '+name)
            node = current[name][0]
            edits.append((node.lineno-1, node.end_lineno, new))
        else:
            if name in current:
                raise ValueError('New upstream function conflicts with the base: '+name)
            additions.append(new)
    for start, end, text in sorted(edits, reverse=True):
        lines[start:end] = [text]
    result = ''.join(lines).rstrip()+'\n'
    if additions:
        result += '\n\n'+'\n\n'.join(s.rstrip() for s in additions)+'\n'
    ast.parse(result)
    return result


def replay(root, record, name):
    from sync_source import contained, digest
    spec = record['derived_files'][name]
    if set(spec) != {'base_snapshot','upstream_before','source_snapshot','functions'}:
        raise ValueError('Invalid derivation provenance.')
    def load(identity):
        if not re.fullmatch(r'[0-9a-f]{16}', identity):
            raise ValueError('Invalid derivation snapshot identity.')
        manifest = json.loads(contained(root, f'snapshots/{identity}/manifest.json').read_text(encoding='utf-8'))
        if manifest['snapshot_id'] != identity:
            raise ValueError('Derivation snapshot identity mismatch.')
        path = contained(contained(root, manifest['snapshot_directory']), name)
        if digest(path) != manifest['files'][name]:
            raise ValueError('Derivation input hash mismatch.')
        return manifest, path.read_text(encoding='utf-8')
    base, base_text = load(spec['base_snapshot'])
    _, before = load(spec['upstream_before'])
    source, after = load(spec['source_snapshot'])
    if source['files'][name] != record['source_files'][name]:
        raise ValueError('Derivation source does not match captured upstream.')
    result = transplant(base_text, before, after, spec['functions']).encode('utf-8')
    if hashlib.sha256(result).hexdigest() != record['files'][name]:
        raise ValueError('Derived file does not match its replayed upstream functions.')
    return result
