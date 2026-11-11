part of 'manufacturing_workspace.dart';

const linkedActions = {
  'group_targets': 'Replace group source contours',
  'group_rule': 'Group geometry rule',
  'group_contours': 'Group contour compensation / order',
  'group_sequence': 'Whole-group sequence / prerequisites',
};

extension _LinkedManufacturing on _ManufacturingWorkspaceState {
  Future<void> linkedEditor(String action, Json op) async {
    if (data['linkedRevisions'] != true) {
      linkedError(
        'This tab uses an earlier backend. New tabs support linked revisions; save and reopen when convenient.',
      );
      return;
    }
    final before = m.state['revision'];
    try {
      final response = await m.request('manufacturing.linked_info', {
        'id': op['id'],
      });
      if (response['ok'] != true) {
        throw StateError(response['error'].toString());
      }
      if (!mounted || m.state['revision'] != before) return;
      final info = response['manufacturing'] as Json,
          source = Map<String, dynamic>.from(
            response['manufacturing']['source'] as Map,
          );
      Json? p;
      if (action == 'group_contours') {
        p = await profileContourControls(context, m, source, linked: true);
      } else if (action == 'group_sequence') {
        p = await operationSequenceControls(
          context,
          source,
          List<Json>.from(data['operations'] as List)
              .where((o) => !(info['member_ids'] as List).contains(o['id']))
              .toList(),
          linked: true,
        );
      } else {
        p = await showDialog<Json>(
          context: context,
          barrierDismissible: false,
          builder: (_) => _LinkedTargetDialog(
            model: m,
            info: info,
            rule: action == 'group_rule',
          ),
        );
      }
      if (p == null || !mounted) return;
      if (m.state['revision'] != before) {
        throw StateError('Drawing changed; reopen the complete-group editor.');
      }
      await request('management_prepare', {
        'action': action,
        'params': {...p, 'id': op['id'], 'fingerprint': info['fingerprint']},
      });
    } catch (e) {
      if (mounted) {
        linkedError(e.toString());
      }
    }
  }

  Widget linkedReview(Json row) => Column(
    crossAxisAlignment: CrossAxisAlignment.start,
    children: [
      Text(
        '${row['name']} — complete linked workflow',
        style: const TextStyle(fontWeight: FontWeight.bold),
      ),
      Text(
        'Source contours: ${(row['before'] as List).length} → ${(row['after'] as List).length}',
      ),
      Text(
        'Stages: ${(row['kept_stage_ids'] as List).length} retained identities, ${(row['added_stage_ids'] as List).length} added, ${(row['removed_stage_ids'] as List).length} removed.',
      ),
      Text(
        'Geometry rule: ${(row['selector'] as Map).isEmpty ? 'Fixed selection' : jsonEncode(row['selector'])}',
      ),
      Text(
        'Contour overrides: ${(row['entity_sides'] as Map).length}; ${(row['manual_contour_order'] as List).isEmpty ? 'automatic contour order' : 'manual order: ${row['manual_contour_order']}'}',
      ),
      Text(
        'Group sequence: ${row['sequence_role']}, priority ${row['sequence_priority']}; ${(row['prerequisites'] as List).length} prerequisites.',
      ),
      const Text(
        'Review all regenerated stages and retained support. No previous motion is reused.',
      ),
    ],
  );
}

class _LinkedTargetDialog extends StatefulWidget {
  const _LinkedTargetDialog({
    required this.model,
    required this.info,
    required this.rule,
  });
  final DesignController model;
  final Json info;
  final bool rule;
  @override
  State<_LinkedTargetDialog> createState() => _LinkedTargetDialogState();
}

class _LinkedTargetDialogState extends State<_LinkedTargetDialog> {
  late final source = widget.info['source'] as Json;
  late final ids = Set<String>.from(source['entity_ids']);
  late final eligible = Set<String>.from(widget.info['eligible_ids']);
  late final revision = widget.model.state['revision'];
  bool reset = false;
  String mode = 'bind';
  String? error;
  bool get stale =>
      revision != widget.model.state['revision'] ||
      !widget.model.ready ||
      widget.model.busy;
  @override
  void initState() {
    super.initState();
    widget.model.addListener(changed);
  }

  void changed() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    widget.model.removeListener(changed);
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: Text(
      widget.rule ? 'Group geometry rule' : 'Replace group source contours',
    ),
    content: SizedBox(
      width: 620,
      child: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '${source['name']} · ${(widget.info['member_ids'] as List).length} linked stages',
            ),
            const Text(
              'This replans the entire workflow with both cutters. The review shows changed targets, stage identities and actual motion; nothing changes until Apply.',
            ),
            if (widget.rule) ...[
              Text(
                'Current rule: ${(source['geometry_selector'] as Map? ?? {}).isEmpty ? 'Fixed selection' : jsonEncode(source['geometry_selector'])}',
              ),
              DropdownButtonFormField<String>(
                initialValue: mode,
                decoration: const InputDecoration(labelText: 'Rule action'),
                items: const [
                  DropdownMenuItem(
                    value: 'bind',
                    child: Text('Derive rule from current source contours'),
                  ),
                  DropdownMenuItem(
                    value: 'refresh',
                    child: Text('Refresh existing rule'),
                  ),
                  DropdownMenuItem(
                    value: 'clear',
                    child: Text('Keep current contours, remove rule'),
                  ),
                ],
                onChanged: (v) => setState(() => mode = v!),
              ),
              const Text(
                'A derived rule can include other matching contours on the same layer. Inspect the expanded selection before Apply. Hidden/locked matches are rejected.',
              ),
            ] else ...[
              TextButton.icon(
                onPressed: () => setState(() {
                  ids
                    ..clear()
                    ..addAll(widget.model.selection.where(eligible.contains));
                }),
                icon: const Icon(Icons.select_all),
                label: const Text('Use drawing selection'),
              ),
              for (final e in widget.model.entities.where(
                (e) => eligible.contains(e['id']),
              ))
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text('${e['kind']} · ${e['layer']} · ${e['id']}'),
                  subtitle: Text(
                    'Bounds: ${e['bounds']} ${widget.model.units}',
                  ),
                  value: ids.contains(e['id']),
                  onChanged: (v) => setState(() {
                    if (v == true) {
                      ids.add(e['id'] as String);
                    } else {
                      ids.remove(e['id']);
                    }
                  }),
                ),
              Text(
                '${ids.length} source contours selected. Replacing targets clears the old geometry rule.',
              ),
            ],
            CheckboxListTile(
              contentPadding: EdgeInsets.zero,
              value: reset,
              onChanged: (v) => setState(() => reset = v == true),
              title: const Text(
                'Reset per-contour compensation and manual order',
              ),
              subtitle: const Text(
                'New contours use the primary cut side. Required if removed targets invalidate saved overrides/order.',
              ),
            ),
            if (stale)
              const Text(
                'The drawing changed. Cancel and reopen this editor.',
                style: TextStyle(color: Colors.red),
              ),
            if (error != null)
              Text(error!, style: const TextStyle(color: Colors.red)),
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(
        onPressed: stale
            ? null
            : () {
                if (!widget.rule && ids.isEmpty) {
                  setState(() => error = 'Choose at least one source contour.');
                  return;
                }
                Navigator.pop(context, <String, dynamic>{
                  if (widget.rule) 'mode': mode else 'entity_ids': ids.toList(),
                  'reset_controls': reset,
                });
              },
        child: const Text('Review complete group'),
      ),
    ],
  );
}
