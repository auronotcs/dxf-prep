import 'package:flutter/material.dart';
import 'controller.dart';

class ClosedOverrunsDialog extends StatefulWidget {
  const ClosedOverrunsDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<ClosedOverrunsDialog> createState() => _ClosedOverrunsDialogState();
}

class _ClosedOverrunsDialogState extends State<ClosedOverrunsDialog> {
  Json? data;
  String? error;
  bool busy = false;
  final Map<String, int> choices = {};
  @override
  void initState() {
    super.initState();
    refresh();
  }

  Future<void> refresh() async {
    setState(() => busy = true);
    final reply = await widget.model.request('manufacturing.closed_overruns');
    if (!mounted) return;
    setState(() {
      busy = false;
      error = reply['ok'] == true ? null : '${reply['error']}';
      data = reply['manufacturing'] as Json?;
      choices.clear();
    });
  }

  Future<void> apply(Json row, int? number) async {
    final token = data!['token'];
    final approved = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text(
          number == null
              ? 'Revoke overrun approval?'
              : 'Approve complete closed outline?',
        ),
        content: Text(
          number == null
              ? 'Output will be blocked if this contour still crosses stock.'
              : 'Plate $number · ${row['id']}\nSource bounds: ${row['bounds']} ${data!['units']}\nThe complete contour is retained. Confirm available machine travel and fixture clearance beyond stock; cutter travel may exceed the source outline.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Confirm'),
          ),
        ],
      ),
    );
    if (!mounted || approved != true) return;
    setState(() => busy = true);
    final ok = await widget.model.run('manufacturing.closed_overrun_assign', {
      'id': row['id'],
      'plate_number': number,
      'token': token,
      'reviewed': true,
    });
    if (!mounted) return;
    if (ok) {
      await refresh();
    } else {
      setState(() {
        busy = false;
        error = widget.model.message;
      });
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Closed-outline plate overruns'),
    content: SizedBox(
      width: 680,
      child: SingleChildScrollView(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              'Maximum source overrun per edge: ${data?['limit'] ?? '…'} ${data?['units'] ?? ''}. Approval is bound to this exact geometry, plate and units. Changes require a fresh review. Each change is undoable.',
            ),
            if (busy) const LinearProgressIndicator(),
            if (error != null)
              Text(
                error!,
                style: TextStyle(color: Theme.of(context).colorScheme.error),
              ),
            if (data != null && (data!['rows'] as List).isEmpty)
              const Text('No eligible or previously approved closed outlines.'),
            for (final row in (data?['rows'] as List? ?? []).cast<Json>()) ...[
              const Divider(),
              SelectableText('${row['id']} · Layer ${row['layer']}'),
              Text(
                'Bounds: ${row['bounds']} · Operations: ${(row['operations'] as List).join(', ')}',
              ),
              Text(
                row['stale'] == true
                    ? 'Approval is stale; output must be reviewed again.'
                    : 'Approved plate: ${row['owner'] ?? 'none'}',
              ),
              for (final p in (row['choices'] as List).cast<Json>())
                CheckboxListTile(
                  dense: true,
                  value: choices[row['id']] == p['number'],
                  onChanged: busy
                      ? null
                      : (v) => setState(() {
                          if (v == true) {
                            choices[row['id']] = p['number'] as int;
                          } else {
                            choices.remove(row['id']);
                          }
                        }),
                  title: Text('Plate ${p['number']}'),
                  subtitle: Text(
                    'Left / right / bottom / top: ${(p['edges'] as List).map((n) => (n as num).toStringAsFixed(4)).join(' / ')} ${data!['units']}',
                  ),
                ),
              Wrap(
                spacing: 8,
                children: [
                  FilledButton(
                    onPressed:
                        busy ||
                            row['editable'] != true ||
                            choices[row['id']] == null
                        ? null
                        : () => apply(row, choices[row['id']]),
                    child: const Text('Review approval'),
                  ),
                  TextButton(
                    onPressed:
                        busy || row['editable'] != true || row['saved'] == null
                        ? null
                        : () => apply(row, null),
                    child: const Text('Revoke'),
                  ),
                ],
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
