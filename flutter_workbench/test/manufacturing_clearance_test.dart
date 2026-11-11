import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/knife_clearance_dialog.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'package:workbench_prototype/design/manufacturing_picker.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

Json finding({bool accepted = false, int? plate}) => {
  'operation_name': 'Knife coupon',
  'operation_id': 'knife',
  'entity_id': 'entity_123456',
  'units': 'in',
  'accepted': accepted,
  'message': accepted
      ? 'Checked edge swivel near stock edge.'
      : 'Knife loop leaves its plate clearance.',
  'point': [9.85, 2.0],
  'plate': [0.0, 0.0, 10.0, 10.0],
  'margin': .05,
  'source': [
    [2.0, 2.0],
    [9.85, 2.0],
    [9.85, 6.0],
    [2.0, 6.0],
  ],
  'outline': [
    [9.85, 2.0],
    [10.05, 2.0],
    [10.25, 1.8],
    [10.05, 1.6],
    [9.85, 1.8],
  ],
  'fallback_envelope': accepted
      ? [
          [9.75, 1.9],
          [9.95, 1.9],
          [9.95, 2.1],
          [9.75, 2.1],
        ]
      : [],
  'obstacles': [
    {
      'name': 'Clamp',
      'closed': true,
      'points': [
        [10.4, 1.6],
        [10.5, 1.6],
        [10.5, 2.2],
        [10.4, 2.2],
      ],
    },
  ],
  'plate_number': ?plate,
  if (plate != null) 'frame': 'Plate-local',
};

class ClearanceModel extends ExpandedModel {
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command == 'manufacturing.small_centers') {
      requested = params;
      requestedCommand = command;
      return {
        'ok': true,
        'manufacturing': {
          'id': 'profile',
          'fingerprint': 'center-fp',
          'selected': ['entity_123456'],
          'units': 'in',
          'diameter': .25,
          'rows': [
            {
              'id': 'entity_123456',
              'kind': 'rectangle',
              'eligible': true,
              'loops': 2,
            },
            {
              'id': 'too_large',
              'kind': 'rectangle',
              'eligible': false,
              'reason': 'Larger than four cutter diameters; use Cleanout.',
            },
          ],
        },
      };
    }
    return super.request(command, params);
  }
}

