import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

void main() {
  test(
    'Stopped bridge refuses requests without leaving a pending timeout',
    () async {
      final model = DesignController();
      await expectLater(model.request('manufacturing.poll'), throwsStateError);
      expect(model.pending, isEmpty);
      model.dispose();
    },
  );
  Future<ExpandedModel> mount(WidgetTester t) async {
    t.view.physicalSize = const Size(1600, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = ExpandedModel();
    addTearDown(m.dispose);
    m.state['manufacturing']['tools'] = [
      for (final kind in ['engraver', 'drag_knife', 'thread_mill', 'end_mill'])
        <String, dynamic>{
          'station': kind == 'end_mill' ? 2 : 8,
          'name': kind,
          'kind': kind,
          'diameter': .125,
          'max_stepdown': .05,
        },
    ];
    m.state['manufacturing']['operations'] = [
      for (final kind in ['router_offset', 'island_fill'])
        <String, dynamic>{
          'id': kind,
          'name': 'Source $kind',
          'kind': kind,
          'enabled': true,
          'tool_station': 1,
          'entity_ids': ['entity_123456'],
          'target_depth': .2,
          'safe_z': .25,
          'side': 'outside',
          'profile_retention': <String, dynamic>{},
        },
    ];
    m.response = {'status': 'failed', 'error': 'Synthetic motion draft'};
    await t.pumpWidget(
      MaterialApp(
        home: ManufacturingWorkspace(model: m, onSave: () async => true),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, String label) async {
    final f = find.text(label).last;
    if (f.evaluate().isEmpty) {
      final scroll = find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first;
      t.state<ScrollableState>(scroll).position.jumpTo(0);
      await t.pumpAndSettle();
      await t.scrollUntilVisible(f, 200, scrollable: scroll);
    }
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  for (final entry in {
    'Drag engraving': 'engrave',
    'Drag knife': 'drag_knife',
    'Thread milling': 'thread_mill',
    'Pocket rest machining': 'rest_machining',
    'Profile rest machining': 'profile_rest_machining',
  }.entries) {
    testWidgets('${entry.key} opens a private motion draft', (t) async {
      final m = await mount(t);
      if (entry.value == 'thread_mill') {
        m.state['entities'][0]['kind'] = 'circle';
        m.state['entities'][0]['center'] = {'x': 4, 'y': 3};
        m.state['entities'][0]['radius'] = .1035;
      }
      await click(t, 'More');
      await click(t, entry.key);
      await click(t, 'Apply');
      expect(m.requestedCommand, 'manufacturing.operation_prepare');
      expect(m.requested!['command'], 'special_save');
      expect(m.requested!['params']['kind'], entry.value);
      if (entry.value.endsWith('rest_machining')) {
        expect(
          m.requested!['params']['source_operation_id'],
          entry.value == 'rest_machining' ? 'island_fill' : 'router_offset',
        );
        expect(m.requested!['params'].containsKey('entity_ids'), false);
      } else {
        expect(m.requested!['params']['entity_ids'], ['entity_123456']);
      }
      if (entry.value == 'drag_knife') {
        expect(m.requested!['params']['setup_verified'], false);
      }
      if (entry.value == 'thread_mill') {
        expect(m.requested!['params']['cutter_verified'], false);
      }
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    });
  }
  testWidgets('Release audit cannot Apply; restraint and waste are explicit', (
    t,
  ) async {
    final m = await mount(t);
    final details = <String, dynamic>{
      'source_id': 'router_offset',
      'plate_number': 1,
      'fingerprint': 'fp',
      'parts': ['body1', 'body2'],
      'warning_key': 'warning-fp',
      'audit': {
        'hazards': [],
        'warnings': ['loose scrap'],
        'events': [],
        'cleanup_spans': 8,
      },
    };
    m.response = {
      'status': 'management_ready',
      'action': 'release_audit',
      'token': 'audit',
      'motions': [],
      'validation': {'issues': []},
      'details': {
        'final_release': details,
        'operation_order': [],
        'geometry_changes': [],
      },
    };
    await click(t, 'More');
    await click(t, 'Final tab release');
    await click(t, 'Apply');
    expect(m.requested!['action'], 'release_audit');
    await click(t, 'Qualify final release…');
    await click(t, 'Apply');
    expect(
      find.textContaining('Confirm all bodies and describe'),
      findsOneWidget,
    );
    final note = find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.labelText == 'Evidence note',
    );
    await t.enterText(note, 'Synthetic clamp review');
    await click(t, 'All bodies fixed');
    await click(t, 'Waste reviewed');
    await click(t, 'Apply');
    expect(m.requested!['action'], 'final_release');
    expect(m.requested!['params']['confirmed_parts'], ['body1', 'body2']);
    expect(m.requested!['params']['waste_warning_review'], 'warning-fp');
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Graphical release order is re-audited and qualification carries the fresh order and audit key',
    (t) async {
      final m = await mount(t);
      m.state['entities'].add(<String, dynamic>{
        'id': 'other',
        'kind': 'rectangle',
        'layer': '0',
        'corner_a': {'x': 8, 'y': 2},
        'corner_b': {'x': 10, 'y': 4},
        'bounds': [8, 2, 10, 4],
      });
      Json details(List<String> order, String key) => {
        'source_id': 'router_offset',
        'plate_number': 1,
        'fingerprint': 'source-fp',
        'parts': ['entity_123456', 'other'],
        'part_order': order,
        'order_locked': false,
        'warning_key': key,
        'audit': {
          'hazards': [],
          'warnings': ['loose scrap'],
          'events': [],
          'cleanup_spans': 8,
        },
      };
      Json response(Json d) => {
        'status': 'management_ready',
        'action': 'release_audit',
        'token': 'audit',
        'motions': [],
        'validation': {'issues': []},
        'details': {
          'final_release': d,
          'operation_order': [],
          'geometry_changes': [],
        },
      };
      m.response = response(details(['entity_123456', 'other'], 'old-key'));
      await click(t, 'More');
      await click(t, 'Final tab release');
      await click(t, 'Apply');
      await click(t, 'Pick release order in drawing…');
      await click(t, 'Clear picks');
      await t.tap(find.widgetWithText(FilterChip, '2: rectangle · other'));
      await t.pumpAndSettle();
      await t.tap(
        find.widgetWithText(FilterChip, '1: rectangle · entity_123456'),
      );
      await t.pumpAndSettle();
      m.response = response(details(['other', 'entity_123456'], 'fresh-key'));
      await click(t, 'Re-audit release order');
      expect(m.requested!['action'], 'release_audit');
      expect(m.requested!['params']['part_order'], ['other', 'entity_123456']);
      expect(m.requested!['params']['fingerprint'], 'source-fp');
      await click(t, 'Qualify final release…');
      expect(
        t
            .widget<CheckboxListTile>(
              find.widgetWithText(CheckboxListTile, 'All bodies fixed'),
            )
            .value,
        false,
      );
      await t.enterText(
        find.byWidgetPredicate(
          (w) => w is TextField && w.decoration?.labelText == 'Evidence note',
        ),
        'Synthetic restraint test',
      );
      await click(t, 'All bodies fixed');
      await click(t, 'Waste reviewed');
      await click(t, 'Apply');
      expect(m.requested!['action'], 'final_release');
      expect(m.requested!['params']['part_order'], ['other', 'entity_123456']);
      expect(m.requested!['params']['audit_key'], 'fresh-key');
      expect(m.requested!['params']['waste_warning_review'], 'fresh-key');
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
}
