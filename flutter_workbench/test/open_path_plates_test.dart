import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/open_path_plates_dialog.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'manufacturing_expanded_test.dart' show ExpandedModel;

class OwnershipModel extends ExpandedModel {
  Json details = {
    'token': 'job-1',
    'units': 'in',
    'plates': [],
    'paths': <Json>[
      {
        'id': 'crossing-line',
        'layer': '0',
        'bounds': [90, 10, 100, 10],
        'assigned': null,
        'owner': null,
        'choices': [1, 2],
        'overtravel': false,
        'operations': ['Cut line'],
        'editable': true,
      },
    ],
  };
  OwnershipModel() {
    state['manufacturing']['openPathOwnership'] = true;
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command == 'manufacturing.open_paths') {
      return {'ok': true, 'manufacturing': details};
    }
    return super.request(command, params);
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    final ok = await super.run(command, params);
    if (ok && command == 'manufacturing.open_path_assign') {
      final row = (details['paths'] as List).first;
      row['assigned'] = params['plate_number'];
      row['owner'] = params['plate_number'];
      row['overtravel'] = true;
      details['token'] = 'job-2';
    }
    return ok;
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
  Future<OwnershipModel> mount(
    WidgetTester t, {
    bool workspace = false,
    bool compact = false,
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1400, 1100);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = OwnershipModel();
    addTearDown(m.dispose);
    await t.pumpWidget(
      MaterialApp(
        home: workspace
            ? ManufacturingWorkspace(
                model: m,
                onSave: () async => true,
                initialInspectorOpen: true,
              )
            : Scaffold(
                body: RepaintBoundary(
                  key: const ValueKey('capture'),
                  child: OpenPathPlatesDialog(model: m),
                ),
              ),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  Future<void> click(WidgetTester t, String label) async {
    if (find.text(label).evaluate().isEmpty) {
      final scroll = find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first;
      t.state<ScrollableState>(scroll).position.jumpTo(0);
      await t.pumpAndSettle();
      await t.scrollUntilVisible(find.text(label), 250, scrollable: scroll);
    }
    await Scrollable.ensureVisible(t.element(find.text(label)), alignment: .5);
    await t.pumpAndSettle();
    await t.tap(find.text(label));
    await t.pumpAndSettle();
  }

  testWidgets('Assignment uses reviewed token and explicit touched plate', (
    t,
  ) async {
    final m = await mount(t);
    expect(find.textContaining('Unassigned'), findsOneWidget);
    await t.tap(find.byType(DropdownButtonFormField<int>));
    await t.pumpAndSettle();
    await t.tap(find.text('Plate 2').last);
    await t.pumpAndSettle();
    expect(m.applied, {
      'id': 'crossing-line',
      'plate_number': 2,
      'token': 'job-1',
    });
    expect(find.textContaining('Output owner: Plate 2'), findsOneWidget);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Clear assignment restores automatic ownership and Select path is separate',
    (t) async {
      final m = await mount(t);
      await t.tap(find.byType(DropdownButtonFormField<int>));
      await t.pumpAndSettle();
      await t.tap(find.text('Plate 1').last);
      await t.pumpAndSettle();
      await t.tap(find.byType(DropdownButtonFormField<int>));
      await t.pumpAndSettle();
      await t.tap(find.text('Automatic ownership').last);
      await t.pumpAndSettle();
      expect(m.applied!['plate_number'], isNull);
      expect(m.applied!['token'], 'job-2');
      await t.tap(find.text('Select path'));
      await t.pumpAndSettle();
      expect(m.calls.last, 'selection.set');
      expect(m.applied, {
        'ids': ['crossing-line'],
      });
    },
  );
  testWidgets(
    'Stale assignment stays open with error and Refresh is available',
    (t) async {
      final m = await mount(t);
      final before = jsonEncode(m.details);
      m.reject = true;
      await t.tap(find.byType(DropdownButtonFormField<int>));
      await t.pumpAndSettle();
      await t.tap(find.text('Plate 2').last);
      await t.pumpAndSettle();
      expect(find.textContaining('Cutter is used'), findsOneWidget);
      expect(jsonEncode(m.details), before);
      await t.tap(find.text('Refresh'));
      await t.pumpAndSettle();
      expect(find.textContaining('Cutter is used'), findsNothing);
    },
  );
  testWidgets('Compact assignment review is readable without changing job', (
    t,
  ) async {
    final m = await mount(t, compact: true);
    final before = jsonEncode(m.state);
    expect(t.takeException(), isNull);
    await t.runAsync(() async {
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('capture')),
      );
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      await Directory('build/ui-review').create(recursive: true);
      await File(
        'build/ui-review/open-path-assignments-compact.png',
      ).writeAsBytes(bytes!.buffer.asUint8List());
      image.dispose();
    });
    expect(jsonEncode(m.state), before);
    expect(m.calls, isEmpty);
  });
  testWidgets('Manufacturing exposes assignments through backend capability', (
    t,
  ) async {
    await mount(t, workspace: true);
    await click(t, 'Open-path plate assignments');
    expect(find.byType(OpenPathPlatesDialog), findsOneWidget);
    expect(t.takeException(), isNull);
  });
  testWidgets('Export requires overtravel review and regeneration clears it', (
    t,
  ) async {
    final m = await mount(t, workspace: true);
    m.response = {
      'status': 'ready',
      'token': 'motion-1',
      'motions': [],
      'validation': {'issues': []},
      'openPathOvertravel': [
        {
          'id': 'crossing-line',
          'owner': 1,
          'operations': ['Cut line'],
        },
      ],
    };
    const review =
        'I reviewed the complete cuts, machine travel and fixture clearance beyond stock.';
    await click(t, 'Generate and check toolpaths');
    await click(t, 'I reviewed the toolpath motion and preflight messages.');
    await click(t, 'Export reviewed package');
    expect(m.requestedCommand, 'manufacturing.generate');
    await click(t, review);
    await click(
      t,
      'Allow negative X/Y for this export; I verified the displayed range against the machine setup.',
    );
    await click(t, 'Export reviewed package');
    expect(m.requestedCommand, 'manufacturing.export');
    expect(m.requested!['overtravel_reviewed'], true);
    expect(m.requested!['allow_negative_xy'], true);
    await click(t, 'Generate and check toolpaths');
    await click(t, 'I reviewed the toolpath motion and preflight messages.');
    await click(t, 'Export reviewed package');
    expect(m.requestedCommand, 'manufacturing.generate');
    expect(
      t
          .widget<CheckboxListTile>(
            find.widgetWithText(
              CheckboxListTile,
              'Allow negative X/Y for this export; I verified the displayed range against the machine setup.',
            ),
          )
          .value,
      false,
    );
    expect(t.takeException(), isNull);
  });
}
