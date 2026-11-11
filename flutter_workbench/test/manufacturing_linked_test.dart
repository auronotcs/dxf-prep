import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

class LinkedModel extends ExpandedModel {
  bool review = false;
  Json get source => {
    'id': 'main',
    'name': 'Linked profile',
    'kind': 'router_offset',
    'entity_ids': ['entity_123456', 'second'],
    'side': 'inside',
    'entity_sides': <String, dynamic>{},
    'manual_contour_order': <String>[],
    'geometry_selector': <String, dynamic>{},
    'depends_on_ids': <String>[],
    'sequence_role': 'automatic',
    'sequence_priority': 0,
    'sequence_locked': false,
  };
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command == 'manufacturing.linked_info') {
      return {
        'ok': true,
        'manufacturing': {
          'source': source,
          'member_ids': ['main', 'rest'],
          'fingerprint': 'current',
          'eligible_ids': ['entity_123456', 'second'],
          'workflow': 'retained_skin_profile',
        },
      };
    }
    if (command == 'manufacturing.management_prepare' && review) {
      requested = params;
      requestedCommand = command;
      return {
        'ok': true,
        'manufacturing': {
          'status': 'management_ready',
          'action': params['action'],
          'token': 'draft',
          'motions': <dynamic>[],
          'motionCount': 12,
          'validation': {'issues': []},
          'details': {
            'linked_changes': [
              {
                'name': 'Linked profile',
                'before': ['first'],
                'after': ['second'],
                'kept_stage_ids': ['main', 'rest'],
                'added_stage_ids': [],
                'removed_stage_ids': [],
                'selector': {},
                'entity_sides': {},
                'manual_contour_order': [],
                'sequence_role': 'automatic',
                'sequence_priority': 0,
                'prerequisites': [],
              },
            ],
            'geometry_changes': [],
            'operation_order': [],
          },
        },
      };
    }
    return super.request(command, params);
  }
}

