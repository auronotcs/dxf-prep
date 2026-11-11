import 'package:flutter/material.dart';
import 'controller.dart';

class GuidedReadinessDialog extends StatefulWidget {
  const GuidedReadinessDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<GuidedReadinessDialog> createState() => _GuidedReadinessDialogState();
}

class _GuidedReadinessDialogState extends State<GuidedReadinessDialog> {
  DesignController get m => widget.model;
  Json? review, comparison;
  String? selected, error;
  bool busy = false, onlyProblems = false, showExcluded = false;
  Object? revision;
  bool get stale =>
      review != null && (revision != m.state['revision'] || !m.ready);
  List<Json> get rows => (review?['operations'] as List? ?? []).cast<Json>();
  Json? get operation => rows.where((r) => r['id'] == selected).firstOrNull;
  bool get active => !busy && review != null && !stale;
  @override
  void initState() {
    super.initState();
    m.addListener(changed);
    refresh();
  }

  void changed() {
    if (mounted) {
      setState(() {
        if (stale) comparison = null;
      });
    }
  }

  @override
  void dispose() {
    m.removeListener(changed);
    super.dispose();
  }

  Future<void> refresh() async {
    final opened = m.state['revision'];
    setState(() {
      busy = true;
      error = null;
      review = null;
      comparison = null;
    });
    try {
      final r = await m.request('manufacturing.guided_readiness');
      if (r['ok'] != true) throw StateError('${r['error']}');
      if (!mounted) return;
      if (opened != m.state['revision']) {
        throw StateError('Drawing changed during review. Refresh readiness.');
      }
      review = r['review'] as Json;
      revision = opened;
      if (!rows.any((r) => r['id'] == selected)) {
        selected = rows.firstOrNull?['id'];
      }
    } catch (e) {
      if (mounted) error = '$e';
    }
    if (mounted) setState(() => busy = false);
  }

