import 'package:flutter/material.dart';
import 'controller.dart';

String layerLabel(String name) => name == '0' ? '0 (Default)' : name;

class LayersDialog extends StatefulWidget {
  const LayersDialog({
    super.key,
    required this.model,
    required this.current,
    required this.onCurrent,
  });
  final DesignController model;
  final String current;
  final ValueChanged<String> onCurrent;
  @override
  State<LayersDialog> createState() => _LayersDialogState();
}

class _LayersDialogState extends State<LayersDialog> {
  late String current = widget.current;
  String? error;
  DesignController get m => widget.model;
  List<Json> get layers => [
    if (!m.layers.any((l) => l['name'] == '0'))
      {'name': '0', 'objects': 0, 'visible': true, 'locked': false},
    ...m.layers,
  ];

  void choose(String name) {
    setState(() => current = name);
    widget.onCurrent(name);
  }

  Future<bool> run(String command, Json params) async {
    final ok = await m.run(command, params);
    if (mounted) setState(() => error = ok ? null : m.message);
    return ok;
  }

  Future<void> addLayer() async {
    var input = '';
    String? validation;
    final name = await showDialog<String>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, update) {
          void submit() {
            final name = input.trim();
            if (name.isEmpty || layers.any((l) => l['name'] == name)) {
              update(
                () => validation = name.isEmpty
                    ? 'Enter a layer name.'
                    : 'That layer already exists.',
              );
            } else {
              Navigator.pop(context, name);
            }
          }

          return AlertDialog(
            title: const Text('Add layer'),
            content: SizedBox(
              width: 300,
              child: TextField(
                onChanged: (value) => input = value,
                autofocus: true,
                decoration: InputDecoration(
                  labelText: 'Layer name',
                  hintText: 'e.g. Cut, Engrave, Notes',
                  errorText: validation,
                ),
                onSubmitted: (_) => submit(),
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(context),
                child: const Text('Cancel'),
              ),
              FilledButton(
                onPressed: submit,
                child: const Text('Create layer'),
              ),
            ],
          );
        },
      ),
    );
    if (name == null || !mounted) return;
    if (await run('layer.set', {
          'name': name,
          'visible': true,
          'locked': false,
        }) &&
        mounted) {
      choose(name);
    }
  }

  Future<void> deleteLayer(Json layer) async {
    final name = layer['name'] as String;
    final count = layer['objects'] ?? 0;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text('Delete layer “$name”?'),
        content: Text(
          count == 0
              ? 'This layer is empty. You can restore it with Undo.'
              : '$count objects will move to layer 0 (Default). No geometry will be deleted. You can undo this change.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('Delete layer'),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted) return;
    if (await run('design.delete_layer', {'name': name}) &&
        mounted &&
        current == name) {
      choose('0');
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: m,
    builder: (context, _) {
      final canEdit = m.ready && !m.busy && !m.readOnly;
      final canDelete = (m.state['capabilities'] as List? ?? []).contains(
        'delete_layer',
      );
      return AlertDialog(
        title: const Text('Layers'),
        content: SizedBox(
          width: 600,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text(
                'The drawing layer receives new objects. Selecting it does not move existing objects.',
              ),
              const SizedBox(height: 12),
              FilledButton.icon(
                onPressed: canEdit ? addLayer : null,
                icon: const Icon(Icons.add, size: 18),
                label: const Text('Add layer'),
              ),
              if (!canDelete)
                const Padding(
                  padding: EdgeInsets.only(top: 12),
                  child: Text(
                    'To enable layer deletion, save your drawing and restart development mode once to load the updated backend.',
                  ),
                ),
              if (m.readOnly)
                const Text(
                  'Layer editing is unavailable for this view-only job.',
                ),
              if (error != null)
                Text(
                  error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              const SizedBox(height: 12),
              Flexible(
                child: SingleChildScrollView(
                  child: Column(
                    children: [
                      for (final layer in layers)
                        Row(
                          children: [
                            Expanded(
                              child: TextButton.icon(
                                onPressed:
                                    !m.busy &&
                                        layer['locked'] != true &&
                                        layer['visible'] != false
                                    ? () => choose(layer['name'])
                                    : null,
                                style: TextButton.styleFrom(
                                  alignment: Alignment.centerLeft,
                                ),
                                icon: Icon(
                                  current == layer['name']
                                      ? Icons.radio_button_checked
                                      : Icons.radio_button_unchecked,
                                  size: 18,
                                ),
                                label: Text(
                                  layerLabel(layer['name']),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            ),
                            Text(
                              '${layer['objects'] ?? 0} objects',
                              style: const TextStyle(fontSize: 11),
                            ),
                            IconButton(
                              tooltip: 'Visibility: ${layer['name']}',
                              onPressed: canEdit
                                  ? () => run('layer.set', {
                                      'name': layer['name'],
                                      'visible': layer['visible'] == false,
                                    })
                                  : null,
                              icon: Icon(
                                layer['visible'] == false
                                    ? Icons.visibility_off_outlined
                                    : Icons.visibility_outlined,
                                size: 18,
                              ),
                            ),
                            IconButton(
                              tooltip: 'Lock: ${layer['name']}',
                              onPressed: canEdit
                                  ? () => run('layer.set', {
                                      'name': layer['name'],
                                      'locked': layer['locked'] != true,
                                    })
                                  : null,
                              icon: Icon(
                                layer['locked'] == true
                                    ? Icons.lock_outline
                                    : Icons.lock_open,
                                size: 18,
                              ),
                            ),
                            Tooltip(
                              message: layer['name'] == '0'
                                  ? 'The default layer cannot be deleted'
                                  : layer['locked'] == true
                                  ? 'Unlock this layer before deleting it'
                                  : 'Delete this layer; keep its objects on layer 0',
                              child: TextButton(
                                onPressed:
                                    canEdit &&
                                        canDelete &&
                                        layer['name'] != '0' &&
                                        layer['locked'] != true
                                    ? () => deleteLayer(layer)
                                    : null,
                                child: const Text('Delete'),
                              ),
                            ),
                          ],
                        ),
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Done'),
          ),
        ],
      );
    },
  );
}
