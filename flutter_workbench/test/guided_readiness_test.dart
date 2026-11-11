import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/guided_readiness_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'package:workbench_prototype/design/manufacturing_workspace.dart';
import 'design_workspace_test.dart' show TestDesignController;

class ReadinessModel extends TestDesignController {
  final Json fixture = jsonDecode(
    File('test/fixtures/guided_readiness.json').readAsStringSync(),
  );
  final requests = <Json>[];
  String mode = 'mixed';
  bool rejectActions = false;
  int refreshes = 0;
  Json? returned;
  ReadinessModel() {
    state = jsonDecode(jsonEncode(fixture['state'])) as Json;
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    requests.add({'command': command, 'params': params});
    if (command == 'manufacturing.guided_readiness') {
      final Json r = jsonDecode(jsonEncode(fixture[mode]));
      r['token'] = 'review-${++refreshes}';
      r['revision'] = state['revision'];
      return {'ok': true, 'review': r};
    }
    if (command == 'manufacturing.guided_action') {
      if (rejectActions) {
        return {
          'ok': false,
          'error': 'Job, cutter, stock or library changed. Refresh readiness.',
        };
      }
      final rows = fixture[mode]['operations'] as List;
      final op = rows
          .where((r) => r['id'] == params['operation_id'])
          .firstOrNull;
      return {
        'ok': true,
        'review': {
          'action': params['action'],
          'operation_id': params['operation_id'],
          if (op != null) 'bounds': op['bounds'],
          if (params['action'] == 'compare') 'operation': op,
        },
      };
    }
    return {'ok': true};
  }

