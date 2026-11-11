import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'package:workbench_prototype/design/manufacturing_tools.dart';
import 'manufacturing_test.dart' show ManufacturingModel;

class ToolsModel extends ManufacturingModel {
  Json? lastParams;
  String? lastCommand;
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    lastCommand = command;
    lastParams = params;
    if (command == 'manufacturing.catalog_list') {
      return {
        'ok': true,
        'version': 'v1',
        'catalog': [
          {
            'id': 'c1',
            'name': 'Aluminum O-Flute',
            'kind': 'end_mill',
            'diameter_in': .125,
            'recipes': [
              {
                'material': 'Aluminum prototype',
                'rpm': 18000,
                'feed': 72,
                'plunge': 30,
                'stepdown': .0625,
              },
            ],
          },
        ],
      };
    }
    if (command == 'manufacturing.catalog_resolve') {
      return {
        'ok': true,
        'tool': {
          'station': params['station'],
          'name': 'Aluminum O-Flute',
          'kind': 'end_mill',
          'diameter': .125,
          'spindle_mode': 'required',
          'spindle_rpm': 18000,
          'feed_rate': 1.2,
          'plunge_rate': .5,
          'max_stepdown': .0625,
          'metadata': {},
        },
      };
    }
    if (command == 'manufacturing.management_prepare') {
      return {
        'ok': true,
        'manufacturing': {
          'status': 'management_ready',
          'token': 'draft',
          'action': 'turret',
          'details': {
            'changed_stations': [1],
            'operation_order': [],
            'geometry_changes': [],
          },
          'motions': [],
        },
      };
    }
    return super.request(command, params);
  }
}

void main() {
  setUpAll(() async {
    final font = FontLoader('Roboto')
      ..addFont(
        File(
          'C:/Windows/Fonts/segoeui.ttf',
        ).readAsBytes().then((b) => ByteData.sublistView(b)),
      );
    final icons = FontLoader('MaterialIcons')
      ..addFont(
        File(
          '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
        ).readAsBytes().then((b) => ByteData.sublistView(b)),
      );
    await font.load();
    await (FontLoader('Segoe UI')..addFont(
          File(
            'C:/Windows/Fonts/segoeui.ttf',
          ).readAsBytes().then((b) => ByteData.sublistView(b)),
        ))
        .load();
    await icons.load();
  });
  Future<ToolsModel> mount(
    WidgetTester t, {
    double width = 1440,
    double height = 900,
  }) async {
    t.view.physicalSize = Size(width, height);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = ToolsModel();
    addTearDown(m.dispose);
    await t.pumpWidget(
      MaterialApp(
        theme: ThemeData(
          colorScheme: ColorScheme.fromSeed(seedColor: Colors.blue),
          useMaterial3: true,
        ),
        home: RepaintBoundary(
          key: const ValueKey('ribbon-image'),
          child: ManufacturingWorkspace(model: m, onSave: () async => true),
        ),
      ),
    );
    await t.pumpAndSettle();
    return m;
  }

  testWidgets(
    'Ribbon gives drawing full width and toggles inspector without losing selection',
    (t) async {
      final m = await mount(t);
      final canvas = find.byKey(const ValueKey('manufacturing-drawing-canvas'));
      expect(t.getSize(canvas).width, greaterThan(1300));
      expect(t.getTopLeft(find.text('VIEW')).dx, lessThan(20));
      expect(find.text('Operations & review'), findsNothing);
      await t.tap(find.text('Inspect'));
      await t.pumpAndSettle();
      expect(find.text('Operations & review'), findsOneWidget);
      await t.tap(find.byTooltip('Close inspector'));
      await t.pumpAndSettle();
      expect(m.selection, contains('entity_123456'));
      expect(t.takeException(), isNull);
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('ribbon-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        await Directory('build/ui-review').create(recursive: true);
        await File(
          'build/ui-review/manufacturing-ribbon.png',
        ).writeAsBytes(bytes!.buffer.asUint8List());
      });
    },
  );
  testWidgets(
    'Library resolves a recipe into draft and Cancel preserves the job',
    (t) async {
      final m = await mount(t);
      await t.tap(find.text('Library'));
      await t.pumpAndSettle();
      expect(find.text('Tool & Recipe Library'), findsOneWidget);
      await t.tap(find.text('Load into T1 draft'));
      await t.pumpAndSettle();
      expect(m.lastCommand, 'manufacturing.catalog_resolve');
      expect(m.lastParams!['version'], 'v1');
      expect(find.text('Turret Setup'), findsOneWidget);
      expect(m.state['manufacturing']['tools'], isEmpty);
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(m.calls.where((v) => v.contains('apply')), isEmpty);
    },
  );
  testWidgets(
    'Turret review sends per-second values and opens explicit review panel',
    (t) async {
      final m = await mount(t);
      await t.tap(find.text('Library'));
      await t.pumpAndSettle();
      await t.tap(find.text('Load into T1 draft'));
      await t.pumpAndSettle();
      await t.tap(find.text('Review turret changes'));
      await t.pumpAndSettle();
      expect(m.lastCommand, 'manufacturing.management_prepare');
      expect(m.lastParams!['params']['tools'][0]['feed_rate'], 1.2);
      expect(find.byType(ManufacturingToolsDialog), findsNothing);
      expect(find.text('Operations & review'), findsOneWidget);
      final scroll = find
          .descendant(
            of: find.byType(ListView).first,
            matching: find.byType(Scrollable),
          )
          .first;
      t.state<ScrollableState>(scroll).position.jumpTo(0);
      await t.pumpAndSettle();
      await t.scrollUntilVisible(
        find.text('Apply workflow draft'),
        200,
        scrollable: find
            .descendant(
              of: find.byType(ListView),
              matching: find.byType(Scrollable),
            )
            .first,
      );
      expect(find.text('Apply workflow draft'), findsOneWidget);
      expect(m.calls.where((v) => v.endsWith('management_apply')), isEmpty);
    },
  );
  testWidgets('Compact ribbon remains usable at 800 by 600', (t) async {
    await mount(t, width: 800, height: 600);
    expect(t.takeException(), isNull);
    expect(
      t
          .getSize(find.byKey(const ValueKey('manufacturing-drawing-canvas')))
          .height,
      greaterThan(350),
    );
    await t.tap(find.text('Turret'));
    await t.pumpAndSettle();
    expect(t.takeException(), isNull);
    expect(find.text('Review turret changes'), findsOneWidget);
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
  });
}
