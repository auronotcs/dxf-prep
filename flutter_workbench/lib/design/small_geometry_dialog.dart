import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'controller.dart';
import 'shape_tools_dialog.dart';

Future<bool> recommendSmallGeometry(
  BuildContext context,
  DesignController model, {
  List<String>? entityIds,
}) async {
  if (model.state['smallGeometryReview'] != true) return false;
  final response = await model.request('design.small_scan', {
    'entity_ids': ?entityIds,
  });
  if (!context.mounted) return false;
  if (response['ok'] != true) throw StateError(response['error'].toString());
  final review = response['review'] as Json;
  final findings = (review['findings'] as List);
  if (findings.isEmpty) return false;
  final accepted = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Small geometry / DXF joins need review'),
      content: Text(
        '${findings.length} small object(s) or uncertain DXF join(s). Review their sizes and locations? These may be intentional. Nothing is deleted or repaired automatically.',
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('Not now'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('Review findings'),
        ),
      ],
    ),
  );
  if (!context.mounted ||
      accepted != true ||
      review['revision'] != model.state['revision']) {
    return false;
  }
  await showDialog<void>(
    context: context,
    barrierDismissible: false,
    builder: (_) => SmallGeometryDialog(
      model: model,
      initialReview: review,
      entityIds: entityIds,
    ),
  );
  return true;
}

class SmallGeometryDialog extends StatefulWidget {
  const SmallGeometryDialog({
    super.key,
    required this.model,
    this.initialReview,
    this.entityIds,
  });
  final DesignController model;
  final Json? initialReview;
  final List<String>? entityIds;
  @override
  State<SmallGeometryDialog> createState() => _SmallGeometryDialogState();
}

class _SmallGeometryDialogState extends State<SmallGeometryDialog> {
  DesignController get m => widget.model;
  late final TextEditingController threshold;
  Json? review;
  bool working = false, includeKept = false, isolatedOnly = false, zoom = false;
  final selected = <String>{};
  String message = 'Scan to review whole objects and uncertain DXF joins.';
  List<Json> get rows => (review?['findings'] as List? ?? []).cast<Json>();
  bool get valid =>
      review != null && review!['revision'] == m.state['revision'];

