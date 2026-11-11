import 'package:flutter/material.dart';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

class KnifeModel extends ExpandedModel {
  bool rejectPreset = false;
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (!command.startsWith('manufacturing.knife_')) {
      return super.request(command, params);
    }
    requestedCommand = command;
    requested = params;
    if (rejectPreset) {
      return {'ok': false, 'error': 'Job changed; reopen knife presets.'};
    }
    if (command.endsWith('resolve')) {
      return {
        'ok': true,
        'values': {
          'name': 'Saved knife',
          'tool_station': 8,
          'target_depth': .1,
          'blade_offset': .05,
          'loop_radius': .2,
          'outside_corner_loops': true,
          'corner_feed_ipm': 20,
          'lead_in_feed_ipm': 25,
          'feed_rate_override': 1.2,
          'plunge_rate_override': .3,
          'setup_verified': false,
        },
      };
    }
    return {
      'ok': true,
      'version': 'v',
      'fingerprint': 'fp',
      'recipes': [
        {
          'index': 0,
          'name': 'Saved knife',
          'material': 'Plywood',
          'stock_thickness_in': .5,
          'catalog_tool_id': 'knife',
          'compatible': true,
          'stations': [8],
        },
        {
          'index': 1,
          'name': 'Wrong material',
          'material': 'Acrylic',
          'stock_thickness_in': .5,
          'catalog_tool_id': 'knife',
          'compatible': false,
          'reason': 'Material differs from the saved preset.',
          'stations': [],
        },
      ],
    };
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
  Future<KnifeModel> mount(WidgetTester t, [String units = 'in']) async {
    t.view.physicalSize = const Size(1600, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = KnifeModel();
    addTearDown(m.dispose);
    m.state['units'] = units;
    m.state['manufacturing']['knifeWorkflows'] = true;
    m.state['manufacturing']['tools'] = [
      <String, dynamic>{
        'station': 8,
        'name': 'Knife',
        'kind': 'drag_knife',
        'diameter': .1,
      },
    ];
    m.state['manufacturing']['operations'] = [
      <String, dynamic>{
        'id': 'knife-op',
        'name': 'Applied knife',
        'kind': 'drag_knife',
        'enabled': true,
      },
    ];
    m.response = {'status': 'failed', 'error': 'Synthetic draft'};
    await t.pumpWidget(
      MaterialApp(
        builder: (context, child) => RepaintBoundary(
          key: const ValueKey('knife-capture'),
          child: child!,
        ),
        home: ManufacturingWorkspace(model: m, onSave: () async => true),
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
      await t.scrollUntilVisible(f, 200, scrollable: scroll);
    }
    await t.ensureVisible(f.last);
    await t.pumpAndSettle();
    await t.tap(f.last);
    await t.pumpAndSettle();
  }

  Future<void> field(WidgetTester t, String label, String value) async {
    final f = find.widgetWithText(TextField, label);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.enterText(f, value);
  }

  testWidgets('Edge-aware draft requires separate confirmation before Apply', (
    t,
  ) async {
    final m = await mount(t);
    m.response = {
      'status': 'operation_ready',
      'token': 'knife-draft',
      'requiresKnifeReview': true,
      'operation': {'name': 'Knife'},
      'motions': <dynamic>[],
      'geometry': <dynamic>[],
      'validation': {'issues': <dynamic>[], 'blocking': false},
    };
    await click(t, 'More');
    await click(t, 'Drag knife');
    await click(t, 'Apply');
    await click(
      t,
      'I reviewed the entry, depth passes, finishing/cleanout settings and motion.',
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
      'I reviewed edge-aware knife motion and verified corner separation on scrap.',
    );
    await click(t, 'Apply operation draft');
    expect(m.calls, contains('manufacturing.operation_apply'));
    expect(m.applied!['knife_reviewed'], true);
    expect(t.takeException(), isNull);
  });

  testWidgets(
    'Compact preset browser scrolls and older backend hides unsupported controls',
    (t) async {
      final m = await mount(t);
      t.view.physicalSize = const Size(800, 600);
      await t.pumpAndSettle();
      await click(t, 'More');
      await click(t, 'Knife presets');
      await t.ensureVisible(find.text('Save applied operation as preset'));
      await t.pumpAndSettle();
      expect(t.takeException(), isNull);
      await t.runAsync(() async {
        final boundary = t.renderObject<RenderRepaintBoundary>(
          find.byKey(const ValueKey('knife-capture')),
        );
        final picture = await boundary.toImage();
        final bytes = await picture.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/ui-review').create(recursive: true);
        await File(
          'build/ui-review/knife-presets-compact.png',
        ).writeAsBytes(bytes!.buffer.asUint8List());
        picture.dispose();
      });
      await click(t, 'Close');
      await t.pump(const Duration(milliseconds: 300));
      m.state['manufacturing']['knifeWorkflows'] = false;
      await click(t, 'More');
      expect(find.text('Knife presets'), findsNothing);
      await click(t, 'Drag knife');
      expect(find.text('Outside corner loops'), findsNothing);
      expect(t.takeException(), isNull);
    },
  );

  for (final units in ['in', 'mm']) {
    testWidgets(
      'Knife feed form converts $units and preserves optional defaults',
      (t) async {
        final m = await mount(t, units);
        await click(t, 'More');
        await click(t, 'Drag knife');
        final f = units == 'mm' ? 25.4 : 1;
        await field(t, 'Corner feed ($units/min, blank = legacy)', '${20 * f}');
        await field(
          t,
          'Lead-in feed ($units/min, blank = legacy)',
          '${25 * f}',
        );
        await field(t, 'Cut feed ($units/min, blank = cutter)', '${72 * f}');
        await click(t, 'Apply');
        expect(m.requestedCommand, 'manufacturing.operation_prepare');
        final p = m.requested!['params'];
        expect(p['corner_feed_ipm'], closeTo(20, 1e-9));
        expect(p['lead_in_feed_ipm'], closeTo(25, 1e-9));
        expect(p['feed_rate_override'], closeTo(1.2 * f, 1e-9));
        expect(p['plunge_rate_override'], isNull);
        expect(p['setup_verified'], false);
        expect(m.calls, isEmpty);
        expect(t.takeException(), isNull);
      },
    );
  }
  testWidgets(
    'Preset browser resolves into unconfirmed editable form without applying',
    (t) async {
      final m = await mount(t);
      await click(t, 'More');
      await click(t, 'Knife presets');
      expect(
        find.text('Material differs from the saved preset.'),
        findsOneWidget,
      );
      await click(t, 'Review with station 8');
      await t.pump(const Duration(milliseconds: 300));
      await t.pumpAndSettle();
      expect(find.text('Add Drag knife'), findsOneWidget);
      expect(m.requestedCommand, 'manufacturing.knife_resolve');
      expect(m.requested!['fingerprint'], 'fp');
      expect(m.calls, isEmpty);
      await click(t, 'Apply');
      expect(m.requested!['params']['name'], 'Saved knife');
      expect(m.requested!['params']['setup_verified'], false);
      expect(m.requested!['params']['outside_corner_loops'], true);
      expect(m.requested!['params']['corner_feed_ipm'], 20);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Preset import and save use private versioned commands and surface stale failure',
    (t) async {
      final m = await mount(t);
      await click(t, 'More');
      await click(t, 'Knife presets');
      await field(t, 'Knife recipe JSON file path', 'C:/chosen/knife.json');
      await click(t, 'Import presets');
      expect(m.requestedCommand, 'manufacturing.knife_import');
      expect(m.requested!['path'], 'C:/chosen/knife.json');
      await field(t, 'New preset name', 'New knife');
      await click(t, 'Save applied operation as preset');
      expect(m.requestedCommand, 'manufacturing.knife_save');
      expect(m.requested!['operation_id'], 'knife-op');
      expect(m.requested!['version'], 'v');
      m.rejectPreset = true;
      await click(t, 'Import presets');
      expect(
        find.textContaining('Job changed; reopen knife presets.'),
        findsOneWidget,
      );
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
}
