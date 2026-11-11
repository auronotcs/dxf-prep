import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'design_workspace_test.dart' show TestDesignController;

class ManufacturingModel extends TestDesignController {
  final calls = <String>[];
  Json? applied;
  bool reject = false, running = false;
  ManufacturingModel() {
    state['manufacturing'] = <String, dynamic>{
      'editable': true,
      'tools': <Json>[],
      'operations': <Json>[],
      'scope': 'Basic profiles',
    };
    state['setup'] = <String, dynamic>{
      'material': 'Plywood',
      'material_thickness': .5,
      'safe_z': .25,
    };
    state['entities'] = <Json>[
      {
        'id': 'entity_123456',
        'kind': 'rectangle',
        'layer': '0',
        'bounds': [2, 2, 6, 4],
        'corner_a': {'x': 2, 'y': 2},
        'corner_b': {'x': 6, 'y': 4},
      },
    ];
    state['selected'] = <String>['entity_123456'];
  }
  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    calls.add(command);
    applied = params;
    if (reject) {
      message = 'Cutter is used by an operation';
      return false;
    }
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
    return true;
  }

  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    calls.add(command);
    if (command == 'manufacturing.export') {
      applied = params;
      return {
        'ok': true,
        'manufacturing': {
          'status': 'exported',
          'paths': ['build/manufacturing-output/test.cnc'],
        },
      };
    }
    return {
      'ok': true,
      'manufacturing': {
        'status': running ? 'running' : 'ready',
        'token': 'review-1',
        'motionCount': 3,
        'validation': {
          'issues': [
            {'severity': 'warning', 'message': 'Review hold-down'},
          ],
        },
        'motions': [
          [
            {'kind': 'rapid', 'x': 2, 'y': 2, 'z': .25},
            {'kind': 'linear', 'x': 2, 'y': 2, 'z': -.5},
            {
              'kind': 'arc_cw',
              'x': 3,
              'y': 3,
              'z': -.5,
              'center': {'x': 3, 'y': 2},
            },
          ],
        ],
      },
    };
  }

  void externalEdit() {
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
  }
}

