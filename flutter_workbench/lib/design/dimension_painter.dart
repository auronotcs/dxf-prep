import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';

// Paint core-resolved annotation geometry in a Y-up world canvas.
void paintDimension(
  Canvas canvas,
  Json d,
  double scale, {
  bool dark = false,
  bool selected = false,
  bool hovered = false,
}) {
  if (d['error'] != null) return;
  final annotation = d['annotation'] as Json;
  final color = selected
      ? Colors.orange
      : hovered
      ? Colors.teal
      : d['resolved'] == true
      ? (dark ? const Color(0xffb4c7f5) : const Color(0xff405f98))
      : Colors.red;
  final stroke = Paint()
    ..color = color
    ..style = PaintingStyle.stroke
    ..strokeWidth = (selected || hovered ? 2 : 1.2) / scale;
  for (final line in d['extension_lines'] as List) {
    canvas.drawLine(point(line[0]), point(line[1]), stroke);
  }
  final points = (d['path'] as List).map(point).toList();
  if (points.isEmpty) {
    points.addAll([point(d['dimension_start']), point(d['dimension_end'])]);
  }
  final path = Path()..moveTo(points.first.dx, points.first.dy);
  for (final p in points.skip(1)) {
    path.lineTo(p.dx, p.dy);
  }
  canvas.drawPath(path, stroke);
  final arrow =
      (number(annotation['text_height']) * scale * .7).clamp(5.0, 11.0) / scale;
  void tip(Offset end, Offset from) {
    final delta = from - end;
    if (delta.distance < 1e-12) return;
    final unit = delta / delta.distance;
    final normal = Offset(-unit.dy, unit.dx);
    canvas.drawPath(
      Path()
        ..moveTo(end.dx, end.dy)
        ..lineTo(
          (end + unit * arrow + normal * arrow * .35).dx,
          (end + unit * arrow + normal * arrow * .35).dy,
        )
        ..lineTo(
          (end + unit * arrow - normal * arrow * .35).dx,
          (end + unit * arrow - normal * arrow * .35).dy,
        )
        ..close(),
      Paint()..color = color,
    );
  }

  final mode = d['arrow_mode'];
  if (mode == 'both' || mode == 'first') tip(points.first, points[1]);
  if (mode == 'both' || mode == 'last') {
    tip(points.last, points[points.length - 2]);
  }
  var rotation = number(d['text_rotation']);
  while (rotation > 90) {
    rotation -= 180;
  }
  while (rotation < -90) {
    rotation += 180;
  }
  final position = point(d['text_point']);
  canvas.save();
  canvas.translate(position.dx, position.dy);
  canvas.rotate(rotation * math.pi / 180);
  canvas.scale(1, -1);
  final tp = TextPainter(
    text: TextSpan(
      text: d['label'],
      style: TextStyle(
        fontSize: math.max(number(annotation['text_height']), 8 / scale),
        color: color,
      ),
    ),
    textDirection: TextDirection.ltr,
  )..layout();
  canvas.drawRect(
    Rect.fromCenter(
      center: Offset.zero,
      width: tp.width + 4 / scale,
      height: tp.height + 2 / scale,
    ),
    Paint()..color = dark ? const Color(0xff182a2e) : Colors.white,
  );
  tp.paint(canvas, Offset(-tp.width / 2, -tp.height / 2));
  if (selected || hovered) {
    canvas.drawRect(
      Rect.fromCenter(
        center: Offset.zero,
        width: tp.width + 6 / scale,
        height: tp.height + 4 / scale,
      ),
      stroke,
    );
  }
  tp.dispose();
  canvas.restore();
  if (selected || hovered) {
    final handle = dimensionHandle(d, scale);
    if (handle != null) {
      final outline = Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5 / scale;
      final fill = Paint()
        ..color = dark ? const Color(0xff182a2e) : Colors.white;
      if (annotation['kind'] == 'angle') {
        canvas.drawCircle(handle, 5 / scale, fill);
        canvas.drawCircle(handle, 5 / scale, outline);
      } else if (hasFreeDimensionLanding(d)) {
        final rect = Rect.fromCenter(
          center: handle,
          width: 10 / scale,
          height: 10 / scale,
        );
        canvas.drawRect(rect, fill);
        canvas.drawRect(rect, outline);
      } else {
        final r = 5 / scale;
        final diamond = Path()
          ..moveTo(handle.dx, handle.dy + r)
          ..lineTo(handle.dx + r, handle.dy)
          ..lineTo(handle.dx, handle.dy - r)
          ..lineTo(handle.dx - r, handle.dy)
          ..close();
        canvas.drawPath(diamond, fill);
        canvas.drawPath(diamond, outline);
      }
    }
  }
}

