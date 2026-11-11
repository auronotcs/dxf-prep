import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'canvas.dart' show entityPath;

class SheetCutoffDialog extends StatefulWidget {
  const SheetCutoffDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<SheetCutoffDialog> createState() => _SheetCutoffDialogState();
}

class _SheetCutoffDialogState extends State<SheetCutoffDialog> {
  DesignController get m => widget.model;
  late final revision = m.state['revision'];
  late final clearance = TextEditingController(
    text: m.units == 'mm' ? '12.7' : '0.5',
  );
  late int plate = (m.state['activePlate'] as num? ?? 1).toInt();
  String scope = 'selected', orientation = 'vertical';
  bool working = false, applying = false, stale = false;
  Json? preview;
  String? message;
  int shown = 0;
  @override
  void initState() {
    super.initState();
    m.addListener(changed);
  }

  void changed() {
    if (!applying && (m.state['revision'] != revision || !m.ready)) {
      setState(() {
        stale = true;
        preview = null;
        message = 'Drawing changed. Close and reopen Sheet Cutoff Lines.';
      });
    }
  }

  @override
  void dispose() {
    m.removeListener(changed);
    clearance.dispose();
    super.dispose();
  }

  void edit(VoidCallback action) => setState(() {
    action();
    preview = null;
    message = null;
  });
  Future<void> calculate() async {
    final value = double.tryParse(clearance.text);
    if (value == null || !value.isFinite || value < 0) {
      setState(() {
        preview = null;
        message = 'Enter a finite, nonnegative clearance.';
      });
      return;
    }
    setState(() {
      working = true;
      preview = null;
      message = null;
    });
    try {
      final response = await m.request('design.cutoff_preview', {
        'scope': scope,
        'plate_number': plate,
        'orientation': orientation,
        'clearance': value,
      });
      if (!mounted) return;
      if (response['ok'] != true) throw StateError('${response['error']}');
      if (stale || m.state['revision'] != revision) {
        throw StateError('Drawing changed. Reopen Sheet Cutoff Lines.');
      }
      setState(() {
        preview = response['preview'] as Json;
        shown = 0;
      });
    } catch (e) {
      if (mounted) setState(() => message = '$e');
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> apply() async {
    setState(() {
      working = true;
      applying = true;
    });
    final ok = await m.run('design.cutoff_apply', {'token': preview!['token']});
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
      return;
    }
    setState(() {
      working = false;
      applying = false;
      preview = null;
      message = m.message;
    });
  }

  @override
  Widget build(BuildContext context) {
    final rows = (preview?['rows'] as List? ?? []).cast<Json>();
    final active = !working && !stale;
    return PopScope(
      canPop: !working,
      child: AlertDialog(
        title: const Text('Sheet Cutoff Lines'),
        content: SizedBox(
          width: 780,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Create editable lines using geometry on all layers, including hidden parts. Existing automatic cutoffs are updated on their Cutoff layer. Inspect placement before machining.',
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 16,
                  runSpacing: 12,
                  children: [
                    SizedBox(
                      width: 175,
                      child: DropdownButtonFormField<String>(
                        initialValue: scope,
                        decoration: const InputDecoration(labelText: 'Sheets'),
                        items: const [
                          DropdownMenuItem(
                            value: 'selected',
                            child: Text('Selected sheet'),
                          ),
                          DropdownMenuItem(
                            value: 'all',
                            child: Text('All sheets'),
                          ),
                        ],
                        onChanged: active
                            ? (v) => edit(() => scope = v!)
                            : null,
                      ),
                    ),
                    if (scope == 'selected')
                      SizedBox(
                        width: 210,
                        child: DropdownButtonFormField<int>(
                          initialValue: plate,
                          decoration: const InputDecoration(labelText: 'Sheet'),
                          items: [
                            for (final p in m.plates)
                              DropdownMenuItem(
                                value: (p['number'] as num).toInt(),
                                child: Text(
                                  '${p['number']} · ${p['name'] ?? 'Plate'}',
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                          ],
                          onChanged: active
                              ? (v) => edit(() => plate = v!)
                              : null,
                        ),
                      ),
                    SizedBox(
                      width: 230,
                      child: DropdownButtonFormField<String>(
                        initialValue: orientation,
                        decoration: const InputDecoration(
                          labelText: 'Placement',
                        ),
                        items: const [
                          DropdownMenuItem(
                            value: 'vertical',
                            child: Text('Vertical · right of parts'),
                          ),
                          DropdownMenuItem(
                            value: 'horizontal',
                            child: Text('Horizontal · above parts'),
                          ),
                        ],
                        onChanged: active
                            ? (v) => edit(() => orientation = v!)
                            : null,
                      ),
                    ),
                    SizedBox(
                      width: 180,
                      child: TextField(
                        controller: clearance,
                        enabled: active,
                        decoration: InputDecoration(
                          labelText: 'Clearance (${m.units})',
                        ),
                        onChanged: (_) => edit(() {}),
                      ),
                    ),
                  ],
                ),
                if (message != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(
                      message!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ),
                if (rows.isNotEmpty) ...[
                  const SizedBox(height: 12),
                  for (var i = 0; i < rows.length; i++)
                    ListTile(
                      dense: true,
                      selected: shown == i,
                      onTap: () => setState(() => shown = i),
                      title: Text(
                        'Sheet ${rows[i]['plate']['number']} · ${rows[i]['action']}',
                      ),
                      subtitle: Text('${rows[i]['message']}'),
                      trailing:
                          rows[i]['replaced'] != null && rows[i]['replaced'] > 0
                          ? Text('${rows[i]['replaced']} existing')
                          : null,
                    ),
                  const Text(
                    'Blue: proposed line · gray: existing geometry · red: sheet edge',
                  ),
                  SizedBox(
                    height: 220,
                    width: double.infinity,
                    child: CustomPaint(
                      key: const ValueKey('cutoff-preview'),
                      painter: CutoffPreviewPainter(m.entities, rows[shown]),
                    ),
                  ),
                  if ((preview!['affected_operations'] as List).isNotEmpty)
                    Text(
                      'Existing Cut Line operations remain attached and need fresh toolpaths: ${(preview!['affected_operations'] as List).map((o) => o['name']).join(', ')}.',
                    ),
                  Text(
                    preview!['token'] == null
                        ? 'No lines can be added. Review the skipped-sheet reasons.'
                        : 'Apply adds or replaces the listed lines in one Undo step and selects them. Toolpaths are not generated.',
                  ),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: working ? null : () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          OutlinedButton(
            onPressed: active ? calculate : null,
            child: Text(working ? 'Working…' : 'Preview placement'),
          ),
          FilledButton(
            onPressed: active && preview?['token'] != null ? apply : null,
            child: const Text('Apply cutoff lines'),
          ),
        ],
      ),
    );
  }
}

class CutoffPreviewPainter extends CustomPainter {
  CutoffPreviewPainter(this.entities, this.row);
  final List<Json> entities;
  final Json row;
  @override
  void paint(Canvas canvas, Size size) {
    final p = row['plate'] as Json;
    final rect = Rect.fromLTWH(
      number(p['x']),
      number(p['y']),
      number(p['width']),
      number(p['height']),
    );
    if (rect.width <= 0 || rect.height <= 0) return;
    final scale = math.min(
      (size.width - 24) / rect.width,
      (size.height - 24) / rect.height,
    );
    final matrix = Matrix4.identity()
      ..translateByDouble(
        size.width / 2 - rect.center.dx * scale,
        size.height / 2 + rect.center.dy * scale,
        0,
        1,
      )
      ..scaleByDouble(scale, -scale, 1, 1);
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawColor(Colors.white, BlendMode.src);
    for (final e in entities) {
      canvas.drawPath(
        entityPath(e).transform(matrix.storage),
        Paint()
          ..color = Colors.grey
          ..strokeWidth = 1
          ..style = PaintingStyle.stroke,
      );
    }
    canvas.drawPath(
      (Path()..addRect(rect)).transform(matrix.storage),
      Paint()
        ..color = Colors.red
        ..strokeWidth = 1
        ..style = PaintingStyle.stroke,
    );
    if (row['line'] != null) {
      canvas.drawPath(
        entityPath(row['line'] as Json).transform(matrix.storage),
        Paint()
          ..color = Colors.blue
          ..strokeWidth = 3
          ..style = PaintingStyle.stroke,
      );
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant CutoffPreviewPainter old) => true;
}
