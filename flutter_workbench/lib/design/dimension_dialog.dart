import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'canvas.dart' show entityPath;
import 'dimension_painter.dart';

Future<void> manageDimensions(
  BuildContext context,
  DesignController model,
) async {
  await showDialog<void>(
    context: context,
    builder: (context) => AnimatedBuilder(
      animation: model,
      builder: (context, _) {
        final dimensions = (model.state['dimensions'] as List? ?? [])
            .cast<Json>();
        return AlertDialog(
          title: const Text('Shop drawing dimensions'),
          content: SizedBox(
            width: 660,
            height: 360,
            child: dimensions.isEmpty
                ? const Center(
                    child: Text(
                      'No dimensions yet. Add one here, or use Create → Dimension: pick points.',
                    ),
                  )
                : ListView(
                    children: [
                      for (final d in dimensions)
                        ListTile(
                          title: Text(d['label'] as String),
                          subtitle: Text(
                            '${d['annotation']['kind']} · ${d['annotation']['layer']}',
                          ),
                          trailing: Wrap(
                            children: [
                              IconButton(
                                tooltip: 'Edit dimension',
                                icon: const Icon(Icons.edit_outlined),
                                onPressed: model.readOnly || model.busy
                                    ? null
                                    : () => showDialog<void>(
                                        context: context,
                                        barrierDismissible: false,
                                        builder: (_) => DimensionDialog(
                                          model: model,
                                          annotation: d['annotation'] as Json,
                                        ),
                                      ),
                              ),
                              IconButton(
                                tooltip: 'Delete dimension',
                                icon: const Icon(Icons.delete_outline),
                                onPressed: model.readOnly || model.busy
                                    ? null
                                    : () async {
                                        final confirmed =
                                            await showDialog<bool>(
                                              context: context,
                                              builder: (context) => AlertDialog(
                                                title: const Text(
                                                  'Delete this dimension?',
                                                ),
                                                content: Text(
                                                  d['label'] as String,
                                                ),
                                                actions: [
                                                  TextButton(
                                                    onPressed: () =>
                                                        Navigator.pop(
                                                          context,
                                                          false,
                                                        ),
                                                    child: const Text('Cancel'),
                                                  ),
                                                  FilledButton(
                                                    onPressed: () =>
                                                        Navigator.pop(
                                                          context,
                                                          true,
                                                        ),
                                                    child: const Text('Delete'),
                                                  ),
                                                ],
                                              ),
                                            );
                                        if (confirmed == true) {
                                          await model.run(
                                            'design.dimension_delete',
                                            {'id': d['id']},
                                          );
                                        }
                                      },
                              ),
                            ],
                          ),
                        ),
                    ],
                  ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context),
              child: const Text('Close'),
            ),
            FilledButton(
              onPressed: model.readOnly || model.busy
                  ? null
                  : () => showDialog<void>(
                      context: context,
                      barrierDismissible: false,
                      builder: (_) => DimensionDialog(model: model),
                    ),
              child: const Text('New dimension'),
            ),
          ],
        );
      },
    ),
  );
}

class DimensionDialog extends StatefulWidget {
  const DimensionDialog({
    super.key,
    required this.model,
    this.annotation,
    this.references,
  });
  final DesignController model;
  final Json? annotation;
  final List<Json>? references;
  @override
  State<DimensionDialog> createState() => _DimensionDialogState();
}

