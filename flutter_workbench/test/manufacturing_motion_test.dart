import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

void main() {
  Future<ExpandedModel> mount(
    WidgetTester t, {
    String kind = 'router_offset',
  }) async {
    final m = ExpandedModel();
    addTearDown(m.dispose);
    t.view.physicalSize = const Size(1400, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    m.state['manufacturing']['operations'] = [
      <String, dynamic>{
        'id': 'operation',
        'kind': kind,
        'name': 'Test operation',
        'enabled': true,
        'tool_station': 1,
        'entity_ids': [
          kind == 'drill_circles' ? 'entity_hole' : 'entity_123456',
        ],
        'target_depth': .3,
        'safe_z': .25,
        'stepdown': .1,
        'stepover': .1,
        'side': 'outside',
        'cut_direction': 'climb',
        'entry_mode': 'plunge',
        'strategy': 'concentric',
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
    await t.ensureVisible(find.text(text));
    await t.pumpAndSettle();
    // Lazy list children can update their extent after the first scroll.
    await t.ensureVisible(find.text(text));
    await t.pumpAndSettle();
    await t.tap(find.text(text));
    await t.pumpAndSettle();
  }

  Future<void> open(WidgetTester t, String tip) async {
    await t.ensureVisible(find.byTooltip(tip));
    await t.tap(find.byTooltip(tip));
    await t.pumpAndSettle();
  }

  Future<void> choose(WidgetTester t, String label, String value) async {
    final dropdown = find.ancestor(
      of: find.text(label),
      matching: find.byType(DropdownButtonFormField<String>),
    );
    await t.ensureVisible(dropdown);
    await t.tap(dropdown);
    await t.pumpAndSettle();
    await click(t, value);
  }

  Future<void> enter(WidgetTester t, String label, String text) async {
    final field = find.byWidgetPredicate(
      (w) => w is TextField && w.decoration?.labelText == label,
    );
    await t.ensureVisible(field);
    await t.enterText(field, text);
  }

  testWidgets(
    'Curve feed percentages and physical transition survive reviewed form',
    (t) async {
      final m = await mount(t);
      await open(t, 'Profile entry / finishing');
      await choose(t, 'Curve feed', 'wall_compensation');
      await enter(t, 'Minimum curve feed (%)', '30');
      await enter(t, 'Curve feed transition (in)', '.4');
      await click(t, 'Apply');
      expect(m.requestedCommand, 'manufacturing.operation_prepare');
      expect(m.requested!['params']['curve_feed_mode'], 'wall_compensation');
      expect(m.requested!['params']['curve_feed_min_percent'], 30);
      expect(m.requested!['params']['curve_feed_transition_distance'], .4);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Contour order literal override has explanation and cancel preserves drawing',
    (t) async {
      final m = await mount(t);
      await open(t, 'Profile entry / finishing');
      expect(find.textContaining('literal advanced override'), findsOneWidget);
      await choose(t, 'Contour order', 'source_order');
      await click(t, 'Cancel');
      expect(m.requestedCommand, isNull);
      expect(m.calls, isEmpty);
      await open(t, 'Profile entry / finishing');
      await choose(t, 'Contour order', 'part_by_part');
      await click(t, 'Apply');
      expect(m.requested!['params']['contour_order'], 'part_by_part');
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Paired rectangles share-edge form validates numeric match tolerance',
    (t) async {
      final m = await mount(t);
      await open(t, 'Profile entry / finishing');
      await choose(t, 'Shared rectangle edge', 'paired_rectangles');
      await enter(t, 'Shared-edge match tolerance (in)', 'NaN');
      await click(t, 'Apply');
      expect(m.requestedCommand, isNull);
      await enter(t, 'Shared-edge match tolerance (in)', '.001');
      await click(t, 'Apply');
      expect(m.requested!['params']['common_line_mode'], 'paired_rectangles');
      expect(m.requested!['params']['common_line_tolerance'], .001);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Short-loop cleanup enable and limits remain a nonmutating draft',
    (t) async {
      final m = await mount(t, kind: 'island_fill');
      await open(t, 'Cleanout passes / entry / links');
      await click(t, 'Clean short residual loops once at final depth');
      await enter(t, 'Maximum short-loop length (in)', '1');
      await enter(t, 'Short-loop link distance (in, 0 = retract)', '.1875');
      await click(t, 'Apply');
      expect(m.requested!['params']['sliver_cleanup'], true);
      expect(m.requested!['params']['sliver_max_path_length'], 1);
      expect(m.requested!['params']['sliver_link_distance'], .1875);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Drilling exact pecks reject fractional count and preserve optional feeds',
    (t) async {
      final m = await mount(t, kind: 'drill_circles');
      await open(t, 'Drilling cycle');
      await choose(t, 'Peck definition', 'count');
      await enter(t, 'Equal peck count', '2.5');
      await click(t, 'Apply');
      expect(m.requestedCommand, isNull);
      await enter(t, 'Equal peck count', '5');
      await enter(t, 'Surface approach feed (in/s, blank = cutter)', '2.5');
      await enter(t, 'Drilling feed (in/s, blank = cutter)', '5');
      await enter(t, 'Controlled retract feed (in/s, blank = cutter)', '5');
      await enter(t, 'Drilling RPM (blank = cutter)', '16000');
      await click(t, 'Feed during chip-break retracts');
      await click(t, 'Feed to surface before final rapid');
      await click(t, 'Apply');
      expect(m.requested!['command'], 'drill_save');
      final p = m.requested!['params'];
      expect(p['peck_count'], 5);
      expect(p['controlled_retract'], true);
      expect(p['return_to_surface'], true);
      expect(p['surface_feed_override'], 2.5);
      expect(p['spindle_rpm_override'], 16000);
      expect(p['peck_depth'], isNull);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Drill review shows computed depths and ordered blocks before Apply',
    (t) async {
      final m = await mount(t, kind: 'drill_circles');
      m.response = {
        'status': 'operation_ready',
        'token': 'draft',
        'operation': m.state['manufacturing']['operations'][0],
        'drillSchedule': [.1, .2, .3],
        'blockCount': 1,
        'blockReview': [
          {
            'label': 'Hole 1',
            'details': {'entity_id': 'entity_hole'},
          },
        ],
        'motions': [],
        'geometry': [],
        'motionCount': 0,
        'validation': {'blocking': false, 'issues': []},
      };
      await open(t, 'Drilling cycle');
      await click(t, 'Apply');
      await click(t, 'Core peck depths: [0.1, 0.2, 0.3] in');
      expect(find.text('Core peck depths: [0.1, 0.2, 0.3] in'), findsOneWidget);
      await click(t, 'Generated block order / details (1)');
      expect(find.text('Hole 1'), findsOneWidget);
      expect(find.text('Contour hole'), findsOneWidget);
      await t.scrollUntilVisible(
        find.text('Apply operation draft'),
        200,
        scrollable: find
            .descendant(
              of: find.byType(ListView).first,
              matching: find.byType(Scrollable),
            )
            .first,
      );
      final button = t.widget<FilledButton>(
        find.widgetWithText(FilledButton, 'Apply operation draft'),
      );
      expect(button.onPressed, isNull);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
}