void main() {
  Future<ManufacturingModel> mount(WidgetTester t) async {
    t.view.physicalSize = const Size(1400, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = ManufacturingModel();
    addTearDown(m.dispose);
    await t.pumpWidget(
      MaterialApp(
        home: ManufacturingWorkspace(
          initialInspectorOpen: true,
          model: m,
          onSave: () async => true,
        ),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, String text) async {
    if (find.text(text).evaluate().isEmpty) {
      final scroll = find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first;
      t.state<ScrollableState>(scroll).position.jumpTo(0);
      await t.pumpAndSettle();
      await t.scrollUntilVisible(
        find.text(text),
        250,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
    }
    await Scrollable.ensureVisible(t.element(find.text(text)), alignment: .5);
    await t.pumpAndSettle();
    await t.pumpAndSettle();
    await Scrollable.ensureVisible(t.element(find.text(text)), alignment: .5);
    await t.pumpAndSettle();
    await t.pumpAndSettle();
    await t.tap(find.text(text));
    await t.pumpAndSettle();
  }

  testWidgets(
    'Add cutter cannot overwrite an occupied station; short contour IDs render',
    (t) async {
      final m = await mount(t);
      m.state['manufacturing']['tools'] = [
        {
          'station': 1,
          'name': 'Existing cutter',
          'diameter': .25,
          'feed_rate': 2,
        },
      ];
      m.state['entities'][0]['id'] = 'a';
      m.state['selected'] = ['a'];
      m.externalEdit();
      await t.pumpAndSettle();
      await click(t, 'Add cutter');
      await t.enterText(
        find.widgetWithText(TextField, 'Cutter station (1-12)'),
        '1',
      );
      await click(t, 'Apply');
      expect(m.calls, isEmpty);
      expect(
        find.textContaining('That cutter station is occupied'),
        findsOneWidget,
      );
      await click(t, 'Cancel');
      await click(t, 'Add profile');
      expect(find.text('rectangle - 0 - a'), findsOneWidget);
      await click(t, 'Cancel');
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Stock draft cancel and validation preserve the job; successful apply uses explicit units',
    (t) async {
      final m = await mount(t);
      await click(t, 'Stock / job setup');
      await t.enterText(
        find.widgetWithText(TextField, 'material thickness (in)'),
        '-1',
      );
      await click(t, 'Cancel');
      expect(m.calls, isEmpty);
      await click(t, 'Stock / job setup');
      await t.enterText(
        find.widgetWithText(TextField, 'material thickness (in)'),
        'bad',
      );
      await click(t, 'Apply');
      expect(m.calls, isEmpty);
      expect(
        find.textContaining('Enter a valid material thickness'),
        findsOneWidget,
      );
      await t.enterText(
        find.widgetWithText(TextField, 'material thickness (in)'),
        '.75',
      );
      await click(t, 'Apply');
      expect(m.calls, ['manufacturing.setup']);
      expect(m.applied?['material_thickness'], .75);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Profile editor sends chosen contours and keeps failed draft open',
    (t) async {
      final m = await mount(t);
      m.reject = true;
      await click(t, 'Add profile');
      expect(find.textContaining('rectangle - 0'), findsOneWidget);
      await click(t, 'Apply');
      expect(m.applied?['entity_ids'], ['entity_123456']);
      expect(find.text('Cutter is used by an operation'), findsOneWidget);
      expect(find.text('Add basic profile'), findsOneWidget);
      m.reject = false;
      await click(t, 'Apply');
      expect(find.text('Add basic profile'), findsNothing);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Output requires review; document changes discard the preview and review token',
    (t) async {
      final m = await mount(t);
      await click(t, 'Generate and check toolpaths');
      await t.scrollUntilVisible(
        find.text('Export reviewed package'),
        250,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
      await t.ensureVisible(find.text('Export reviewed package'));
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Export reviewed package'),
            )
            .onPressed,
        isNull,
      );
      await click(t, 'I reviewed the toolpath motion and preflight messages.');
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Export reviewed package'),
            )
            .onPressed,
        isNotNull,
      );
      m.externalEdit();
      await t.pumpAndSettle();
      expect(find.text('Export reviewed package'), findsNothing);
      await click(t, 'Generate and check toolpaths');
      await click(t, 'I reviewed the toolpath motion and preflight messages.');
      await click(t, 'Export reviewed package');
      expect(m.applied?['token'], 'review-1');
      expect(m.applied?['reviewed'], true);
      expect(find.text('build/manufacturing-output/test.cnc'), findsOneWidget);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Leaving a running preview cancels its document worker; compact layout fits',
    (t) async {
      final m = await mount(t);
      t.view.physicalSize = const Size(700, 800);
      await t.pumpAndSettle();
      m.running = true;
      await t.scrollUntilVisible(
        find.text('Generate and check toolpaths'),
        200,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
      await t.ensureVisible(find.text('Generate and check toolpaths'));
      await t.tap(find.text('Generate and check toolpaths'));
      await t.pump();
      await t.pumpWidget(const MaterialApp(home: SizedBox()));
      await t.pump();
      expect(m.calls, contains('manufacturing.cancel'));
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Design opens Manufacturing through the header and retains its drawing',
    (t) async {
      final m = ManufacturingModel();
      addTearDown(m.dispose);
      t.view.physicalSize = const Size(1400, 900);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      await click(t, 'Manufacturing');
      expect(find.byType(ManufacturingWorkspace), findsOneWidget);
      await t.tap(find.byKey(const ValueKey('workspace-design')));
      await t.pumpAndSettle();
      expect(find.byType(ManufacturingWorkspace), findsNothing);
      expect(m.entities.single['id'], 'entity_123456');
      expect(t.takeException(), isNull);
    },
  );
}
