import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/shape_tools_dialog.dart';

class ShapeModel extends DesignController {
  final previews = <Json>[];
  final applied = <String>[];
  bool failApply = false;
  ShapeModel() {
    ready = true;
    busy = false;
    state = {
      'units': 'mm',
      'selected': ['rectangle'],
      'entities': <Json>[
        {
          'id': 'rectangle',
          'kind': 'rectangle',
          'corner_a': {'x': 0, 'y': 0},
          'corner_b': {'x': 10, 'y': 5},
          'bounds': [0, 0, 10, 5],
        },
      ],
    };
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    previews.add(params);
    return {
      'ok': true,
      'preview': {
        'token': 'preview-token',
        'entities': entities,
        'sourceCount': 1,
        'removedCount': 0,
        'maximumError': 0,
        'movedCount': 1,
        'gap': params['gap'],
        'referenceBounds': [0, 0, 96, 48],
        'closedCount': 0,
        'joinCount': 1,
        'removedPoints': 2,
        'backtrackCount': 1,
        'remainingIssues': 1,
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
    applied.add(params['token'] as String);
    message = 'The drawing changed. Preview again.';
    return !failApply;
  }
}

void main() {
  Future<ShapeModel> mount(
    WidgetTester tester,
    String tool, {
    Size size = const Size(900, 800),
    void Function(ShapeModel)? configure,
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final model = ShapeModel();
    configure?.call(model);
    addTearDown(model.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => showDialog<void>(
                context: context,
                builder: (_) => ShapeToolsDialog(controller: model, tool: tool),
              ),
              child: const Text('Open'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
    return model;
  }

  bool canApply(WidgetTester tester) =>
      tester
          .widget<FilledButton>(find.widgetWithText(FilledButton, 'Apply'))
          .onPressed !=
      null;

  testWidgets(
    'Corner alignment choices require the backend capability and each new choice clears review',
    (tester) async {
      final model = await mount(
        tester,
        'align_left',
        configure: (m) {
          m.state['arrangementCorners'] = true;
        },
      );
      for (final entry in {
        'align_top_left': 'Align top left',
        'align_top_right': 'Align top right',
        'align_bottom_left': 'Align bottom left',
        'align_bottom_right': 'Align bottom right',
      }.entries) {
        await tester.tap(find.byType(DropdownButtonFormField<String>).first);
        await tester.pumpAndSettle();
        await tester.ensureVisible(find.text(entry.value).last);
        await tester.tap(find.text(entry.value).last);
        await tester.pumpAndSettle();
        expect(canApply(tester), false);
        await tester.tap(find.text('Preview'));
        await tester.pumpAndSettle();
        expect(model.previews.last['action'], entry.key);
        expect(canApply(tester), true);
      }
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();
      expect(model.applied, ['preview-token']);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('Older backend does not advertise corner choices', (
    tester,
  ) async {
    await mount(tester, 'align_left');
    await tester.tap(find.byType(DropdownButtonFormField<String>).first);
    await tester.pumpAndSettle();
    expect(find.text('Align top left'), findsNothing);
    expect(find.text('Align bottom right'), findsNothing);
  });

  testWidgets(
    'Offset uses document units, preview is required, edits invalidate and cancel never applies',
    (tester) async {
      final model = await mount(tester, 'outline');
      expect(
        tester.widget<TextField>(find.byType(TextField)).controller!.text,
        '3.175',
      );
      expect(canApply(tester), isFalse);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.single, {'kind': 'offset', 'distance': 3.175});
      expect(canApply(tester), isTrue);
      await tester.enterText(find.byType(TextField), '-2.54');
      await tester.pump();
      expect(canApply(tester), isFalse);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.last['distance'], -2.54);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(model.applied, isEmpty);
    },
  );

  testWidgets(
    'Conversion controls send mode/scope and only the reviewed proposal is applied',
    (tester) async {
      final model = await mount(tester, 'convertShape');
      await tester.tap(find.text('Repair / recognize circle'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Circle from bounds').last);
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextField).last, '25.4');
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.single, {
        'kind': 'convert',
        'mode': 'circle',
        'scope': 'connected',
        'tolerance': 0.0254,
        'diameter': 25.4,
      });
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();
      expect(model.applied, ['preview-token']);
      expect(find.byType(ShapeToolsDialog), findsNothing);
    },
  );

  testWidgets(
    'Editable outlines stay usable at small window height and stale apply is recoverable',
    (tester) async {
      final model = await mount(
        tester,
        'editableOutline',
        size: const Size(680, 520),
      );
      expect(tester.takeException(), isNull);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.single['mode'], 'outline');
      expect(model.previews.single['scope'], 'each');
      model.failApply = true;
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();
      expect(find.byType(ShapeToolsDialog), findsOneWidget);
      expect(canApply(tester), isFalse);
      expect(find.text('The drawing changed. Preview again.'), findsOneWidget);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Arrangement previews plate and fixed reference, validates gaps and invalidates edits',
    (tester) async {
      final model = await mount(tester, 'space_horizontal_gap');
      expect(
        tester.widget<TextField>(find.byType(TextField)).controller!.text,
        '6.35',
      );
      await tester.tap(find.text('Selection bounds'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Active plate').last);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.last, {
        'kind': 'arrange',
        'action': 'space_horizontal_gap',
        'reference': 'plate',
        'use_body_bounds': true,
        'gap': 6.35,
      });
      expect(canApply(tester), isTrue);
      await tester.tap(find.text('Active plate'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Fixed selected object').last);
      await tester.pumpAndSettle();
      expect(canApply(tester), isFalse);
      await tester.enterText(find.byType(TextField), 'NaN');
      await tester.pump();
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.length, 1);
      expect(canApply(tester), isFalse);
      await tester.enterText(find.byType(TextField), '2');
      await tester.pump();
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.last['reference_item_id'], 'rectangle');
      expect(model.previews.last['gap'], 2);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(model.applied, isEmpty);
    },
  );

  testWidgets(
    'Associated items use backend identities and body bounds edits invalidate preview',
    (tester) async {
      final model = await mount(
        tester,
        'align_top',
        configure: (m) {
          m.state['arrangementItems'] = <Json>[
            {
              'id': 'assembly',
              'entityIds': ['rectangle', 'hole'],
              'label': 'Panel and hole',
            },
          ];
          (m.state['entities'] as List).add(<String, dynamic>{
            'id': 'hole',
            'kind': 'circle',
            'center': {'x': 2, 'y': 2},
            'radius': 1,
            'bounds': [1, 1, 3, 3],
          });
        },
      );
      await tester.tap(find.text('Selection bounds'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Fixed selected object').last);
      await tester.pumpAndSettle();
      expect(find.text('Panel and hole'), findsOneWidget);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.single['reference_item_id'], 'assembly');
      expect(model.previews.single['use_body_bounds'], true);
      expect(canApply(tester), isTrue);
      await tester.ensureVisible(find.text('Use original body bounds'));
      await tester.tap(find.text('Use original body bounds'));
      await tester.pumpAndSettle();
      expect(canApply(tester), isFalse);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.last['use_body_bounds'], false);
      expect(model.selection, {'rectangle'});
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Locked references stay outside selection and require a fresh preview',
    (tester) async {
      final model = await mount(
        tester,
        'align_top',
        size: const Size(760, 650),
        configure: (m) {
          m.state['capabilities'] = ['locked_arrangement_reference'];
          m.state['lockedArrangementReferences'] = [
            {
              'id': 'anchor',
              'entityIds': ['anchor'],
              'label': 'Locked panel',
              'bounds': [20, 20, 30, 30],
            },
            {
              'id': 'overlap',
              'entityIds': ['rectangle'],
              'label': 'Overlapping group',
              'bounds': [0, 0, 10, 5],
            },
          ];
        },
      );
      await tester.tap(find.text('Selection bounds'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Locked object / group').last);
      await tester.pumpAndSettle();
      expect(find.textContaining('Locked panel'), findsOneWidget);
      expect(find.textContaining('Overlapping group'), findsNothing);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.single, {
        'kind': 'arrange',
        'action': 'align_top',
        'reference': 'locked',
        'use_body_bounds': true,
        'reference_item_id': 'anchor',
      });
      expect(model.selection, {'rectangle'});
      expect(canApply(tester), isTrue);
      expect(tester.takeException(), isNull);
      await tester.ensureVisible(find.text('Locked object / group'));
      await tester.tap(find.text('Locked object / group'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Selection bounds').last);
      await tester.pumpAndSettle();
      expect(canApply(tester), isFalse);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(model.applied, isEmpty);
    },
  );

  testWidgets(
    'Empty locked references explain the next step and disable preview',
    (tester) async {
      await mount(
        tester,
        'align_top',
        configure: (m) {
          m.state['capabilities'] = ['locked_arrangement_reference'];
        },
      );
      await tester.tap(find.text('Selection bounds'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Locked object / group').last);
      await tester.pumpAndSettle();
      expect(
        find.textContaining('No visible locked references'),
        findsOneWidget,
      );
      expect(
        tester
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Preview'),
            )
            .onPressed,
        isNull,
      );
      expect(canApply(tester), isFalse);
    },
  );

  testWidgets(
    'Repair switches mode and tolerance, shows unresolved issues and invalidates preview',
    (tester) async {
      final model = await mount(
        tester,
        'joinRepair',
        size: const Size(760, 650),
      );
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.last, {
        'kind': 'repair',
        'mode': 'join',
        'cleanup': true,
        'backtracks': true,
        'tolerance': .0254,
      });
      expect(
        find.textContaining('WARNING: 1 crossing/overlap'),
        findsOneWidget,
      );
      final summaryRect = tester.getRect(
        find.byKey(const ValueKey('shape-preview-message')),
      );
      expect(summaryRect.top, greaterThan(0));
      expect(
        summaryRect.bottom,
        lessThan(tester.getRect(find.text('Apply')).top),
      );
      expect(summaryRect.bottom, lessThan(650));
      expect(canApply(tester), isTrue);
      await tester.ensureVisible(
        find.text('Remove repeated / negligible points'),
      );
      await tester.tap(find.text('Remove repeated / negligible points'));
      await tester.pumpAndSettle();
      expect(canApply(tester), isFalse);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.last['cleanup'], isFalse);
      await tester.ensureVisible(find.byType(DropdownButtonFormField<String>));
      await tester.tap(find.byType(DropdownButtonFormField<String>));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Near-duplicate points only').last);
      await tester.pumpAndSettle();
      expect(find.byType(CheckboxListTile), findsNothing);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(model.previews.last['mode'], 'points');
      expect(model.previews.last['tolerance'], .00254);
      expect(tester.takeException(), isNull);
    },
  );
}