  @override
  void initState() {
    super.initState();
    threshold = TextEditingController(
      text:
          '${widget.initialReview?['maximumSize'] ?? (m.units == 'mm' ? .254 : .01)}',
    );
    review = widget.initialReview;
    m.addListener(documentChanged);
    if (review == null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) scan();
      });
    } else {
      message = '${rows.length} finding(s). Select rows to inspect or keep.';
    }
  }

  @override
  void dispose() {
    m.removeListener(documentChanged);
    threshold.dispose();
    super.dispose();
  }

  void documentChanged() {
    if (mounted && review != null && !valid) setState(invalidate);
  }

  void invalidate() {
    review = null;
    selected.clear();
    zoom = false;
    message =
        'Drawing or settings changed. Scan again before acting on findings.';
  }

  Future<void> scan() async {
    setState(() {
      working = true;
      invalidate();
    });
    try {
      final value = double.tryParse(threshold.text);
      if (value == null || !value.isFinite || value <= 0) {
        throw const FormatException('Enter a positive finite maximum size.');
      }
      final result = await m.request('design.small_scan', {
        'maximum_size': value,
        'include_kept': includeKept,
        'isolated_only': isolatedOnly,
        'entity_ids': ?widget.entityIds,
      });
      if (!mounted) return;
      if (result['ok'] != true) throw StateError(result['error'].toString());
      setState(() {
        review = result['review'] as Json;
        message = rows.isEmpty
            ? 'No findings at this threshold. Geometry is unchanged.'
            : '${rows.length} finding(s). Select rows to inspect or keep.';
      });
    } catch (error) {
      if (mounted) setState(() => message = error.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> keep() async {
    if (!valid || selected.isEmpty || m.readOnly) return;
    final token = review!['token'];
    setState(() => working = true);
    final ok = await m.run('design.small_keep', {
      'token': token,
      'entity_ids': selected.toList(),
    });
    if (!mounted) return;
    if (ok) {
      await scan();
      if (mounted) {
        setState(
          () => message = valid
              ? 'Kept the reviewed objects. Undo restores the review marks. ${rows.length} finding(s) remain.'
              : 'Kept the review marks. $message',
        );
      }
    } else {
      setState(() {
        working = false;
        invalidate();
        message = m.message;
      });
    }
  }

  Future<void> reviewedAction(bool unlock) async {
    if (!valid || selected.isEmpty || m.readOnly || working) return;
    final token = review!['token'];
    final ids = selected.toList();
    final layers = rows
        .where((r) => ids.contains(r['id']) && r['locked'] == true)
        .map((r) => r['layer'])
        .toSet();
    final accepted = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(
          unlock ? 'Unlock selected layers?' : 'Delete selected small objects?',
        ),
        content: Text(
          unlock
              ? 'Unlock ${layers.join(', ')}? This affects every object on these layers. Visibility stays unchanged. Scan again before deleting anything. One Undo restores the locks.'
              : 'Delete ${ids.length} reviewed isolated object(s)? This removes whole objects, not short segments. One Undo restores them. Attached, generated or locked geometry cannot be deleted here.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: Text(unlock ? 'Unlock layers' : 'Delete objects'),
          ),
        ],
      ),
    );
    if (!mounted || accepted != true || !valid || review!['token'] != token) {
      return;
    }
    setState(() => working = true);
    final ok = await m.run(
      unlock ? 'design.small_unlock' : 'design.small_delete',
      {'token': token, 'entity_ids': ids, 'confirmed': true},
    );
    if (!mounted) return;
    if (ok) {
      await scan();
    } else {
      setState(() {
        working = false;
        invalidate();
        message = m.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final ids = rows.map((r) => r['id']).toSet();
    final findings = m.entities.where((e) => ids.contains(e['id'])).toList();
    final highlighted = findings
        .where((e) => selected.contains(e['id']))
        .toList();
    final boxes = highlighted.map((e) => bounds(e['bounds'])).whereType<Rect>();
    final box = zoom && boxes.isNotEmpty
        ? boxes.reduce((a, b) => a.expandToInclude(b))
        : null;
    final sources = m.entities
        .where(
          (e) =>
              ids.contains(e['id']) ||
              m.layers
                  .where((layer) => layer['name'] == e['layer'])
                  .every((layer) => layer['visible'] != false),
        )
        .toList();
    final affected = rows
        .where((r) => selected.contains(r['id']))
        .expand((r) => (r['affectedOperations'] as List? ?? []).cast<String>())
        .toSet();
    return PopScope(
      canPop: !working,
      child: AlertDialog(
        title: const Text('Small geometry / Specks'),
        content: SizedBox(
          width: 900,
          height: math.min(610, MediaQuery.sizeOf(context).height - 180),
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'Review whole objects, not every short segment. Size alone does not mean junk. Deletion requires explicit review and confirmation. Attached or protected details must use their owning workflow.',
                ),
                if (widget.entityIds != null)
                  const Text(
                    'Reviewing the imported objects; surrounding geometry is considered for attachments.',
                  ),
                Wrap(
                  spacing: 12,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    SizedBox(
                      width: 220,
                      child: TextField(
                        controller: threshold,
                        enabled: !working,
                        decoration: InputDecoration(
                          labelText: 'Maximum width AND height (${m.units})',
                        ),
                        onChanged: (_) => setState(invalidate),
                      ),
                    ),
                    TextButton(
                      onPressed: working ? null : scan,
                      child: const Text('Scan'),
                    ),
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Checkbox(
                          value: includeKept,
                          onChanged: working
                              ? null
                              : (v) => setState(() {
                                  includeKept = v!;
                                  invalidate();
                                }),
                        ),
                        const Text('Include kept'),
                      ],
                    ),
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Checkbox(
                          value: isolatedOnly,
                          onChanged: working
                              ? null
                              : (v) => setState(() {
                                  isolatedOnly = v!;
                                  invalidate();
                                }),
                        ),
                        const Text('Isolated candidates only'),
                      ],
                    ),
                  ],
                ),
                Text('$message ${selected.length} selected.'),
                Wrap(
                  spacing: 8,
                  children: [
                    TextButton(
                      onPressed: working || !valid || rows.isEmpty
                          ? null
                          : () => setState(
                              () => selected.addAll(
                                rows.map((r) => r['id'] as String),
                              ),
                            ),
                      child: const Text('Select all findings'),
                    ),
                    TextButton(
                      onPressed: working || selected.isEmpty
                          ? null
                          : () => setState(() {
                              selected.clear();
                              zoom = false;
                            }),
                      child: const Text('Clear findings selection'),
                    ),
                    TextButton(
                      onPressed: working || selected.isEmpty
                          ? null
                          : () => setState(() => zoom = true),
                      child: const Text('Zoom to selected'),
                    ),
                    TextButton(
                      onPressed: working || !zoom
                          ? null
                          : () => setState(() => zoom = false),
                      child: const Text('Fit context'),
                    ),
                  ],
                ),
                SizedBox(
                  height: 260,
                  child: Row(
                    children: [
                      Expanded(
                        child: ListView.builder(
                          itemCount: rows.length,
                          itemBuilder: (_, index) {
                            final row = rows[index];
                            final id = row['id'] as String;
                            return CheckboxListTile(
                              value: selected.contains(id),
                              dense: true,
                              title: Text(
                                '${row['label']} · ${row['layer']}${row['kept'] == true ? ' · Kept' : ''}',
                              ),
                              subtitle: Text(
                                '${number(row['width']).toStringAsPrecision(5)} × ${number(row['height']).toStringAsPrecision(5)} ${m.units}\n'
                                'X ${number(row['x']).toStringAsPrecision(6)} · Y ${number(row['y']).toStringAsPrecision(6)}\n'
                                '${row['hidden'] == true ? 'Hidden layer/object. ' : ''}${row['reason']}',
                              ),
                              onChanged: working || !valid
                                  ? null
                                  : (value) => setState(() {
                                      if (value!) {
                                        selected.add(id);
                                      } else {
                                        selected.remove(id);
                                      }
                                    }),
                            );
                          },
                        ),
                      ),
                      Expanded(
                        child: ColoredBox(
                          color: Colors.white,
                          child: CustomPaint(
                            size: Size.infinite,
                            painter: ShapePreviewPainter(
                              sources,
                              findings,
                              highlight: highlighted,
                              zoomBounds: box?.inflate(
                                math.max(
                                  math.max(box.width, box.height) * .2,
                                  m.units == 'mm' ? .0254 : .001,
                                ),
                              ),
                              markers: {
                                'points': rows
                                    .where((r) => selected.contains(r['id']))
                                    .map((r) => {'x': r['x'], 'y': r['y']})
                                    .toList(),
                              },
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const Text(
                  'Blue = findings; orange = selected. Preview can show flagged hidden objects without changing layer visibility or locks.',
                ),
                for (final row in rows.where((r) => selected.contains(r['id'])))
                  Padding(
                    padding: const EdgeInsets.only(top: 8),
                    child: Text('${row['label']}: ${row['recommendation']}'),
                  ),
                if (affected.isNotEmpty)
                  Text(
                    'Dependent machining: ${affected.join(', ')}. Review its owning workflow before changing geometry.',
                  ),
                if (m.readOnly)
                  const Text(
                    'This machining job is view-only; review marks cannot be saved here.',
                  ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: working ? null : () => Navigator.pop(context),
            child: const Text('Close'),
          ),
          if (m.state['smallGeometryActions'] == true) ...[
            TextButton(
              onPressed:
                  working ||
                      !valid ||
                      m.readOnly ||
                      !rows.any(
                        (r) =>
                            selected.contains(r['id']) && r['locked'] == true,
                      )
                  ? null
                  : () => reviewedAction(true),
              child: const Text('Unlock selected layers'),
            ),
            TextButton(
              onPressed:
                  working ||
                      !valid ||
                      m.readOnly ||
                      selected.isEmpty ||
                      rows
                          .where((r) => selected.contains(r['id']))
                          .any(
                            (r) =>
                                r['isolated'] != true ||
                                (r['affectedOperations'] as List? ?? [])
                                    .isNotEmpty,
                          )
                  ? null
                  : () => reviewedAction(false),
              child: const Text('Delete selected'),
            ),
          ],
          FilledButton(
            onPressed: working || !valid || selected.isEmpty || m.readOnly
                ? null
                : keep,
            child: const Text('Keep selected'),
          ),
        ],
      ),
    );
  }
}
