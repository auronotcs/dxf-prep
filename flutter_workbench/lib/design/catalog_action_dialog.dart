import 'package:flutter/material.dart';
import 'controller.dart';

class CatalogActionDialog extends StatefulWidget {
  const CatalogActionDialog({
    super.key,
    required this.model,
    required this.entry,
    required this.version,
    required this.recipeIndex,
    required this.action,
  });
  final DesignController model;
  final Json entry;
  final String version, action;
  final int recipeIndex;
  @override
  State<CatalogActionDialog> createState() => _CatalogActionDialogState();
}

class _CatalogActionDialogState extends State<CatalogActionDialog> {
  final name = TextEditingController();
  bool busy = false;
  String? error;
  bool get tool => widget.action == 'catalog_tool_duplicate';
  bool get duplicate => tool || widget.action == 'catalog_recipe_duplicate';
  Json get recipe =>
      (widget.entry['recipes'] as List)[widget.recipeIndex] as Json;
  String get title => tool
      ? 'Duplicate custom tool'
      : duplicate
      ? 'Duplicate recipe'
      : widget.action == 'catalog_recipe_remove'
      ? 'Remove recipe'
      : recipe['qualification'] == 'retired'
      ? 'Reactivate recipe'
      : 'Retire recipe';
  @override
  void initState() {
    super.initState();
    if (duplicate) {
      name.text = '${tool ? widget.entry['name'] : recipe['material']} copy';
    }
  }

  @override
  void dispose() {
    name.dispose();
    super.dispose();
  }

  Future<void> save() async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final reply = await widget.model
          .request('manufacturing.${widget.action}', {
            'id': widget.entry['id'],
            'version': widget.version,
            if (!tool) 'recipe_index': widget.recipeIndex,
            if (duplicate) 'name': name.text.trim(),
            if (!duplicate) 'confirmed': true,
            if (widget.action == 'catalog_recipe_status')
              'retired': recipe['qualification'] != 'retired',
          });
      if (reply['ok'] != true) throw StateError('${reply['error']}');
      if (mounted) Navigator.pop(context, reply);
    } catch (e) {
      if (mounted) {
        setState(() {
          busy = false;
          error = '$e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: !busy,
    child: AlertDialog(
      title: Text(title),
      scrollable: true,
      content: SizedBox(
        width: 520,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              tool
                  ? '${widget.entry['name']} · ${(widget.entry['recipes'] as List).length} recipes'
                  : '${widget.entry['name']} · ${recipe['material']}',
            ),
            const SizedBox(height: 12),
            Text(
              duplicate
                  ? 'The copy receives a new identity. All copied recipes start as Prototype, with physical-test qualification cleared.'
                  : widget.action == 'catalog_recipe_remove'
                  ? 'Remove this recipe from the reusable library? This cannot be undone through drawing history.'
                  : recipe['qualification'] == 'retired'
                  ? 'Reactivate as Prototype. Record a new physical test before marking it Shop tested.'
                  : 'Retired recipes cannot be loaded or matched to new jobs. You can reactivate them later.',
            ),
            const SizedBox(height: 12),
            const Text(
              'Recipes already loaded into drawings and existing turret drafts remain unchanged. Load and review library changes separately.',
            ),
            if (duplicate)
              Padding(
                padding: const EdgeInsets.only(top: 16),
                child: TextField(
                  controller: name,
                  enabled: !busy,
                  decoration: const InputDecoration(
                    labelText: 'New name',
                    border: OutlineInputBorder(),
                  ),
                ),
              ),
            if (error != null)
              Padding(
                padding: const EdgeInsets.only(top: 12),
                child: Text(
                  error!,
                  style: TextStyle(color: Theme.of(context).colorScheme.error),
                ),
              ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: busy ? null : () => Navigator.pop(context),
          child: const Text('Cancel'),
        ),
        FilledButton(
          onPressed: busy ? null : save,
          child: Text(busy ? 'Saving…' : title),
        ),
      ],
    ),
  );
}
