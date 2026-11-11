import 'package:flutter/material.dart';
import 'controller.dart';

void paintKeepouts(
  Canvas canvas,
  List<Json> zones,
  Offset Function(Offset) transform,
) {
  for (final z in zones) {
    final points = (z['points'] as List? ?? [])
        .map(point)
        .map(transform)
        .toList();
    if (points.length < 3) continue;
    final active = z['enabled'] != false;
    final color = active ? Colors.red.shade700 : Colors.grey;
    final path = Path()..addPolygon(points, true);
    final margin = (z['extra_margin'] as num? ?? 0).toDouble();
    if (z['kind'] == 'hole_exclusion' && margin > 0) {
      final world = (z['points'] as List).map(point).toList();
      final bounds = Path()..addPolygon(world, true);
      final expanded = bounds.getBounds().inflate(margin);
      canvas.drawRect(
        Rect.fromPoints(
          transform(expanded.topLeft),
          transform(expanded.bottomRight),
        ),
        Paint()..color = color.withValues(alpha: .08),
      );
      canvas.drawRect(
        Rect.fromPoints(
          transform(expanded.topLeft),
          transform(expanded.bottomRight),
        ),
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1,
      );
    }
    canvas.drawPath(
      path,
      Paint()..color = color.withValues(alpha: active ? .13 : .04),
    );
    canvas.drawPath(
      path,
      Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1.5,
    );
    final text = TextPainter(
      text: TextSpan(
        text:
            '${z['name']} · Z ${z['clearance_z']}${active ? '' : ' · disabled'}',
        style: TextStyle(
          fontSize: 11,
          fontFamily: 'Segoe UI',
          color: color,
          backgroundColor: Colors.white.withValues(alpha: .85),
        ),
      ),
      textDirection: TextDirection.ltr,
      maxLines: 1,
      ellipsis: '…',
    )..layout(maxWidth: 240);
    text.paint(canvas, points.first + const Offset(3, 3));
  }
}

class KeepoutPainter extends CustomPainter {
  KeepoutPainter(this.zones, this.transform);
  final List<Json> zones;
  final Offset Function(Offset) transform;
  @override
  void paint(Canvas canvas, Size size) =>
      paintKeepouts(canvas, zones, transform);
  @override
  bool shouldRepaint(covariant KeepoutPainter oldDelegate) => true;
}
