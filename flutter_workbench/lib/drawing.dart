import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'model.dart';

// Illustrative vector artwork in canvas units. Never used as machining geometry.
Path letterPath(int index) {
  final p = Path()..fillType = PathFillType.evenOdd;
  switch (index) {
    case 0:
      p.moveTo(0, 150);
      p.lineTo(0, 0);
      p.lineTo(28, 0);
      p.lineTo(74, 94);
      p.lineTo(74, 0);
      p.lineTo(102, 0);
      p.lineTo(102, 150);
      p.lineTo(74, 150);
      p.lineTo(28, 56);
      p.lineTo(28, 150);
      p.close();
    case 1:
      p.addRRect(
        RRect.fromRectAndRadius(
          const Rect.fromLTWH(0, 0, 102, 150),
          const Radius.circular(35),
        ),
      );
      p.addRRect(
        RRect.fromRectAndRadius(
          const Rect.fromLTWH(28, 28, 46, 94),
          const Radius.circular(15),
        ),
      );
    case 2:
      p.moveTo(0, 150);
      p.lineTo(0, 0);
      p.lineTo(65, 0);
      p.cubicTo(117, 0, 118, 78, 77, 89);
      p.lineTo(108, 150);
      p.lineTo(75, 150);
      p.lineTo(47, 94);
      p.lineTo(28, 94);
      p.lineTo(28, 150);
      p.close();
      p.addRRect(
        RRect.fromRectAndRadius(
          const Rect.fromLTWH(28, 26, 46, 42),
          const Radius.circular(12),
        ),
      );
    case 3:
      p.moveTo(0, 0);
      p.lineTo(108, 0);
      p.lineTo(108, 28);
      p.lineTo(68, 28);
      p.lineTo(68, 150);
      p.lineTo(40, 150);
      p.lineTo(40, 28);
      p.lineTo(0, 28);
      p.close();
    case 4:
      p.moveTo(0, 0);
      p.lineTo(28, 0);
      p.lineTo(28, 59);
      p.lineTo(74, 59);
      p.lineTo(74, 0);
      p.lineTo(102, 0);
      p.lineTo(102, 150);
      p.lineTo(74, 150);
      p.lineTo(74, 87);
      p.lineTo(28, 87);
      p.lineTo(28, 150);
      p.lineTo(0, 150);
      p.close();
  }
  return p;
}

Offset letterOrigin(int i, int sheet) =>
    Offset(112 + i * 131, sheet == 0 ? 181 : 252);

class DrawingView extends StatelessWidget {
  const DrawingView({super.key, required this.model, required this.controller});
  final WorkbenchModel model;
  final TransformationController controller;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    builder: (context, constraints) {
      final scale = math.min(
        constraints.maxWidth / 950,
        constraints.maxHeight / 590,
      );
      return InteractiveViewer(
        transformationController: controller,
        minScale: 0.65,
        maxScale: 5,
        boundaryMargin: const EdgeInsets.all(500),
        panEnabled: model.pan,
        child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTapUp: model.pan
              ? null
              : (details) {
                  final offset = Offset(
                    (constraints.maxWidth - 950 * scale) / 2,
                    (constraints.maxHeight - 590 * scale) / 2,
                  );
                  final point = (details.localPosition - offset) / scale;
                  var selected = -1;
                  if (model.layers[0].visible) {
                    for (var i = 0; i < 5; i++) {
                      if (letterPath(
                        i,
                      ).contains(point - letterOrigin(i, model.sheet))) {
                        selected = i;
                      }
                    }
                  }
                  model.update(() => model.selected = selected);
                },
          child: CustomPaint(
            painter: SheetPainter(model, scale),
            size: Size(constraints.maxWidth, constraints.maxHeight),
          ),
        ),
      );
    },
  );
}

class SheetPainter extends CustomPainter {
  SheetPainter(this.model, this.scale);
  final WorkbenchModel model;
  final double scale;

  void label(
    Canvas c,
    String text,
    Offset at, {
    double size = 12,
    Color color = muted,
  }) {
    final painter = TextPainter(
      text: TextSpan(
        text: text,
        style: TextStyle(fontSize: size, color: color, fontFamily: 'Segoe UI'),
      ),
      textDirection: TextDirection.ltr,
    )..layout();
    painter.paint(c, at);
  }

