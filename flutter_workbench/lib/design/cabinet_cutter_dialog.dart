import 'package:flutter/material.dart';
import 'controller.dart';

/// Resolves only a catalog reference. The source document's turret is untouched.
class CabinetCutterDialog extends StatefulWidget {
  const CabinetCutterDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<CabinetCutterDialog> createState() => _CabinetCutterDialogState();
}

class _CabinetCutterDialogState extends State<CabinetCutterDialog> {
  Json? catalog;
  String? selected, error;
  int recipe = 0, station = 1;
  bool busy = true;
  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load() async {
    try {
      final result = await widget.model.request('manufacturing.catalog_list');
      if (result['ok'] != true) throw StateError('${result['error']}');
      if (mounted) setState(() => catalog = result);
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> choose() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final ref = <String, dynamic>{
        'id': selected,
        'recipe': recipe,
        'station': station,
        'version': catalog!['version'],
      };
      final result = await widget.model.request(
        'manufacturing.catalog_resolve',
        ref,
      );
      if (result['ok'] != true) throw StateError('${result['error']}');
      if (mounted) {
        Navigator.pop(context, <String, dynamic>{
          'reference': ref,
          'tool': result['tool'],
        });
      }
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final tools = (catalog?['catalog'] as List? ?? [])
        .cast<Json>()
        .where(
          (t) => t['kind'] == 'end_mill' && t['spindle_mode'] != 'disabled',
        )
        .toList();
    final row = tools.where((t) => t['id'] == selected).firstOrNull;
    final recipes = (row?['recipes'] as List? ?? []).cast<Json>();
    return PopScope(
      canPop: !busy,
      child: AlertDialog(
        title: const Text('Private Cabinet cutter'),
        content: SizedBox(
          width: 560,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'Choose a recipe from the Flutter tool library. This station is used only in the exported copies; the assembly turret stays unchanged.',
                ),
                if (busy) const LinearProgressIndicator(),
                if (error != null)
                  Text(
                    error!,
                    style: TextStyle(
                      color: Theme.of(context).colorScheme.error,
                    ),
                  ),
                DropdownButtonFormField<String>(
                  initialValue: selected,
                  isExpanded: true,
                  decoration: const InputDecoration(labelText: 'Flat end mill'),
                  items: [
                    for (final t in tools)
                      DropdownMenuItem(
                        value: t['id'] as String,
                        child: Text(
                          '${t['name']} · Ø ${t['diameter_in']} in',
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                  ],
                  onChanged: busy
                      ? null
                      : (v) => setState(() {
                          selected = v;
                          recipe = 0;
                        }),
                ),
                if (row != null) ...[
                  DropdownButtonFormField<int>(
                    key: ValueKey('recipe-$selected-$recipe'),
                    initialValue: recipes.isEmpty ? null : recipe,
                    isExpanded: true,
                    decoration: const InputDecoration(
                      labelText: 'Material recipe',
                    ),
                    items: [
                      for (var i = 0; i < recipes.length; i++)
                        DropdownMenuItem(
                          value: i,
                          enabled: recipes[i]['qualification'] != 'retired',
                          child: Text(
                            '${recipes[i]['material']} · ${recipes[i]['rpm']} RPM · ${recipes[i]['qualification'] ?? 'prototype'}',
                            overflow: TextOverflow.ellipsis,
                          ),
                        ),
                    ],
                    onChanged: busy ? null : (v) => setState(() => recipe = v!),
                  ),
                  Text(
                    'Cutting edge length: ${row['cutting_edge_length_in'] ?? 'unspecified'} in. Dimensions convert to ${widget.model.units} in the preview.',
                  ),
                ],
                DropdownButtonFormField<int>(
                  initialValue: station,
                  decoration: const InputDecoration(
                    labelText: 'Station in exported jobs',
                  ),
                  items: [
                    for (var i = 1; i <= 12; i++)
                      if (i != 8)
                        DropdownMenuItem(value: i, child: Text('T$i')),
                  ],
                  onChanged: busy ? null : (v) => setState(() => station = v!),
                ),
                const Text(
                  'The fabrication check verifies cutter fit and reach. Review feeds, depth, material and the actual machine rack in Manufacturing.',
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: busy ? null : () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed:
                !busy &&
                    selected != null &&
                    recipes.isNotEmpty &&
                    recipes[recipe]['qualification'] != 'retired'
                ? choose
                : null,
            child: const Text('Use private cutter'),
          ),
        ],
      ),
    );
  }
}
