import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/repair_suite_dialog.dart';
import 'package:workbench_prototype/design/small_geometry_dialog.dart';
import 'package:workbench_prototype/design/shape_tools_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'manufacturing_test.dart' show ManufacturingModel;
import 'small_geometry_test.dart' show SmallModel;

class SuiteModel extends ManufacturingModel {
  Json? options;
  int previews = 0;
  bool empty = false;
  SuiteModel() {
    state['repairSuite'] = true;
  }
  @override
  Future<Json> request(String command, [Json p = const {}]) async {
    if (command != 'design.suite_preview') return super.request(command, p);
    options = p;
    previews++;
    return {
      'ok': true,
      'preview': {
        'token': empty ? null : 'suite-$previews',
        'revision': state['revision'],
        'count': empty ? 0 : 1,
        'sources': entities,
        'outputs': empty ? <Json>[] : entities,
        'rows': <Json>[
          {
            'ids': [entities.first['id']],
            'kind': 'repair',
            'message': '1 redundant point; maximum change 0 in.',
            'point': {'x': 2.0, 'y': 2.0},
            'outputs': entities,
          },
          {
            'ids': [entities.first['id']],
            'kind': 'review',
            'message': 'Endpoint fragments require artwork review.',
            'point': {'x': 2.0, 'y': 2.0},
            'outputs': <Json>[],
          },
        ],
      },
    };
  }
}

class ActionModel extends SmallModel {
  bool layerLocked = true, deleted = false;
  Json? action;
  ActionModel() {
    state['smallGeometryActions'] = true;
  }
  @override
  Json get finding => {
    ...super.finding,
    'locked': layerLocked,
    'isolated': !layerLocked,
  };
  @override
  Future<Json> request(String command, [Json p = const {}]) async {
    final result = await super.request(command, p);
    if (deleted) (result['review'] as Json)['findings'] = <Json>[];
    return result;
  }

