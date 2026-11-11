import 'package:flutter/material.dart';
import 'controller.dart';

/// Chooses a private operation draft; actual motion and Apply live in Manufacturing.
class OperationRecipeDialog extends StatefulWidget {
  const OperationRecipeDialog({
    super.key,
    required this.model,
    required this.operationId,
  });
  final DesignController model;
  final String operationId;
  @override
  State<OperationRecipeDialog> createState() => _OperationRecipeDialogState();
}

class _OperationRecipeDialogState extends State<OperationRecipeDialog> {
  Json? listing, draft;
  final settingsKey = GlobalKey();
  Json? chosen;
  Object? revision;
  String? error;
  bool busy = false,
      excluded = false,
      reviewed = false,
      holdingReviewed = false;
  DesignController get m => widget.model;
  bool get stale => revision != m.state['revision'] || !m.ready;
  String settingLabel(String key) {
    if (draft?['operation_kind'] != 'island_fill') return key;
    final units = draft?['units'];
    final label =
        <String, String>{
          'stepover': 'Stepover ($units)',
          'pass_mode': 'Pass schedule',
          'pass_depths': 'Pass increments ($units)',
          'bottom_finish_pass': 'Floor finishing pass',
          'bottom_finish_depth': 'Floor stock ($units)',
          'bottom_finish_feed_override': 'Floor finishing feed ($units/s)',
          'helix_feed_override': 'Helix feed ($units/s)',
          'corner_tags': 'Cover cleanout corners',
          'keep_tool_down': 'Keep tool down',
          'target_depth': 'Target depth ($units)',
          'stay_down_max_engagement': 'Maximum link engagement ($units)',
          'stay_down_max_distance': 'Maximum link distance ($units)',
          'stay_down_feed_percent': 'Link feed (%)',
          'speed_max_full_depth_engagement':
              'Maximum full-depth engagement ($units)',
          'speed_link_distance': 'Cleared-entry link distance ($units)',
          'speed_center_feed_percent': 'Center feed (%)',
          'speed_fallback': 'Speed Cleanout fallback',
        }[key] ??
        key.replaceAll('_', ' ');
    return '${label[0].toUpperCase()}${label.substring(1)}';
  }

  @override
  void initState() {
    super.initState();
    m.addListener(changed);
    refresh();
  }

  @override
  void dispose() {
    m.removeListener(changed);
    super.dispose();
  }

  void changed() {
    if (mounted) {
      setState(() {
        if (stale) {
          draft = null;
          reviewed = false;
          holdingReviewed = false;
        }
      });
    }
  }

  Future<void> refresh() async {
    final start = m.state['revision'];
    setState(() {
      busy = true;
      error = null;
      listing = null;
      draft = null;
      chosen = null;
      reviewed = false;
      holdingReviewed = false;
    });
    try {
      final r = await m.request('manufacturing.attachment_list', {
        'operation_id': widget.operationId,
      });
      if (r['ok'] != true) throw StateError('${r['error']}');
      if (!mounted) return;
      if (start != m.state['revision']) {
        throw StateError('Document changed. Refresh recipe choices.');
      }
      listing = r['review'] as Json;
      revision = start;
    } catch (e) {
      if (mounted) error = '$e';
    }
    if (mounted) setState(() => busy = false);
  }