// Fixed-size handles sit beside radial text or at the angular arc midpoint.
Offset? dimensionHandle(Json d, double scale) {
  if (d['error'] != null) return null;
  final a = d['annotation'] as Json;
  if (a['kind'] == 'angle') {
    final vertex = point(a['vertex']['fallback']);
    final direction = point(d['text_point']) - vertex;
    if (direction.distance <= 1e-12) return null;
    return vertex + direction / direction.distance * number(a['offset']).abs();
  }
  if (a['kind'] != 'radius' && !hasFreeDimensionLanding(d)) return null;
  final text = TextPainter(
    text: TextSpan(
      text: d['label'],
      style: TextStyle(fontSize: math.max(number(a['text_height']), 8 / scale)),
    ),
    textDirection: TextDirection.ltr,
  )..layout();
  final width = text.width;
  text.dispose();
  var rotation = number(d['text_rotation']);
  while (rotation > 90) {
    rotation -= 180;
  }
  while (rotation < -90) {
    rotation += 180;
  }
  final angle = rotation * math.pi / 180;
  return point(d['text_point']) +
      Offset(math.cos(angle), math.sin(angle)) * (width / 2 + 12 / scale);
}

// Hit the actual strokes and rotated label, never the empty measured area.
bool hitDimension(Json d, Offset p, double scale) {
  if (d['error'] != null) return false;
  final handle = dimensionHandle(d, scale);
  if (handle != null && (p - handle).distance <= 8 / scale) return true;
  var rotation = number(d['text_rotation']);
  while (rotation > 90) {
    rotation -= 180;
  }
  while (rotation < -90) {
    rotation += 180;
  }
  final angle = rotation * math.pi / 180, delta = p - point(d['text_point']);
  final local = Offset(
    delta.dx * math.cos(angle) + delta.dy * math.sin(angle),
    -delta.dx * math.sin(angle) + delta.dy * math.cos(angle),
  );
  final text = TextPainter(
    text: TextSpan(
      text: d['label'],
      style: TextStyle(
        fontSize: math.max(number(d['annotation']['text_height']), 8 / scale),
      ),
    ),
    textDirection: TextDirection.ltr,
  )..layout();
  final labelHit = Rect.fromCenter(
    center: Offset.zero,
    width: text.width + 10 / scale,
    height: text.height + 10 / scale,
  ).contains(local);
  text.dispose();
  if (labelHit) {
    return true;
  }
  final points = (d['path'] as List).map(point).toList();
  if (points.isEmpty) {
    points.addAll([point(d['dimension_start']), point(d['dimension_end'])]);
  }
  final lines = <List<Offset>>[
    for (var i = 1; i < points.length; i++) [points[i - 1], points[i]],
    for (final line in d['extension_lines'] as List)
      [point(line[0]), point(line[1])],
  ];
  return lines.any((line) {
    final a = line[0], vector = line[1] - a, length2 = vector.distanceSquared;
    final t = length2 < 1e-20
        ? 0.0
        : (((p - a).dx * vector.dx + (p - a).dy * vector.dy) / length2).clamp(
            0.0,
            1.0,
          );
    return (p - a - vector * t).distance <= 5 / scale;
  });
}

Offset? dimensionDragAxis(Json d) {
  switch (d['annotation']['kind']) {
    case 'horizontal':
      return const Offset(0, 1);
    case 'vertical':
      return const Offset(1, 0);
    case 'aligned':
      final delta = point(d['end']) - point(d['start']);
      if (delta.distance < 1e-12) return null;
      return Offset(-delta.dy, delta.dx) / delta.distance;
    case 'radius':
      final delta = point(d['end']) - point(d['start']);
      return delta.distance > 1e-12 ? delta / delta.distance : null;
    case 'angle':
      final vertex = point(d['annotation']['vertex']['fallback']);
      final delta = point(d['text_point']) - vertex;
      return delta.distance > 1e-12 ? delta / delta.distance : null;
  }
  return null;
}

bool hasFreeDimensionLanding(Json d) {
  final a = d['annotation'] as Json;
  if (a['kind'] != 'leader' && a['kind'] != 'note') return false;
  final end = a['end'] as Json;
  return (end['entity_id'] ?? '') == '' && end['feature'] == 'free';
}

bool canDragDimension(Json d) =>
    d['error'] == null &&
    (hasFreeDimensionLanding(d) || dimensionDragAxis(d) != null);