  Future<void> action(String verb, {bool global = false}) async {
    if (!active) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final r = await m.request('manufacturing.guided_action', {
        'token': review!['token'],
        'operation_id': global ? null : selected,
        'action': verb,
      });
      if (r['ok'] != true) throw StateError('${r['error']}');
      if (!mounted) return;
      if (stale) throw StateError('Drawing changed. Refresh readiness.');
      if (verb == 'compare') {
        setState(() => comparison = r['review']['operation'] as Json);
      } else {
        Navigator.pop(context, r['review']);
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          error = '$e';
          review = null;
          comparison = null;
        });
      }
    }
    if (mounted) setState(() => busy = false);
  }

  Widget button(
    String label,
    String verb, {
    bool global = false,
    bool allowed = true,
  }) => TextButton(
    onPressed: active && allowed ? () => action(verb, global: global) : null,
    child: Text(label),
  );
  @override
  Widget build(BuildContext context) {
    final op = operation;
    final editable = review?['editable'] == true;
    final candidates = (comparison?['candidates'] as List? ?? [])
        .cast<Json>()
        .where((r) => showExcluded || r['eligible'] == true)
        .toList();
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: const Text('Guided Production readiness'),
        content: SizedBox(
          width: 1000,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                const Text(
                  'Check stock, cutter, saved operation recipe and current library before planning Guided Production. This review does not approve machining or output.',
                ),
                if (busy) const LinearProgressIndicator(),
                if (error != null)
                  Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (stale)
                  const Text(
                    'Drawing changed. Refresh readiness before using these findings.',
                  ),
                if (review != null) ...[
                  const SizedBox(height: 10),
                  Text(
                    'Physical stock: ${review!['stock']['verified'] == true ? 'Verified and current' : 'Needs verification'}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  Text(
                    '${review!['stock']['fingerprint']?['material'] ?? 'Material not set'} · ${review!['units']} drawing units',
                  ),
                  Wrap(
                    children: [
                      button(
                        'Job setup',
                        'stock',
                        global: true,
                        allowed: editable,
                      ),
                      button(
                        'Verify stock',
                        'verify',
                        global: true,
                        allowed: editable,
                      ),
                      button(
                        'Turret setup',
                        'turret',
                        global: true,
                        allowed: editable,
                      ),
                      button('Tool library', 'library', global: true),
                    ],
                  ),
                  Text(
                    '${review!['counts']['ready']} pass readiness · ${review!['counts']['blocked']} need attention · ${review!['counts']['unsupported']} outside review scope · ${review!['counts']['disabled']} disabled',
                  ),
                  if (rows.isEmpty)
                    const Padding(
                      padding: EdgeInsets.all(12),
                      child: Text(
                        'No operations yet. Create an operation through the normal Manufacturing tools, then refresh.',
                      ),
                    ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    title: const Text('Show only operations needing attention'),
                    value: onlyProblems,
                    onChanged: (v) => setState(() => onlyProblems = v == true),
                  ),
                  for (final row in rows.where(
                    (r) =>
                        !onlyProblems ||
                        !['ready', 'disabled'].contains(r['status']),
                  ))
                    ListTile(
                      dense: true,
                      selected: selected == row['id'],
                      title: Text('${row['name']} · T${row['station']}'),
                      subtitle: Text('${row['role']} · ${row['status']}'),
                      leading: Icon(
                        row['status'] == 'ready'
                            ? Icons.check_circle_outline
                            : row['status'] == 'disabled'
                            ? Icons.pause_circle_outline
                            : Icons.info_outline,
                      ),
                      onTap: busy
                          ? null
                          : () => setState(() {
                              selected = row['id'];
                              comparison = null;
                            }),
                    ),
                  if (op != null) ...[
                    const Divider(),
                    Text(
                      '${op['name']} — readiness details',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    for (final check in (op['checks'] as List).cast<Json>())
                      ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        leading: Icon(
                          check['passed'] == true
                              ? Icons.check
                              : Icons.warning_amber_outlined,
                        ),
                        title: Text('${check['detail']}'),
                      ),
                    if ((op['checks'] as List).isEmpty)
                      for (final finding in op['findings'] as List)
                        Text('$finding'),
                    Wrap(
                      children: [
                        button(
                          'Locate geometry',
                          'locate',
                          allowed: op['bounds'] != null,
                        ),
                        button(
                          'Edit operation',
                          'operation',
                          allowed: editable && op['enabled'] == true,
                        ),
                        if (m.state['operationRecipes'] == true)
                          button(
                            'Attach recipe...',
                            'attach',
                            allowed:
                                op['enabled'] == true &&
                                ![
                                  'unsupported',
                                  'disabled',
                                ].contains(op['status']),
                          ),
                        button(
                          'Compare recipes',
                          'compare',
                          allowed:
                              op['enabled'] == true &&
                              ![
                                'unsupported',
                                'disabled',
                              ].contains(op['status']),
                        ),
                      ],
                    ),
                  ],
                  if (comparison != null) ...[
                    const Divider(),
                    Text(
                      'Saved operation recipe',
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                    Text(
                      '${comparison!['recipe']['material'] ?? 'None attached'} · ${comparison!['recipe']['qualification'] ?? 'Unqualified'}',
                    ),
                    for (final key in ['rpm', 'feed', 'plunge', 'stepdown'])
                      Text(
                        '$key: saved ${comparison!['recipe'][key] ?? 'not recorded'} · effective ${comparison!['effective']?[key] ?? 'not set'}',
                      ),
                    const Text(
                      'Library values below use inches/minute and inches; the drawing keeps its own units. Compatible means loadable for a separate review, not approved for this operation.',
                    ),
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      dense: true,
                      title: const Text('Show excluded library recipes'),
                      value: showExcluded,
                      onChanged: (v) =>
                          setState(() => showExcluded = v == true),
                    ),
                    if (candidates.isEmpty)
                      const Text(
                        'No compatible recipes. Show excluded recipes or review the stock/cutter setup.',
                      ),
                    for (final candidate in candidates)
                      Card(
                        child: Padding(
                          padding: const EdgeInsets.all(10),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '${candidate['tool_name']} · ${candidate['name']}',
                                style: Theme.of(context).textTheme.titleSmall,
                              ),
                              Text(
                                '${candidate['qualification']} · ${candidate['eligible'] == true ? 'Compatible for review' : 'Excluded'}',
                              ),
                              Text(
                                'Material: ${candidate['scope']['material_family'] ?? 'Unspecified'} · thickness ${candidate['scope']['thickness_min_in'] ?? '?'}–${candidate['scope']['thickness_max_in'] ?? '?'} in · roles ${candidate['scope']['process_roles'] ?? 'Unspecified'}',
                              ),
                              Text(
                                'RPM ${candidate['values']['rpm']} · feed ${candidate['values']['feed']} in/min · plunge ${candidate['values']['plunge']} in/min · stepdown ${candidate['values']['stepdown']} in',
                              ),
                              Text(
                                'Evidence: ${candidate['evidence'].toString().isEmpty ? 'None recorded' : candidate['evidence']}',
                              ),
                              for (final reason in [
                                ...candidate['reasons'] as List,
                                ...candidate['warnings'] as List,
                              ])
                                Text('• $reason'),
                            ],
                          ),
                        ),
                      ),
                    button('Review tool library', 'library', global: true),
                  ],
                  const SizedBox(height: 8),
                  Text('${review!['note']}'),
                  const Text(
                    'After correcting settings, reopen or refresh this review. Existing motion and output checks still apply.',
                  ),
                ],
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: busy ? null : () => Navigator.pop(context),
            child: const Text('Close'),
          ),
          OutlinedButton(
            onPressed: busy ? null : refresh,
            child: const Text('Refresh review'),
          ),
        ],
      ),
    );
  }
}