void main() {
  setUpAll(() async {
    for (final font in {
      'Roboto': 'C:/Windows/Fonts/segoeui.ttf',
      'MaterialIcons':
          '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
    }.entries) {
      final bytes = await File(font.value).readAsBytes();
      await (FontLoader(
        font.key,
      )..addFont(Future.value(ByteData.sublistView(bytes)))).load();
    }
  });
  Future<ClearanceModel> mount(
    WidgetTester t, {
    bool viewer = false,
    bool compact = false,
    DesignViewport? view,
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1600, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = ClearanceModel();
    addTearDown(m.dispose);
    m.state['manufacturing']['knifeWorkflows'] = true;
    m.state['manufacturing']['smallCenters'] = true;
    m.state['manufacturing']['tools'] = [
      <String, dynamic>{
        'station': 8,
        'kind': 'drag_knife',
        'name': 'Knife',
        'diameter': .1,
      },
    ];
    m.state['manufacturing']['operations'] = [
      <String, dynamic>{
        'id': 'profile',
        'kind': 'router_offset',
        'name': 'Profile',
        'enabled': true,
        'entity_ids': ['entity_123456'],
        'profile_retention': <String, dynamic>{},
        'side': 'inside',
        'tool_station': 1,
        'target_depth': .1,
        'safe_z': .25,
      },
    ];
    await t.pumpWidget(
      MaterialApp(
        builder: (context, child) => RepaintBoundary(
          key: const ValueKey('clearance-capture'),
          child: child!,
        ),
        home: viewer
            ? Scaffold(
                body: KnifeClearanceDialog(
                  model: m,
                  findings: [finding(), finding(accepted: true, plate: 2)],
                ),
              )
            : ManufacturingWorkspace(
                model: m,
                onSave: () async => true,
                initialInspectorOpen: true,
                view: view,
              ),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, Finder finder) async {
    if (finder.evaluate().isEmpty) {
      final scroll = find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first;
      t.state<ScrollableState>(scroll).position.jumpTo(0);
      await t.pumpAndSettle();
      await t.scrollUntilVisible(finder, 200, scrollable: scroll);
    }
    await t.ensureVisible(finder.last);
    await t.pumpAndSettle();
    await t.tap(finder.last);
    await t.pumpAndSettle();
  }

  testWidgets(
    'Read-only clearance viewer fits blocked and accepted geometry without changing selection',
    (t) async {
      final m = await mount(t, viewer: true);
      final selected = Set<String>.from(m.selection);
      expect(
        find.text('Generation blocked — this cut was not omitted.'),
        findsOneWidget,
      );
      await click(t, find.text('Whole contour / plate'));
      await click(t, find.text('Fit finding'));
      await t.drag(find.byType(InteractiveViewer), const Offset(30, 20));
      await t.pumpAndSettle();
      await t.tap(find.byType(DropdownButton<int>));
      await t.pumpAndSettle();
      await click(t, find.text('Plate 2 · Knife coupon · 2'));
      expect(
        find.text('Checked shallow swivel replaces this plate-crossing loop.'),
        findsOneWidget,
      );
      expect(find.textContaining('Plate-local'), findsOneWidget);
      expect(m.selection, selected);
      expect(m.calls, isEmpty);
      m.state['revision'] = (m.state['revision'] as int) + 1;
      m.notifyListeners();
      await t.pumpAndSettle();
      expect(find.textContaining('Drawing changed —'), findsOneWidget);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Compact clearance viewer remains readable and paints reserved bounds',
    (t) async {
      await mount(t, viewer: true, compact: true);
      expect(t.takeException(), isNull);
      await t.runAsync(() async {
        final picture = await t
            .renderObject<RenderRepaintBoundary>(
              find.byKey(const ValueKey('clearance-capture')),
            )
            .toImage();
        final bytes = await picture.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/ui-review').create(recursive: true);
        await File(
          'build/ui-review/clearance-five-compact.png',
        ).writeAsBytes(bytes!.buffer.asUint8List());
        picture.dispose();
      });
      expect(find.textContaining('reserved bounds'), findsOneWidget);
    },
  );
  testWidgets(
    'Failed knife draft reopens entered values and original contour selection',
    (t) async {
      final m = await mount(t);
      m.response = {
        'status': 'failed',
        'error': 'Clearance blocked',
        'knifeFindings': [finding()],
        'retryOperation': {
          'command': 'special_save',
          'params': {
            'kind': 'drag_knife',
            'name': 'Kept knife settings',
            'tool_station': 8,
            'entity_ids': ['entity_123456'],
            'blade_offset': .05,
            'loop_radius': .24,
            'corner_feed_ipm': 19,
            'setup_verified': true,
          },
        },
      };
      await click(t, find.text('Generate'));
      await click(t, find.text('Return to knife settings'));
      expect(find.text('Kept knife settings'), findsOneWidget);
      expect(
        t
            .widget<CheckboxListTile>(
              find.widgetWithText(
                CheckboxListTile,
                'Knife setup physically verified',
              ),
            )
            .value,
        false,
      );
      await click(t, find.text('Apply'));
      expect(m.requestedCommand, 'manufacturing.operation_prepare');
      expect(m.requested!['params']['entity_ids'], ['entity_123456']);
      expect(m.requested!['params']['loop_radius'], .24);
      expect(m.requested!['params']['corner_feed_ipm'], 19);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Blocked output opens plate-local findings without moving the shared camera',
    (t) async {
      final view = DesignViewport();
      addTearDown(view.dispose);
      final m = await mount(t, view: view);
      final scale = view.scale, pan = view.translation;
      m.response = {
        'status': 'blocked',
        'knifeFindings': [finding(plate: 2)],
        'validation': {'blocking': true, 'issues': <dynamic>[]},
      };
      await click(t, find.text('Generate'));
      await click(t, find.text('Show knife clearance (1)'));
      expect(find.byType(KnifeClearanceDialog), findsOneWidget);
      await click(t, find.text('Whole contour / plate'));
      await click(t, find.text('Back to review'));
      expect(view.scale, scale);
      expect(view.translation, pan);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Small-center review shows ineligible reasons and clear-all prepares an atomic draft',
    (t) async {
      final m = await mount(t);
      await click(t, find.byTooltip('Operation workflow'));
      await click(t, find.text('Small-center cleanout'));
      expect(find.textContaining('Larger than four'), findsOneWidget);
      final invalid = t
          .widgetList<CheckboxListTile>(find.byType(CheckboxListTile))
          .where((w) => w.onChanged == null);
      expect(invalid, isNotEmpty);
      await click(t, find.text('Turn off all'));
      m.response = {'status': 'failed', 'error': 'Synthetic preview'};
      await click(t, find.text('Preview selected centers'));
      expect(m.requestedCommand, 'manufacturing.management_prepare');
      expect(m.requested!['action'], 'small_centers');
      expect(m.requested!['params'], {
        'id': 'profile',
        'ids': <String>[],
        'fingerprint': 'center-fp',
      });
      expect(m.calls, isEmpty);
    },
  );
  testWidgets('Small-center cancel and stale review never apply changes', (
    t,
  ) async {
    final m = await mount(t);
    await click(t, find.byTooltip('Operation workflow'));
    await click(t, find.text('Small-center cleanout'));
    await click(t, find.text('Cancel'));
    expect(m.requestedCommand, 'manufacturing.small_centers');
    await click(t, find.byTooltip('Operation workflow'));
    await click(t, find.text('Small-center cleanout'));
    m.state['revision'] = (m.state['revision'] as int) + 1;
    await click(t, find.text('Preview selected centers'));
    expect(m.requestedCommand, 'manufacturing.small_centers');
    expect(m.calls, isEmpty);
    expect(
      find.textContaining('Drawing changed; reopen small-center review.'),
      findsWidgets,
    );
  });
  testWidgets(
    'Small centers can be selected graphically without changing document selection',
    (t) async {
      final m = await mount(t);
      final original = Set<String>.from(m.selection);
      await click(t, find.byTooltip('Operation workflow'));
      await click(t, find.text('Small-center cleanout'));
      await click(t, find.text('Pick in drawing'));
      expect(find.byType(ManufacturingPicker), findsOneWidget);
      final area = find.byKey(const ValueKey('manufacturing-picker-canvas'));
      final paint = t.widget<CustomPaint>(
        find.descendant(of: area, matching: find.byType(CustomPaint)).first,
      );
      final view = (paint.painter as DesignPainter).v;
      await t.tapAt(t.getTopLeft(area) + view.screen(const Offset(4, 3)));
      await t.pumpAndSettle();
      await click(t, find.text('Review picked changes'));
      expect(m.selection, original);
      expect(m.calls, isEmpty);
      m.response = {'status': 'failed', 'error': 'Synthetic preview'};
      await click(t, find.text('Preview selected centers'));
      expect(m.requested!['params']['ids'], isEmpty);
      expect(t.takeException(), isNull);
    },
  );
}
