import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'package:workbench_prototype/design/stock_material.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

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
  Future<ExpandedModel> mount(WidgetTester t, {bool bar = false}) async {
    final m = ExpandedModel();
    addTearDown(m.dispose);
    t.view.physicalSize = const Size(1400, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    m.state['manufacturing']['stockVerification'] = {
      'eligible': true,
      'verified': false,
      'token': 'stock-1',
      'plates': [
        {'number': 1, 'width': 96, 'height': 48},
      ],
      'fingerprint': {'machine_profile': 'Evaluation router'},
    };
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
    m.state['manufacturing']['operations'] = [
      <String, dynamic>{
        'id': 'profile',
        'name': 'Profile',
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
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('helix-image'),
        child: MaterialApp(
          home: bar
              ? Scaffold(body: StockMaterialControls(model: m))
              : ManufacturingWorkspace(
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
    await t.tap(find.text(text));
    await t.pumpAndSettle();
  }

  Finder field(String key) => find.byWidgetPredicate(
    (w) => w is TextField && w.decoration?.labelText == key,
  );
  testWidgets(
    'Profile helix controls survive ordinary editor routing and require whole turns',
    (t) async {
      final m = await mount(t);
      t.view.physicalSize = const Size(800, 600);
      final op = m.state['manufacturing']['operations'][0];
      op.addAll({
        'entry_mode': 'plunge',
        'helix_min_diameter': .2,
        'helix_ramp_angle': 3,
        'helix_clearance': .0001,
        'helix_finish_revolutions': 2,
      });
      final dynamic workspace = t.state(find.byType(ManufacturingWorkspace));
      workspace.operationEditor(op);
      await t.pumpAndSettle();
      expect(find.text('Profile entry / finishing draft'), findsOneWidget);
      for (final pair in <String, String>{
        'Minimum helix orbit diameter (in)': '0.2',
        'Maximum helix angle (degrees; 0 = pitch only)': '3',
        'Additional helix clearance (in)': '0.0001',
        'Final-depth helix revolutions (whole number)': '2',
      }.entries) {
        expect(
          t.widget<TextField>(field(pair.key)).controller!.text,
          pair.value,
        );
      }
      final turns = field('Final-depth helix revolutions (whole number)');
      await Scrollable.ensureVisible(
        t.element(field('Minimum helix orbit diameter (in)')),
        alignment: .1,
      );
      await t.pumpAndSettle();
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('helix-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(
          'build/ui-review/profile-helix-controls.png',
        ).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await t.ensureVisible(turns);
      await t.enterText(turns, '1.5');
      await click(t, 'Apply');
      expect(m.requestedCommand, isNull);
      expect(find.text('Profile entry / finishing draft'), findsOneWidget);
      await t.ensureVisible(turns);
      await t.enterText(turns, '3');
      m.response = {'status': 'failed', 'error': 'Synthetic motion test'};
      await click(t, 'Apply');
      expect(m.requestedCommand, 'manufacturing.operation_prepare');
      final params = m.requested!['params'];
      expect(params['helix_min_diameter'], .2);
      expect(params['helix_ramp_angle'], 3);
      expect(params['helix_clearance'], .0001);
      expect(params['helix_finish_revolutions'], 3);
      expect(
        m.calls.where((c) => c == 'manufacturing.operation_apply'),
        isEmpty,
      );
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Actual relocated entry is shown independently from requested helix settings',
    (t) async {
      final m = await mount(t);
      m.response = {
        'status': 'operation_ready',
        'token': 'entry-review',
        'operation': {
          'name': 'Profile',
          'kind': 'router_offset',
          'entry_mode': 'helical',
          'target_depth': .5,
          'safe_z': .25,
          'helix_min_diameter': .2,
          'helix_ramp_angle': 3,
          'helix_clearance': .0001,
          'helix_finish_revolutions': 2,
        },
        'validation': {'issues': []},
        'motions': [],
        'geometry': m.entities,
        'blockCount': 1,
        'blockReview': [
          {
            'label': 'Relocated profile',
            'details': {
              'entry_mode': 'helical',
              'entry_mode_actual': 'helical',
              'entry_original_start': [0, 0],
              'entry_effective_start': [5, 2],
              'entry_adjustment_kind': 'straight_interior',
              'entry_rejection_reason':
                  'The entry cutter sweep crosses its own contour.',
            },
          },
        ],
      };
      await t.ensureVisible(find.byTooltip('Profile entry / finishing'));
      await t.tap(find.byTooltip('Profile entry / finishing'));
      await t.pumpAndSettle();
      await click(t, 'Apply');
      await click(t, 'Generated block order / details (1)');
      expect(
        find.textContaining('Accepted entry X/Y (in): [5, 2]'),
        findsOneWidget,
      );
      expect(
        find.textContaining('Why entry moved: The entry cutter sweep'),
        findsOneWidget,
      );
      expect(find.textContaining('angle 3 degrees'), findsOneWidget);
      expect(
        m.calls.where((c) => c == 'manufacturing.operation_apply'),
        isEmpty,
      );
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Profile draft preserves optional blanks, finishing switch and explicit review',
    (t) async {
      final m = await mount(t);
      m.response = {
        'status': 'operation_ready',
        'token': 'advanced-1',
        'operation': {
          'name': 'Profile',
          'kind': 'router_offset',
          'entry_mode': 'ramp',
          'target_depth': .5,
          'safe_z': .25,
        },
        'validation': {'issues': []},
        'motions': [],
        'geometry': m.entities,
      };
      await t.ensureVisible(find.byTooltip('Profile entry / finishing'));
      await t.tap(find.byTooltip('Profile entry / finishing'));
      await t.pumpAndSettle();
      await click(t, 'Add finishing / spring pass');
      await click(t, 'Apply');
      expect(m.requestedCommand, 'manufacturing.operation_prepare');
      expect(m.requested!['command'], 'profile_save');
      final p = m.requested!['params'];
      expect(p['finishing_pass'], true);
      expect(p['finish_feed_override'], isNull);
      expect(p['id'], 'profile');
      await t.scrollUntilVisible(
        find.text('Apply operation draft'),
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
              find.widgetWithText(FilledButton, 'Apply operation draft'),
            )
            .onPressed,
        isNull,
      );
      await click(
        t,
        'I reviewed the entry, depth passes, finishing/cleanout settings and motion.',
      );
      await click(t, 'Apply operation draft');
      expect(m.calls.last, 'manufacturing.operation_apply');
      expect(m.applied, {
        'token': 'advanced-1',
        'reviewed': true,
        'release_reviewed': false,
      });
      expect(t.takeException(), isNull);
    },
  );
  for (final label in [
    'Tabbed profile + rest',
    'Clear waste + tabbed finish',
  ]) {
    testWidgets('$label prepares whole-group through-stock inputs', (t) async {
      final m = await mount(t);
      m.response = {'status': 'failed', 'error': 'Review test draft'};
      await t.ensureVisible(find.byTooltip('Second-tool workflow'));
      await t.tap(find.byTooltip('Second-tool workflow'));
      await t.pumpAndSettle();
      await click(t, label);
      expect(find.textContaining('Through depth'), findsOneWidget);
      await click(t, 'Apply');
      expect(m.requestedCommand, 'manufacturing.group_prepare');
      expect(m.requested!['source']['cut_intent'], 'through');
      expect(m.requested!['source']['tab_count'], 4);
      if (label.startsWith('Tabbed')) {
        expect(m.requested!['settings']['retain_tabs'], 2);
      } else {
        expect(m.requested!['settings']['main_stepover'], .1);
        expect(m.requested!['settings']['stepover'], .05);
      }
      expect(m.calls.where((c) => c == 'manufacturing.group_apply'), isEmpty);
      expect(t.takeException(), isNull);
    });
  }
  testWidgets(
    'Speed Cleanout validates numerical limits and sends explicit fallback',
    (t) async {
      final m = await mount(t);
      m.response = {'status': 'failed', 'error': 'Proof failed'};
      await click(t, 'Add Speed Cleanout');
      await t.ensureVisible(field('Center feed (% of cut feed)'));
      await t.enterText(field('Center feed (% of cut feed)'), 'NaN');
      await click(t, 'Apply');
      expect(m.requestedCommand, isNull);
      await t.ensureVisible(field('Center feed (% of cut feed)'));
      await t.enterText(field('Center feed (% of cut feed)'), '55');
      await click(t, 'Apply');
      expect(m.requestedCommand, 'manufacturing.operation_prepare');
      expect(m.requested!['command'], 'fill_save');
      expect(m.requested!['params']['strategy'], 'speed_cleanout');
      expect(m.requested!['params']['speed_center_feed_percent'], 55);
      expect(m.requested!['params']['speed_fallback'], 'depth_step');
      expect(
        m.calls.where((c) => c == 'manufacturing.operation_apply'),
        isEmpty,
      );
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Physical stock confirmation requires all three checks; cancel and failure preserve draft',
    (t) async {
      final m = await mount(t, bar: true);
      await click(t, 'Verify stock');
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Confirm stock'),
            )
            .onPressed,
        isNull,
      );
      await click(t, 'I checked the physical sheet material/label.');
      await click(t, 'I checked or measured the physical sheet thickness.');
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Confirm stock'),
            )
            .onPressed,
        isNull,
      );
      await click(t, 'Cancel');
      expect(m.calls, isEmpty);
      await click(t, 'Verify stock');
      for (final text in [
        'I checked the physical sheet material/label.',
        'I checked or measured the physical sheet thickness.',
        'I checked that sheet size and machine setup match this job.',
      ]) {
        await click(t, text);
      }
      m.reject = true;
      await click(t, 'Confirm stock');
      expect(find.text('Verify physical stock'), findsOneWidget);
      m.reject = false;
      await click(t, 'Confirm stock');
      expect(find.text('Verify physical stock'), findsNothing);
      expect(m.calls.last, 'manufacturing.verify_stock');
      expect(m.applied, {
        'token': 'stock-1',
        'material_checked': true,
        'thickness_checked': true,
        'machine_size_checked': true,
      });
      expect(t.takeException(), isNull);
    },
  );
}