  Future<void> choose(Json row) async {
    if (busy || stale) return;
    final choice = <String, dynamic>{
      'operation_id': widget.operationId,
      'token': listing!['token'],
      'id': row['id'],
      'recipe': row['recipe'],
    };
    setState(() {
      busy = true;
      error = null;
      draft = null;
      chosen = null;
      reviewed = false;
      holdingReviewed = false;
    });
    try {
      final r = await m.request('manufacturing.attachment_review', choice);
      if (r['ok'] != true) throw StateError('${r['error']}');
      if (!mounted) return;
      if (stale) throw StateError('Document changed. Refresh recipe choices.');
      draft = r['review'] as Json;
      chosen = choice;
    } catch (e) {
      if (mounted) {
        error = '$e';
        listing = null;
      }
    }
    if (mounted) {
      setState(() => busy = false);
      if (draft != null) {
        WidgetsBinding.instance.addPostFrameCallback((_) {
          final target = settingsKey.currentContext;
          if (mounted && target != null) Scrollable.ensureVisible(target);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = (listing?['matches'] as List? ?? [])
        .cast<Json>()
        .where((r) => excluded || r['eligible'] == true)
        .toList();
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: const Text('Attach operation recipe'),
        content: SizedBox(
          width: 720,
          height: 470,
          child: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (busy) const LinearProgressIndicator(),
                if (error != null)
                  Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                if (listing != null) ...[
                  Text('${listing!['name']} · ${listing!['units']}'),
                  Text('${listing!['note']}'),
                  if (stale)
                    const Text('Document changed. Refresh recipe choices.'),
                  if (listing!['stock']['verified'] != true)
                    const Text(
                      'Verify physical stock before reviewing a recipe.',
                    ),
                  Text(
                    'Saved recipe: ${listing!['saved_recipe']['material'] ?? 'None'} · ${listing!['saved_recipe']['qualification'] ?? 'Unqualified'}',
                  ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    dense: true,
                    title: const Text('Show excluded recipes'),
                    value: excluded,
                    onChanged: busy
                        ? null
                        : (v) => setState(() => excluded = v == true),
                  ),
                  if (rows.isEmpty)
                    const Text(
                      'No compatible recipes. Review the stock, cutter and tool library.',
                    ),
                  for (final row in rows)
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(10),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text('${row['tool_name']} · ${row['name']}'),
                            Text(
                              '${row['qualification']} · ${row['eligible'] == true ? 'Compatible for review' : 'Excluded'}',
                            ),
                            Text(
                              'Scope: ${row['scope']['material_family'] ?? 'Unspecified'}; ${row['scope']['thickness_min_in'] ?? '?'}–${row['scope']['thickness_max_in'] ?? '?'} in; ${row['scope']['process_roles']}; entry ${row['scope']['entry_strategies'] ?? 'Unspecified'}',
                            ),
                            Text(
                              'Evidence: ${row['evidence'].toString().isEmpty ? 'None recorded' : row['evidence']}',
                            ),
                            Text(
                              'RPM ${row['values']['rpm']} · feed ${row['values']['feed']} in/min · plunge ${row['values']['plunge']} in/min · stepdown ${row['values']['stepdown']} in',
                            ),
                            for (final reason in [
                              ...row['reasons'] as List,
                              ...row['warnings'] as List,
                            ])
                              Text('• $reason'),
                            OutlinedButton(
                              onPressed:
                                  !busy &&
                                      !stale &&
                                      row['eligible'] == true &&
                                      listing!['stock']['verified'] == true
                                  ? () => choose(row)
                                  : null,
                              child: const Text('Review settings'),
                            ),
                          ],
                        ),
                      ),
                    ),
                ],
                if (draft != null) ...[
                  const Divider(),
                  Text(
                    'Proposed settings · ${draft!['units']}',
                    key: settingsKey,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  Text('${draft!['note']}'),
                  for (final change in (draft!['changes'] as List).cast<Json>())
                    Text(
                      '${settingLabel(change['field'].toString())}: ${change['before']} → ${change['after']}${change['changed'] == true ? ' (changed)' : ' (kept)'}',
                    ),
                  for (final finding in draft!['readiness']['findings'] as List)
                    Text('Readiness: $finding'),
                  const Text(
                    'The saved recipe record is retained in the attachment history. Cancel leaves the job unchanged. Generated motion must be reviewed before Apply.',
                  ),
                  if (draft!['requiresHoldingReview'] == true)
                    CheckboxListTile(
                      contentPadding: EdgeInsets.zero,
                      title: const Text(
                        'I reviewed the changes to depth, tabs and hold-down.',
                      ),
                      value: holdingReviewed,
                      onChanged: busy || stale
                          ? null
                          : (v) => setState(() => holdingReviewed = v == true),
                    ),
                  CheckboxListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text(
                      'I reviewed recipe scope and the exact setting changes.',
                    ),
                    value: reviewed,
                    onChanged: busy || stale
                        ? null
                        : (v) => setState(() => reviewed = v == true),
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
            onPressed: busy ? null : refresh,
            child: const Text('Refresh choices'),
          ),
          FilledButton(
            onPressed:
                !busy &&
                    !stale &&
                    draft != null &&
                    reviewed &&
                    (draft!['requiresHoldingReview'] != true || holdingReviewed)
                ? () => Navigator.pop(context, <String, dynamic>{
                    'choice': chosen,
                    'reviewed': true,
                    if (draft!['requiresHoldingReview'] == true)
                      'holding_reviewed': holdingReviewed,
                  })
                : null,
            child: const Text('Generate motion review'),
          ),
        ],
      ),
    );
  }
}
