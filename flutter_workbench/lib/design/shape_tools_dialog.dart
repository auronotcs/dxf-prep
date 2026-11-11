import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'canvas.dart';
import 'controller.dart';

const arrangementActions = {
  'align_left': 'Align left',
  'align_horizontal_center': 'Center horizontally',
  'align_right': 'Align right',
  'align_bottom': 'Align bottom',
  'align_vertical_center': 'Center vertically',
  'align_top': 'Align top',
  'align_both_centers': 'Center both',
  'align_top_left': 'Align top left',
  'align_top_right': 'Align top right',
  'align_bottom_left': 'Align bottom left',
  'align_bottom_right': 'Align bottom right',
  'space_horizontal_equal': 'Equal horizontal spacing',
  'space_vertical_equal': 'Equal vertical spacing',
  'space_horizontal_gap': 'Exact horizontal gap',
  'space_vertical_gap': 'Exact vertical gap',
};

/// A proposal is held by the backend and applied only against its original revision.
class ShapeToolsDialog extends StatefulWidget {
  const ShapeToolsDialog({
    super.key,
    required this.controller,
    required this.tool,
    this.openingReview = false,
  });
  final DesignController controller;
  final String tool;
  final bool openingReview;

  @override
  State<ShapeToolsDialog> createState() => _ShapeToolsDialogState();
}

class _ShapeToolsDialogState extends State<ShapeToolsDialog> {
  final fields = <String, TextEditingController>{};
  late String mode;
  String scope = 'connected';
  Json? proposal;
  String message = 'Choose settings, then preview the result.';
  bool working = false;
  bool get offset => widget.tool == 'outline';
  bool get outlines => widget.tool == 'editableOutline';
  bool get arrange => arrangementActions.containsKey(widget.tool);
  bool get repair =>
      ['joinRepair', 'backtracks', 'nearPoints'].contains(widget.tool);
  late String arrangeAction;
  String reference = 'selection';
  bool useBodyBounds = true;
  String? fixedItem;
  final referenceItems = <String, String>{};
  final lockedItems = <String, String>{};
  String? lockedItem;
  final patchworkItems = <String, String>{};
  String? patchworkItem;
  String repairMode = 'join';
  bool cleanup = true, backtracks = true;
  DesignController get m => widget.controller;
  late final List<Json> sources;

  @override
  void initState() {
    super.initState();
    mode = outlines ? 'outline' : 'repair';
    arrangeAction = arrange ? widget.tool : 'align_left';
    repairMode = widget.tool == 'nearPoints'
        ? 'points'
        : widget.tool == 'backtracks'
        ? 'backtracks'
        : 'join';
    final assemblies = arrange
        ? (m.state['arrangementItems'] as List?)?.cast<Json>()
        : null;
    final sourceIds = assemblies == null
        ? m.selection
        : assemblies
              .expand((item) => (item['entityIds'] as List).cast<String>())
              .toSet();
    sources = m.entities.where((e) => sourceIds.contains(e['id'])).toList();
    if (assemblies != null) {
      for (final item in assemblies) {
        referenceItems[item['id'] as String] = item['label'] as String;
      }
    }
    for (final e in assemblies == null ? sources : <Json>[]) {
      final group = e['metadata']?['group_id'];
      final key = (group ?? e['id']).toString();
      final box = bounds(e['bounds']);
      referenceItems.putIfAbsent(
        key,
        () =>
            '${referenceItems.length + 1}. ${group != null ? 'Group' : e['kind']} · ${e['metadata']?['name'] ?? e['layer'] ?? '0'}'
            '${box == null ? '' : ' · at ${box.center.dx.toStringAsFixed(3)}, ${box.center.dy.toStringAsFixed(3)}'}',
      );
    }
    fixedItem = referenceItems.keys.firstOrNull;
    for (final item
        in (m.state['lockedArrangementReferences'] as List? ?? [])
            .cast<Json>()) {
      if ((item['entityIds'] as List).any(m.selection.contains)) continue;
      final box = bounds(item['bounds']);
      lockedItems[item['id'] as String] =
          '${item['label']}'
          '${box == null ? '' : ' · at ${box.center.dx.toStringAsFixed(3)}, ${box.center.dy.toStringAsFixed(3)}'}';
    }
    lockedItem = lockedItems.keys.firstOrNull;
    for (final a in (m.state['patchworkAreas'] as List? ?? []).cast<Json>()) {
      patchworkItems[a['id'] as String] =
          '${a['name']} · Plate ${a['plate_number']} · ${a['width']} × ${a['height']} ${m.units}';
    }
    patchworkItem = patchworkItems.keys.firstOrNull;
    final factor = m.units == 'mm' ? 25.4 : 1.0;
    for (final entry in {
      'distance': '${0.125 * factor}',
      'tolerance': '${(repairMode == 'points' ? .0001 : .001) * factor}',
      'gap': '${.25 * factor}',
      'diameter': '',
      'width': '',
      'height': '',
    }.entries) {
      fields[entry.key] = TextEditingController(text: entry.value);
    }
  }