  @override
  void paint(Canvas canvas, Size size) {
    canvas.translate(
      (size.width - 950 * scale) / 2,
      (size.height - 590 * scale) / 2,
    );
    canvas.scale(scale);
    const sheet = Rect.fromLTWH(65, 90, 820, 410);
    canvas.drawShadow(
      Path()..addRect(sheet),
      Colors.black.withValues(alpha: .15),
      12,
      true,
    );
    canvas.drawRect(
      sheet,
      Paint()
        ..color = model.dark
            ? const Color(0xff26383c)
            : const Color(0xfffefefd),
    );
    final stroke = Paint()
      ..style = PaintingStyle.stroke
      ..strokeWidth = 1;
    if (model.grid) {
      stroke.color = model.dark
          ? const Color(0xff31464a)
          : const Color(0xffedf0ed);
      for (double x = 65; x <= 885; x += 20.5) {
        canvas.drawLine(Offset(x, 90), Offset(x, 500), stroke);
      }
      for (double y = 90; y <= 500; y += 20.5) {
        canvas.drawLine(Offset(65, y), Offset(885, y), stroke);
      }
    }
    canvas.drawRect(sheet, stroke..color = const Color(0xffb6c7c1));
    for (var i = 0; i <= 8; i++) {
      final x = 65 + i * 102.5;
      canvas.drawLine(Offset(x, 82), Offset(x, 87), stroke..color = muted);
      label(canvas, '${i * 12}', Offset(x - 6, 61), size: 11);
    }
    for (var i = 0; i <= 4; i++) {
      label(canvas, '${48 - i * 12}', Offset(34, 82 + i * 102.5), size: 11);
    }
    label(
      canvas,
      'PLATE ${model.sheet + 1}  /  96 × 48 IN',
      const Offset(65, 28),
      size: 12,
    );
    label(canvas, '96.00 in', const Offset(445, 526));
    canvas.drawLine(
      const Offset(65, 519),
      const Offset(885, 519),
      stroke..color = const Color(0xffb6c7c1),
    );
    for (final x in [65.0, 885.0]) {
      canvas.drawLine(Offset(x, 514), Offset(x, 524), stroke);
    }
    if (model.layers[2].visible) {
      final rect = Rect.fromLTWH(96, model.sheet == 0 ? 159 : 230, 686, 194);
      final path = Path()..addRect(rect);
      dashed(
        canvas,
        path,
        stroke..color = model.layers[2].color.withValues(alpha: .6),
      );
      label(
        canvas,
        'NORTH / dimensional letters',
        Offset(102, rect.bottom + 14),
        color: model.layers[2].color,
      );
    }
    for (var i = 0; i < 5; i++) {
      canvas.save();
      final origin = letterOrigin(i, model.sheet);
      canvas.translate(origin.dx, origin.dy);
      final path = letterPath(i);
      if (model.layers[0].visible) {
        canvas.drawPath(
          path,
          Paint()
            ..color = model.layers[0].color.withValues(
              alpha: model.selected == i ? .22 : .07,
            ),
        );
        canvas.drawPath(
          path,
          stroke
            ..color = model.layers[0].color
            ..strokeWidth = model.selected == i ? 2.5 : 1.5,
        );
        if (model.paths) {
          canvas.save();
          canvas.clipPath(path);
          for (double y = 5; y < 150; y += 7) {
            canvas.drawLine(
              Offset(0, y),
              Offset(110, y),
              stroke
                ..color = const Color(0xffda9761)
                ..strokeWidth = .8,
            );
          }
          canvas.restore();
        }
        if (model.selected == i) {
          final bounds = path.getBounds().inflate(8);
          dashed(
            canvas,
            Path()..addRect(bounds),
            stroke
              ..color = teal
              ..strokeWidth = 1,
          );
          for (final point in [
            bounds.topLeft,
            bounds.topRight,
            bounds.bottomLeft,
            bounds.bottomRight,
          ]) {
            canvas.drawRect(
              Rect.fromCenter(center: point, width: 7, height: 7),
              Paint()..color = teal,
            );
          }
        }
      }
      if (model.layers[1].visible) {
        final x = i == 3 ? 54.0 : 14.0;
        for (final y in [38.0, 114.0]) {
          canvas.drawCircle(
            Offset(x, y),
            3.8,
            stroke
              ..color = model.layers[1].color
              ..strokeWidth = 1.3,
          );
          canvas.drawLine(
            Offset(x - 7, y),
            Offset(x + 7, y),
            stroke..strokeWidth = .7,
          );
          canvas.drawLine(Offset(x, y - 7), Offset(x, y + 7), stroke);
        }
      }
      canvas.restore();
    }
    canvas.drawCircle(const Offset(65, 500), 4, Paint()..color = teal);
    canvas.drawLine(
      const Offset(65, 500),
      const Offset(94, 500),
      stroke
        ..color = teal
        ..strokeWidth = 2,
    );
    canvas.drawLine(const Offset(65, 500), const Offset(65, 471), stroke);
    label(canvas, '0,0', const Offset(72, 480), size: 10, color: teal);
  }

  void dashed(Canvas canvas, Path path, Paint paint) {
    for (final metric in path.computeMetrics()) {
      for (double d = 0; d < metric.length; d += 10) {
        canvas.drawPath(
          metric.extractPath(d, math.min(d + 5, metric.length)),
          paint,
        );
      }
    }
  }

  @override
  bool shouldRepaint(covariant SheetPainter oldDelegate) => true;
}
