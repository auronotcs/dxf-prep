import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/small_geometry_dialog.dart';
import 'package:workbench_prototype/design/repair_review.dart';
import 'package:workbench_prototype/design/shape_tools_dialog.dart';

class SmallModel extends DesignController {
  final calls = <String>[];
  Json? params;
  Json? keptParams;
  bool kept = false;
  SmallModel() {
    ready = true;
    busy = false;
    state = {
      'revision': 1,
      'units': 'mm',
      'smallGeometryReview': true,
      'capabilities': ['opening_repair_review'],
      'selected': ['original'],
      'layers': <Json>[
        {'name': 'hidden', 'visible': false, 'locked': true},
      ],
      'entities': <Json>[
        {
          'id': 'tiny',
          'kind': 'line',
          'layer': 'hidden',
          'start': {'x': 0, 'y': 0},
          'end': {'x': .1, 'y': 0},
          'bounds': [0, 0, .1, 0],
        },
      ],
    };
  }
  Json get finding => {
    'id': 'tiny',
    'label': 'Small detail',
    'layer': 'hidden',
    'width': .1,
    'height': 0,
    'x': .05,
    'y': 0,
    'bounds': [0, 0, .1, 0],
    'reason': 'Layer is locked.',
    'hidden': true,
    'locked': true,
    'kept': kept,
    'isolated': false,
    'affectedOperations': <String>[],
    'recommendation': 'Inspect the detail and its owning workflow.',
  };
  @override
  Future<Json> request(String command, [Json p = const {}]) async {
    calls.add(command);
    params = p;
    if (command == 'design.repair_scan') {
      return {
        'ok': true,
        'review': {'affectedCount': 0},
      };
    }
    return {
      'ok': true,
      'review': {
        'revision': state['revision'],
        'token': 'review-token',
        'maximumSize': p['maximum_size'] ?? .254,
        'findings': kept && p['include_kept'] != true
            ? <Json>[]
            : <Json>[finding],
      },
    };
  }

  @override
  Future<bool> run(String command, [Json p = const {}]) async {
    calls.add(command);
    params = p;
    keptParams = p;
    kept = true;
    state['revision'] = 2;
    notifyListeners();
    return true;
  }
}

void main() {
  Future<void> mount(
    WidgetTester tester,
    SmallModel m, {
    bool recommendation = false,
    Size size = const Size(1000, 760),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(m.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => recommendation
                  ? reviewOpeningRepair(context, m)
                  : showDialog<void>(
                      context: context,
                      builder: (_) => SmallGeometryDialog(model: m),
                    ),
              child: const Text('Open review'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open review'));
    await tester.pumpAndSettle();
  }

  bool keepEnabled(WidgetTester tester) =>
      tester
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Keep selected'),
          )
          .onPressed !=
      null;

  testWidgets(
    'Inspection highlights and zooms hidden findings without editing layers or selection',
    (tester) async {
      final m = SmallModel();
      await mount(tester, m, size: const Size(820, 520));
      expect(m.params!['maximum_size'], .254);
      expect(find.textContaining('Small detail · hidden'), findsOneWidget);
      expect(keepEnabled(tester), false);
      await tester.ensureVisible(find.textContaining('Small detail · hidden'));
      await tester.tap(find.textContaining('Small detail · hidden'));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text('Zoom to selected'));
      await tester.tap(find.text('Zoom to selected'));
      await tester.pumpAndSettle();
      final canvas = find.byWidgetPredicate(
        (w) => w is CustomPaint && w.painter is ShapePreviewPainter,
      );
      final painter =
          tester.widget<CustomPaint>(canvas).painter! as ShapePreviewPainter;
      expect(painter.highlight.single['id'], 'tiny');
      expect(painter.zoomBounds, isNotNull);
      expect(m.selection, {'original'});
      expect(m.layers.single['locked'], true);
      expect(m.layers.single['visible'], false);
      await tester.ensureVisible(find.byType(TextField));
      await tester.enterText(find.byType(TextField), '0');
      await tester.pumpAndSettle();
      expect(keepEnabled(tester), false);
      await tester.tap(find.text('Scan'));
      await tester.pumpAndSettle();
      expect(find.textContaining('positive finite'), findsOneWidget);
      expect(m.calls, ['design.small_scan']);
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Keep is explicit and includes the reviewed token; kept findings can be included again',
    (tester) async {
      final m = SmallModel();
      await mount(tester, m);
      await tester.tap(find.textContaining('Small detail · hidden'));
      await tester.pumpAndSettle();
      expect(keepEnabled(tester), true);
      await tester.tap(find.text('Keep selected'));
      await tester.pumpAndSettle();
      expect(m.calls, [
        'design.small_scan',
        'design.small_keep',
        'design.small_scan',
      ]);
      expect(m.keptParams, {
        'token': 'review-token',
        'entity_ids': ['tiny'],
      });
      expect(find.textContaining('Kept the reviewed objects'), findsOneWidget);
      expect(m.entities.length, 1);
      expect(m.selection, {'original'});
      await tester.tap(find.byType(Checkbox).first);
      await tester.pumpAndSettle();
      await tester.tap(find.text('Scan'));
      await tester.pumpAndSettle();
      expect(m.params!['include_kept'], true);
      expect(
        find.textContaining('Small detail · hidden · Kept'),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Read-only jobs inspect but cannot keep; document changes invalidate findings',
    (tester) async {
      final m = SmallModel();
      m.state['readOnly'] = true;
      await mount(tester, m);
      await tester.tap(find.textContaining('Small detail · hidden'));
      await tester.pumpAndSettle();
      expect(keepEnabled(tester), false);
      m.state['revision'] = 2;
      m.notifyListeners();
      await tester.pumpAndSettle();
      expect(
        find.textContaining('Drawing or settings changed'),
        findsOneWidget,
      );
      expect(find.textContaining('Small detail · hidden'), findsNothing);
      expect(m.calls, ['design.small_scan']);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Opening recommendations can be declined or reviewed without a mutation',
    (tester) async {
      final m = SmallModel();
      await mount(tester, m, recommendation: true);
      expect(
        find.text('Small geometry / DXF joins need review'),
        findsOneWidget,
      );
      await tester.tap(find.text('Not now'));
      await tester.pumpAndSettle();
      expect(m.calls, ['design.small_scan', 'design.repair_scan']);
      await tester.tap(find.text('Open review'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Review findings'));
      await tester.pumpAndSettle();
      expect(find.byType(SmallGeometryDialog), findsOneWidget);
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      expect(m.calls, [
        'design.small_scan',
        'design.repair_scan',
        'design.small_scan',
      ]);
      expect(m.selection, {'original'});
      expect(m.entities.length, 1);
      expect(tester.takeException(), isNull);
    },
  );
}