class _DimensionDialogState extends State<DimensionDialog> {
  DesignController get m => widget.model;
  final fields = <String, TextEditingController>{};
  final refs = <String, Json>{};
  List<Json> candidates = [];
  String kind = 'aligned',
      angleMode = 'minor',
      message = 'Preview the dimension before applying.';
  int precision = 3;
  bool working = false;
  Json? preview;
  @override
  void initState() {
    super.initState();
    final a = widget.annotation;
    final factor = m.units == 'mm' ? 25.4 : 1.0;
    kind = a?['kind'] ?? 'aligned';
    precision = a?['precision'] ?? 3;
    angleMode = a?['metadata']?['angle_mode'] ?? 'minor';
    for (final entry in {
      'offset': a?['offset'] ?? .5 * factor,
      'text_height': a?['text_height'] ?? .18 * factor,
      'extension_offset': a?['extension_offset'] ?? .0625 * factor,
      'layer': a?['layer'] ?? 'DIMENSIONS',
      'text': a?['metadata']?['text'] ?? '',
    }.entries) {
      fields[entry.key] = TextEditingController(text: '${entry.value}');
    }
    for (final key in ['start', 'end', 'vertex']) {
      final index = key == 'start' ? 0 : 1;
      final ref =
          a?[key] as Json? ??
          (key != 'vertex' && widget.references != null
              ? widget.references![index]
              : null) ??
          {
            'entity_id': '',
            'feature': 'free',
            'fallback': {
              'x': key == 'end' ? factor : 0.0,
              'y': key == 'vertex' ? -factor : 0.0,
            },
          };
      refs[key] = Map<String, dynamic>.from(ref);
      fields['${key}X'] = TextEditingController(
        text: '${ref['fallback']['x']}',
      );
      fields['${key}Y'] = TextEditingController(
        text: '${ref['fallback']['y']}',
      );
    }
    m.addListener(changed);
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      try {
        final response = await m.request('design.dimension_context');
        if (mounted && response['ok'] == true) {
          setState(
            () => candidates = (response['references'] as List).cast<Json>(),
          );
        }
      } catch (e) {
        if (mounted) setState(() => message = e.toString());
      }
    });
  }

  @override
  void dispose() {
    m.removeListener(changed);
    for (final f in fields.values) {
      f.dispose();
    }
    super.dispose();
  }

  void changed() {
    if (mounted &&
        preview != null &&
        preview!['revision'] != m.state['revision']) {
      setState(invalidate);
    }
  }

  void invalidate() {
    preview = null;
    message = 'Preview again to review the changes.';
  }

  double value(String key) {
    final v = double.tryParse(fields[key]!.text);
    if (v == null || !v.isFinite) {
      throw FormatException('$key: enter a finite number.');
    }
    return v;
  }

  Json reference(String key) => {
    ...refs[key]!,
    'fallback': {'x': value('${key}X'), 'y': value('${key}Y')},
  };
  Future<void> prepare() async {
    setState(() {
      working = true;
      preview = null;
    });
    try {
      final result = await m.request('design.dimension_preview', {
        'id': ?widget.annotation?['id'],
        'annotation': {
          'kind': kind,
          'start': reference('start'),
          'end': reference('end'),
          if (kind == 'angle') 'vertex': reference('vertex'),
          'offset': value('offset'),
          'text_height': value('text_height'),
          'extension_offset': value('extension_offset'),
          'precision': precision,
          'layer': fields['layer']!.text.trim(),
          'metadata': {
            ...?widget.annotation?['metadata'] as Json?,
            'angle_mode': angleMode,
            'text': fields['text']!.text,
          },
        },
      });
      if (!mounted) return;
      if (result['ok'] != true) throw StateError(result['error'].toString());
      setState(() {
        preview = result['preview'] as Json;
        message = preview!['dimension']['label'] as String;
      });
    } catch (e) {
      if (mounted) setState(() => message = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> apply() async {
    if (preview == null || working) return;
    setState(() => working = true);
    final ok = await m.run('design.dimension_apply', {
      'token': preview!['token'],
    });
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() {
        working = false;
        invalidate();
        message = m.message;
      });
    }
  }

  Widget field(
    String key,
    String label, {
    bool enabled = true,
    double width = 155,
    VoidCallback? edited,
  }) => SizedBox(
    width: width,
    child: TextField(
      key: ValueKey('dimension-$key'),
      controller: fields[key],
      enabled: enabled && !working,
      decoration: InputDecoration(labelText: label),
      onChanged: (_) {
        setState(() {
          edited?.call();
          invalidate();
        });
      },
    ),
  );
  Widget choice(
    String label,
    String current,
    List<String> options,
    void Function(String) update,
  ) => SizedBox(
    width: 180,
    child: DropdownButtonFormField<String>(
      key: ValueKey('$label:$current'),
      initialValue: current,
      isExpanded: true,
      decoration: InputDecoration(labelText: label),
      items: options
          .map((v) => DropdownMenuItem(value: v, child: Text(v)))
          .toList(),
      onChanged: working
          ? null
          : (v) => setState(() {
              update(v!);
              invalidate();
            }),
    ),
  );
  Widget referenceFields(String key, String label) {
    final ref = refs[key]!;
    final linked = (ref['entity_id'] as String? ?? '').isNotEmpty;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('$label${linked ? ' · linked to ${ref['feature']}' : ''}'),
        Wrap(
          spacing: 10,
          runSpacing: 8,
          children: [
            field('${key}X', 'X (${m.units})', enabled: !linked),
            field('${key}Y', 'Y (${m.units})', enabled: !linked),
            PopupMenuButton<int>(
              enabled: !working,
              tooltip: 'Choose reference',
              onSelected: (index) => setState(() {
                if (index < 0) {
                  refs[key] = {'entity_id': '', 'feature': 'free'};
                } else {
                  final chosen = candidates[index]['reference'] as Json;
                  refs[key] = Map<String, dynamic>.from(chosen);
                  fields['${key}X']!.text = '${chosen['fallback']['x']}';
                  fields['${key}Y']!.text = '${chosen['fallback']['y']}';
                }
                invalidate();
              }),
              itemBuilder: (_) => [
                const PopupMenuItem(value: -1, child: Text('Free coordinates')),
                for (var i = 0; i < candidates.length; i++)
                  PopupMenuItem(
                    value: i,
                    child: Text(candidates[i]['label'] as String),
                  ),
              ],
              child: const Padding(
                padding: EdgeInsets.all(12),
                child: Text('Choose reference ▾'),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
      ],
    );
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !working,
    child: AlertDialog(
      title: Text(
        widget.annotation == null
            ? 'New shop dimension'
            : 'Edit shop dimension',
      ),
      content: SizedBox(
        width: 850,
        height: math.min(660, MediaQuery.sizeOf(context).height - 180),
        child: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'Dimensions annotate the drawing; they are not cutting geometry. Select geometry before opening to choose its linked features.',
              ),
              Wrap(
                spacing: 12,
                runSpacing: 8,
                children: [
                  choice('Type', kind, [
                    'horizontal',
                    'vertical',
                    'aligned',
                    'radius',
                    'diameter',
                    'angle',
                    'leader',
                    'note',
                  ], (v) => kind = v),
                  choice('Decimal places', '$precision', [
                    for (var i = 0; i <= 6; i++) '$i',
                  ], (v) => precision = int.parse(v)),
                  if (kind == 'angle')
                    choice('Angle side', angleMode, [
                      'minor',
                      'reflex',
                    ], (v) => angleMode = v),
                ],
              ),
              const SizedBox(height: 12),
              if (kind != 'note')
                referenceFields(
                  'start',
                  kind == 'radius'
                      ? 'Center'
                      : kind == 'angle'
                      ? 'First ray point'
                      : 'Start / anchor',
                ),
              referenceFields(
                'end',
                kind == 'note' || kind == 'leader'
                    ? 'Text landing'
                    : kind == 'radius'
                    ? 'Point on circumference'
                    : kind == 'angle'
                    ? 'Second ray point'
                    : 'End',
              ),
              if (kind == 'angle') referenceFields('vertex', 'Angle vertex'),
              if (kind == 'diameter')
                const Text(
                  'Choose opposite points on the circle, not its center.',
                ),
              Wrap(
                spacing: 12,
                runSpacing: 8,
                children: [
                  field(
                    'offset',
                    'Line offset / arc radius (${m.units})',
                    width: 220,
                  ),
                  field('text_height', 'Text height (${m.units})'),
                  field('extension_offset', 'Extension gap (${m.units})'),
                  field('layer', 'Annotation layer'),
                  if (kind == 'leader' || kind == 'note')
                    field('text', 'Annotation text', width: 600),
                ],
              ),
              const SizedBox(height: 12),
              Text(message),
              if (preview != null)
                SizedBox(
                  height: 230,
                  width: 800,
                  child: ColoredBox(
                    color: Colors.white,
                    child: CustomPaint(
                      painter: DimensionPreview(
                        m.entities.where(m.visible).toList(),
                        preview!['dimension'] as Json,
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ),
      ),
      actions: [
        TextButton(
          onPressed: working ? null : () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: working || m.readOnly ? null : prepare,
          child: const Text('Preview'),
        ),
        FilledButton(
          onPressed: working || preview == null || m.readOnly ? null : apply,
          child: const Text('Apply'),
        ),
      ],
    ),
  );
}

class DimensionPreview extends CustomPainter {
  DimensionPreview(this.entities, this.dimension);
  final List<Json> entities;
  final Json dimension;
  @override
  void paint(Canvas canvas, Size size) {
    final boxes = [
      bounds(dimension['bounds']),
      ...entities.map((e) => bounds(e['bounds'])),
    ].whereType<Rect>().toList();
    if (boxes.isEmpty) return;
    final box = boxes.reduce((a, b) => a.expandToInclude(b));
    final scale = math.min(
      (size.width - 30) / math.max(box.width, .001),
      (size.height - 30) / math.max(box.height, .001),
    );
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.translate(size.width / 2, size.height / 2);
    canvas.scale(scale, -scale);
    canvas.translate(-box.center.dx, -box.center.dy);
    for (final entity in entities) {
      canvas.drawPath(
        entityPath(entity),
        Paint()
          ..color = Colors.grey
          ..style = PaintingStyle.stroke
          ..strokeWidth = 1 / scale,
      );
    }
    paintDimension(canvas, dimension, scale);
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant DimensionPreview oldDelegate) => true;
}