  @override
  Future<bool> run(String command, [Json p = const {}]) async {
    calls.add(command);
    action = p;
    if (command == 'design.small_unlock') layerLocked = false;
    if (command == 'design.small_delete') deleted = true;
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
    return true;
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
  });
  Future<void> mount(
    WidgetTester t,
    DesignController m,
    Widget dialog, {
    bool compact = false,
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1100, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    addTearDown(m.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('repair-image'),
        child: MaterialApp(
          home: Builder(
            builder: (c) => Scaffold(
              body: TextButton(
                onPressed: () =>
                    showDialog<void>(context: c, builder: (_) => dialog),
                child: const Text('Open'),
              ),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Open'));
    await t.pumpAndSettle();
  }

  Future<void> tap(WidgetTester t, String label) async {
    await t.ensureVisible(find.text(label));
    await t.pumpAndSettle();
    await t.tap(find.text(label));
    await t.pumpAndSettle();
  }

  bool enabled(WidgetTester t, String label) =>
      t
          .widget<FilledButton>(find.widgetWithText(FilledButton, label))
          .onPressed !=
      null;

  testWidgets(
    'Safe batch preview requires token; fragment approval invalidates old proposal',
    (t) async {
      final m = SuiteModel();
      await mount(t, m, RepairSuiteDialog(model: m));
      expect(enabled(t, 'Apply safe repairs'), false);
      await tap(t, 'Preview repair batch');
      expect(m.options, {'scope': 'selected', 'include_fragments': false});
      expect(enabled(t, 'Apply safe repairs'), true);
      await tap(t, 'Include reviewed endpoint fragments');
      expect(enabled(t, 'Apply reviewed batch'), false);
      await tap(t, 'Preview repair batch');
      expect(m.options!['include_fragments'], true);
      await tap(t, 'Apply reviewed batch');
      expect(m.calls, ['design.suite_apply']);
      expect(m.applied, {'token': 'suite-2'});
      expect(find.byType(RepairSuiteDialog), findsNothing);
    },
  );
  testWidgets('Whole drawing scope; stale and empty proposals cannot apply', (
    t,
  ) async {
    final m = SuiteModel();
    m.state['selected'] = <String>[];
    m.empty = true;
    await mount(t, m, RepairSuiteDialog(model: m));
    await tap(t, 'Preview repair batch');
    expect(m.options!['scope'], 'all');
    expect(enabled(t, 'Apply safe repairs'), false);
    m.externalEdit();
    await t.pumpAndSettle();
    expect(find.textContaining('Drawing changed'), findsOneWidget);
    expect(
      t
          .widget<TextButton>(
            find.widgetWithText(TextButton, 'Preview repair batch'),
          )
          .onPressed,
      isNull,
    );
    expect(m.calls, isEmpty);
  });
  testWidgets(
    'Row detail uses physical window; Show batch preserves full Apply scope; compact render',
    (t) async {
      final m = SuiteModel();
      m.state['units'] = 'mm';
      await mount(t, m, RepairSuiteDialog(model: m), compact: true);
      await tap(t, 'Preview repair batch');
      await tap(t, '1 redundant point; maximum change 0 in.');
      final canvas = find.byWidgetPredicate(
        (w) => w is CustomPaint && w.painter is ShapePreviewPainter,
      );
      var painter =
          t.widget<CustomPaint>(canvas).painter! as ShapePreviewPainter;
      expect(painter.zoomBounds!.width, closeTo(1.016, 1e-8));
      expect(painter.sources.length, 1);
      await t.ensureVisible(canvas);
      await t.pumpAndSettle();
      await t.runAsync(() async {
        final boundary = t.renderObject<RenderRepaintBoundary>(
          find.byKey(const ValueKey('repair-image')),
        );
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        await File(
          'build/ui-review/repair-suite-compact.png',
        ).writeAsBytes(bytes!.buffer.asUint8List());
        image.dispose();
      });
      await tap(t, 'Show batch');
      painter = t.widget<CustomPaint>(canvas).painter! as ShapePreviewPainter;
      expect(painter.zoomBounds, isNull);
      await tap(t, 'Apply safe repairs');
      expect(m.applied, {'token': 'suite-1'});
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Rejected Apply requires new preview', (t) async {
    final m = SuiteModel();
    m.reject = true;
    await mount(t, m, RepairSuiteDialog(model: m));
    await tap(t, 'Preview repair batch');
    await tap(t, 'Apply safe repairs');
    expect(enabled(t, 'Apply safe repairs'), false);
    expect(find.text(m.message), findsOneWidget);
  });
  testWidgets(
    'Workspace command is gated and Cancel retains selection camera grid and snap',
    (t) async {
      final m = SuiteModel();
      addTearDown(m.dispose);
      t.view.physicalSize = const Size(1200, 900);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      final v = workspace.view as DesignViewport;
      v.scale = 37;
      v.translation = const Offset(144, 288);
      v.grid = false;
      v.snap = true;
      final selected = Set.of(m.selection);
      final command = designGroups.values
          .expand((g) => g)
          .firstWhere((c) => c.id == 'repairSuite');
      expect(workspace.enabled(command), true);
      m.state['readOnly'] = true;
      expect(workspace.enabled(command), false);
      m.state['readOnly'] = false;
      workspace.performAction('repairSuite');
      await t.pumpAndSettle();
      await tap(t, 'Preview repair batch');
      await tap(t, 'Cancel');
      expect(v.scale, 37);
      expect(v.translation, const Offset(144, 288));
      expect(v.grid, false);
      expect(v.snap, true);
      expect(m.selection, selected);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Unlock confirmation names whole layer; canceled deletion preserves objects, confirmed deletion rescans',
    (t) async {
      final m = ActionModel();
      await mount(t, m, SmallGeometryDialog(model: m));
      await tap(t, 'Small detail · hidden');
      await tap(t, 'Unlock selected layers');
      expect(
        find.textContaining('every object on these layers'),
        findsOneWidget,
      );
      await tap(t, 'Cancel');
      expect(m.action, isNull);
      await tap(t, 'Unlock selected layers');
      await tap(t, 'Unlock layers');
      expect(m.action!['confirmed'], true);
      expect(m.calls.contains('design.small_unlock'), true);
      expect(m.layers.single['visible'], false);
      expect(
        t
            .widget<TextButton>(
              find.widgetWithText(TextButton, 'Delete selected'),
            )
            .onPressed,
        isNull,
      );
      await tap(t, 'Small detail · hidden');
      await tap(t, 'Delete selected');
      await tap(t, 'Cancel');
      expect(m.deleted, false);
      await tap(t, 'Delete selected');
      await tap(t, 'Delete objects');
      expect(m.deleted, true);
      expect(m.action, {
        'token': 'review-token',
        'entity_ids': ['tiny'],
        'confirmed': true,
      });
      expect(find.textContaining('Small detail · hidden'), findsNothing);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'External edit while deletion confirmation is open prevents submission',
    (t) async {
      final m = ActionModel();
      m.layerLocked = false;
      await mount(t, m, SmallGeometryDialog(model: m));
      await tap(t, 'Small detail · hidden');
      await tap(t, 'Delete selected');
      m.state['revision'] = 12;
      m.notifyListeners();
      await t.pumpAndSettle();
      await tap(t, 'Delete objects');
      expect(m.action, isNull);
      expect(m.deleted, false);
    },
  );
}
