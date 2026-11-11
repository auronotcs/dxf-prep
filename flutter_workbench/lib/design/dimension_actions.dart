import 'package:flutter/material.dart';
import 'controller.dart';
import 'dimension_dialog.dart';

Future<bool> deleteDimension(
  BuildContext context,
  DesignController m,
  Json d,
) async {
  final revision = m.state['revision'], annotation = d['annotation'] as Json;
  if (m.busy ||
      !m.ready ||
      m.readOnly ||
      m.locked(annotation) ||
      !m.visible(annotation)) {
    return false;
  }
  final yes = await showDialog<bool>(
    context: context,
    builder: (context) => AlertDialog(
      title: const Text('Delete this dimension?'),
      content: Text(d['label'].toString()),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context, false),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: () => Navigator.pop(context, true),
          child: const Text('Delete'),
        ),
      ],
    ),
  );
  if (yes != true || !context.mounted) return false;
  if (revision != m.state['revision']) {
    m.showMessage('Drawing changed. Try the dimension action again.');
    return false;
  }
  return m.run('design.dimension_delete', {'id': d['id']});
}

Future<void> showDimensionMenu(
  BuildContext context,
  DesignController m,
  Json d,
  Offset position,
) async {
  final revision = m.state['revision'], a = d['annotation'] as Json;
  final editable =
      !m.busy && m.ready && !m.readOnly && !m.locked(a) && m.visible(a);
  final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
  final local = overlay.globalToLocal(position);
  final action = await showMenu<String>(
    context: context,
    position: RelativeRect.fromRect(
      local & Size.zero,
      Offset.zero & overlay.size,
    ),
    items: [
      PopupMenuItem(
        enabled: false,
        child: Text('${a['kind']} · ${d['label']}'),
      ),
      PopupMenuItem(
        value: 'edit',
        enabled: editable,
        child: const Text('Edit dimension…'),
      ),
      PopupMenuItem(
        value: 'delete',
        enabled: editable,
        child: const Text('Delete dimension…'),
      ),
    ],
  );
  if (action == null || !context.mounted) return;
  if (revision != m.state['revision']) {
    m.showMessage('Drawing changed. Try the dimension action again.');
    return;
  }
  if (action == 'edit') {
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => DimensionDialog(model: m, annotation: a),
    );
  } else {
    await deleteDimension(context, m, d);
  }
}
