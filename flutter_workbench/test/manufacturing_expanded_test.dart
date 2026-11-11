import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'package:workbench_prototype/design/stock_material.dart';
import 'manufacturing_test.dart' show ManufacturingModel;

class ExpandedModel extends ManufacturingModel {
  Json? requested;
  String? requestedCommand;
  Json? response;
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    requested = params;
    requestedCommand = command;
    if (response != null && command != 'manufacturing.cancel') {
      return {'ok': true, 'manufacturing': response};
    }
    return super.request(command, params);
  }
}

void main() {
  Future<ExpandedModel> mount(
    WidgetTester t, {
    bool bar = false,
    String units = 'in',
  }) async {
    final m = ExpandedModel();
    m.state['units'] = units;
    addTearDown(m.dispose);
    t.view.physicalSize = const Size(1300, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(
      MaterialApp(
        home: bar
            ? Scaffold(body: StockMaterialControls(model: m))
            : ManufacturingWorkspace(
                initialInspectorOpen: true,
                model: m,
                onSave: () async => true,
              ),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, String value) async {
    if (find.text(value).evaluate().isEmpty) {
      final scroll = find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first;
      t.state<ScrollableState>(scroll).position.jumpTo(0);
      await t.pumpAndSettle();
      await t.scrollUntilVisible(
        find.text(value),
        250,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
    }
    await Scrollable.ensureVisible(t.element(find.text(value)), alignment: .5);
    await t.pumpAndSettle();
    await t.tap(find.text(value));
    await t.pumpAndSettle();
  }

  testWidgets(
    'Stock bar uses Tkinter materials and converts physical thickness presets',
    (t) async {
      final m = await mount(t, bar: true, units: 'mm');
      await t.tap(find.byKey(const ValueKey('stock-material')));
      await t.pumpAndSettle();
      await click(t, 'Aluminum');
      expect(m.applied, {'material': 'Aluminum'});
      await t.tap(find.byKey(const ValueKey('stock-thickness')));
      await t.pumpAndSettle();
      await click(t, '.125 in');
      expect(m.applied!['material_thickness'], closeTo(3.175, 1e-9));
      await t.tap(find.byKey(const ValueKey('stock-thickness')));
      await t.pumpAndSettle();
      await click(t, '3 mm');
      expect(m.applied!['material_thickness'], closeTo(3, 1e-9));
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Custom material and thickness validate and cancel without changes',
    (t) async {
      final m = await mount(t, bar: true);
      await t.tap(find.byKey(const ValueKey('stock-material')));
      await t.pumpAndSettle();
      await click(t, 'Custom material...');
      await t.enterText(find.byType(TextField), 'Baltic birch');
      await click(t, 'Apply');
      expect(m.applied, {'material': 'Baltic birch'});
      final count = m.calls.length;
      await t.tap(find.byKey(const ValueKey('stock-thickness')));
      await t.pumpAndSettle();
      await click(t, 'Custom thickness (in)...');
      await t.enterText(find.byType(TextField), 'NaN');
      await click(t, 'Apply');
      expect(m.calls.length, count);
      expect(find.textContaining('positive finite thickness'), findsOneWidget);
      await t.enterText(find.byType(TextField), '.72');
      await click(t, 'Cancel');
      expect(m.calls.length, count);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Drilling selects circles only and cleanout separates boundaries and islands',
    (t) async {
      final m = await mount(t);
      m.state['entities'].add({
        'id': 'entity_hole',
        'kind': 'circle',
        'layer': '0',
        'center': {'x': 3, 'y': 3},
        'radius': .5,
        'bounds': [2.5, 2.5, 3.5, 3.5],
      });
      m.state['selected'] = <String>['entity_hole'];
      m.externalEdit();
      await t.pumpAndSettle();
      await click(t, 'Add drilling');
      expect(find.textContaining('rectangle - 0'), findsNothing);
      await click(t, 'Apply');
      expect(m.calls.last, 'manufacturing.drill_save');
      expect(m.applied!['entity_ids'], ['entity_hole']);
      m.state['selected'] = <String>['entity_123456'];
      m.externalEdit();
      await t.pumpAndSettle();
      await click(t, 'Add cleanout');
      await click(t, 'circle - hole');
      await click(t, 'Apply');
      expect(m.calls.last, 'manufacturing.fill_save');
      expect(m.applied!['entity_ids'], ['entity_123456']);
      expect(m.applied!['island_entity_ids'], ['entity_hole']);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Second-tool draft splits primary inputs and requires review before atomic apply',
    (t) async {
      final m = await mount(t);
      m.state['manufacturing']['operations'] = [
        {
          'id': 'op_1',
          'name': 'Primary',
          'kind': 'router_offset',
          'enabled': true,
          'tool_station': 1,
          'target_depth': .5,
          'safe_z': .25,
          'stepdown': .125,
          'side': 'inside',
          'cut_direction': 'climb',
          'entity_ids': ['entity_123456'],
          'profile_retention': <String, dynamic>{},
        },
      ];
      m.externalEdit();
      await t.pumpAndSettle();
      m.response = {
        'status': 'group_ready',
        'token': 'group-token',
        'skin': .025,
        'thickness': .5,
        'stages': [
          {
            'name': 'Held primary',
            'tool_station': 1,
            'target_depth': .475,
            'depends_on_ids': [],
          },
        ],
        'validation': {'issues': []},
        'motions': [],
        'geometry': m.entities,
      };
      await t.ensureVisible(find.byTooltip('Second-tool workflow'));
      await t.tap(find.byTooltip('Second-tool workflow'));
      await t.pumpAndSettle();
      await click(t, 'Retained skin');
      await click(t, 'Apply');
      expect(m.requestedCommand, 'manufacturing.group_prepare');
      expect(m.requested!['source']['side'], 'inside');
      expect(m.requested!['settings']['skin'], .025);
      await t.scrollUntilVisible(
        find.text('Apply complete group'),
        250,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
      await t.ensureVisible(find.text('Apply complete group'));
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply complete group'),
            )
            .onPressed,
        isNull,
      );
      await click(
        t,
        'I reviewed retained material, linked stages, cutter feeds/reach and motion.',
      );
      await click(t, 'Apply complete group');
      expect(m.calls.last, 'manufacturing.group_apply');
      expect(m.applied, {
        'token': 'group-token',
        'reviewed': true,
        'release_reviewed': false,
      });
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Preflight findings highlight affected contours even when motion is blocked',
    (t) async {
      final m = await mount(t);
      m.response = {
        'status': 'blocked',
        'validation': {
          'issues': [
            {
              'severity': 'error',
              'code': 'test.contour',
              'message': 'Check this contour',
              'entity_ids': ['entity_123456'],
              'plate': 1,
              'point': {'x': 3, 'y': 3},
            },
          ],
        },
        'previews': [
          {
            'plate': {'number': 1},
            'geometry': m.entities,
            'motions': [],
          },
        ],
      };
      await click(t, 'Generate and check toolpaths');
      await click(t, 'error: Check this contour');
      await t.scrollUntilVisible(
        find.text('1 affected contours highlighted in pink.'),
        150,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
      expect(
        find.text('1 affected contours highlighted in pink.'),
        findsOneWidget,
      );
      final painter = t
          .widgetList<CustomPaint>(find.byType(CustomPaint))
          .map((w) => w.painter)
          .whereType<MotionPainter>()
          .single;
      expect(painter.focusedIssue!['entity_ids'], ['entity_123456']);
      expect(painter.focusedIssue!['point'], {'x': 3, 'y': 3});
      expect(find.text('Export reviewed package'), findsNothing);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Selected plates are explicit and changing scope discards reviewed output',
    (t) async {
      final m = ExpandedModel();
      addTearDown(m.dispose);
      m.state['plates'] = [
        {'number': 1, 'name': 'One'},
        {'number': 2, 'name': 'Two'},
      ];
      t.view.physicalSize = const Size(1300, 1000);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
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
      await click(t, 'Plate 1 - One');
      await click(t, 'Generate and check toolpaths');
      expect(m.requested!['plates'], [2]);
      await click(t, 'I reviewed the toolpath motion and preflight messages.');
      await click(t, 'Plate 1 - One');
      expect(m.requestedCommand, 'manufacturing.cancel');
      m.state['plates'] = [
        {'number': 1, 'name': 'One'},
      ];
      m.externalEdit();
      await t.pumpAndSettle();
      await click(t, 'Generate and check toolpaths');
      expect(m.requested!['plates'], [1]);

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
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Export reviewed package'),
            )
            .onPressed,
        isNull,
      );
      expect(t.takeException(), isNull);
    },
  );
}
