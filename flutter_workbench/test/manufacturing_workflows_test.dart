import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

class WorkflowModel extends ExpandedModel {
  bool matched = true;
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command.startsWith('manufacturing.recipe_')) {
      requestedCommand = command;
      requested = params;
      if (command == 'manufacturing.recipe_list') {
        return {
          'ok': true,
          'version': 'library-1',
          'recipes': [
            {
              'id': 'recipe-1',
              'name': 'Quarter to eighth',
              'compatible': matched,
            },
          ],
        };
      }
      if (command == 'manufacturing.recipe_resolve') {
        if (!matched && params['replacement_station'] == null) {
          return {'ok': false, 'error': 'Choose a replacement explicitly.'};
        }
        return {
          'ok': true,
          'settings': {
            'strategy': 'retained_skin',
            'station': 2,
            'feed_rate': 1,
            'plunge_rate': .25,
            'spindle_rpm': 18000,
            'stepdown': .04,
            'safe_z': .25,
            'skin': .025,
            'ignore_stock_less_than': .001,
          },
          'recipe_tool': {'station': 2, 'name': 'Small'},
          'reviewed': false,
        };
      }
      return {'ok': true};
    }
    return super.request(command, params);
  }
}

void main() {
  Future<WorkflowModel> mount(
    WidgetTester t, {
    bool fill = false,
    bool group = false,
  }) async {
    final m = WorkflowModel();
    addTearDown(m.dispose);
    t.view.physicalSize = const Size(1400, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    m.state['manufacturing']['tools'] = [
      {
        'station': 1,
        'kind': 'end_mill',
        'name': 'Large',
        'diameter': .25,
        'max_stepdown': .125,
      },
      {
        'station': 2,
        'kind': 'end_mill',
        'name': 'Small',
        'diameter': .125,
        'max_stepdown': .04,
      },
    ];
    final op = <String, dynamic>{
      'id': 'op-1',
      'name': 'Operation',
      'kind': fill ? 'island_fill' : 'router_offset',
      'enabled': true,
      'tool_station': 1,
      'entity_ids': ['entity_123456'],
      'target_depth': .3,
      'safe_z': .25,
      'stepdown': .1,
      'stepover': .1,
      'side': 'inside',
      'cut_direction': 'climb',
      'strategy': 'concentric',
      'profile_retention': <String, dynamic>{},
    };
    if (group) {
      op['profile_retention'] = {
        'group_id': 'group-1',
        'edit_inputs': {
          'source': Map<String, dynamic>.from(op),
          'settings': <String, dynamic>{},
        },
      };
    }
    m.state['manufacturing']['operations'] = [op];
    m.response = {'status': 'failed', 'error': 'Synthetic draft; no Apply'};
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
    await t.tap(find.text(text));
    await t.pumpAndSettle();
  }

  Finder field(String label) => find.byWidgetPredicate(
    (w) => w is TextField && w.decoration?.labelText == label,
  );
  Future<void> enter(WidgetTester t, String label, String value) async {
    await t.ensureVisible(field(label));
    await t.enterText(field(label), value);
  }

  Future<void> menu(WidgetTester t, String text) async {
    await t.ensureVisible(find.byTooltip('Second-tool workflow'));
    await t.tap(find.byTooltip('Second-tool workflow'));
    await t.pumpAndSettle();
    await click(t, text);
  }

  testWidgets(
    'Custom profile increments reject malformed lists and remain draft-only',
    (t) async {
      final m = await mount(t);
      await t.tap(find.byTooltip('Profile entry / finishing'));
      await t.pumpAndSettle();
      await enter(t, 'Pass increments (in, comma separated)', '0.1, NaN');
      await click(t, 'Apply');
      expect(m.requestedCommand, isNull);
      await enter(t, 'Pass increments (in, comma separated)', '0.1, 0.15');
      await click(t, 'Apply');
      expect(m.requested!['params']['pass_depths'], [.1, .15]);
      expect(
        m.calls.where((c) => c == 'manufacturing.operation_apply'),
        isEmpty,
      );
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Cleanout custom floor passes, helical entry and link controls survive the form',
    (t) async {
      final m = await mount(t, fill: true);
      await t.tap(find.byTooltip('Cleanout passes / entry / links'));
      await t.pumpAndSettle();
      await click(t, 'custom');
      await click(t, 'partial');
      await enter(t, 'Through-stock breakthrough (in)', '.015');
      await click(t, 'Cover cleanout corners');
      await click(t, 'maximum_stepdown');
      await click(t, 'custom_increments');
      await enter(t, 'Pass increments (in, comma separated)', '0.1, 0.1, 0.07');
      await click(t, 'Add floor finishing pass');
      await enter(t, 'Floor stock (in)', '.03');
      await click(t, 'plunge');
      await click(t, 'helical');
      await click(t, 'Keep tool down between proven clear loops');
      await enter(t, 'Maximum link distance (in)', '.25');
      await enter(t, 'Maximum link engagement (in)', '.125');
      await enter(t, 'Link feed (% of cut feed)', '70');
      await click(t, 'Apply');
      final p = m.requested!['params'];
      expect(p['cut_intent'], 'partial');
      expect(p['breakthrough'], .015);
      expect(p['corner_tags'], true);
      expect(p['pass_mode'], 'custom_increments');
      expect(p['pass_depths'], [.1, .1, .07]);
      expect(p['bottom_finish_pass'], true);
      expect(p['entry_mode'], 'helical');
      expect(p['keep_tool_down'], true);
      expect(p['stay_down_feed_percent'], 70);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Recipe save uses library version without changing drawing', (
    t,
  ) async {
    final m = await mount(t, group: true);
    await menu(t, 'Save second-tool recipe...');
    await enter(t, 'Recipe name', 'Acrylic small cutter');
    await click(t, 'Save recipe');
    expect(m.requestedCommand, 'manufacturing.recipe_save');
    expect(m.requested, {
      'version': 'library-1',
      'operation_id': 'op-1',
      'name': 'Acrylic small cutter',
    });
    expect(m.calls, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Recipe recall requires explicit missing-cutter replacement and fresh draft',
    (t) async {
      final m = await mount(t);
      m.matched = false;
      await menu(t, 'Load second-tool recipe...');
      await t.tap(find.byType(DropdownButtonFormField<String>).last);
      await t.pumpAndSettle();
      await click(t, 'Quarter to eighth');
      await click(t, 'Load settings for review');
      expect(
        find.text('Bad state: Choose a replacement explicitly.'),
        findsOneWidget,
      );
      await t.tap(find.byType(DropdownButtonFormField<int>));
      await t.pumpAndSettle();
      await click(t, 'T2 - Small - 0.125 in');
      await click(t, 'Load settings for review');
      expect(m.requested!['replacement_station'], 2);
      expect(find.text('Retained-skin second-tool draft'), findsOneWidget);
      await click(t, 'Apply');
      expect(m.requestedCommand, 'manufacturing.group_prepare');
      expect(m.requested!['recipe_tool'], {'station': 2, 'name': 'Small'});
      expect(m.calls.where((c) => c == 'manufacturing.group_apply'), isEmpty);
      expect(t.takeException(), isNull);
    },
  );
}