void main() {
  setUpAll(() async {
    for (final family in ['Roboto', 'Segoe UI']) {
      await (FontLoader(family)..addFont(
            File(
              'C:/Windows/Fonts/segoeui.ttf',
            ).readAsBytes().then(ByteData.sublistView),
          ))
          .load();
    }
    await (FontLoader('MaterialIcons')..addFont(
          File(
            '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
          ).readAsBytes().then(ByteData.sublistView),
        ))
        .load();
  });
  Future<LinkedModel> mount(
    WidgetTester t, {
    bool compact = false,
    bool ready = true,
  }) async {
    final m = LinkedModel();
    addTearDown(m.dispose);
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1450, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    m.state['manufacturing']['linkedRevisions'] = ready;
    m.state['entities'].add(<String, dynamic>{
      'id': 'second',
      'kind': 'rectangle',
      'layer': 'CUT',
      'corner_a': {'x': 10, 'y': 2},
      'corner_b': {'x': 14, 'y': 4},
      'bounds': [10, 2, 14, 4],
    });
    m.state['manufacturing']['operations'] = [
      for (final id in ['main', 'rest'])
        <String, dynamic>{
          ...m.source,
          'id': id,
          'name': id == 'main' ? 'Main linked stage' : 'Rest linked stage',
          'enabled': true,
          'tool_station': id == 'main' ? 1 : 2,
          'target_depth': .4,
          'safe_z': .25,
          'profile_retention': {
            'group_id': 'group',
            'workflow': 'retained_skin_profile',
            'member_ids': ['main', 'rest'],
          },
        },
      <String, dynamic>{
        'id': 'prior',
        'name': 'Prior drilling',
        'kind': 'drill_circles',
        'enabled': true,
        'tool_station': 3,
        'entity_ids': [],
        'profile_retention': {},
      },
    ];
    m.response = {'status': 'failed', 'error': 'Synthetic draft only'};
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('linked-image'),
        child: MaterialApp(
          home: ManufacturingWorkspace(
            initialInspectorOpen: true,
            model: m,
            onSave: () async => true,
          ),
        ),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, String label) async {
    final f = find.text(label);
    if (f.evaluate().isNotEmpty) {
      await t.ensureVisible(f.first);
      await t.pumpAndSettle();
      await t.tap(f.first);
      await t.pumpAndSettle();
      return;
    }
    await t.scrollUntilVisible(
      f,
      220,
      scrollable: find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first,
    );
    await t.tap(f);
    await t.pumpAndSettle();
  }

  Future<void> menu(WidgetTester t, String label) async {
    final all = find.byTooltip('Linked group workflow');
    for (var i = 0; i < 20 && all.evaluate().isEmpty; i++) {
      await t.drag(find.byType(ListView).first, const Offset(0, -180));
      await t.pumpAndSettle();
    }
    final f = all.first;
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
    await click(t, label);
  }

  Future<void> capture(WidgetTester t, String name) async {
    final b = t.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('linked-image')),
    );
    await t.runAsync(() async {
      final im = await b.toImage();
      final bytes = await im.toByteData(format: ui.ImageByteFormat.png);
      File(
        'build/ui-review/linked-$name.png',
      ).writeAsBytesSync(bytes!.buffer.asUint8List());
      im.dispose();
    });
  }

  testWidgets(
    'Group target replacement uses drawing selection and private fingerprinted draft',
    (t) async {
      final m = await mount(t);
      m.state['selected'] = ['second'];
      final before = jsonEncode(m.state);
      await menu(t, 'Replace group source contours');
      await click(t, 'Use drawing selection');
      await click(t, 'Reset per-contour compensation and manual order');
      await capture(t, 'targets');
      await click(t, 'Review complete group');
      expect(m.requestedCommand, 'manufacturing.management_prepare');
      expect(m.requested!['action'], 'group_targets');
      expect(m.requested!['params'], {
        'entity_ids': ['second'],
        'reset_controls': true,
        'id': 'main',
        'fingerprint': 'current',
      });
      expect(m.calls, isEmpty);
      expect(jsonEncode(m.state), before);
    },
  );
  testWidgets(
    'Group geometry rule offers bind refresh clear without silent mutation',
    (t) async {
      final m = await mount(t);
      await menu(t, 'Group geometry rule');
      await capture(t, 'rule');
      await click(t, 'Review complete group');
      expect(m.requested!['params']['mode'], 'bind');
      await menu(t, 'Group geometry rule');
      await t.tap(find.byType(DropdownButtonFormField<String>));
      await t.pumpAndSettle();
      await t.tap(find.text('Refresh existing rule').last);
      await t.pumpAndSettle();
      await click(t, 'Review complete group');
      expect(m.requested!['params']['mode'], 'refresh');
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Linked contour controls exclude on-contour and preserve complete order',
    (t) async {
      final m = await mount(t);
      await menu(t, 'Group contour compensation / order');
      await t.tap(find.byType(DropdownButton<String>).first);
      await t.pumpAndSettle();
      expect(find.text('on'), findsNothing);
      await t.tap(find.text('outside').last);
      await t.pumpAndSettle();
      await t.tap(find.byTooltip('Move contour down').first);
      await t.pumpAndSettle();
      await click(t, 'Prepare contour draft');
      expect(m.requested!['action'], 'group_contours');
      expect(m.requested!['params']['manual_contour_order'], [
        'second',
        'entity_123456',
      ]);
      expect(m.requested!['params']['entity_sides'], {
        'entity_123456': 'outside',
      });
    },
  );
  testWidgets(
    'Whole-group sequencing excludes its own stages from prerequisites',
    (t) async {
      final m = await mount(t);
      await menu(t, 'Whole-group sequence / prerequisites');
      expect(
        find.widgetWithText(CheckboxListTile, 'Main linked stage'),
        findsNothing,
      );
      expect(
        find.widgetWithText(CheckboxListTile, 'Rest linked stage'),
        findsNothing,
      );
      await t.tap(find.widgetWithText(CheckboxListTile, 'Prior drilling'));
      await t.enterText(find.byType(TextField), '4');
      await click(t, 'Prepare sequence draft');
      expect(m.requested!['action'], 'group_sequence');
      expect(m.requested!['params']['depends_on_ids'], ['prior']);
      expect(m.requested!['params']['sequence_priority'], 4);
    },
  );
  testWidgets('Revised DXF remains review-bound with linked groups', (t) async {
    final m = await mount(t);
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(
          const MethodChannel('workbench/design'),
          (call) async => 'C:/evaluation/revised.dxf',
        );
    addTearDown(
      () => TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
          .setMockMethodCallHandler(
            const MethodChannel('workbench/design'),
            null,
          ),
    );
    await click(t, 'Review revised DXF');
    expect(m.requested!['action'], 'revision');
    expect(m.requested!['params']['path'], 'C:/evaluation/revised.dxf');
    expect(m.calls, isEmpty);
  });
  testWidgets(
    'Compact target editor cancels cleanly and rejects stale revision',
    (t) async {
      final m = await mount(t, compact: true);
      final before = jsonEncode(m.state);
      await menu(t, 'Replace group source contours');
      await capture(t, 'compact');
      await click(t, 'Cancel');
      expect(m.requested, isNull);
      expect(jsonEncode(m.state), before);
      await menu(t, 'Replace group source contours');
      m.state['revision']++;
      m.notifyListeners();
      await t.pumpAndSettle();
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Review complete group'),
            )
            .onPressed,
        isNull,
      );
      await click(t, 'Cancel');
      expect(m.requested, isNull);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Old backend retains open work and explains new-tab capability', (
    t,
  ) async {
    final m = await mount(t, ready: false);
    final before = jsonEncode(m.state);
    await menu(t, 'Group geometry rule');
    expect(find.textContaining('earlier backend'), findsWidgets);
    expect(jsonEncode(m.state), before);
    expect(m.requested, isNull);
  });
  testWidgets(
    'Complete linked review exposes stage identity and requires Apply review',
    (t) async {
      final m = await mount(t);
      m.review = true;
      await menu(t, 'Group geometry rule');
      await click(t, 'Review complete group');
      expect(find.textContaining('2 retained identities'), findsOneWidget);
      await capture(t, 'review');
      final apply = find.widgetWithText(FilledButton, 'Apply workflow draft');
      expect(t.widget<FilledButton>(apply).onPressed, isNull);
      await click(
        t,
        'I reviewed the source geometry, operation order and regenerated motion.',
      );
      await click(t, 'Apply workflow draft');
      expect(m.calls, contains('manufacturing.management_apply'));
      expect(m.applied!['reviewed'], true);
    },
  );
}
