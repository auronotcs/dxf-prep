import 'package:flutter/material.dart';
import 'controller.dart';
import 'shape_tools_dialog.dart';

class RepairSuiteDialog extends StatefulWidget {
  const RepairSuiteDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<RepairSuiteDialog> createState() => _RepairSuiteDialogState();
}

class _RepairSuiteDialogState extends State<RepairSuiteDialog> {
  DesignController get m => widget.model;
  late final revision = m.state['revision'];
  late String scope = m.selection.isEmpty ? 'all' : 'selected';
  bool fragments = false, working = false, applying = false, stale = false;
  Json? preview;
  int? shown;
  bool detail = false;
  String? message;
  @override
  void initState() {
    super.initState();
    m.addListener(changed);
  }

  void changed() {
    if (!applying && (revision != m.state['revision'] || !m.ready)) {
      setState(() {
        stale = true;
        preview = null;
        message = 'Drawing changed. Close and reopen Repair Suite.';
      });
    }
  }

  @override
  void dispose() {
    m.removeListener(changed);
    super.dispose();
  }

  void edit(VoidCallback action) => setState(() {
    action();
    preview = null;
    shown = null;
    detail = false;
    message = null;
  });
  Future<void> calculate() async {
    setState(() {
      working = true;
      preview = null;
      shown = null;
      detail = false;
      message = null;
    });
    try {
      final response = await m.request('design.suite_preview', {
        'scope': scope,
        'include_fragments': fragments,
      });
      if (!mounted) return;
      if (response['ok'] != true) throw StateError('${response['error']}');
      if (stale || revision != m.state['revision']) {
        throw StateError('Drawing changed. Reopen Repair Suite.');
      }
      setState(() => preview = response['preview'] as Json);
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
    final ok = await m.run('design.suite_apply', {'token': preview!['token']});
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
    final row = shown == null ? null : rows[shown!];
    final ids = (row?['ids'] as List? ?? []).toSet();
    final sources = (preview?['sources'] as List? ?? [])
        .cast<Json>()
        .where((e) => row == null || ids.contains(e['id']))
        .toList();
    final outputs = row == null
        ? (preview?['outputs'] as List? ?? []).cast<Json>()
        : rows
              .where(
                (r) =>
                    r['kind'] == 'repair' &&
                    (r['ids'] as List).any(ids.contains),
              )
              .expand((r) => (r['outputs'] as List).cast<Json>())
              .toList();
    final at = row?['point'] as Json?;
    final span = m.units == 'mm' ? 1.016 : .04;
    final active = !working && !stale;
    return PopScope(
      canPop: !working,
      child: AlertDialog(
        title: const Text('Repair Suite'),
        content: SizedBox(
          width: 820,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Review a combined contour repair before applying. Tolerances are calculated in physical units. Locked, hidden and protected geometry stays unchanged. Specks are never deleted by this batch.',
                ),
                const SizedBox(height: 12),
                DropdownButtonFormField<String>(
                  initialValue: scope,
                  decoration: const InputDecoration(labelText: 'Repair scope'),
                  items: const [
                    DropdownMenuItem(
                      value: 'selected',
                      child: Text('Selected objects'),
                    ),
                    DropdownMenuItem(
                      value: 'all',
                      child: Text('Whole drawing'),
                    ),
                  ],
                  onChanged: active ? (v) => edit(() => scope = v!) : null,
                ),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Include reviewed endpoint fragments'),
                  subtitle: Text(
                    'Changes artwork; maximum ${m.units == 'mm' ? '0.0508 mm' : '0.002 in'}. Inspect the proposal before Apply.',
                  ),
                  value: fragments,
                  onChanged: active ? (v) => edit(() => fragments = v!) : null,
                ),
                TextButton(
                  onPressed: active ? calculate : null,
                  child: const Text('Preview repair batch'),
                ),
                if (message != null) Text(message!),
                if (working) const LinearProgressIndicator(),
                if (preview != null) ...[
                  Text(
                    '${preview!['count']} contour repair(s) ready. One Undo restores the complete batch.',
                  ),
                  if (rows.isEmpty)
                    const Text(
                      'No repair or inspection findings in this scope.',
                    ),
                  for (var i = 0; i < rows.length; i++)
                    ListTile(
                      dense: true,
                      selected: shown == i,
                      leading: rows[i]['kind'] == 'finding'
                          ? Icon(
                              rows[i]['severity'] == 'error'
                                  ? Icons.error_outline
                                  : Icons.warning_amber_outlined,
                              color: rows[i]['severity'] == 'error'
                                  ? Theme.of(context).colorScheme.error
                                  : null,
                            )
                          : null,
                      title: Text('${rows[i]['message']}'),
                      subtitle: Text((rows[i]['ids'] as List).join(', ')),
                      onTap: () => setState(() {
                        shown = i;
                        detail = rows[i]['point'] != null;
                      }),
                    ),
                  Wrap(
                    spacing: 12,
                    children: [
                      TextButton(
                        onPressed: () => setState(() {
                          shown = null;
                          detail = false;
                        }),
                        child: const Text('Show batch'),
                      ),
                      if (at != null)
                        TextButton(
                          onPressed: () => setState(() => detail = !detail),
                          child: Text(
                            detail
                                ? 'Show whole objects'
                                : 'Show defect detail',
                          ),
                        ),
                    ],
                  ),
                  Text(
                    detail && at != null
                        ? 'Defect detail: $span ${m.units} window. Gray = original; blue = proposed.'
                        : 'Gray = original; blue = proposed. This preview does not move the drawing.',
                  ),
                  const SizedBox(height: 6),
                  SizedBox(
                    height: 230,
                    width: double.infinity,
                    child: ColoredBox(
                      color: Colors.white,
                      child: CustomPaint(
                        painter: ShapePreviewPainter(
                          sources,
                          outputs,
                          zoomBounds: detail && at != null
                              ? Rect.fromCenter(
                                  center: Offset(
                                    number(at['x']),
                                    number(at['y']),
                                  ),
                                  width: span,
                                  height: span,
                                )
                              : null,
                          markers: {
                            'joins': at != null ? [at] : <Json>[],
                          },
                        ),
                        child: const SizedBox.expand(),
                      ),
                    ),
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
          FilledButton(
            onPressed: active && preview?['token'] != null ? apply : null,
            child: Text(
              fragments ? 'Apply reviewed batch' : 'Apply safe repairs',
            ),
          ),
        ],
      ),
    );
  }
}
