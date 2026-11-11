import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/dimension_dialog.dart';
import 'package:workbench_prototype/design/canvas.dart';

Json ref(double x, double y) => {
  'entity_id': '',
  'feature': 'free',
  'fallback': {'x': x, 'y': y},
};
Json rendered(Json annotation) => {
  'id': 'dimension-test',
  'annotation': {...annotation, 'id': 'dimension-test'},
  'bounds': [0, 0, 5, 3],
  'label': '5.000 mm',
  'resolved': true,
  'extension_lines': <dynamic>[],
  'path': <dynamic>[],
  'dimension_start': {'x': 0, 'y': 0},
  'dimension_end': {'x': 5, 'y': 0},
  'text_point': {'x': 2.5, 'y': 0},
  'text_rotation': 0,
  'arrow_mode': 'both',
};

class DimensionModel extends DesignController {
  final calls = <String>[];
  Json? payload;
  DimensionModel() {
    ready = true;
    busy = false;
    state = {
      'revision': 1,
      'units': 'mm',
      'entities': <Json>[],
      'selected': ['unchanged'],
      'layers': <Json>[],
      'dimensions': <Json>[],
    };
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    calls.add(command);
    payload = params;
    if (command == 'design.dimension_context') {
      return {'ok': true, 'references': <Json>[]};
    }
    if (command == 'design.dimension_pick') {
      return {
        'ok': true,
        'reference': ref(number(params['x']), number(params['y'])),
      };
    }
    return {
      'ok': true,
      'preview': {
        'token': 'dimension-token',
        'revision': state['revision'],
        'dimension': rendered(params['annotation'] as Json),
      },
    };
  }

  @override
  Future<bool> run(String command, [Json params = const {}]) async {
    calls.add(command);
    payload = params;
    if (command == 'design.dimension_delete') state['dimensions'] = <Json>[];
    state['revision'] = (state['revision'] as int) + 1;
    notifyListeners();
    return true;
  }
}

void main() {
  Future<void> mount(
    WidgetTester tester,
    DimensionModel m, {
    bool manager = false,
    Size size = const Size(1000, 900),
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
              onPressed: () => manager
                  ? manageDimensions(context, m)
                  : showDialog<void>(
                      context: context,
                      builder: (_) => DimensionDialog(model: m),
                    ),
              child: const Text('Open'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('Open'));
    await tester.pumpAndSettle();
  }

  bool enabled(WidgetTester tester) =>
      tester
          .widget<FilledButton>(find.widgetWithText(FilledButton, 'Apply'))
          .onPressed !=
      null;

  testWidgets(
    'Dimension preview uses mm style defaults, explicit Apply and invalidates on changes',
    (tester) async {
      final m = DimensionModel();
      await mount(tester, m, size: const Size(820, 520));
      expect(enabled(tester), false);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(m.payload!['annotation']['text_height'], closeTo(4.572, 1e-9));
      expect(m.payload!['annotation']['offset'], 12.7);
      expect(enabled(tester), true);
      expect(m.calls.where((c) => c == 'design.dimension_apply'), isEmpty);
      await tester.ensureVisible(
        find.byKey(const ValueKey('dimension-offset')),
      );
      await tester.enterText(
        find.byKey(const ValueKey('dimension-offset')),
        '20',
      );
      await tester.pumpAndSettle();
      expect(enabled(tester), false);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(m.payload!['annotation']['offset'], 20);
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();
      expect(m.payload, {'token': 'dimension-token'});
      expect(m.selection, {'unchanged'});
      expect(find.byType(DimensionDialog), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets(
    'Revision changes invalidate review; cancelling leaves the document alone',
    (tester) async {
      final m = DimensionModel();
      await mount(tester, m);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      m.state['revision'] = 2;
      m.notifyListeners();
      await tester.pumpAndSettle();
      expect(enabled(tester), false);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(m.calls.where((c) => c == 'design.dimension_apply'), isEmpty);
    },
  );

  testWidgets(
    'Dimension manager requires delete confirmation and protects readonly jobs',
    (tester) async {
      final m = DimensionModel();
      m.state['dimensions'] = [
        rendered({
          'kind': 'horizontal',
          'layer': 'DIMENSIONS',
          'text_height': 4.572,
        }),
      ];
      await mount(tester, m, manager: true);
      await tester.tap(find.byTooltip('Delete dimension'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(m.calls, isEmpty);
      m.state['readOnly'] = true;
      m.notifyListeners();
      await tester.pumpAndSettle();
      expect(
        tester
            .widget<IconButton>(
              find.byWidgetPredicate(
                (w) => w is IconButton && w.tooltip == 'Delete dimension',
              ),
            )
            .onPressed,
        isNull,
      );
      expect(
        tester
            .widget<IconButton>(
              find.byWidgetPredicate(
                (w) => w is IconButton && w.tooltip == 'Edit dimension',
              ),
            )
            .onPressed,
        isNull,
      );
      m.state['readOnly'] = false;
      m.notifyListeners();
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Delete dimension'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Delete'));
      await tester.pumpAndSettle();
      expect(m.payload, {'id': 'dimension-test'});
      expect(find.textContaining('No dimensions yet'), findsOneWidget);
    },
  );

  testWidgets(
    'Canvas two-point dimension workflow uses backend feature picks without geometry creation',
    (tester) async {
      final m = DimensionModel();
      final view = DesignViewport()
        ..scale = 10
        ..translation = const Offset(100, 300)
        ..setTool('dimension');
      addTearDown(m.dispose);
      addTearDown(view.dispose);
      List<Json>? picked;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: DesignCanvas(
              model: m,
              view: view,
              layer: '0',
              dark: false,
              onDimension: (refs) async {
                picked = refs;
              },
            ),
          ),
        ),
      );
      await tester.tapAt(const Offset(110, 200));
      await tester.pumpAndSettle();
      expect(view.dimensionStart, isNotNull);
      expect(picked, isNull);
      await tester.tapAt(const Offset(160, 200));
      await tester.pumpAndSettle();
      expect(picked!.map((r) => r['fallback']['x']).toList(), [1.0, 6.0]);
      expect(m.calls, ['design.dimension_pick', 'design.dimension_pick']);
      expect(view.tool, 'select');
      expect(m.selection, {'unchanged'});
    },
  );
}
