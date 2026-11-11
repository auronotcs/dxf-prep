import 'package:flutter/material.dart';
import 'controller.dart';

/// Explicit annotation selection, since guides are intentionally not geometry.
Future<Json?> chooseClipboardItems(
  BuildContext context,
  DesignController m, {
  String? dimensionId,
}) {
  final dimensions = (m.state['dimensions'] as List? ?? []).cast<Json>();
  final guides = (m.state['guides'] as List? ?? []).cast<Json>();
  final selectedDimensions = <String>{?dimensionId};
  final selectedGuides = <String>{};
  var geometry = m.selection.isNotEmpty;
  return showDialog<Json>(
    context: context,
    builder: (context) => StatefulBuilder(
      builder: (context, update) {
        final any =
            geometry ||
            selectedDimensions.isNotEmpty ||
            selectedGuides.isNotEmpty;
        final lockedGuide = guides.any(
          (g) => selectedGuides.contains(g['id']) && g['locked'] == true,
        );
        Json result(bool cut) => {
          'cut': cut,
          'include_geometry': geometry,
          'dimension_ids': selectedDimensions.toList(),
          'guide_ids': selectedGuides.toList(),
        };
        return AlertDialog(
          title: const Text('Copy geometry, dimensions and guides'),
          content: SizedBox(
            width: 520,
            height: 360,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                const Text(
                  'References stay linked to copied geometry. Other references become fixed measurement points. Paste uses matching drawing units. Guides never become cutting geometry.',
                ),
                CheckboxListTile(
                  title: Text('Selected geometry (${m.selection.length})'),
                  value: geometry,
                  onChanged: m.selection.isEmpty
                      ? null
                      : (v) => update(() => geometry = v!),
                ),
                Expanded(
                  child: ListView(
                    children: [
                      for (final d in dimensions)
                        CheckboxListTile(
                          title: Text(
                            '${d['annotation']['kind']}: ${d['label'] ?? d['id']}',
                          ),
                          subtitle: Text('Layer ${d['annotation']['layer']}'),
                          value: selectedDimensions.contains(d['id']),
                          onChanged:
                              !m.visible(d['annotation'] as Json) ||
                                  m.locked(d['annotation'] as Json)
                              ? null
                              : (v) => update(() {
                                  v!
                                      ? selectedDimensions.add(
                                          d['id'] as String,
                                        )
                                      : selectedDimensions.remove(d['id']);
                                }),
                        ),
                      for (final g in guides)
                        CheckboxListTile(
                          title: Text(
                            '${g['orientation']} guide at ${g['position']} ${m.units}',
                          ),
                          subtitle: Text(
                            '${g['name'] ?? ''}${g['locked'] == true ? ' (locked; copy only)' : ''}',
                          ),
                          value: selectedGuides.contains(g['id']),
                          onChanged: g['visible'] != true
                              ? null
                              : (v) => update(() {
                                  v!
                                      ? selectedGuides.add(g['id'] as String)
                                      : selectedGuides.remove(g['id']);
                                }),
                        ),
                    ],
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
            OutlinedButton(
              onPressed: any && !lockedGuide && !m.readOnly
                  ? () => Navigator.pop(context, result(true))
                  : null,
              child: const Text('Cut'),
            ),
            FilledButton(
              onPressed: any
                  ? () => Navigator.pop(context, result(false))
                  : null,
              child: const Text('Copy'),
            ),
          ],
        );
      },
    ),
  );
}
