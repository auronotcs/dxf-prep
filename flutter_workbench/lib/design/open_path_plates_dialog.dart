import 'package:flutter/material.dart';
import 'controller.dart';

class OpenPathPlatesDialog extends StatefulWidget {
  const OpenPathPlatesDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<OpenPathPlatesDialog> createState() => _OpenPathPlatesDialogState();
}

class _OpenPathPlatesDialogState extends State<OpenPathPlatesDialog> {
  Json? data;
  String? error;
  bool busy = false;
  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final reply = await widget.model.request('manufacturing.open_paths');
      if (reply['ok'] != true) throw StateError('${reply['error']}');
      if (mounted) setState(() => data = reply['manufacturing'] as Json);
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> assign(Json row, int number) async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final ok = await widget.model.run('manufacturing.open_path_assign', {
        'id': row['id'],
        'plate_number': number == 0 ? null : number,
        'token': data!['token'],
      });
      if (!ok) throw StateError(widget.model.message);
      if (mounted) await refresh();
    } catch (e) {
      if (mounted) setState(() => error = '$e');
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Open-path plate assignments'),
    content: SizedBox(
      width: 700,
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'Each open cut belongs to one plate. Its complete geometry and direction are preserved, including travel beyond stock. Assignment does not trim the drawing. Changes apply immediately and can be undone.',
            ),
            if (busy) const LinearProgressIndicator(),
            if (error != null)
              SelectableText(
                error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            if (data != null && (data!['paths'] as List).isEmpty)
              const Padding(
                padding: EdgeInsets.all(16),
                child: Text('No open paths in this drawing.'),
              ),
            for (final row in (data?['paths'] as List? ?? []).cast<Json>()) ...[
              const Divider(),
              SelectableText('Path ${row['id']} · Layer ${row['layer']}'),
              Text(
                'Enabled operations: ${(row['operations'] as List).join(', ').isEmpty ? 'none' : (row['operations'] as List).join(', ')}',
              ),
              Text(
                row['owner'] == null
                    ? 'Unassigned — choose a touched plate before output.'
                    : 'Output owner: Plate ${row['owner']}${row['overtravel'] == true ? ' · extends beyond stock' : ''}',
              ),
              Text(
                'Source bounds (${data!['units']}): ${(row['bounds'] as List? ?? []).map((v) => (v as num).toStringAsFixed(3)).join(', ')}',
              ),
              if (row['assigned'] != null)
                Text('Saved assignment: Plate ${row['assigned']}'),
              Row(
                children: [
                  Expanded(
                    child: DropdownButtonFormField<int>(
                      key: ValueKey('${row['id']}-${data!['token']}'),
                      initialValue:
                          row['assigned'] is int &&
                              (row['choices'] as List).contains(row['assigned'])
                          ? row['assigned'] as int
                          : 0,
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: 'Output plate',
                      ),
                      items: [
                        const DropdownMenuItem(
                          value: 0,
                          child: Text('Automatic ownership'),
                        ),
                        for (final n in row['choices'] as List)
                          DropdownMenuItem(
                            value: n as int,
                            child: Text('Plate $n'),
                          ),
                      ],
                      onChanged: busy || row['editable'] != true
                          ? null
                          : (v) {
                              if (v != null) assign(row, v);
                            },
                    ),
                  ),
                  TextButton(
                    onPressed: busy
                        ? null
                        : () async {
                            await widget.model.run('selection.set', {
                              'ids': [row['id']],
                            });
                          },
                    child: const Text('Select path'),
                  ),
                ],
              ),
              if (row['editable'] != true)
                const Text(
                  'Hidden, locked or reference geometry cannot be reassigned.',
                ),
            ],
          ],
        ),
      ),
    ),
    actions: [
      TextButton(
        onPressed: busy ? null : refresh,
        child: const Text('Refresh'),
      ),
      TextButton(
        onPressed: busy ? null : () => Navigator.pop(context),
        child: const Text('Done'),
      ),
    ],
  );
}
