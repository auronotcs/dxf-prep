import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/patchwork_dialog.dart';
import 'package:workbench_prototype/design/shape_tools_dialog.dart';

class PatchModel extends DesignController {
  final calls = <String>[];
  Json? params;
  bool failPreview = false;
  PatchModel() {
    ready = true;
    state = {
      'units': 'in',
      'activePlate': 1,
      'patchworkPacking': true,
      'capabilities': ['patchwork'],
      'plates': [
        {'number': 1, 'x': 100, 'y': 0, 'width': 96, 'height': 48},
      ],
      'patchworkAreas': [
        {
          'id': 'drop',
          'name': 'Drop',
          'plate_number': 1,
          'x': 0,
          'y': 0,
          'width': 12,
          'height': 6,
          'bounds': [100, 0, 112, 6],
        },
      ],
    };
  }
  @override
  Future<Json> request(String command, [Json p = const {}]) async {
    calls.add(command);
    params = p;
    if (failPreview) {
      return {'ok': false, 'error': 'Piece does not fit; resize the envelope.'};
    }
    if (command == 'design.preview_shapes') {
      return {'ok': false, 'error': 'Test arrangement request'};
    }
    return {
      'ok': true,
      'preview': {
        'token': 'layout',
        'areas': p['repeat'] != null ? state['patchworkAreas'] : p['areas'],
        'plate': {
          ...(state['plates'] as List).first as Json,
          ...?p['envelope'] as Json?,
        },
        'envelopeChanged':
            p['envelope']?['width'] != (state['plates'] as List).first['width'],
      },
    };
  }

  @override
  Future<bool> run(String command, [Json p = const {}]) async {
    calls.add(command);
    params = p;
    return true;
  }
}

void main() {
  Future<void> open(
    WidgetTester tester,
    Widget child, {
    Size size = const Size(1266, 714),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () =>
                showDialog<void>(context: context, builder: (_) => child),
            child: const Text('Open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
  }

  testWidgets(
    'Patchwork drafts validate before Apply and edits invalidate the token',
    (tester) async {
      final m = PatchModel();
      await open(tester, PatchworkDialog(model: m));
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply layout'),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.text('Add pieces'));
      await tester.pumpAndSettle();
      expect(m.calls, ['design.patchwork_preview']);
      expect(m.params!['repeat']['count'], 1);
      await tester.enterText(find.byType(TextField).first, 'Changed');
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply layout'),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.text('Drop'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Remove selected'));
      await tester.pumpAndSettle();
      expect(m.params!['areas'], isEmpty);
      expect(m.calls.contains('design.patchwork_apply'), isFalse);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(m.calls.contains('design.patchwork_apply'), isFalse);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets('Patchwork applies only the validated layout', (tester) async {
    final m = PatchModel();
    await open(tester, PatchworkDialog(model: m));
    await tester.tap(find.text('Preview layout'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Apply layout'));
    await tester.pumpAndSettle();
    expect(m.calls.last, 'design.patchwork_apply');
    expect(m.params, {'token': 'layout'});
    expect(tester.takeException(), isNull);
  });
  testWidgets('Arrangement exposes named Patchwork references', (tester) async {
    final m = PatchModel();
    await open(tester, ShapeToolsDialog(controller: m, tool: 'align_left'));
    await tester.tap(find.text('Selection bounds'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Patchwork cut area').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('Drop · Plate 1'), findsOneWidget);
    await tester.tap(find.text('Preview'));
    await tester.pumpAndSettle();
    expect(m.params!['patchwork_id'], 'drop');
    expect(m.params!['reference'], 'patchwork');
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'Table preset, automatic placement and packing remain draft until Apply',
    (tester) async {
      final m = PatchModel();
      await open(tester, PatchworkDialog(model: m));
      await tester.tap(find.text('Use 120 × 62 in table'));
      await tester.pumpAndSettle();
      expect(m.params!['envelope'], {'width': 120.0, 'height': 62.0});
      expect(find.textContaining('120.0 × 62.0'), findsOneWidget);
      expect(
        find.textContaining('Plate size changes on Apply'),
        findsOneWidget,
      );
      expect((m.state['plates'] as List).first['width'], 96);
      await tester.ensureVisible(find.text('Auto-place piece'));
      await tester.tap(find.text('Auto-place piece'));
      await tester.pumpAndSettle();
      expect(m.params!['auto_place'], {
        'name': 'Piece',
        'width': 12.0,
        'height': 6.0,
        'direction': 'x',
      });
      expect(m.params!.containsKey('repeat'), false);
      await tester.tap(find.text('X'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Y').last);
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply layout'),
            )
            .onPressed,
        isNull,
      );
      await tester.ensureVisible(find.text('Pack all pieces'));
      await tester.tap(find.text('Pack all pieces'));
      await tester.pumpAndSettle();
      expect(m.params!['pack_direction'], 'y');
      expect(m.params!['envelope'], {'width': 120.0, 'height': 62.0});
      await tester.ensureVisible(find.text('Restore opening plate size'));
      await tester.tap(find.text('Restore opening plate size'));
      await tester.pumpAndSettle();
      expect(m.params!['envelope'], {'width': 96, 'height': 48});
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(m.calls.contains('design.patchwork_apply'), false);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Millimeter preset and failed preview stay usable in a compact window',
    (tester) async {
      final m = PatchModel();
      m.state['units'] = 'mm';
      await open(tester, PatchworkDialog(model: m), size: const Size(820, 520));
      await tester.tap(find.text('Use 120 × 62 in table'));
      await tester.pumpAndSettle();
      expect(m.params!['envelope'], {'width': 3048.0, 'height': 1574.8});
      m.failPreview = true;
      await tester.ensureVisible(find.text('Pack all pieces'));
      await tester.tap(find.text('Pack all pieces'));
      await tester.pumpAndSettle();
      expect(
        find.text('Piece does not fit; resize the envelope.'),
        findsOneWidget,
      );
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply layout'),
            )
            .onPressed,
        isNull,
      );
      expect(find.text('Drop'), findsOneWidget);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(m.calls.contains('design.patchwork_apply'), false);
      expect(tester.takeException(), isNull);
    },
  );
}
