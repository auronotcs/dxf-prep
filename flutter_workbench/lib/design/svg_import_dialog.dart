import 'package:flutter/material.dart';
import 'controller.dart';
import 'canvas.dart' show entityPath;

class SvgImportDialog extends StatefulWidget {
  const SvgImportDialog({
    super.key,
    required this.model,
    required this.path,
    this.newDocument = false,
  });
  final DesignController model;
  final String path;
  final bool newDocument;
  @override
  State<SvgImportDialog> createState() => _SvgImportDialogState();
}

class _SvgImportDialogState extends State<SvgImportDialog> {
  DesignController get m => widget.model;
  late final Object? revision;
  Json? data, preview;
  String? error;
  String placement = 'original';
  bool busy = false, stale = false, reviewed = false;
  final x = TextEditingController(text: '0'),
      y = TextEditingController(text: '0'),
      tolerance = TextEditingController();
  final camera = TransformationController();
  @override
  void initState() {
    super.initState();
    revision = m.state['revision'];
    m.addListener(changed);
    begin();
  }

  @override
  void dispose() {
    m.removeListener(changed);
    x.dispose();
    y.dispose();
    tolerance.dispose();
    camera.dispose();
    super.dispose();
  }

  void changed() {
    if (m.state['revision'] != revision || !m.ready) {
      setState(() {
        stale = true;
        preview = null;
        reviewed = false;
        error = 'Drawing changed. Close and reopen SVG import.';
      });
    }
  }

