import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

void main() {
  Future<ExpandedModel> mount(WidgetTester t) async {
    final m = ExpandedModel();
    addTearDown(m.dispose);
    t.view.physicalSize = const Size(1450, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    m.state['entities'].add(<String, dynamic>{
      'id': 'second',
      'kind': 'rectangle',
      'layer': '0',
      'bounds': [10, 2, 14, 4],
      'corner_a': {'x': 10, 'y': 2},
      'corner_b': {'x': 14, 'y': 4},
    });
    m.state['manufacturing']['operations'] = [
      <String, dynamic>{
        'id': 'op',
        'name': 'Outside profile',
        'kind': 'router_offset',
        'enabled': true,
        'tool_station': 1,
        'entity_ids': ['entity_123456', 'second'],
        'side': 'outside',
        'target_depth': .5,
        'safe_z': .25,
        'stepdown': .125,
        'profile_retention': <String, dynamic>{},
      },
      <String, dynamic>{
        'id': 'prior',
        'name': 'Prior drilling',
        'kind': 'drill_circles',
        'enabled': true,
        'tool_station': 2,
        'entity_ids': <String>[],
        'profile_retention': <String, dynamic>{},
      },
    ];
    m.response = {'status': 'failed', 'error': 'Synthetic draft only'};
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
    final f = find.text(text);
    if (f.evaluate().isEmpty) {
      final scroll = find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first;
      t.state<ScrollableState>(scroll).position.jumpTo(0);
      await t.pumpAndSettle();
      await t.scrollUntilVisible(
        f,
        200,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
    }
    await Scrollable.ensureVisible(t.element(f), alignment: .5);
    await t.pumpAndSettle();
    await Scrollable.ensureVisible(t.element(f), alignment: .5);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  Future<void> menu(WidgetTester t, String text) async {
    final f = find.byTooltip('Operation workflow').first;
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
    await click(t, text);
  }

  testWidgets('Per-contour compensation and manual order use reviewed draft', (
    t,
  ) async {
    final m = await mount(t);
    await menu(t, 'Contour compensation / order');
    await t.tap(find.byType(DropdownButton<String>).first);
    await t.pumpAndSettle();
    await t.tap(find.text('inside').last);
    await t.pumpAndSettle();
    await click(t, 'Use manual contour order');
    await click(t, 'Prepare contour draft');
    expect(m.requestedCommand, 'manufacturing.management_prepare');
    expect(m.requested!['action'], 'profile_controls');
    expect(m.requested!['params']['entity_sides'], {'entity_123456': 'inside'});
    expect(m.requested!['params']['manual_contour_order'], [
      'entity_123456',
      'second',
    ]);
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets('Moving a contour changes its proposed manual order', (t) async {
    final m = await mount(t);
    await menu(t, 'Contour compensation / order');
    await t.tap(find.byTooltip('Move contour down').first);
    await t.pumpAndSettle();
    await click(t, 'Prepare contour draft');
    expect(m.requested!['params']['manual_contour_order'], [
      'second',
      'entity_123456',
    ]);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Sequence rejects fractional priority and includes prerequisites',
    (t) async {
      final m = await mount(t);
      await menu(t, 'Sequence / prerequisites');
      final priority = find.byWidgetPredicate(
        (w) =>
            w is TextField &&
            w.decoration?.labelText == 'Sequence priority (-999 to 999)',
      );
      await t.enterText(priority, '1.5');
      await click(t, 'Prepare sequence draft');
      expect(m.requestedCommand, isNull);
      await t.enterText(priority, '5');
      await t.tap(find.widgetWithText(CheckboxListTile, 'Prior drilling'));
      await t.pumpAndSettle();
      await click(t, 'Lock current sequence position');
      await click(t, 'Prepare sequence draft');
      expect(m.requested!['params']['depends_on_ids'], ['prior']);
      expect(m.requested!['params']['sequence_priority'], 5);
      expect(m.requested!['params']['sequence_locked'], true);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Geometry rule binding remains a draft until reviewed Apply', (
    t,
  ) async {
    final m = await mount(t);
    m.response = {
      'status': 'management_ready',
      'action': 'associate',
      'token': 'workflow',
      'motions': [],
      'validation': {'issues': []},
      'details': {
        'geometry_changes': [
          {
            'name': 'Outside profile',
            'before': ['a'],
            'after': ['a', 'b'],
            'selector': {
              'layers': ['0'],
            },
          },
        ],
        'operation_order': [],
      },
    };
    await menu(t, 'Bind geometry selection rule');
    expect(m.requested!['params'], {'id': 'op', 'enabled': true});
    await click(t, 'Apply workflow draft');
    expect(m.calls, isEmpty);
    await click(
      t,
      'I reviewed the source geometry, operation order and regenerated motion.',
    );
    await click(t, 'Apply workflow draft');
    expect(m.calls, contains('manufacturing.management_apply'));
    expect(m.applied!['token'], 'workflow');
    expect(t.takeException(), isNull);
  });
  testWidgets('Revised DXF uses file chooser and cancel makes no request', (
    t,
  ) async {
    final m = await mount(t);
    const channel = MethodChannel('workbench/design');
    String? path;
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(channel, (call) async => path);
    addTearDown(
      () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(channel, null),
    );
    await click(t, 'Review revised DXF');
    expect(m.requestedCommand, isNull);
    path = 'C:/evaluation/revision.dxf';
    await click(t, 'Review revised DXF');
    expect(m.requested!['action'], 'revision');
    expect(m.requested!['params']['path'], path);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Protected imported groups can request verification without enabling edits',
    (t) async {
      final m = await mount(t);
      m.state['manufacturing']['editable'] = false;
      m.state['manufacturing']['operations'][0]['profile_retention'] = {
        'group_id': 'imported',
      };
      m.notifyListeners();
      await t.pumpAndSettle();
      await click(t, 'Verify imported linked workflows');
      expect(m.requested!['action'], 'import_groups');
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Retained cleanup exposes per-part count and target plate', (
    t,
  ) async {
    final m = await mount(t);
    await menu(t, 'Plan retained tab cleanup');
    await click(t, 'Apply');
    expect(m.requested!['action'], 'retained_cleanup');
    expect(m.requested!['params']['count'], 2);
    expect(m.requested!['params']['plate'], 1);
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
}
