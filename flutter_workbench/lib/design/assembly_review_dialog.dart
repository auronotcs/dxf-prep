import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'canvas.dart' show entityPath;

class AssemblyReviewDialog extends StatefulWidget {
  const AssemblyReviewDialog({
    super.key,
    required this.model,
    required this.cabinet,
  });
  final DesignController model;
  final bool cabinet;
  @override
  State<AssemblyReviewDialog> createState() => _AssemblyReviewDialogState();
}

class _AssemblyReviewDialogState extends State<AssemblyReviewDialog> {
  Json? data;
  String? error;
  bool busy = false;
  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() => busy = true);
    try {
      final r = await widget.model.request(
        widget.cabinet ? 'design.cabinet_review' : 'design.pan_review',
      );
      if (!mounted) return;
      if (r['ok'] != true) throw StateError('${r['error']}');
      setState(() {
        data = r['review'] as Json;
        error = null;
      });
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> select(Json row, String kind) async {
    setState(() => busy = true);
    final ok = await widget.model.run('design.pan_select', {
      'id': row['id'],
      'kind': kind,
      'token': data!['token'],
    });
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context, true);
    } else {
      setState(() {
        busy = false;
        error = widget.model.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(
      widget.cabinet
          ? 'Cabinet Tabs registration review'
          : 'DR-pan machining features',
    ),
    content: SizedBox(
      width: 740,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              widget.cabinet
                  ? 'Private inspection of saved registration and paired review geometry. No registration, fabrication or output approval is changed.'
                  : 'Select existing verified features, then choose Outside, Engrave or Drill and review tool/depth settings in Manufacturing. Reference bend guides are never machining targets.',
            ),
            if (busy) const LinearProgressIndicator(),
            if (error != null)
              Text(
                error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            if (data != null && (data!['rows'] as List).isEmpty)
              Text(
                widget.cabinet
                    ? 'No saved Cabinet Tabs plans.'
                    : 'No applied DR pans in this drawing.',
              ),
            for (final row in (data?['rows'] as List? ?? []).cast<Json>()) ...[
              const Divider(),
              SelectableText('${row['name'] ?? row['id']}'),
              if (row['error'] != null)
                Text(
                  '${row['error']}',
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              if (widget.cabinet) ...[
                Text(
                  row['valid'] == true
                      ? 'Registration matches current source geometry.'
                      : 'Registration requires review in the owning workflow.',
                ),
                for (final plane in (row['planes'] as List).cast<Json>()) ...[
                  SelectableText(
                    'Owner ${plane['owner']} · members ${(plane['members'] as List).join(', ')}',
                  ),
                  Text(
                    'Origin ${plane['origin']} · angle ${plane['angle']}° · inward setback ${plane['setback']} ${data!['units']}',
                  ),
                  SelectableText(
                    'Saved: ${plane['saved']}\nCurrent: ${plane['current']}',
                  ),
                ],
                Text('${(row['tabs'] as List).length} registered tab pairs'),
                if (row['valid'] == true)
                  SizedBox(
                    height: 230,
                    width: double.infinity,
                    child: CustomPaint(
                      painter: AssemblyPreviewPainter(
                        (row['geometry'] as List).cast<Json>(),
                      ),
                    ),
                  ),
              ] else
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final kind in ['outline', 'marks', 'holes'])
                      FilledButton.tonal(
                        onPressed: busy || row['editable'][kind] != true
                            ? null
                            : () => select(row, kind),
                        child: Text(
                          '${{'outline': 'Select pan → Outside', 'marks': 'Select marks → Engrave', 'holes': 'Select holes → Drill'}[kind]} (${(row['targets'][kind] as List).length})',
                        ),
                      ),
                  ],
                ),
            ],
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: busy ? null : refresh,
        child: const Text('Refresh'),
      ),
      TextButton(
        onPressed: busy ? null : () => Navigator.pop(context),
        child: const Text('Close'),
      ),
    ],
  );
}

class AssemblyPreviewPainter extends CustomPainter {
  AssemblyPreviewPainter(this.entities);
  final List<Json> entities;
  @override
  void paint(Canvas canvas, Size size) {
    final paths = entities.map(entityPath).toList();
    if (paths.isEmpty) return;
    var bounds = paths.first.getBounds();
    for (final p in paths.skip(1)) {
      bounds = bounds.expandToInclude(p.getBounds());
    }
    if (bounds.isEmpty) return;
    final scale = math.min(
      (size.width - 24) / bounds.width,
      (size.height - 24) / bounds.height,
    );
    final matrix = Matrix4.identity()
      ..translateByDouble(
        size.width / 2 - bounds.center.dx * scale,
        size.height / 2 + bounds.center.dy * scale,
        0,
        1,
      )
      ..scaleByDouble(scale, -scale, 1, 1);
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawColor(Colors.white, BlendMode.src);
    for (var i = 0; i < paths.length; i++) {
      final meta = entities[i]["metadata"] as Json? ?? {};
      canvas.drawPath(
        paths[i].transform(matrix.storage),
        Paint()
          ..color = meta["cabinet_tab_role"] == "nominal_slot_review"
              ? Colors.blue
              : meta["reference_only"] == true
              ? Colors.purple
              : meta["dr_pan_bend_mark"] == true
              ? Colors.orange
              : meta["dr_pan_hole"] == true
              ? Colors.blue
              : Colors.teal
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.4,
      );
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant AssemblyPreviewPainter old) => true;
}
