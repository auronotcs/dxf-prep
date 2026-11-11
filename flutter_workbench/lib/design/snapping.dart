import 'package:flutter/material.dart';
import 'controller.dart';

/// Pointer-only geometry queries over a revision-cached backend snapshot.
/// These features mirror Tkinter _snap_point; all committed edits go to Python.
class SnapTarget {
  const SnapTarget(this.position, this.label, [this.entityId]);
  final Offset position;
  final String label;
  final String? entityId;
}

class SnapIndex {
  SnapIndex(Iterable<Json> entities) {
    for (final e in entities) {
      final targets = <SnapTarget>[];
      void add(Offset p, String label) {
        if (p.dx.isFinite && p.dy.isFinite) {
          targets.add(SnapTarget(p, label, e['id'] as String));
        }
      }

      switch (e['kind']) {
        case 'line':
          final a = point(e['start']), b = point(e['end']);
          add(a, 'Endpoint');
          add(b, 'Endpoint');
          add((a + b) / 2, 'Midpoint');
        case 'circle':
          final c = point(e['center']), r = number(e['radius']);
          add(c, 'Center');
          for (final d in [
            Offset(r, 0),
            Offset(-r, 0),
            Offset(0, r),
            Offset(0, -r),
          ]) {
            add(c + d, 'Quadrant');
          }
        case 'rectangle':
          final r = Rect.fromPoints(point(e['corner_a']), point(e['corner_b']));
          for (final p in [
            r.topLeft,
            r.topRight,
            r.bottomRight,
            r.bottomLeft,
          ]) {
            add(p, 'Corner');
          }
          add(r.center, 'Center');
        case 'polyline':
          final points = (e['points'] as List).map(point).toList();
          for (final p in points) {
            add(p, 'Vertex');
          }
          for (var i = 1; i < points.length; i++) {
            add((points[i - 1] + points[i]) / 2, 'Midpoint');
          }
          if (e['closed'] == true && points.length > 1) {
            add((points.last + points.first) / 2, 'Midpoint');
          }
      }
      if (targets.isNotEmpty) {
        var box = Rect.fromPoints(
          targets.first.position,
          targets.first.position,
        );
        for (final target in targets.skip(1)) {
          box = box.expandToInclude(
            Rect.fromPoints(target.position, target.position),
          );
        }
        _items.add((e['id'] as String, box, targets));
      }
    }
  }
  final _items = <(String, Rect, List<SnapTarget>)>[];

  SnapTarget? nearest(
    Offset p,
    double radius, {
    Set<String> exclude = const {},
    Set<String>? only,
  }) {
    SnapTarget? best;
    var distance = radius;
    for (final (id, box, targets) in _items) {
      if (exclude.contains(id) ||
          (only != null && !only.contains(id)) ||
          !box.inflate(radius + 1e-9).contains(p)) {
        continue;
      }
      for (final target in targets) {
        final d = (target.position - p).distance;
        if (d <= radius && (best == null || d < distance)) {
          best = target;
          distance = d;
        }
      }
    }
    return best;
  }
}