Json? previewDimensionDrag(Json d, Offset from, Offset to) {
  if (!canDragDimension(d)) return null;
  if (!hasFreeDimensionLanding(d)) {
    final offset = draggedDimensionOffset(d, from, to);
    return offset == null ? null : previewDimensionOffset(d, offset);
  }
  final a = d['annotation'] as Json;
  final end = point(d['end']) + to - from;
  final distance = (end - point(d['start'])).distance;
  if (!end.dx.isFinite ||
      !end.dy.isFinite ||
      !distance.isFinite ||
      (a['kind'] == 'leader' && distance <= 1e-12)) {
    return null;
  }
  final xy = <String, dynamic>{'x': end.dx, 'y': end.dy};
  return {
    ...d,
    'annotation': {
      ...a,
      'end': {...a['end'] as Json, 'fallback': xy},
    },
    'end': xy,
    'dimension_start': a['kind'] == 'note' ? xy : d['dimension_start'],
    'dimension_end': xy,
    'text_point': xy,
    'value': a['kind'] == 'note' ? 0.0 : distance,
  };
}

bool dimensionPlacementChanged(Json before, Json after) =>
    (number(after['offset']) - number(before['offset'])).abs() > 1e-12 ||
    (point(after['end']['fallback']) - point(before['end']['fallback']))
            .distance >
        1e-12;

double? draggedDimensionOffset(Json d, Offset from, Offset to) {
  final axis = dimensionDragAxis(d);
  if (axis == null) return null;
  final a = d['annotation'] as Json, old = number(a['offset']);
  double value;
  if (a['kind'] == 'angle') {
    final vertex = point(a['vertex']['fallback']);
    final radius =
        old.abs() + (to - vertex).distance - (from - vertex).distance;
    if (radius <= 1e-12) return null;
    value = old < 0 ? -radius : radius;
  } else {
    final delta = to - from;
    value = old + delta.dx * axis.dx + delta.dy * axis.dy;
  }
  return value.isFinite ? value : null;
}

// Display-only preview. Python resolves and validates the final annotation.
Json previewDimensionOffset(Json d, double offset) {
  final axis = dimensionDragAxis(d)!;
  final annotation = d['annotation'] as Json;
  final delta = axis * (offset - number(annotation['offset']));
  Json xy(Offset p) => {'x': p.dx, 'y': p.dy};
  if (annotation['kind'] == 'radius') {
    return {
      ...d,
      'annotation': {...annotation, 'offset': offset},
      'text_point': xy(point(d['text_point']) + delta),
    };
  }
  if (annotation['kind'] == 'angle') {
    final radius = offset.abs(), oldRadius = number(annotation['offset']).abs();
    if (!radius.isFinite || radius <= 1e-12 || oldRadius <= 1e-12) {
      throw ArgumentError('Angular arc radius must be greater than zero.');
    }
    final vertex = point(annotation['vertex']['fallback']);
    Offset radial(Offset p) => vertex + (p - vertex) * (radius / oldRadius);
    final label = point(d['text_point']) - vertex;
    final gap = number(annotation['extension_offset']);
    List<Json> extension(Offset ray) {
      final vector = ray - vertex, unit = vector / vector.distance;
      return [xy(vertex + unit * gap), xy(vertex + unit * (radius + gap * .5))];
    }

    return {
      ...d,
      'annotation': {...annotation, 'offset': offset},
      'path': (d['path'] as List).map((p) => xy(radial(point(p)))).toList(),
      'dimension_start': xy(radial(point(d['dimension_start']))),
      'dimension_end': xy(radial(point(d['dimension_end']))),
      'text_point': xy(
        vertex +
            label /
                label.distance *
                (radius + number(annotation['text_height']) * .9),
      ),
      'extension_lines': [
        extension(point(d['start'])),
        extension(point(d['end'])),
      ],
    };
  }
  final a = point(d['dimension_start']) + delta,
      b = point(d['dimension_end']) + delta;
  List<Json> extension(Offset from, Offset target) {
    final vector = target - from, distance = vector.distance;
    if (distance <= 1e-12) return [xy(from), xy(target)];
    final unit = vector / distance,
        gap = number(annotation['extension_offset']);
    return [
      xy(from + unit * math.min(gap, distance * .45)),
      xy(target + unit * gap * .5),
    ];
  }

  return {
    ...d,
    'annotation': {...annotation, 'offset': offset},
    'dimension_start': xy(a),
    'dimension_end': xy(b),
    'path': <Json>[],
    'text_point': xy(point(d['text_point']) + delta),
    'extension_lines': [
      extension(point(d['start']), a),
      extension(point(d['end']), b),
    ],
  };
}
