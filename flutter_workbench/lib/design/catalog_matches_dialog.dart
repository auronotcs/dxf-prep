import 'package:flutter/material.dart';
import 'controller.dart';

/// Match applied job settings, then return a private station draft for review.
class CatalogMatchesDialog extends StatefulWidget {
  const CatalogMatchesDialog({
    super.key,
    required this.model,
    required this.station,
  });
  final DesignController model;
  final int station;
  @override
  State<CatalogMatchesDialog> createState() => _CatalogMatchesDialogState();
}

class _CatalogMatchesDialogState extends State<CatalogMatchesDialog> {
  static const roles = {
    'outside_profile': 'Outside profile',
    'inside_profile': 'Inside profile',
    'pocket_fill': 'Pocket / cleanout',
    'shelf_recess': 'Shelf / recess',
    'mark_engrave': 'Mark / engrave',
    'drill_thread': 'Drill / thread',
    'finish_cutoff': 'Finish / cutoff',
  };
  String role = 'outside_profile';
  Json? result, selected;
  String? error;
  bool busy = false, reviewed = false, showExcluded = false;
  dynamic revision;
  DesignController get m => widget.model;
  bool get stale => revision != m.state['revision'];
  @override
  void initState() {
    super.initState();
    m.addListener(changed);
    refresh();
  }

  void changed() {
    if (mounted) setState(() {});
  }

  @override
  void dispose() {
    m.removeListener(changed);
    super.dispose();
  }

  Future<void> refresh() async {
    setState(() {
      busy = true;
      error = null;
      selected = null;
      reviewed = false;
      result = null;
      revision = m.state['revision'];
    });
    try {
      final r = await m.request('manufacturing.catalog_matches', {
        'station': widget.station,
        'process_role': role,
      });
      if (!mounted) return;
      if (r['ok'] != true) throw StateError('${r['error']}');
      result = r;
    } catch (e) {
      if (mounted) error = '$e';
    }
    if (mounted) setState(() => busy = false);
  }