  void externalChange() {
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
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
  Future<void> size(WidgetTester t, [Size s = const Size(800, 600)]) async {
    t.view.physicalSize = s;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
  }

  Future<void> mount(WidgetTester t, ReadinessModel m) async {
    await size(t);
    addTearDown(m.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('readiness-image'),
        child: MaterialApp(
          home: Builder(
            builder: (context) => Scaffold(
              body: TextButton(
                onPressed: () async {
                  m.returned = await showDialog<Json>(
                    context: context,
                    builder: (_) => GuidedReadinessDialog(model: m),
                  );
                },
                child: const Text('Start'),
              ),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Start'));
    await t.pumpAndSettle();
  }

  Future<void> click(WidgetTester t, String label) async {
    final f = find.text(label);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  bool enabled(WidgetTester t, String label) =>
      t.widget<TextButton>(find.widgetWithText(TextButton, label)).onPressed !=
      null;
  testWidgets(
    'Stock and operation readiness distinguish unsupported work; compact review is nonmutating',
    (t) async {
      final m = ReadinessModel();
      final before = jsonEncode(m.state);
      await mount(t, m);
      expect(find.textContaining('0 pass readiness'), findsOneWidget);
      expect(find.textContaining('1 outside review scope'), findsOneWidget);
      expect(find.text('Physical stock: Verified and current'), findsOneWidget);
      await click(t, 'Drill fixture · T1');
      expect(find.textContaining('Specialized and dependent'), findsOneWidget);
      expect(enabled(t, 'Compare recipes'), false);
      await click(t, 'Outside · T1');
      await t.ensureVisible(find.text('Outside — readiness details'));
      await t.pumpAndSettle();
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('readiness-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(
          'build/ui-review/guided-readiness-compact.png',
        ).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await click(t, 'Close');
      expect(jsonEncode(m.state), before);
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Compare exposes frozen and effective values plus qualification evidence and exclusions',
    (t) async {
      final m = ReadinessModel()..mode = 'ready';
      await mount(t, m);
      await click(t, 'Compare recipes');
      expect(m.requests.last['params'], {
        'token': 'review-1',
        'operation_id': m.fixture['ready']['operations'][0]['id'],
        'action': 'compare',
      });
      expect(find.textContaining('rpm: saved 18000'), findsOneWidget);
      expect(find.textContaining('effective 18000'), findsOneWidget);
      expect(find.textContaining('Synthetic fixture only'), findsOneWidget);
      expect(find.textContaining('Compatible for review'), findsOneWidget);
      await click(t, 'Show excluded library recipes');
      expect(find.textContaining('Excluded'), findsWidgets);
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Changed job disables actions and refresh binds a new review', (
    t,
  ) async {
    final m = ReadinessModel();
    await mount(t, m);
    m.externalChange();
    await t.pumpAndSettle();
    expect(enabled(t, 'Job setup'), false);
    expect(
      find.text(
        'Drawing changed. Refresh readiness before using these findings.',
      ),
      findsOneWidget,
    );
    await click(t, 'Refresh review');
    expect(enabled(t, 'Job setup'), true);
    await click(t, 'Job setup');
    expect(m.requests.last['params']['token'], 'review-2');
    expect(m.returned?['action'], 'stock');
    expect(m.commands, isEmpty);
    expect(t.takeException(), isNull);
  });
  testWidgets(
    'Library changes reject old actions and clear findings until refresh',
    (t) async {
      final m = ReadinessModel();
      await mount(t, m);
      m.rejectActions = true;
      await click(t, 'Compare recipes');
      expect(find.textContaining('library changed'), findsOneWidget);
      expect(find.text('Outside — readiness details'), findsNothing);
      expect(m.returned, isNull);
      m.rejectActions = false;
      await click(t, 'Refresh review');
      await click(t, 'Tool library');
      expect(m.returned?['action'], 'library');
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Corrective actions return only the validated action; empty jobs have no operation action',
    (t) async {
      final m = ReadinessModel()..mode = 'blocked';
      await mount(t, m);
      await click(t, 'Verify stock');
      expect(m.returned?['action'], 'verify');
      expect(m.requests.last['params']['operation_id'], isNull);
      await t.tap(find.text('Start'));
      await t.pumpAndSettle();
      await click(t, 'Turret setup');
      expect(m.returned?['action'], 'turret');
      await t.tap(find.text('Start'));
      await t.pumpAndSettle();
      await click(t, 'Edit operation');
      expect(
        m.returned?['operation_id'],
        m.fixture['blocked']['operations'][0]['id'],
      );
      m.fixture['blocked']['operations'] = [];
      m.fixture['blocked']['counts'] = {
        'ready': 0,
        'blocked': 0,
        'unsupported': 0,
        'disabled': 0,
      };
      await t.tap(find.text('Start'));
      await t.pumpAndSettle();
      expect(find.textContaining('No operations yet'), findsOneWidget);
      expect(find.text('Edit operation'), findsNothing);
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Manufacturing entry preserves camera on close and Locate changes only the view',
    (t) async {
      await size(t, const Size(1500, 950));
      final m = ReadinessModel();
      addTearDown(m.dispose);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      workspace.view.scale = 17.0;
      workspace.view.translation = const Offset(111, 333);
      workspace.view.grid = true;
      workspace.view.snap = true;
      await t.tap(find.byKey(const ValueKey('workspace-manufacturing')));
      await t.pumpAndSettle();
      final dynamic manufacturing = t.state(
        find.byType(ManufacturingWorkspace),
      );
      final before = jsonEncode(m.state);
      final task = manufacturing.guidedReadiness() as Future<void>;
      await t.pumpAndSettle();
      await click(t, 'Close');
      await task;
      expect(workspace.view.scale, 17);
      expect(workspace.view.translation, const Offset(111, 333));
      expect(workspace.view.grid && workspace.view.snap, true);
      final locate = manufacturing.guidedReadiness() as Future<void>;
      await t.pumpAndSettle();
      await click(t, 'Locate geometry');
      await locate;
      expect(workspace.view.scale, isNot(17));
      expect(jsonEncode(m.state), before);
      expect(m.commands, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
}
