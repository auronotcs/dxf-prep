import 'package:flutter/material.dart';
import 'controller.dart';

Future<Json?> profileContourControls(
  BuildContext context,
  DesignController model,
  Json operation, {
  bool linked = false,
}) {
  final ids = (operation['entity_ids'] as List).cast<String>();
  final stored = (operation['manual_contour_order'] as List? ?? [])
      .cast<String>();
  final order = [
    ...stored.where(ids.contains),
    ...ids.where((id) => !stored.contains(id)),
  ];
  final sides = Map<String, dynamic>.from(
    operation['entity_sides'] as Map? ?? {},
  );
  bool manual = stored.isNotEmpty;
  return showDialog<Json>(
    context: context,
    builder: (context) => StatefulBuilder(
      builder: (context, update) => AlertDialog(
        scrollable: true,
        title: Text(
          linked
              ? 'Group contour compensation and cut order'
              : 'Contour compensation and cut order',
        ),
        content: SizedBox(
          width: 620,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Text(
                'Choose each contour’s cut side. Use the arrow buttons to change the manual sequence. Nested cuts must precede their containing part. The draft regenerates actual motion before Apply.',
              ),
              CheckboxListTile(
                title: const Text('Use manual contour order'),
                value: manual,
                onChanged: (v) => update(() => manual = v == true),
              ),
              SizedBox(
                height: 340,
                child: ListView.builder(
                  itemCount: order.length,
                  itemBuilder: (context, index) {
                    final id = order[index];
                    final e = model.entities
                        .where((e) => e['id'] == id)
                        .firstOrNull;
                    return ListTile(
                      key: ValueKey(id),
                      leading: SizedBox(
                        width: 96,
                        child: Row(
                          children: [
                            IconButton(
                              tooltip: 'Move contour up',
                              icon: const Icon(Icons.arrow_upward),
                              onPressed: index == 0
                                  ? null
                                  : () => update(() {
                                      order.insert(
                                        index - 1,
                                        order.removeAt(index),
                                      );
                                      manual = true;
                                    }),
                            ),
                            IconButton(
                              tooltip: 'Move contour down',
                              icon: const Icon(Icons.arrow_downward),
                              onPressed: index == order.length - 1
                                  ? null
                                  : () => update(() {
                                      order.insert(
                                        index + 1,
                                        order.removeAt(index),
                                      );
                                      manual = true;
                                    }),
                            ),
                          ],
                        ),
                      ),
                      title: Text(
                        '${index + 1}. ${e?['kind'] ?? 'Missing contour'} · ${e?['layer'] ?? ''} · ${id.length > 10 ? id.substring(id.length - 10) : id}',
                      ),
                      trailing: DropdownButton<String>(
                        value: sides[id] ?? 'default',
                        items: [
                          DropdownMenuItem(
                            value: 'default',
                            child: Text('Default (${operation['side']})'),
                          ),
                          for (final side in [
                            'inside',
                            'outside',
                            if (!linked) 'on',
                          ])
                            DropdownMenuItem(value: side, child: Text(side)),
                        ],
                        onChanged: (v) => update(() {
                          if (v == 'default') {
                            sides.remove(id);
                          } else {
                            sides[id] = v!;
                          }
                        }),
                      ),
                    );
                  },
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, <String, dynamic>{
              'id': operation['id'],
              'entity_sides': sides,
              'manual_contour_order': manual ? order : <String>[],
            }),
            child: const Text('Prepare contour draft'),
          ),
        ],
      ),
    ),
  );
}

Future<Json?> operationSequenceControls(
  BuildContext context,
  Json operation,
  List<Json> operations, {
  bool linked = false,
}) async {
  var role = operation['sequence_role'] as String? ?? 'automatic';
  var locked = operation['sequence_locked'] == true;
  final dependencies = (operation['depends_on_ids'] as List? ?? [])
      .cast<String>()
      .toSet();
  final priority = TextEditingController(
    text: '${operation['sequence_priority'] ?? 0}',
  );
  String? error;
  try {
    return await showDialog<Json>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) => AlertDialog(
          title: Text(
            linked
                ? 'Whole-group sequence and prerequisites'
                : 'Operation sequence and prerequisites',
          ),
          content: SizedBox(
            width: 520,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Text(
                    'Process role and priority guide ordering; prerequisites are hard constraints. The regenerated draft shows the resulting operation sequence.',
                  ),
                  DropdownButtonFormField<String>(
                    initialValue: role,
                    decoration: const InputDecoration(
                      labelText: 'Process role',
                    ),
                    items: [
                      for (final v in [
                        'automatic',
                        'mark_engrave',
                        'drill_thread',
                        'shelf_recess',
                        'pocket_fill',
                        'inside_profile',
                        'outside_profile',
                        'finish_cutoff',
                      ])
                        DropdownMenuItem(value: v, child: Text(v)),
                    ],
                    onChanged: (v) => role = v!,
                  ),
                  TextField(
                    controller: priority,
                    decoration: const InputDecoration(
                      labelText: 'Sequence priority (-999 to 999)',
                    ),
                  ),
                  CheckboxListTile(
                    title: const Text('Lock current sequence position'),
                    value: locked,
                    onChanged: (v) => update(() => locked = v == true),
                  ),
                  const Text('Must run after:'),
                  for (final other in operations.where(
                    (o) => o['id'] != operation['id'],
                  ))
                    CheckboxListTile(
                      title: Text('${other['name']}'),
                      subtitle: Text(
                        other['enabled'] == false
                            ? 'Disabled: enable it before output'
                            : 'T${other['tool_station']}',
                      ),
                      value: dependencies.contains(other['id']),
                      onChanged: (v) => update(() {
                        if (v == true) {
                          dependencies.add(other['id'] as String);
                        } else {
                          dependencies.remove(other['id']);
                        }
                      }),
                    ),
                  if (error != null)
                    Text(
                      error!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
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
              onPressed: () {
                final n = int.tryParse(priority.text);
                if (n == null || n < -999 || n > 999) {
                  update(
                    () => error = 'Enter a whole priority from -999 to 999.',
                  );
                  return;
                }
                Navigator.pop(context, <String, dynamic>{
                  'id': operation['id'],
                  'sequence_role': role,
                  'sequence_priority': n,
                  'sequence_locked': locked,
                  'depends_on_ids': dependencies.toList(),
                });
              },
              child: const Text('Prepare sequence draft'),
            ),
          ],
        ),
      ),
    );
  } finally {
    await Future<void>.delayed(const Duration(milliseconds: 250));
    priority.dispose();
  }
}
