import 'package:flutter/material.dart';
import 'controller.dart';
import 'cleave_dialog.dart';

/// Reuse the manager's atomic, revision/digest-checked draft for a direct edit.
Future<bool> applyGuideAction(
  DesignController model,
  int revision,
  Json action,
) => model.editGuide(revision, action);

Future<void> showGuideMenu(
  BuildContext context,
  DesignController model,
  Json guide,
  Offset position,
) async {
  final revision = model.state['revision'] as int;
  final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
  final local = overlay.globalToLocal(position);
  final locked = guide['locked'] == true;
  final editable = !model.readOnly && !model.busy && model.ready;
  final choice = await showMenu<String>(
    context: context,
    position: RelativeRect.fromRect(
      local & Size.zero,
      Offset.zero & overlay.size,
    ),
    items: [
      PopupMenuItem<String>(
        enabled: false,
        child: Text(
          '${guide['orientation'] == 'horizontal' ? 'Horizontal' : 'Vertical'} guide · ${guide['position']} ${model.units}',
        ),
      ),
      PopupMenuItem(
        value: 'cleave',
        enabled:
            editable &&
            model.selection.isNotEmpty &&
            model.state['guideCleave'] == true,
        child: const Text('Split selection along guide…'),
      ),
      PopupMenuItem(
        value: 'edit',
        enabled: editable && !locked,
        child: const Text('Edit guide…'),
      ),
      PopupMenuItem(
        value: 'locked',
        enabled: editable,
        child: Text(locked ? 'Unlock guide' : 'Lock guide'),
      ),
      PopupMenuItem(
        value: 'visible',
        enabled: editable,
        child: const Text('Hide guide'),
      ),
      PopupMenuItem(
        value: 'delete',
        enabled: editable && !locked,
        child: const Text('Delete guide'),
      ),
    ],
  );
  if (choice == null || !context.mounted) return;
  if (choice == 'cleave') {
    if (model.state['revision'] != revision) {
      model.showMessage('Drawing changed. Open the guide menu again.');
      return;
    }
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) =>
          CleaveDialog(model: model, guideId: guide['id'] as String),
    );
    return;
  }
  Json? action;
  if (choice == 'edit') {
    action = await showDialog<Json>(
      context: context,
      builder: (_) => GuideCoordinateDialog(guide: guide, units: model.units),
    );
  } else {
    action = {
      'operation': choice,
      'id': guide['id'],
      if (choice == 'locked') 'value': !locked,
      if (choice == 'visible') 'value': false,
    };
  }
  if (action != null && context.mounted) {
    await applyGuideAction(model, revision, action);
  }
}

class GuideCoordinateDialog extends StatefulWidget {
  const GuideCoordinateDialog({
    super.key,
    required this.guide,
    required this.units,
  });
  final Json guide;
  final String units;
  @override
  State<GuideCoordinateDialog> createState() => _GuideCoordinateDialogState();
}

class _GuideCoordinateDialogState extends State<GuideCoordinateDialog> {
  late final coordinate = TextEditingController(
    text: '${widget.guide['position']}',
  );
  late final name = TextEditingController(
    text: widget.guide['name'] as String? ?? '',
  );
  String? error;
  @override
  void dispose() {
    coordinate.dispose();
    name.dispose();
    super.dispose();
  }

  void save() {
    final value = double.tryParse(coordinate.text);
    if (value == null || !value.isFinite) {
      setState(() => error = 'Enter a finite coordinate.');
      return;
    }
    Navigator.pop(context, <String, dynamic>{
      'operation': 'edit',
      'id': widget.guide['id'],
      'position': value,
      'name': name.text,
    });
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
    title: const Text('Edit guide'),
    content: SizedBox(
      width: 320,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          TextField(
            controller: coordinate,
            autofocus: true,
            decoration: InputDecoration(
              labelText:
                  '${widget.guide['orientation'] == 'horizontal' ? 'Y' : 'X'} coordinate (${widget.units})',
              errorText: error,
            ),
            onSubmitted: (_) => save(),
          ),
          TextField(
            controller: name,
            decoration: const InputDecoration(labelText: 'Name (optional)'),
          ),
        ],
      ),
    ),
    actions: [
      TextButton(
        onPressed: () => Navigator.pop(context),
        child: const Text('Cancel'),
      ),
      FilledButton(onPressed: save, child: const Text('Save guide')),
    ],
  );
}