  Future<void> load() async {
    if (stale || !reviewed || selected?['eligible'] != true) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final r = await m.request('manufacturing.catalog_match_resolve', {
        'station': widget.station,
        'process_role': role,
        'version': result!['version'],
        'token': result!['token'],
        'id': selected!['id'],
        'recipe': selected!['recipe'],
        'reviewed': true,
      });
      if (!mounted) return;
      if (r['ok'] != true) throw StateError('${r['error']}');
      if (stale) {
        throw StateError('Job changed. Refresh before loading a draft.');
      }
      Navigator.pop(context, r);
    } catch (e) {
      if (mounted) {
        setState(() {
          error = '$e';
          busy = false;
          reviewed = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final rows = (result?['matches'] as List? ?? []).cast<Json>();
    final eligible = rows.where((r) => r['eligible'] == true).length;
    final units = result?['units'] == 'mm' ? 'mm' : 'in';
    final factor = units == 'mm' ? 25.4 : 1.0;
    String dimension(dynamic v) =>
        v is num ? (v * factor).toStringAsPrecision(5) : 'Not recorded';
    final scope = (selected?['scope'] as Map?) ?? {};
    final values = (selected?['values'] as Map?) ?? {};
    return Dialog(
      child: SizedBox(
        width: 920,
        height: 740,
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'Match job recipes · T${widget.station}',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const Text(
                'Matches use the applied cutter and stock. Unsaved turret edits are not used; loading replaces this station’s draft.',
              ),
              const SizedBox(height: 8),
              Row(
                children: [
                  Expanded(
                    child: DropdownButtonFormField<String>(
                      initialValue: role,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Process role',
                      ),
                      items: [
                        for (final e in roles.entries)
                          DropdownMenuItem(value: e.key, child: Text(e.value)),
                      ],
                      onChanged: busy
                          ? null
                          : (value) {
                              role = value!;
                              refresh();
                            },
                    ),
                  ),
                  IconButton(
                    tooltip: 'Refresh recipe matches',
                    onPressed: busy ? null : refresh,
                    icon: const Icon(Icons.refresh),
                  ),
                ],
              ),
              if (busy) const LinearProgressIndicator(),
              Expanded(
                child: ListView(
                  children: [
                    if (stale)
                      const Text(
                        'Job changed. Refresh before loading a draft.',
                      ),
                    if (error != null)
                      Text(
                        error!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    if (result != null) ...[
                      const SizedBox(height: 12),
                      Text(
                        '${result!['material']} · ${result!['thickness']} $units · $eligible matching recipes',
                      ),
                      Text(
                        'Affected operations: ${(result!['affected_operations'] as List).map((o) => o['name']).join(', ').isEmpty ? 'None yet' : (result!['affected_operations'] as List).map((o) => o['name']).join(', ')}',
                      ),
                      SwitchListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(
                          'Show excluded recipes (${rows.length - eligible})',
                        ),
                        value: showExcluded,
                        onChanged: (v) => setState(() => showExcluded = v),
                      ),
                      if (eligible == 0)
                        const Text(
                          'No applicable recipe. Check the applied cutter, stock, role and exclusions, or record a suitable custom recipe in the library.',
                        ),
                      for (final row in rows.where(
                        (r) => showExcluded || r['eligible'] == true,
                      ))
                        ListTile(
                          key: ValueKey('match-${row['id']}-${row['recipe']}'),
                          selected: identical(selected, row),
                          leading: Icon(
                            row['eligible'] == true ? Icons.tune : Icons.block,
                          ),
                          title: Text('${row['name']} · ${row['tool_name']}'),
                          subtitle: Text(
                            '${row['qualification'].toString().replaceAll('_', ' ')}${row['eligible'] == true ? '' : ' · ${(row['reasons'] as List).join(' ')}'}',
                          ),
                          onTap: busy
                              ? null
                              : () => setState(() {
                                  selected = row;
                                  reviewed = false;
                                }),
                        ),
                    ],
                    if (selected != null) ...[
                      const Divider(),
                      Text(
                        'Recipe review',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      Text(
                        'Material scope: ${scope['material_family'] ?? selected!['name']}',
                      ),
                      Text(
                        'Thickness: ${dimension(scope['thickness_min_in'])} – ${dimension(scope['thickness_max_in'])} $units',
                      ),
                      Text(
                        'Roles: ${scope['process_roles'] ?? 'Any'} · Entry strategies: ${scope['entry_strategies'] ?? 'Not recorded'}',
                      ),
                      Text(
                        'RPM ${values['rpm']} · Feed ${dimension(values['feed'])} $units/min · Plunge ${dimension(values['plunge'])} $units/min',
                      ),
                      Text(
                        'Stepdown recommendation ${dimension(values['stepdown'])} $units · Stepover record ${dimension(values['stepover'])} $units',
                      ),
                      Text(
                        'Evidence: ${selected!['evidence'].toString().isEmpty ? 'None recorded' : selected!['evidence']}',
                      ),
                      if ('${selected!['notes'] ?? ''}'.isNotEmpty)
                        Text('${selected!['notes']}'),
                      for (final warning in [
                        ...(selected!['reasons'] as List),
                        ...(selected!['warnings'] as List),
                      ])
                        Text('• $warning'),
                      const SizedBox(height: 8),
                      const Text(
                        'Loads station feeds/RPM and the stepdown recommendation only. Operation depths, entries, pass schedules, stepover and holding stay as configured. Review regenerated motion in Turret Setup before Apply.',
                      ),
                      CheckboxListTile(
                        contentPadding: EdgeInsets.zero,
                        title: const Text(
                          'I reviewed scope, evidence and affected operations',
                        ),
                        value: reviewed,
                        onChanged:
                            busy || stale || selected!['eligible'] != true
                            ? null
                            : (v) => setState(() => reviewed = v == true),
                      ),
                    ],
                  ],
                ),
              ),
              const Divider(),
              Wrap(
                alignment: WrapAlignment.end,
                spacing: 8,
                children: [
                  TextButton(
                    onPressed: busy ? null : () => Navigator.pop(context),
                    child: const Text('Cancel'),
                  ),
                  FilledButton(
                    onPressed:
                        busy ||
                            stale ||
                            !reviewed ||
                            selected?['eligible'] != true
                        ? null
                        : load,
                    child: Text('Load T${widget.station} draft'),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
