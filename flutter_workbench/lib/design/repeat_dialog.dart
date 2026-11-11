import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'canvas.dart' show entityPath;

class RepeatDialog extends StatefulWidget {
  const RepeatDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<RepeatDialog> createState() => _RepeatDialogState();
}

class _RepeatDialogState extends State<RepeatDialog> {
  DesignController get m => widget.model;
  late final revision = m.state['revision'];
  late final fields = <String, TextEditingController>{
    'columns': TextEditingController(text: '2'),
    'rows': TextEditingController(text: '1'),
    'x': TextEditingController(text: m.units == 'mm' ? '6.35' : '0.25'),
    'y': TextEditingController(text: m.units == 'mm' ? '6.35' : '0.25'),
  };
  String spacing = 'gap', layer = '';
  int dx = 1, dy = 1;
  late int plate = (m.state['activePlate'] as num? ?? 1).toInt();
  Json? preview;
  String? message;
  bool busy = false, applying = false, stale = false, reviewed = false;
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
        message = 'Drawing changed. Reopen Step and Repeat.';
      });
    }
  }

  @override
  void dispose() {
    m.removeListener(changed);
    for (final c in fields.values) {
      c.dispose();
    }
    super.dispose();
  }

  void edit(VoidCallback f) => setState(() {
    f();
    preview = null;
    reviewed = false;
    message = null;
  });
  Future<void> calculate() async {
    final cols = int.tryParse(fields['columns']!.text),
        rows = int.tryParse(fields['rows']!.text);
    final x = double.tryParse(fields['x']!.text),
        y = double.tryParse(fields['y']!.text);
    if (cols == null ||
        rows == null ||
        cols < 1 ||
        rows < 1 ||
        cols * rows < 2 ||
        cols * rows > 400 ||
        x == null ||
        y == null ||
        !x.isFinite ||
        !y.isFinite ||
        x < 0 ||
        y < 0) {
      setState(
        () => message =
            'Use 2–400 total placements and finite nonnegative spacing.',
      );
      return;
    }
    setState(() {
      busy = true;
      preview = null;
      reviewed = false;
      message = null;
    });
    try {
      final r = await m.request('design.repeat_preview', {
        'columns': cols,
        'rows': rows,
        'x': x,
        'y': y,
        'spacing': spacing,
        'direction_x': dx,
        'direction_y': dy,
        'target_layer': layer.isEmpty ? null : layer,
        'plate_number': plate,
      });
      if (!mounted) return;
      if (r['ok'] != true) throw StateError('${r['error']}');
      if (!stale && m.state['revision'] == revision) {
        setState(() => preview = r['preview'] as Json);
      }
    } catch (e) {
      if (mounted) setState(() => message = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> apply() async {
    setState(() {
      busy = true;
      applying = true;
    });
    final ok = await m.run('design.repeat_apply', {
      'token': preview!['token'],
      'reviewed': reviewed,
    });
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() {
        busy = false;
        applying = false;
        message = m.message;
        preview = null;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final active = !busy && !stale;
    Widget choice<T>(
      String label,
      T value,
      List<DropdownMenuItem<T>> items,
      void Function(T) change,
    ) => SizedBox(
      width: 210,
      child: DropdownButtonFormField<T>(
        initialValue: value,
        isExpanded: true,
        decoration: InputDecoration(labelText: label),
        items: items,
        onChanged: active ? (v) => edit(() => change(v as T)) : null,
      ),
    );
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: const Text('Step and Repeat'),
        content: SizedBox(
          width: 760,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Repeat selected artwork only. Rows and columns include the original. Distances in ${m.units}. Operations and generated-owner links are not copied.',
                ),
                Wrap(
                  spacing: 12,
                  runSpacing: 10,
                  children: [
                    for (final id in fields.keys)
                      SizedBox(
                        width: 160,
                        child: TextField(
                          controller: fields[id],
                          enabled: active,
                          decoration: InputDecoration(
                            labelText: {
                              'columns': 'Columns',
                              'rows': 'Rows',
                              'x': 'Horizontal spacing',
                              'y': 'Vertical spacing',
                            }[id],
                          ),
                          onChanged: (_) => edit(() {}),
                        ),
                      ),
                    choice<String>('Spacing', spacing, const [
                      DropdownMenuItem(value: 'gap', child: Text('Edge gap')),
                      DropdownMenuItem(
                        value: 'distance',
                        child: Text('Origin distance'),
                      ),
                    ], (v) => spacing = v),
                    choice<int>('Horizontal direction', dx, const [
                      DropdownMenuItem(value: 1, child: Text('Right')),
                      DropdownMenuItem(value: -1, child: Text('Left')),
                    ], (v) => dx = v),
                    choice<int>('Vertical direction', dy, const [
                      DropdownMenuItem(value: 1, child: Text('Up')),
                      DropdownMenuItem(value: -1, child: Text('Down')),
                    ], (v) => dy = v),
                    choice<String>('Target layer', layer, [
                      const DropdownMenuItem(
                        value: '',
                        child: Text('Preserve source layers'),
                      ),
                      for (final l in m.layers.where(
                        (l) => l['locked'] != true && l['visible'] != false,
                      ))
                        DropdownMenuItem(
                          value: l['name'] as String,
                          child: Text('${l['name']}'),
                        ),
                    ], (v) => layer = v),
                    choice<int>('Boundary sheet', plate, [
                      for (final p in m.plates)
                        DropdownMenuItem(
                          value: p['number'] as int,
                          child: Text('Plate ${p['number']}'),
                        ),
                    ], (v) => plate = v),
                  ],
                ),
                if (busy) const LinearProgressIndicator(),
                if (message != null)
                  Text(
                    message!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (preview != null) ...[
                  Text(
                    '${preview!['count']} new objects. Blue: copies; orange: possible overlap/touching; red: beyond sheet.',
                  ),
                  for (final warning in preview!['warnings'] as List)
                    Text(
                      '$warning',
                      style: const TextStyle(color: Colors.deepOrange),
                    ),
                  SizedBox(
                    height: 220,
                    width: double.infinity,
                    child: CustomPaint(
                      painter: RepeatPreviewPainter(m.entities, preview!),
                    ),
                  ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: reviewed,
                    onChanged: active
                        ? (v) => setState(() => reviewed = v == true)
                        : null,
                    title: const Text(
                      'I reviewed placement, overlaps and stock boundaries.',
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: busy ? null : () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          OutlinedButton(
            onPressed: active ? calculate : null,
            child: const Text('Preview repeat'),
          ),
          FilledButton(
            onPressed: active && reviewed && preview != null ? apply : null,
            child: const Text('Apply repeat'),
          ),
        ],
      ),
    );
  }
}

class RepeatPreviewPainter extends CustomPainter {
  RepeatPreviewPainter(this.source, this.preview);
  final List<Json> source;
  final Json preview;
  @override
  void paint(Canvas canvas, Size size) {
    final boxes = (preview['boxes'] as List)
        .map(
          (b) => Rect.fromLTRB(
            number(b[0]),
            number(b[1]),
            number(b[2]),
            number(b[3]),
          ),
        )
        .toList();
    final p = preview['plate'] as Json;
    final plate = Rect.fromLTWH(
      number(p['x']),
      number(p['y']),
      number(p['width']),
      number(p['height']),
    );
    var bounds = plate;
    for (final b in boxes) {
      bounds = bounds.expandToInclude(b);
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
    void path(Path path, Color color) => canvas.drawPath(
      path.transform(matrix.storage),
      Paint()
        ..color = color
        ..style = PaintingStyle.stroke
        ..strokeWidth = 1,
    );
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawColor(Colors.white, BlendMode.src);
    path(Path()..addRect(plate), Colors.black);
    for (final e in source) {
      path(entityPath(e), Colors.grey);
    }
    for (final e in (preview['geometry'] as List).cast<Json>()) {
      path(entityPath(e), Colors.blue);
    }
    for (var i = 0; i < boxes.length; i++) {
      if ((preview['outside'] as List).contains(i)) {
        path(Path()..addRect(boxes[i]), Colors.red);
      } else if ((preview['overlap'] as List).contains(i)) {
        path(Path()..addRect(boxes[i]), Colors.orange);
      }
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant RepeatPreviewPainter old) => true;
}
