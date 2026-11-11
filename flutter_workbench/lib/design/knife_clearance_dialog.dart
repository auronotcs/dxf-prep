import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';

/// Independent, read-only camera: inspecting a finding never moves the document.
class KnifeClearanceDialog extends StatefulWidget {
  const KnifeClearanceDialog({
    super.key,
    required this.model,
    required this.findings,
  });
  final DesignController model;
  final List<Json> findings;
  @override
  State<KnifeClearanceDialog> createState() => _KnifeClearanceDialogState();
}

class _KnifeClearanceDialogState extends State<KnifeClearanceDialog> {
  final camera = TransformationController();
  late final dynamic revision = widget.model.state['revision'];
  int index = 0;
  bool whole = false;
  @override
  void dispose() {
    camera.dispose();
    super.dispose();
  }

  void reset() {
    camera.value = Matrix4.identity();
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: widget.model,
    builder: (context, _) {
      final finding = widget.findings[index];
      final stale = revision != widget.model.state['revision'];
      final p = finding['point'] as List? ?? [0, 0];
      return AlertDialog(
        title: const Text('Knife clearance'),
        content: SizedBox(
          width: 850,
          height: 620,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                stale
                    ? 'Drawing changed — close and regenerate these findings.'
                    : finding['accepted'] == true
                    ? 'Checked shallow swivel replaces this plate-crossing loop.'
                    : 'Generation blocked — this cut was not omitted.',
                style: TextStyle(
                  color: stale || finding['accepted'] != true
                      ? Theme.of(context).colorScheme.error
                      : null,
                ),
              ),
              DropdownButton<int>(
                value: index,
                isExpanded: true,
                items: [
                  for (var i = 0; i < widget.findings.length; i++)
                    DropdownMenuItem(
                      value: i,
                      child: Text(
                        '${widget.findings[i]['plate_number'] == null ? 'Drawing' : 'Plate ${widget.findings[i]['plate_number']}'} · ${widget.findings[i]['operation_name']} · ${i + 1}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: (v) {
                  if (v != null) {
                    setState(() {
                      index = v;
                      reset();
                    });
                  }
                },
              ),
              Text('${finding['message']}'),
              Text(
                'X ${number(p[0]).toStringAsFixed(5)}, Y ${number(p[1]).toStringAsFixed(5)} ${finding['units']} · ${finding['frame'] ?? 'Drawing coordinates'}',
              ),
              Wrap(
                spacing: 8,
                children: [
                  TextButton(
                    onPressed: () => setState(() {
                      whole = false;
                      reset();
                    }),
                    child: const Text('Fit finding'),
                  ),
                  TextButton(
                    onPressed: () => setState(() {
                      whole = true;
                      reset();
                    }),
                    child: const Text('Whole contour / plate'),
                  ),
                  const Text('Scroll to zoom · drag to pan'),
                ],
              ),
              Expanded(
                child: ClipRect(
                  child: LayoutBuilder(
                    builder: (context, box) => InteractiveViewer(
                      transformationController: camera,
                      minScale: .5,
                      maxScale: 40,
                      child: CustomPaint(
                        size: Size(box.maxWidth, box.maxHeight),
                        painter: KnifeClearancePainter(finding, whole: whole),
                      ),
                    ),
                  ),
                ),
              ),
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  'Black: source · Red: stock · Purple: obstacles\nBlue: attempted path · Orange dashed: reserved bounds (not a cut) · Green: checked fallback',
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Back to review'),
          ),
        ],
      );
    },
  );
}

class KnifeClearancePainter extends CustomPainter {
  KnifeClearancePainter(this.finding, {this.whole = false});
  final Json finding;
  final bool whole;
  List<Offset> points(dynamic value) => [
    for (final p in value as List? ?? [])
      if (p is List && p.length >= 2) Offset(number(p[0]), number(p[1])),
  ];
  @override
  void paint(Canvas canvas, Size size) {
    canvas.drawRect(Offset.zero & size, Paint()..color = Colors.white);
    final source = points(finding['source']),
        outline = points(finding['outline']),
        fallback = points(finding['fallback_envelope']);
    final location = points([finding['point']]).firstOrNull ?? Offset.zero;
    final plate = (finding['plate'] as List? ?? [0, 0, 1, 1])
        .map(number)
        .toList();
    final stock = [
      Offset(plate[0], plate[1]),
      Offset(plate[2], plate[1]),
      Offset(plate[2], plate[3]),
      Offset(plate[0], plate[3]),
    ];
    final margin = math.max(0.0, number(finding['margin']));
    Rect bounds(List<Offset> pts) {
      var r = Rect.fromPoints(pts.first, pts.first);
      for (final p in pts.skip(1)) {
        r = r.expandToInclude(Rect.fromPoints(p, p));
      }
      return r;
    }

    final reserved = bounds(
      outline.isEmpty ? [location] : outline,
    ).inflate(margin);
    var region = whole
        ? bounds([...stock, ...source, ...outline, location])
        : reserved;
    if (fallback.isNotEmpty) region = region.expandToInclude(bounds(fallback));
    final span = math.max(math.max(region.width, region.height), .00001);
    region = region.inflate(span * .18);
    final scale = math.min(
      (size.width - 32) / region.width,
      (size.height - 32) / region.height,
    );
    Offset xy(Offset p) => Offset(
      size.width / 2 + (p.dx - region.center.dx) * scale,
      size.height / 2 - (p.dy - region.center.dy) * scale,
    );
    void line(List<Offset> pts, Color color, {bool close = false}) {
      if (pts.length < 2) return;
      final path = Path()..moveTo(xy(pts.first).dx, xy(pts.first).dy);
      for (final p in pts.skip(1)) {
        path.lineTo(xy(p).dx, xy(p).dy);
      }
      if (close) path.close();
      canvas.drawPath(
        path,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
    }

    line(source, Colors.black, close: true);
    line(stock, Colors.red, close: true);
    for (final o in finding['obstacles'] as List? ?? []) {
      line(points(o['points']), Colors.purple, close: o['closed'] == true);
    }
    line(outline, Colors.blue);
    line(fallback, Colors.green, close: true);
    final corners = [
      reserved.topLeft,
      reserved.topRight,
      reserved.bottomRight,
      reserved.bottomLeft,
      reserved.topLeft,
    ].map(xy).toList();
    for (var i = 1; i < corners.length; i++) {
      final a = corners[i - 1], b = corners[i], d = (b - a).distance;
      for (double t = 0; t < d; t += 10) {
        canvas.drawLine(
          Offset.lerp(a, b, t / d)!,
          Offset.lerp(a, b, math.min(t + 5, d) / d)!,
          Paint()
            ..color = Colors.orange
            ..strokeWidth = 1.5,
        );
      }
    }
    canvas.drawCircle(
      xy(location),
      5,
      Paint()
        ..color = Colors.blue
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2,
    );
  }

  @override
  bool shouldRepaint(covariant KnifeClearancePainter old) =>
      old.finding != finding || old.whole != whole;
}
