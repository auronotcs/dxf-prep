import 'package:flutter/material.dart';
import 'controller.dart';
import 'shape_tools_dialog.dart' show ShapePreviewPainter;

class CleaveDialog extends StatefulWidget {
  const CleaveDialog({super.key, required this.model, this.guideId, this.line});
  final DesignController model;
  final String? guideId;
  final List<Json>? line;
  @override
  State<CleaveDialog> createState() => _CleaveDialogState();
}

class _CleaveDialogState extends State<CleaveDialog> {
  DesignController get m => widget.model;
  late final int revision;
  late final List<Json> guides, sources;
  String? guideId;
  Json? preview;
  bool working = false, stale = false;
  String message =
      'Choose a guide and preview. Both sides of the split are kept.';
  bool get editable =>
      !working &&
      !stale &&
      !m.busy &&
      m.ready &&
      !m.readOnly &&
      m.state['guideCleave'] == true;
  Json? get guide => guides.where((g) => g['id'] == guideId).firstOrNull;
  @override
  void initState() {
    super.initState();
    revision = m.state['revision'] as int;
    guides = (m.state['guides'] as List? ?? [])
        .cast<Json>()
        .where((g) => g['visible'] == true)
        .toList();
    sources = m.entities.where((e) => m.selection.contains(e['id'])).toList();
    guideId = guides.any((g) => g['id'] == widget.guideId)
        ? widget.guideId
        : guides.firstOrNull?['id'] as String?;
    if (m.state['guideCleave'] != true) {
      message = 'Save your drawing and restart Flutter to load guide cleaving.';
    }
    m.addListener(changed);
  }

  @override
  void dispose() {
    m.removeListener(changed);
    super.dispose();
  }

  void changed() {
    if (!mounted) return;
    setState(() {
      if (m.state['revision'] != revision) {
        stale = true;
        preview = null;
        message =
            'Drawing or selection changed. Reopen Cleave / Split to review it again.';
      }
    });
  }

  Future<void> prepare() async {
    if (!editable ||
        (guideId == null && widget.line == null) ||
        sources.isEmpty) {
      return;
    }
    setState(() {
      working = true;
      preview = null;
    });
    try {
      final r = await m.request('design.preview_shapes', {
        'kind': 'cleave',
        if (widget.line != null) 'line': widget.line else 'guide_id': guideId,
      });
      if (!mounted || stale || m.state['revision'] != revision) return;
      setState(() {
        if (r['ok'] != true) {
          message = r['error'].toString();
          return;
        }
        preview = r['preview'] as Json;
        final p = preview!;
        message =
            '${p['sourceCount']} source contour(s) → ${p['leftCount']} left / ${p['rightCount']} right contour(s), including ${p['holeCount']} hole(s). '
            '${p['unaffectedCount']} selected contour(s) unchanged; ${p['expandedCount']} compound member(s) included. '
            'Area: ${number(p['areaBefore']).toStringAsPrecision(7)} → ${number(p['areaAfter']).toStringAsPrecision(7)} ${m.units}². '
            '${p['removedCount']} generated dependent(s) removed; ${(p['reviewedLedPlanIds'] as List).length} LED plan(s) need regeneration.'
            '${number(p['circleCount']) > 0 ? ' Circular sources use 160-segment polygon outlines.' : ''}'
            '${number(p['detachedDimensionCount']) > 0 ? ' ${p['detachedDimensionCount']} dimension(s) retain their last measurement points and will need new references.' : ''}';
      });
    } catch (e) {
      if (mounted) setState(() => message = e.toString());
    } finally {
      if (mounted) setState(() => working = false);
    }
  }

  Future<void> apply() async {
    if (!editable || preview == null || m.state['revision'] != revision) return;
    final token = preview!['token'];
    setState(() => working = true);
    final ok = await m.run('design.apply_shapes', {'token': token});
    if (!mounted) return;
    if (ok) {
      Navigator.pop(context);
    } else {
      setState(() {
        working = false;
        preview = null;
        message = m.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final outputs = (preview?['entities'] as List? ?? []).cast<Json>();
    return PopScope(
      canPop: !working,
      child: AlertDialog(
        title: Text(
          widget.line == null
              ? 'Cleave / Split along guide'
              : 'Cleave / Split along drawn line',
        ),
        content: SizedBox(
          width: 680,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                if (widget.line != null)
                  Text(
                    'Finite line: (${widget.line![0]['x']}, ${widget.line![0]['y']}) to (${widget.line![1]['x']}, ${widget.line![1]['y']}) ${m.units}',
                  )
                else if (guides.isEmpty)
                  const Text(
                    'Create a guide by dragging from a ruler, then reopen this tool.',
                  )
                else
                  DropdownButtonFormField<String>(
                    initialValue: guideId,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Splitting guide',
                    ),
                    items: guides
                        .map(
                          (g) => DropdownMenuItem<String>(
                            value: g['id'] as String,
                            child: Text(
                              '${g['orientation']} · ${g['position']} ${m.units}${g['name'] == '' ? '' : ' · ${g['name']}'}${g['locked'] == true ? ' (locked reference)' : ''}',
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        )
                        .toList(),
                    onChanged: editable
                        ? (id) => setState(() {
                            guideId = id;
                            preview = null;
                            message =
                                'Guide changed. Preview again before applying.';
                          })
                        : null,
                  ),
                const SizedBox(height: 12),
                Text(
                  widget.line != null
                      ? 'Splits only where the drawn segment crosses both sides of a closed shape. Both pieces remain; no material is removed. Extend the endpoints beyond the outline. Compound holes are included.'
                      : 'Splits selected closed shapes along the full guide. Both sides remain; no material is removed. Compound holes are included. The guide stays unchanged, even when locked.',
                ),
                if (sources.isEmpty)
                  const Text('Select closed shapes before opening this tool.'),
                const SizedBox(height: 8),
                const Text(
                  'Gray = source · Teal = left · Blue = right · Purple = guide',
                ),
                Container(
                  height: 220,
                  color: Colors.white,
                  margin: const EdgeInsets.symmetric(vertical: 8),
                  child: CustomPaint(
                    painter: ShapePreviewPainter(
                      (preview?['sources'] as List?)?.cast<Json>() ?? sources,
                      outputs
                          .where((e) => e['metadata']['cleave_side'] == 'right')
                          .toList(),
                      acrylic: outputs
                          .where((e) => e['metadata']['cleave_side'] == 'left')
                          .toList(),
                      splitter: widget.line == null
                          ? guide
                          : {'start': widget.line![0], 'end': widget.line![1]},
                    ),
                  ),
                ),
                const Text(
                  'Apply replaces crossed source contours with new contours in one Undo step. Review dependent removals and LED regeneration below before applying.',
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
                Text(message, key: const ValueKey('cleave-message')),
                const SizedBox(height: 10),
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
                          editable &&
                              (guideId != null || widget.line != null) &&
                              sources.isNotEmpty
                          ? prepare
                          : null,
                      child: const Text('Preview'),
                    ),
                    FilledButton(
                      onPressed: editable && preview != null ? apply : null,
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
}
