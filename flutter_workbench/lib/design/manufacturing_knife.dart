part of 'manufacturing_workspace.dart';

extension _KnifePresets on _ManufacturingWorkspaceState {
  Future<void> knifePresets() async {
    Json library;
    try {
      library = await m.request('manufacturing.knife_list');
      if (library['ok'] != true) throw StateError('${library['error']}');
    } catch (e) {
      if (mounted) refreshSpecial(() => error = '$e');
      return;
    }
    if (!mounted) return;
    final path = TextEditingController();
    final name = TextEditingController();
    final operations = ops
        .where((o) => o['kind'] == 'drag_knife' && o['enabled'] != false)
        .toList();
    String? operationId = operations.isEmpty
        ? null
        : '${operations.first['id']}';
    String? problem;
    bool busy = false;
    final values = await showDialog<Json>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
        builder: (context, update) {
          Future<void> act(
            String action,
            Json params, {
            bool resolve = false,
          }) async {
            if (busy) return;
            update(() {
              busy = true;
              problem = null;
            });
            try {
              final result = await m.request('manufacturing.$action', {
                'version': library['version'],
                'fingerprint': library['fingerprint'],
                ...params,
              });
              if (result['ok'] != true) throw StateError('${result['error']}');
              if (!context.mounted) return;
              if (resolve) {
                Navigator.pop(context, result['values'] as Json);
                return;
              }
              update(() {
                library = result;
                busy = false;
              });
            } catch (e) {
              if (context.mounted) {
                update(() {
                  problem = '$e';
                  busy = false;
                });
              }
            }
          }

          return AlertDialog(
            title: const Text('Knife presets'),
            content: SizedBox(
              width: 680,
              height: 480,
              child: SingleChildScrollView(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Presets fill an editable setup form. Confirm the physical blade setup and review generated motion before Apply. Imported and saved presets remain Prototype.',
                    ),
                    if (problem != null)
                      Text(
                        problem!,
                        style: TextStyle(
                          color: Theme.of(context).colorScheme.error,
                        ),
                      ),
                    const SizedBox(height: 12),
                    if ((library['recipes'] as List).isEmpty)
                      const Text('No saved knife presets.'),
                    for (final r in library['recipes'] as List)
                      Card(
                        child: Padding(
                          padding: const EdgeInsets.all(8),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                '${r['name']} — ${r['material']} · ${r['stock_thickness_in']} in',
                              ),
                              Text('${r['catalog_tool_id']} · Prototype'),
                              if (r['compatible'] != true)
                                Text('${r['reason']}'),
                              for (final station in r['stations'] as List)
                                TextButton(
                                  onPressed: busy
                                      ? null
                                      : () => act('knife_resolve', {
                                          'index': r['index'],
                                          'station': station,
                                        }, resolve: true),
                                  child: Text('Review with station $station'),
                                ),
                            ],
                          ),
                        ),
                      ),
                    const Divider(),
                    TextField(
                      controller: path,
                      decoration: const InputDecoration(
                        labelText: 'Knife recipe JSON file path',
                      ),
                    ),
                    TextButton(
                      onPressed: busy
                          ? null
                          : () =>
                                act('knife_import', {'path': path.text.trim()}),
                      child: const Text('Import presets'),
                    ),
                    if (operations.isNotEmpty) ...[
                      const Divider(),
                      DropdownButton<String>(
                        value: operationId,
                        isExpanded: true,
                        items: [
                          for (final o in operations)
                            DropdownMenuItem(
                              value: '${o['id']}',
                              child: Text('${o['name']}'),
                            ),
                        ],
                        onChanged: busy
                            ? null
                            : (v) => update(() => operationId = v),
                      ),
                      TextField(
                        controller: name,
                        decoration: const InputDecoration(
                          labelText: 'New preset name',
                        ),
                      ),
                      TextButton(
                        onPressed: busy
                            ? null
                            : () => act('knife_save', {
                                'operation_id': operationId,
                                'name': name.text.trim(),
                              }),
                        child: const Text('Save applied operation as preset'),
                      ),
                    ] else
                      const Text(
                        'Apply a knife operation to save its settings as a preset.',
                      ),
                  ],
                ),
              ),
            ),
            actions: [
              TextButton(
                onPressed: busy ? null : () => Navigator.pop(context),
                child: const Text('Close'),
              ),
            ],
          );
        },
      ),
    );
    // Controllers live through the dialog route's dismissal animation.
    await Future<void>.delayed(const Duration(milliseconds: 250));
    path.dispose();
    name.dispose();
    if (mounted && values != null) specialOperation('drag_knife', null, values);
  }
}
