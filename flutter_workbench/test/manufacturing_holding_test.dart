import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

void main() {
  Future<ExpandedModel> mount(WidgetTester t) async {
    final m = ExpandedModel();
    addTearDown(m.dispose);
    t.view.physicalSize = const Size(1400, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    m.state['manufacturing']['operations'] = [
      <String, dynamic>{
        'id': 'operation',
        'kind': 'router_offset',
        'name': 'Test operation',
        'enabled': true,
        'tool_station': 1,
        'entity_ids': ['entity_123456'],
        'target_depth': .3,
        'safe_z': .25,
        'stepdown': .1,
        'side': 'outside',
        'cut_direction': 'climb',
        'entry_mode': 'plunge',
        'profile_retention': <String, dynamic>{},
      },
    ];
    m.response = {'status': 'failed', 'error': 'Synthetic draft: no Apply'};
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
    await Scrollable.ensureVisible(t.element(find.text(text)), alignment: .5);
    await t.pumpAndSettle();
    await t.tap(find.text(text));
    await t.pumpAndSettle();
  }

  Future<void> open(WidgetTester t) async {
    await t.ensureVisible(find.byTooltip('Profile entry / finishing'));
    await t.tap(find.byTooltip('Profile entry / finishing'));
    await t.pumpAndSettle();
  }

  Future<void> choose(WidgetTester t, String label, String value) async {
    final field = find.ancestor(
      of: find.text(label),
      matching: find.byType(DropdownButtonFormField<String>),
    );
    await t.ensureVisible(field);
    await t.tap(field);
    await t.pumpAndSettle();
    await t.tap(find.text(value).last);
    await t.pumpAndSettle();
  }

  Future<void> enter(WidgetTester t, String label, String value) async {
    final field = find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.labelText == label,
    );
    await t.ensureVisible(field);
    await t.enterText(field, value);
  }

  testWidgets('Through intent is reviewed with breakthrough', (t) async {
    final m = await mount(t);
    await open(t);
    await choose(t, 'Depth intent', 'through');
    await enter(t, 'Through-stock breakthrough (in)', '.015');
    await click(t, 'Apply');
    expect(m.requested!['params']['cut_intent'], 'through');
    expect(m.requested!['params']['breakthrough'], .015);
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets('Onion skin explains final removal and retains numeric depth', (
    t,
  ) async {
    final m = await mount(t);
    await open(t);
    expect(
      find.textContaining('removed by the final full-depth pass'),
      findsOneWidget,
    );
    await enter(t, 'Roughing onion skin (in)', '.05');
    await click(t, 'Apply');
    expect(m.requested!['params']['onion_skin'], .05);
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets('Automatic tabs validate whole counts and send dimensions', (
    t,
  ) async {
    final m = await mount(t);
    await open(t);
    await choose(t, 'Automatic tab placement', 'support_aware');
    await enter(t, 'Requested tab count', '2.5');
    await click(t, 'Apply');
    expect(m.requestedCommand, isNull);
    await enter(t, 'Requested tab count', '4');
    await enter(t, 'Finished tab width (in)', '.5');
    await enter(t, 'Retained tab height (in)', '.1');
    await enter(t, 'Maximum tab center spacing (in)', '4');
    await enter(t, 'Tab offset from corners (in)', '.5');
    await enter(t, 'Minimum inside perimeter for tabs (in)', '0');
    await click(t, 'Apply');
    final p = m.requested!['params'];
    expect(p['tab_mode'], 'support_aware');
    expect(p['tab_count'], 4);
    expect(p['tab_width'], .5);
    expect(p['tab_height'], .1);
    expect(p['tab_spacing'], 4);
    expect(p['corner_tab_offset'], .5);
    expect(p['female_tab_min_perimeter'], 0);
    expect(p['remove_tabs_after'], isFalse);
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets('Cut Line offers only open geometry and applicable controls', (
    t,
  ) async {
    final m = await mount(t);
    m.state['entities'].add(<String, dynamic>{
      'id': 'entity_open',
      'kind': 'line',
      'layer': '0',
      'start': {'x': 2, 'y': 2},
      'end': {'x': 6, 'y': 2},
      'bounds': [2, 2, 6, 2],
    });
    m.state['selected'] = <String>['entity_open'];
    await click(t, 'Add Cut Line');
    expect(find.text('Automatic tab placement'), findsNothing);
    expect(find.textContaining('rectangle -'), findsNothing);
    await choose(t, 'Cut side', 'left');
    await choose(t, 'Entry mode', 'ramp');
    await enter(t, 'Pass increments (in, comma separated)', '.1,.15');
    await click(t, 'Apply');
    final p = m.requested!['params'];
    expect(p['side'], 'left');
    expect(p['entry_mode'], 'ramp');
    expect(p['entity_ids'], ['entity_open']);
    expect(p['pass_depths'], [.1, .15]);
    expect(p.containsKey('id'), isFalse);
    expect(p.containsKey('onion_skin'), isFalse);
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Closed on-contour authoring is reviewed and cancellation preserves drawing',
    (t) async {
      final m = await mount(t);
      await click(t, 'Add on-contour cut');
      expect(
        find.textContaining('On follows the drawn centerline'),
        findsOneWidget,
      );
      await click(t, 'Cancel');
      expect(m.requestedCommand, isNull);
      await click(t, 'Add on-contour cut');
      await click(t, 'Apply');
      expect(m.requested!['params']['side'], 'on');
      expect(m.requested!['params']['entity_ids'], ['entity_123456']);
      expect(m.requested!['params'].containsKey('id'), isFalse);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Open operation edit returns to Cut Line and hides second-tool groups',
    (t) async {
      final m = await mount(t);
      m.state['entities'].add(<String, dynamic>{
        'id': 'entity_open',
        'kind': 'polyline',
        'closed': false,
        'layer': '0',
        'points': [
          {'x': 2, 'y': 2},
          {'x': 6, 'y': 2},
        ],
        'bounds': [2, 2, 6, 2],
      });
      final op = m.state['manufacturing']['operations'][0];
      op['entity_ids'] = ['entity_open'];
      op['side'] = 'on';
      m.notifyListeners();
      await t.pumpAndSettle();
      expect(find.byTooltip('Second-tool workflow'), findsNothing);
      await click(t, 'Test operation');
      expect(find.text('Cut Line draft'), findsOneWidget);
      await click(t, 'Apply');
      expect(m.requested!['params']['id'], 'operation');
      expect(m.requested!['params']['entity_ids'], ['entity_open']);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Manual tab percentages convert and reject full perimeter', (
    t,
  ) async {
    final m = await mount(t);
    await open(t);
    await choose(t, 'Automatic tab placement', 'manual');
    await enter(t, 'Manual tab centers (%, comma separated)', '0, 100');
    await click(t, 'Apply');
    expect(m.requestedCommand, isNull);
    await enter(t, 'Manual tab centers (%, comma separated)', '0; 25 99.5');
    await click(t, 'Apply');
    expect(m.requested!['params']['manual_tab_positions'], [0, .25, .995]);
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets('Aligned tabs and cleanup timing are explicit draft settings', (
    t,
  ) async {
    final m = await mount(t);
    await open(t);
    await choose(t, 'Automatic tab placement', 'aligned');
    await choose(t, 'Tab removal timing', 'end_of_operation');
    await enter(t, 'Maximum tab center spacing (in)', '3');
    await click(t, 'Apply');
    expect(m.requested!['params']['tab_mode'], 'aligned');
    expect(m.requested!['params']['tab_cleanup_order'], 'end_of_operation');
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets('Preferred start requires a whole index', (t) async {
    final m = await mount(t);
    await open(t);
    await enter(t, 'Preferred start vertex / circle quadrant (0-based)', '1.5');
    await click(t, 'Apply');
    expect(m.requestedCommand, isNull);
    await enter(t, 'Preferred start vertex / circle quadrant (0-based)', '2');
    await click(t, 'Apply');
    expect(m.requested!['params']['start_index'], 2);
    expect(t.takeException(), isNull);
  });
  testWidgets('Cycle comparison shows both motions and requires review', (
    t,
  ) async {
    final m = await mount(t);
    m.response = {
      'status': 'optimization_ready',
      'token': 'compare',
      'settings': {},
      'comparison': {
        'baseline': {'estimated_seconds': 12, 'motions': 5},
        'optimized': {'estimated_seconds': 10, 'motions': 3},
      },
      'motions': [],
      'baselineMotions': [],
      'validation': {'issues': []},
    };
    await click(t, 'Compare cycle optimization');
    expect(find.text('Cycle optimization comparison'), findsOneWidget);
    expect(find.textContaining('Low retracts and arc fitting'), findsOneWidget);
    await enter(t, 'Estimated XY rapid (inches/minute)', '450');
    await click(t, 'Apply');
    expect(m.requestedCommand, 'manufacturing.optimization_prepare');
    expect(m.requested!['rapid_xy_ipm'], 450);
    await click(t, 'Show original motion');
    final button = find.widgetWithText(
      FilledButton,
      'Apply optimization draft',
    );
    await t.ensureVisible(button);
    expect(t.widget<FilledButton>(button).onPressed, isNull);
    await click(
      t,
      'I compared the original and requested motion and settings.',
    );
    await click(t, 'Apply optimization draft');
    expect(m.calls, contains('manufacturing.optimization_apply'));
    expect(t.takeException(), isNull);
  });
  testWidgets('Tab removal needs separate release confirmation', (t) async {
    final m = await mount(t);
    m.response = {
      'status': 'operation_ready',
      'token': 'release',
      'requiresReleaseReview': true,
      'operation': {'kind': 'router_offset'},
      'blockReview': [],
      'motions': [],
      'validation': {'issues': []},
    };
    await open(t);
    await click(t, 'Apply');
    await click(
      t,
      'I reviewed the entry, depth passes, finishing/cleanout settings and motion.',
    );
    final button = find.widgetWithText(FilledButton, 'Apply operation draft');
    await t.ensureVisible(button);
    expect(t.widget<FilledButton>(button).onPressed, isNull);
    await click(t, 'I reviewed tab-removal motion and the release of parts.');
    await click(t, 'Apply operation draft');
    expect(m.applied!['release_reviewed'], true);
    expect(t.takeException(), isNull);
  });
  testWidgets('Regenerated tab removal also gates export on release review', (
    t,
  ) async {
    final m = await mount(t);
    m.response = {
      'status': 'ready',
      'token': 'export-release',
      'requiresReleaseReview': true,
      'motions': [],
      'validation': {'issues': []},
    };
    await click(t, 'Generate and check toolpaths');
    await click(t, 'I reviewed the toolpath motion and preflight messages.');
    await click(t, 'Export reviewed package');
    expect(m.requestedCommand, 'manufacturing.generate');
    await click(t, 'I reviewed tab-removal motion and the release of parts.');
    await click(t, 'Export reviewed package');
    expect(m.requestedCommand, 'manufacturing.export');
    expect(m.requested!['release_reviewed'], true);
    expect(t.takeException(), isNull);
  });
}
