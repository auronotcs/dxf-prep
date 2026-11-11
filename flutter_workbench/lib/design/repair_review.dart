import 'package:flutter/material.dart';
import 'controller.dart';
import 'shape_tools_dialog.dart';
import 'small_geometry_dialog.dart';

Future<void> reviewOpeningRepair(
  BuildContext context,
  DesignController model, {
  List<String>? entityIds,
  bool alwaysShow = false,
}) async {
  if (!(model.state['capabilities'] as List? ?? []).contains(
    'opening_repair_review',
  )) {
    return;
  }
  try {
    if (!alwaysShow &&
        await recommendSmallGeometry(context, model, entityIds: entityIds)) {
      return;
    }
    if (!context.mounted) return;
    final result = await model.request('design.repair_scan', {
      'entity_ids': ?entityIds,
    });
    if (!context.mounted) return;
    if (result['ok'] != true) throw StateError(result['error'].toString());
    final review = result['review'] as Json;
    if (number(review['affectedCount']) == 0 && !alwaysShow) return;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Contour check'),
        content: SizedBox(
          width: 620,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${review['gaps']} small endpoint gap(s), ${review['backtracks']} microscopic backtrack(s) in ${review['affectedCount']} contour piece(s).',
                ),
                const SizedBox(height: 12),
                if (number(review['affectedCount']) > 0) ...[
                  const Text(
                    'Review the repair preview before applying. Nothing has changed. Purple marks gaps; orange marks backtracks (up to 500 markers of each kind).',
                  ),
                  Container(
                    height: 170,
                    color: Colors.white,
                    margin: const EdgeInsets.symmetric(vertical: 8),
                    child: CustomPaint(
                      painter: ShapePreviewPainter(
                        (review['entities'] as List).cast<Json>(),
                        const [],
                        markers: review['markers'] as Json,
                      ),
                      child: const SizedBox.expand(),
                    ),
                  ),
                ] else
                  const Text(
                    'No unambiguous small gaps or microscopic backtracks were found. This is a focused check, not a full geometry inspection.',
                  ),
                if (number(review['blockedCount']) > 0)
                  Text(
                    '${review['blockedCount']} contour piece(s) cannot be repaired here because the chain includes hidden/locked objects or the drawing contains machining operations.',
                  ),
                if (number(review['editableCount']) > 0)
                  const Text(
                    'Review selects complete editable chains. Adjust tolerance, Preview, then Apply; each repair is one undo step.',
                  ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('Keep drawing unchanged'),
          ),
          if (number(review['editableCount']) > 0)
            FilledButton(
              onPressed: () => Navigator.pop(context, true),
              child: const Text('Review repair'),
            ),
        ],
      ),
    );
    if (accepted != true || !context.mounted) return;
    if (!await model.run('design.review_repair', {'token': review['token']})) {
      if (context.mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(model.message)));
      }
      return;
    }
    if (!context.mounted) return;
    await showDialog<void>(
      context: context,
      barrierDismissible: false,
      builder: (_) => ShapeToolsDialog(
        controller: model,
        tool: 'joinRepair',
        openingReview: true,
      ),
    );
  } catch (e) {
    if (context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('Contour check could not finish: $e')),
      );
    }
  }
}