  @override
  void dispose() {
    for (final field in fields.values) {
      field.dispose();
    }
    super.dispose();
  }

  void invalidate() => setState(() {
    proposal = null;
    message = 'Settings changed. Preview again before applying.';
  });

  Future<void> preview() async {
    setState(() {
      working = true;
      proposal = null;
    });
    try {
      final params = <String, dynamic>{
        'kind': arrange
            ? 'arrange'
            : repair
            ? 'repair'
            : offset
            ? 'offset'
            : 'convert',
      };
      if (arrange) {
        params.addAll({
          'action': arrangeAction,
          'reference': reference,
          'use_body_bounds': useBodyBounds,
          if (reference == 'object') 'reference_item_id': fixedItem,
          if (reference == 'locked') 'reference_item_id': lockedItem,
          if (reference == 'patchwork') 'patchwork_id': patchworkItem,
        });
      } else if (repair) {
        params.addAll({
          'mode': repairMode,
          'cleanup': cleanup,
          'backtracks': backtracks,
          if (widget.openingReview) 'opening_review': true,
        });
      } else if (!offset) {
        params.addAll({'mode': mode, 'scope': outlines ? 'each' : scope});
      }
      final keys = arrange
          ? [if (arrangeAction.endsWith('_gap')) 'gap']
          : repair
          ? ['tolerance']
          : offset
          ? ['distance']
          : [
              'tolerance',
              if (mode == 'circle') 'diameter',
              if (mode == 'rectangle') ...['width', 'height'],
            ];
      for (final key in keys) {
        final text = fields[key]!.text.trim();
        if (text.isEmpty && !['distance', 'tolerance', 'gap'].contains(key)) {
          continue;
        }
        final value = double.tryParse(text);
        if (value == null || !value.isFinite) {
          throw FormatException('Enter a finite number for $key.');
        }
        params[key] = value;
      }
      final response = await m.request('design.preview_shapes', params);
      if (!mounted) return;
      setState(() {
        if (response['ok'] == true) {
          proposal = response['preview'] as Json;
          message = arrange
              ? '${proposal!['movedCount']} object(s) move together in their associated groups. ${proposal!['gap'] == null ? '' : 'Gap: ${number(proposal!['gap']).toStringAsPrecision(6)} ${m.units}.'} Purple = reference bounds.'
              : repair
              ? '${proposal!['sourceCount']} source object(s) → ${(proposal!['entities'] as List).length} contour(s); ${proposal!['closedCount']} closed. '
                    '${proposal!['joinCount']} join(s), ${proposal!['removedPoints']} point(s) removed, ${proposal!['backtrackCount']} backtrack(s). '
                    '${repairMode == 'points' ? 'Maximum displacement' : 'Largest gap'}: ${number(proposal!['maximumError']).toStringAsPrecision(5)} ${m.units}. '
                    '${proposal!['removedCount']} generated dependent(s) removed. '
                    '${number(proposal!['remainingIssues']) > 0 ? 'WARNING: ${proposal!['remainingIssues']} crossing/overlap issue(s) remain. Inspect the result before using it.' : ''}'
              : '${proposal!['sourceCount']} source object(s) → ${(proposal!['entities'] as List).length} ${offset ? 'new outline(s)' : 'shape(s)'}. '
                    '${proposal!['removedCount']} generated dependent(s) removed. '
                    'Maximum repair deviation: ${number(proposal!['maximumError']).toStringAsPrecision(5)} ${m.units}.';
        } else {
          message = response['error'].toString();
        }
      });
    } catch (e) {
      if (mounted) setState(() => message = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> apply() async {
    setState(() => working = true);
    final success = await m.run('design.apply_shapes', {
      'token': proposal!['token'],
    });
    if (!mounted) return;
    if (success) {
      Navigator.pop(context);
    } else {
      setState(() {
        working = false;
        proposal = null;
        message = m.message;
      });
    }
  }

  Widget field(String key, String label) => Padding(
    padding: const EdgeInsets.only(bottom: 12),
    child: TextField(
      controller: fields[key],
      enabled: !working,
      keyboardType: const TextInputType.numberWithOptions(
        decimal: true,
        signed: true,
      ),
      decoration: InputDecoration(
        labelText: '$label (${m.units})',
        border: const OutlineInputBorder(),
        isDense: true,
      ),
      onChanged: (_) => invalidate(),
    ),
  );

  @override
  Widget build(BuildContext context) {
    final help = arrange
        ? 'Groups move together. A stationary reference never moves, including a locked object/group. Spacing counts the reference as one item: equal spacing needs three, exact gaps need two. Plate gaps start at its left or bottom edge and can extend beyond the plate.'
        : repair
        ? switch (repairMode) {
            'points' =>
              'Collapse near-duplicate polyline vertices within the tolerance. Open endpoints stay in place; separate contours are not joined.',
            'backtracks' =>
              'Remove microscopic backtracks only. Separate contours are not joined and ordinary short edges are preserved.',
            _ =>
              'Join unambiguous line/polyline endpoints on the same layer and in the same group. Native circles and rectangles stay unchanged. Convert text to curves first.',
          }
        : offset
        ? 'Positive distance adds an outside outline; negative adds an inside outline. Originals stay in the drawing.'
        : switch (mode) {
            'repair' =>
              'Recognize a complete circle from connected pieces. Partial arcs, branches and noncircular paths are rejected.',
            'circle' =>
              'Reshape around the bounds center. Blank diameter uses the smaller bounds dimension.',
            'rectangle' =>
              'Reshape to an axis-aligned rectangle around the bounds center. Blank sizes retain the bounds dimensions.',
            _ =>
              'Convert circles, rectangles and lines to editable polylines. Existing polylines stay unchanged. Convert text separately with Text to curves.',
          };
    return PopScope(
      canPop: !working,
      child: AlertDialog(
        title: Text(
          arrange
              ? 'Align & space'
              : repair
              ? 'Join / repair contours'
              : offset
              ? 'Offset outline / inline'
              : outlines
              ? 'Editable outlines'
              : 'Convert to shape',
        ),
        content: SizedBox(
          width: 680,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              mainAxisSize: MainAxisSize.min,
              children: [
                if (arrange) ...[
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Use original body bounds'),
                    subtitle: const Text(
                      'Ignore generated tabs when aligning or spacing tabbed letters. Associated pieces move together.',
                    ),
                    value: useBodyBounds,
                    onChanged: working
                        ? null
                        : (value) {
                            useBodyBounds = value!;
                            invalidate();
                          },
                  ),
                  choice(
                    'Operation',
                    arrangeAction,
                    Map.fromEntries(
                      arrangementActions.entries.where(
                        (e) =>
                            widget.controller.state['arrangementCorners'] ==
                                true ||
                            !{
                              'align_top_left',
                              'align_top_right',
                              'align_bottom_left',
                              'align_bottom_right',
                            }.contains(e.key),
                      ),
                    ),
                    (v) => arrangeAction = v,
                  ),
                  choice('Reference', reference, {
                    'selection': 'Selection bounds',
                    'object': 'Fixed selected object',
                    if ((m.state['capabilities'] as List? ?? []).contains(
                      'locked_arrangement_reference',
                    ))
                      'locked': 'Locked object / group',
                    'plate': 'Active plate',
                    if ((m.state['capabilities'] as List? ?? []).contains(
                      'patchwork',
                    ))
                      'patchwork': 'Patchwork cut area',
                  }, (v) => reference = v),
                  if (reference == 'patchwork')
                    if (patchworkItem != null)
                      choice(
                        'Cut area',
                        patchworkItem!,
                        patchworkItems,
                        (v) => patchworkItem = v,
                      )
                    else
                      const Text(
                        'Define cut areas in Macros > Patchwork, then reopen Align & space.',
                      ),
                  if (reference == 'object' && fixedItem != null)
                    choice(
                      'Stationary object / group',
                      fixedItem!,
                      referenceItems,
                      (v) => fixedItem = v,
                    ),
                  if (reference == 'locked')
                    if (lockedItem != null)
                      choice(
                        'Stationary locked object / group',
                        lockedItem!,
                        lockedItems,
                        (v) => lockedItem = v,
                      )
                    else
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 12),
                        child: Text(
                          'No visible locked references are available. Lock a reference layer in Layers, then reopen Align & space. All group members must be visible and outside the moving selection.',
                        ),
                      ),
                ],
                if (repair) ...[
                  choice(
                    'Repair mode',
                    repairMode,
                    const {
                      'join': 'Join / repair contours',
                      'backtracks': 'Micro backtracks only',
                      'points': 'Near-duplicate points only',
                    },
                    (v) {
                      repairMode = v;
                      fields['tolerance']!.text =
                          '${(v == 'points' ? .0001 : .001) * (m.units == 'mm' ? 25.4 : 1)}';
                    },
                  ),
                  if (repairMode == 'join')
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      title: const Text('Remove repeated / negligible points'),
                      subtitle: const Text(
                        'Up to 0.00001 in, limited by join tolerance',
                      ),
                      value: cleanup,
                      onChanged: working
                          ? null
                          : (v) {
                              cleanup = v!;
                              invalidate();
                            },
                    ),
                  if (repairMode != 'points')
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      title: const Text('Remove microscopic backtracks'),
                      subtitle: const Text(
                        'Up to 0.0005 in, limited by repair tolerance',
                      ),
                      value: backtracks,
                      onChanged: working
                          ? null
                          : (v) {
                              backtracks = v!;
                              invalidate();
                            },
                    ),
                ],
                if (!offset && !outlines && !arrange && !repair) ...[
                  DropdownButtonFormField<String>(
                    initialValue: mode,
                    decoration: const InputDecoration(labelText: 'Target'),
                    items: const [
                      DropdownMenuItem(
                        value: 'repair',
                        child: Text('Repair / recognize circle'),
                      ),
                      DropdownMenuItem(
                        value: 'circle',
                        child: Text('Circle from bounds'),
                      ),
                      DropdownMenuItem(
                        value: 'rectangle',
                        child: Text('Rectangle from bounds'),
                      ),
                    ],
                    onChanged: working
                        ? null
                        : (v) {
                            mode = v!;
                            invalidate();
                          },
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: scope,
                    decoration: const InputDecoration(labelText: 'Scope'),
                    items: const [
                      DropdownMenuItem(
                        value: 'connected',
                        child: Text('Connected pieces → separate shapes'),
                      ),
                      DropdownMenuItem(
                        value: 'each',
                        child: Text('Each object separately'),
                      ),
                      DropdownMenuItem(
                        value: 'whole',
                        child: Text('Entire selection → one shape'),
                      ),
                    ],
                    onChanged: working
                        ? null
                        : (v) {
                            scope = v!;
                            invalidate();
                          },
                  ),
                ],
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 12),
                  child: Text(help),
                ),
                if (arrange) ...[
                  if (arrangeAction.endsWith('_gap'))
                    field('gap', 'Exact edge-to-edge gap'),
                ] else if (repair)
                  field(
                    'tolerance',
                    repairMode == 'points'
                        ? 'Maximum point displacement'
                        : 'Repair tolerance',
                  )
                else if (offset)
                  field('distance', 'Offset distance')
                else ...[
                  field('tolerance', 'Join / outline tolerance'),
                  if (mode == 'circle')
                    field('diameter', 'Diameter · blank = automatic'),
                  if (mode == 'rectangle') ...[
                    field('width', 'Width · blank = automatic'),
                    field('height', 'Height · blank = automatic'),
                  ],
                ],
                const Text('Gray = source · Blue = result'),
                if (repair)
                  const Text(
                    'Purple = joins · Orange = backtracks · Teal = redundant points',
                  ),
                Container(
                  height: 180,
                  color: Colors.white,
                  margin: const EdgeInsets.symmetric(vertical: 8),
                  child: CustomPaint(
                    painter: ShapePreviewPainter(
                      sources,
                      (proposal?['entities'] as List? ?? []).cast<Json>(),
                      reference: bounds(proposal?['referenceBounds']),
                      markers: (proposal?['markers'] as Json?) ?? {},
                    ),
                  ),
                ),
                Text(
                  arrange
                      ? 'Apply moves the selection as one undoable action.'
                      : offset
                      ? 'Apply adds the outlines as one undoable action.'
                      : 'Apply replaces the source geometry as one undoable action. Merged shapes keep the first source ID, layer and appearance.',
                ),
              ],
            ),
          ),
        ),
        actions: [
          SizedBox(
            width: 680,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(message, key: const ValueKey('shape-preview-message')),
                const SizedBox(height: 12),
                Wrap(
                  alignment: WrapAlignment.end,
                  spacing: 8,
                  children: [
                    TextButton(
                      onPressed: working ? null : () => Navigator.pop(context),
                      child: const Text('Cancel'),
                    ),
                    OutlinedButton(
                      onPressed:
                          working ||
                              (arrange &&
                                  reference == 'locked' &&
                                  lockedItem == null)
                          ? null
                          : preview,
                      child: const Text('Preview'),
                    ),
                    FilledButton(
                      onPressed: working || proposal == null ? null : apply,
                      child: Text(working ? 'Working…' : 'Apply'),
                    ),
                  ],
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget choice(
    String label,
    String value,
    Map<String, String> options,
    void Function(String) changed,
  ) => DropdownButtonFormField<String>(
    initialValue: value,
    isExpanded: true,
    decoration: InputDecoration(labelText: label),
    items: options.entries
        .map(
          (e) => DropdownMenuItem(
            value: e.key,
            child: Text(e.value, overflow: TextOverflow.ellipsis),
          ),
        )
        .toList(),
    onChanged: working
        ? null
        : (v) {
            changed(v!);
            invalidate();
          },
  );
}

class ShapePreviewPainter extends CustomPainter {
  ShapePreviewPainter(
    this.sources,
    this.outputs, {
    this.reference,
    this.markers = const {},
    this.acrylic = const [],
    this.highlight = const [],
    this.zoomBounds,
    this.splitter,
  });
  final List<Json> sources, outputs;
  final Rect? reference;
  final Json markers;
  final List<Json> acrylic, highlight;
  final Rect? zoomBounds;
  final Json? splitter;
  @override
  void paint(Canvas canvas, Size size) {
    final boxes = [
      ...sources,
      ...outputs,
    ].map((e) => bounds(e['bounds'])).whereType<Rect>().toList();
    if (reference != null) boxes.add(reference!);
    if (boxes.isEmpty) return;
    final box = zoomBounds ?? boxes.reduce((a, b) => a.expandToInclude(b));
    final scale = math.min(
      (size.width - 24) / math.max(box.width, .001),
      (size.height - 24) / math.max(box.height, .001),
    );
    canvas.save();
    canvas.clipRect(Offset.zero & size);
    canvas.translate(size.width / 2, size.height / 2);
    canvas.scale(scale, -scale);
    canvas.translate(-box.center.dx, -box.center.dy);
    if (splitter != null) {
      final at = number(splitter!['position']);
      final horizontal = splitter!['orientation'] == 'horizontal';
      canvas.drawLine(
        splitter!['start'] != null
            ? point(splitter!['start'])
            : horizontal
            ? Offset(box.left - 12 / scale, at)
            : Offset(at, box.top - 12 / scale),
        splitter!['end'] != null
            ? point(splitter!['end'])
            : horizontal
            ? Offset(box.right + 12 / scale, at)
            : Offset(at, box.bottom + 12 / scale),
        Paint()
          ..color = const Color(0xff8446b1)
          ..strokeWidth = 1.5 / scale,
      );
    }
    if (reference != null) {
      canvas.drawRect(
        reference!,
        Paint()
          ..style = PaintingStyle.stroke
          ..color = const Color(0xff8446b1)
          ..strokeWidth = 1 / scale,
      );
    }
    for (final group in [sources, outputs]) {
      final result = identical(group, outputs);
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..color = result ? const Color(0xff0075cf) : const Color(0xff969696)
        ..strokeWidth = (result ? 2 : 1) / scale;
      for (final entity in group) {
        canvas.drawPath(entityPath(entity), paint);
      }
    }
    for (final group in [acrylic, highlight]) {
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..color = identical(group, acrylic)
            ? const Color(0xff008577)
            : const Color(0xffdf7900)
        ..strokeWidth = 1.5 / scale;
      for (final entity in group) {
        canvas.drawPath(entityPath(entity), paint);
      }
    }
    for (final entry in {
      'joins': const Color(0xff8446b1),
      'backtracks': const Color(0xffd46a00),
      'points': const Color(0xff008577),
    }.entries) {
      final paint = Paint()
        ..style = PaintingStyle.stroke
        ..color = entry.value
        ..strokeWidth = 2 / scale;
      for (final p in markers[entry.key] as List? ?? []) {
        canvas.drawCircle(point(p), 6 / scale, paint);
      }
    }
    canvas.restore();
  }

  @override
  bool shouldRepaint(covariant ShapePreviewPainter oldDelegate) => true;
}
