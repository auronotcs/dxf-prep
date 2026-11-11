import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/push_fit_dialog.dart';

class FitModel extends DesignController {
  final calls = <String>[];
  final params = <Json>[];
  FitModel() {
    ready = true;
    state = {
      'units': 'in',
      'selected': ['metal'],
      'entities': [
        for (final id in ['metal', 'acrylic'])
          {
            'id': id,
            'kind': 'rectangle',
            'layer': id,
            'corner_a': {'x': 0, 'y': 0},
            'corner_b': {'x': 5, 'y': 4},
            'bounds': [0, 0, 5, 4],
          },
      ],
    };
  }
  @override
  Future<Json> request(String command, [Json p = const {}]) async {
    calls.add(command);
    params.add(p);
    return {
      'ok': true,
      'preview': {
        'token': 'analysis',
        'reviewToken': 'review${calls.length}',
        'sources': entities,
        'entities': entities,
        'acrylic': [],
        'warnings': ['Broad mismatches start skipped.'],
        'removedCount': 0,
        'included': p['included_ids'] ?? ['local'],
        'candidates': [
          for (final id in ['local', 'broad'])
            {
              'id': id,
              'bounds': [0, 0, 1, 1],
              'area': .02,
              'recommended': id == 'local',
              'note': id == 'local'
                  ? 'Local cutter-corner relief'
                  : 'Broad mismatch',
              'entities': entities,
            },
        ],
      },
    };
  }

  @override
  Future<bool> run(String command, [Json p = const {}]) async {
    calls.add(command);
    params.add(p);
    return true;
  }
}

void main() {
  Future<void> open(WidgetTester tester, FitModel model) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => TextButton(
            onPressed: () => showDialog<void>(
              context: context,
              builder: (_) => PushFitDialog(model: model),
            ),
            child: const Text('Open'),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
  }

  testWidgets(
    'Explicit roles, blank clearance, recommended review and settings invalidate Apply',
    (tester) async {
      final model = FitModel();
      await open(tester, model);
      await tester.tap(find.text('Analyze fit'));
      await tester.pumpAndSettle();
      expect(model.calls, isEmpty);
      await tester.tap(find.text('Selection → metal'));
      await tester.pumpAndSettle();
      model.state['selected'] = ['acrylic'];
      await tester.tap(find.text('Selection → acrylic'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).at(1), '.005');
      await tester.tap(find.text('Analyze fit'));
      await tester.pumpAndSettle();
      expect(model.params.single['metal_ids'], ['metal']);
      expect(model.params.single['acrylic_ids'], ['acrylic']);
      expect(
        tester.widget<Checkbox>(find.byType(Checkbox).at(0)).value,
        isTrue,
      );
      expect(
        tester.widget<Checkbox>(find.byType(Checkbox).at(1)).value,
        isFalse,
      );
      await tester.tap(find.byType(Checkbox).at(1));
      await tester.pumpAndSettle();
      expect(model.params.last['included_ids'], ['local', 'broad']);
      await tester.tap(find.text('Zoom to region'));
      await tester.pumpAndSettle();
      expect(find.text('Show whole fit'), findsOneWidget);
      await tester.enterText(find.byType(TextField).first, '.375');
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply metal corrections'),
            )
            .onPressed,
        isNull,
      );
      expect(model.calls.contains('design.push_fit_apply'), isFalse);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets('Apply uses the most recent reviewed token', (tester) async {
    final model = FitModel();
    await open(tester, model);
    await tester.enterText(find.byType(TextField).at(1), '0');
    await tester.tap(find.text('Analyze fit'));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(Checkbox).at(1));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Apply metal corrections'));
    await tester.pumpAndSettle();
    expect(model.calls.last, 'design.push_fit_apply');
    expect(model.params.last['review_token'], 'review2');
    expect(find.byType(PushFitDialog), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