  void edit(VoidCallback callback) => setState(() {
    callback();
    preview = null;
    reviewed = false;
    error = null;
    camera.value = Matrix4.identity();
  });
  Future<void> begin() async {
    setState(() => busy = true);
    try {
      final result = await m.request('design.svg_begin', {'path': widget.path});
      if (result['ok'] != true) throw StateError('${result['error']}');
      if (!mounted || stale) return;
      data = result['context'] as Json;
      tolerance.text = '${data!['tolerance']}';
      if (data!['plate'] != null) {
        x.text = '${data!['plate'][0]}';
        y.text = '${data!['plate'][1]}';
      }
      await calculate();
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  double dimension(TextEditingController field) {
    final n = double.tryParse(field.text);
    if (n == null || !n.isFinite) {
      throw StateError('Enter finite dimensions in ${m.units}.');
    }
    return n;
  }

  Future<void> calculate() async {
    setState(() {
      busy = true;
      preview = null;
      reviewed = false;
      error = null;
      camera.value = Matrix4.identity();
    });
    try {
      final result = await m.request('design.svg_preview', {
        'context_token': data!['context_token'],
        'placement': placement,
        'x': placement == 'custom' ? dimension(x) : 0,
        'y': placement == 'custom' ? dimension(y) : 0,
        'tolerance': dimension(tolerance),
      });
      if (result['ok'] != true) throw StateError('${result['error']}');
      if (mounted && !stale) {
        setState(() => preview = result['preview'] as Json);
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> apply() async {
    final token = preview!['token'];
    setState(() {
      busy = true;
      error = null;
    });
    final ok = await m.run('design.svg_apply', {
      'token': token,
      'reviewed': reviewed,
    });
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context, true);
    } else {
      setState(() {
        busy = false;
        preview = null;
        reviewed = false;
        error = m.message;
      });
    }
  }

  String physical(dynamic value) => number(value).toStringAsFixed(4);
  @override
  Widget build(BuildContext context) {
    final active = !busy && !stale;
    final b = preview?['bounds'] as List?;
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: Text(
          widget.newDocument
              ? 'Open SVG in new drawing'
              : 'Import SVG geometry',
        ),
        content: SizedBox(
          width: 900,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(data?['filename'] ?? widget.path),
                Text(
                  widget.newDocument
                      ? 'Review editable contours and physical size in ${m.units}. This creates a separate unsaved drawing; your open drawings stay in place. SVG page coordinates use a lower-left origin with Y pointing up.'
                      : 'Review editable contours and physical size. SVG page coordinates use a lower-left origin with Y pointing up. Existing drawing geometry and settings stay in place.',
                ),
                if (busy) const LinearProgressIndicator(),
                if (error != null)
                  Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (data != null) ...[
                  DropdownButtonFormField<String>(
                    initialValue: placement,
                    isExpanded: true,
                    decoration: const InputDecoration(labelText: 'Placement'),
                    items: [
                      const DropdownMenuItem(
                        value: 'original',
                        child: Text('Keep SVG page coordinates'),
                      ),
                      DropdownMenuItem(
                        value: 'plate',
                        enabled: data!['plate'] != null,
                        child: const Text(
                          'Geometry lower-left at active plate',
                        ),
                      ),
                      const DropdownMenuItem(
                        value: 'custom',
                        child: Text('Specify geometry lower-left'),
                      ),
                    ],
                    onChanged: active
                        ? (v) => edit(() => placement = v!)
                        : null,
                  ),
                  Wrap(
                    spacing: 12,
                    runSpacing: 8,
                    children: [
                      if (placement == 'custom') ...[
                        SizedBox(
                          width: 180,
                          child: TextField(
                            controller: x,
                            enabled: active,
                            decoration: InputDecoration(
                              labelText: 'Lower-left X',
                              suffixText: m.units,
                            ),
                            onChanged: (_) => edit(() {}),
                          ),
                        ),
                        SizedBox(
                          width: 180,
                          child: TextField(
                            controller: y,
                            enabled: active,
                            decoration: InputDecoration(
                              labelText: 'Lower-left Y',
                              suffixText: m.units,
                            ),
                            onChanged: (_) => edit(() {}),
                          ),
                        ),
                      ],
                      SizedBox(
                        width: 230,
                        child: TextField(
                          controller: tolerance,
                          enabled: active,
                          decoration: InputDecoration(
                            labelText: 'Curve tolerance',
                            suffixText: m.units,
                          ),
                          onChanged: (_) => edit(() {}),
                        ),
                      ),
                    ],
                  ),
                  Text(
                    'Drawing units: ${m.units}. Tolerance range ${data!['tolerance_range'][0]}–${data!['tolerance_range'][1]} ${m.units}; smaller values add more curve points.',
                  ),
                ],
                if (preview != null && b != null) ...[
                  const SizedBox(height: 10),
                  Text(
                    '${preview!['closed']} closed / ${preview!['open']} open contours · geometry ${physical(number(b[2]) - number(b[0]))} × ${physical(number(b[3]) - number(b[1]))} ${m.units}',
                  ),
                  Text(
                    'SVG page: ${physical(preview!['page_size'][0])} × ${physical(preview!['page_size'][1])} ${m.units}. Geometry lower-left: ${physical(b[0])}, ${physical(b[1])} ${m.units}.',
                  ),
                  Row(
                    children: [
                      const Expanded(
                        child: Text(
                          'Teal: closed contours · Orange: open contours',
                        ),
                      ),
                      TextButton(
                        onPressed: () =>
                            setState(() => camera.value = Matrix4.identity()),
                        child: const Text('Fit preview'),
                      ),
                    ],
                  ),
                  LayoutBuilder(
                    builder: (context, c) => SizedBox(
                      height: 280,
                      child: InteractiveViewer(
                        transformationController: camera,
                        minScale: .5,
                        maxScale: 20,
                        child: SizedBox(
                          width: c.maxWidth,
                          height: 280,
                          child: CustomPaint(
                            key: const ValueKey('svg-preview'),
                            painter: SvgPreviewPainter(
                              (preview!['entities'] as List).cast<Json>(),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ),
                  for (final warning in (preview!['warnings'] as List))
                    Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text('$warning'),
                    ),
                  for (final entry
                      in (preview!['renamed_layers'] as Json).entries)
                    Text(
                      'Protected layer ${entry.key}: incoming geometry will use ${entry.value}.',
                    ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    value: reviewed,
                    onChanged: active
                        ? (v) => setState(() => reviewed = v == true)
                        : null,
                    title: const Text(
                      'I reviewed dimensions, contours and omitted artwork notices',
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: busy ? null : () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          OutlinedButton(
            onPressed: active && data != null ? calculate : null,
            child: const Text('Preview placement'),
          ),
          FilledButton(
            onPressed: active && preview != null && reviewed ? apply : null,
            child: Text(
              widget.newDocument ? 'Open drawing tab' : 'Import geometry',
            ),
          ),
        ],
      ),
    );
  }
}

class SvgPreviewPainter extends CustomPainter {
  SvgPreviewPainter(this.entities);
  final List<Json> entities;
  @override
  void paint(Canvas canvas, Size size) {
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.drawColor(Colors.white, BlendMode.src);
    final paths = entities.map(entityPath).toList();
    if (paths.isEmpty) {
      canvas.restore();
      return;
    }
    var bounds = paths.first.getBounds();
    for (final path in paths.skip(1)) {
      bounds = bounds.expandToInclude(path.getBounds());
    }
    final sx = (size.width - 32) / bounds.width.clamp(.001, double.infinity),
        sy = (size.height - 32) / bounds.height.clamp(.001, double.infinity);
    final scale = sx < sy ? sx : sy;
    canvas.translate(size.width / 2, size.height / 2);
    canvas.scale(scale, -scale);
    canvas.translate(-bounds.center.dx, -bounds.center.dy);
    for (var i = 0; i < paths.length; i++) {
      canvas.drawPath(
        paths[i],
        Paint()
          ..color = entities[i]['closed'] == true
              ? const Color(0xff008577)
              : const Color(0xffb45d00)
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1.2 / scale,
      );
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant SvgPreviewPainter oldDelegate) =>
      oldDelegate.entities != entities;
}
