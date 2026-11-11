import 'package:flutter/material.dart';
import 'controller.dart';

Future<bool> showRecoveryDialog(
  BuildContext context,
  DesignController model,
) async =>
    await showDialog<bool>(
      context: context,
      barrierDismissible: false,
      builder: (_) => RecoveryDialog(model: model),
    ) ??
    false;

class RecoveryDialog extends StatefulWidget {
  const RecoveryDialog({super.key, required this.model});
  final DesignController model;
  @override
  State<RecoveryDialog> createState() => _RecoveryDialogState();
}

class _RecoveryDialogState extends State<RecoveryDialog> {
  bool working = false;
  String? error;
  DesignController get m => widget.model;
  Future<void> act(String command, Json item) async {
    if (working || m.busy) return;
    if (command == 'recovery.discard') {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
          title: const Text('Discard this recovery copy?'),
          content: const Text(
            'Only this interrupted-session copy will be removed. The original drawing is unchanged.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Discard copy'),
            ),
          ],
        ),
      );
      if (confirmed != true || !mounted) return;
    }
    setState(() {
      working = true;
      error = null;
    });
    final ok = await m.run(command, {'id': item['id'], 'token': item['token']});
    if (!mounted) return;
    if (ok && command == 'recovery.restore') {
      Navigator.pop(context, true);
    } else {
      setState(() {
        working = false;
        error = ok ? null : m.message;
      });
    }
  }

  String stamp(dynamic value) {
    final date = DateTime.tryParse(value?.toString() ?? '')?.toLocal();
    return date == null ? 'Unknown time' : date.toString().split('.').first;
  }

  @override
  Widget build(BuildContext context) => AnimatedBuilder(
    animation: m,
    builder: (context, _) {
      final recovery = m.state['recovery'] as Json? ?? {};
      final entries = (recovery['candidates'] as List? ?? []).cast<Json>();
      final disabled = working || m.busy;
      final canRestore =
          m.ready &&
          !m.dirty &&
          !m.readOnly &&
          m.entities.isEmpty &&
          (m.state['dimensions'] as List? ?? []).isEmpty &&
          (m.state['guides'] as List? ?? []).isEmpty &&
          (m.state['path'] ?? '').isEmpty &&
          m.state['canUndo'] != true &&
          m.state['canRedo'] != true;
      return PopScope(
        canPop: !disabled,
        child: AlertDialog(
          title: const Text('Recover an unsaved drawing'),
          content: SizedBox(
            width: 520,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'Recovery opens a copy in a new, empty drawing. Use Save As afterward. Original files stay unchanged; Undo history starts at the recovered drawing.',
                  ),
                  const SizedBox(height: 12),
                  if (!canRestore)
                    const Text(
                      'Recovery needs an empty drawing. Finish or save your current work, then choose File > New.',
                    ),
                  if (entries.isEmpty)
                    const Text(
                      'No interrupted-session copies found. Copies belonging to running sessions are excluded.',
                    ),
                  for (final item in entries)
                    Card(
                      child: Padding(
                        padding: const EdgeInsets.all(12),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              item['name'] ?? 'Untitled',
                              style: const TextStyle(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            Text(stamp(item['updated'])),
                            if ((item['source'] ?? '').isNotEmpty)
                              SelectableText('Original: ${item['source']}'),
                            if (item['error'] == null)
                              Text(
                                '${item['objects']} objects · ${item['dimensions']} dimensions · ${item['guides']} guides · ${item['units']}',
                              ),
                            if (item['error'] != null)
                              Text(
                                '${item['error']}',
                                style: TextStyle(
                                  color: Theme.of(context).colorScheme.error,
                                ),
                              ),
                            Wrap(
                              spacing: 8,
                              children: [
                                FilledButton(
                                  onPressed:
                                      disabled ||
                                          !canRestore ||
                                          item['error'] != null
                                      ? null
                                      : () => act('recovery.restore', item),
                                  child: const Text('Recover copy'),
                                ),
                                TextButton(
                                  onPressed: disabled
                                      ? null
                                      : () => act('recovery.discard', item),
                                  child: const Text('Discard…'),
                                ),
                              ],
                            ),
                          ],
                        ),
                      ),
                    ),
                  if (error ?? recovery['warning'] case final String warning)
                    Text(
                      warning,
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
              onPressed: disabled
                  ? null
                  : () async {
                      final ok = await m.run('recovery.list');
                      if (mounted) {
                        setState(() => error = ok ? null : m.message);
                      }
                    },
              child: const Text('Refresh'),
            ),
            TextButton(
              onPressed: disabled ? null : () => Navigator.pop(context, false),
              child: const Text('Later'),
            ),
          ],
        ),
      );
    },
  );
}
