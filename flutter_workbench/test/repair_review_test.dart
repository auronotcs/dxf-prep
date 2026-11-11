import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/repair_review.dart';
import 'package:workbench_prototype/design/shape_tools_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';

class ReviewModel extends DesignController {
  int editable = 2;
  final commands = <String>[];
  Json? preview;
  ReviewModel() {
    ready = true;
    busy = false;
    state = {
      'capabilities': ['opening_repair_review'],
      'units': 'in',
      'selected': [],
      'entities': <Json>[
        {
          'id': 'line',
          'kind': 'line',
          'start': {'x': 0, 'y': 0},
          'end': {'x': 1, 'y': 0},
          'bounds': [0, 0, 1, 0],
        },
      ],
    };
  }
  @override
  Future<void> start() async {}

  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    commands.add(command);
    if (command == 'design.preview_shapes') {
      preview = params;
      return {'ok': false, 'error': 'Test preview'};
    }
    return {
      'ok': true,
      'review': {
        'token': 'review',
        'gaps': 1,
        'backtracks': 0,
        'affectedCount': 2,
        'editableCount': editable,
        'blockedCount': 2 - editable,
        'entities': entities,
        'markers': {
          'joins': [
            {'x': 1, 'y': 0},
          ],
        },
      },
    };
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    commands.add(command);
    state['selected'] = ['line'];
    return true;
  }

  @override
  Future<void> savePreferences(Json value) async {
    preferences = value;
  }
}

void main() {
  testWidgets('Opening a drawing triggers review without applying repairs', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(1200, 850);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final model = ReviewModel();
    final opened = ReviewModel();
    addTearDown(model.dispose);
    const channel = MethodChannel('workbench/design');
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      channel,
      (_) async => 'synthetic.camjob.json',
    );
    addTearDown(
      () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        channel,
        null,
      ),
    );
    await tester.pumpWidget(
      DesignApp(
        controller: model,
        startBackend: false,
        controllerFactory: () => opened,
      ),
    );
    await tester.pumpAndSettle();
    await tester.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
    await tester.sendKeyEvent(LogicalKeyboardKey.keyO);
    await tester.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
    await tester.pumpAndSettle();
    expect(model.commands, isEmpty);
    expect(opened.commands, ['project.open', 'design.repair_scan']);
    expect(find.text('Contour check'), findsOneWidget);
    await tester.tap(find.text('Keep drawing unchanged'));
    await tester.pumpAndSettle();
    expect(opened.commands.contains('design.review_repair'), isFalse);
    expect(opened.commands.contains('design.apply_shapes'), isFalse);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
    'Opening check is nonmutating until review; repair still requires Preview and Apply',
    (tester) async {
      final m = ReviewModel();
      await tester.pumpWidget(
        MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () => reviewOpeningRepair(context, m),
                child: const Text('Check'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('Check'));
      await tester.pumpAndSettle();
      expect(find.textContaining('1 small endpoint'), findsOneWidget);
      await tester.tap(find.text('Keep drawing unchanged'));
      await tester.pumpAndSettle();
      expect(m.commands, ['design.repair_scan']);
      await tester.tap(find.text('Check'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Review repair'));
      await tester.pumpAndSettle();
      expect(find.byType(ShapeToolsDialog), findsOneWidget);
      expect(m.commands.contains('design.apply_shapes'), isFalse);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(m.preview!['opening_review'], isTrue);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      m.editable = 0;
      await tester.tap(find.text('Check'));
      await tester.pumpAndSettle();
      expect(find.text('Review repair'), findsNothing);
      expect(find.textContaining('2 contour piece(s) cannot'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );
}
