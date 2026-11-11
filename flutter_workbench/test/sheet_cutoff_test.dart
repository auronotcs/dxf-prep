import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:workbench_prototype/design/cutoff_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'manufacturing_test.dart' show ManufacturingModel;

class CutoffModel extends ManufacturingModel {
  Json? options;
  int previews = 0;
  CutoffModel({String units = 'in'}) {
    state['units'] = units;
    state['sheetCutoffs'] = true;
    state['activePlate'] = 2;
    state['plates'] = <Json>[
      for (var i = 1; i <= 3; i++)
        {
          'number': i,
          'name': 'Sheet $i',
          'x': (i - 1) * 20.0,
          'y': 0.0,
          'width': 20.0,
          'height': 10.0,
        },
    ];
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command == 'design.cutoff_preview') {
      options = params;
      previews++;
      final ps = plates.where(
        (p) =>
            params['scope'] == 'all' || p['number'] == params['plate_number'],
      );
      return {
        'ok': true,
        'preview': {
          'token': 'review-$previews',
          'units': units,
          'affected_operations': [
            {'id': 'op', 'name': 'Existing cutoff'},
          ],
          'rows': [
            for (final p in ps)
              {
                'plate': p,
                'action': p['number'] == 3 ? 'Skipped' : 'Add',
                'message': p['number'] == 3
                    ? 'No applicable geometry was found on this plate.'
                    : 'Y 8.5 $units',
                'line': p['number'] == 3
                    ? null
                    : {
                        'id': 'cut-${p['number']}',
                        'kind': 'line',
                        'layer': 'Cutoff',
                        'start': {'x': p['x'], 'y': 8.5},
                        'end': {'x': number(p['x']) + 20, 'y': 8.5},
                      },
                'replaced': 0,
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
  Future<CutoffModel> mount(
    WidgetTester t, {
    String units = 'in',
    bool compact = false,
  }) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1100, 950);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    final m = CutoffModel(units: units);
    addTearDown(m.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('cutoff-image'),
        child: MaterialApp(
          home: Builder(
            builder: (c) => Scaffold(
              body: TextButton(
                onPressed: () => showDialog<void>(
                  context: c,
                  builder: (_) => SheetCutoffDialog(model: m),
                ),
                child: const Text('Open'),
              ),
            ),
          ),
        ),
      ),
    );
    await t.tap(find.text('Open'));
    await t.pumpAndSettle();
    return m;
  }

  Future<void> choose(WidgetTester t, String current, String next) async {
    await t.tap(find.text(current).last);
    await t.pumpAndSettle();
    await t.tap(find.text(next).last);
    await t.pumpAndSettle();
  }

  FilledButton applyButton(WidgetTester t) => t.widget<FilledButton>(
    find.widgetWithText(FilledButton, 'Apply cutoff lines'),
  );
  testWidgets(
    'Cutoff drawing units, all-sheet preview, settings invalidation and reviewed Apply',
    (t) async {
      final m = await mount(t, units: 'mm');
      final before = jsonEncode(m.state);
      expect(
        t.widget<TextField>(find.byType(TextField)).controller!.text,
        '12.7',
      );
      expect(applyButton(t).onPressed, isNull);
      await t.tap(find.text('Preview placement'));
      await t.pumpAndSettle();
      expect(m.options!['plate_number'], 2);
      expect(m.options!['clearance'], 12.7);
      expect(jsonEncode(m.state), before);
      await choose(t, 'Vertical · right of parts', 'Horizontal · above parts');
      expect(applyButton(t).onPressed, isNull);
      await choose(t, 'Selected sheet', 'All sheets');
      await t.tap(find.text('Preview placement'));
      await t.pumpAndSettle();
      expect(m.options!['scope'], 'all');
      expect(m.options!['orientation'], 'horizontal');
      expect(find.text('Sheet 3 · Skipped'), findsOneWidget);
      expect(find.byKey(const ValueKey('cutoff-preview')), findsOneWidget);
      await t.tap(find.text('Apply cutoff lines'));
      await t.pumpAndSettle();
      expect(m.calls, ['design.cutoff_apply']);
      expect(m.applied, {'token': 'review-2'});
      expect(find.byType(SheetCutoffDialog), findsNothing);
    },
  );
  testWidgets(
    'Invalid clearance and changed documents cannot apply a prior preview',
    (t) async {
      final m = await mount(t);
      await t.enterText(find.byType(TextField), '-1');
      await t.tap(find.text('Preview placement'));
      await t.pumpAndSettle();
      expect(find.textContaining('finite, nonnegative'), findsOneWidget);
      expect(m.previews, 0);
      await t.enterText(find.byType(TextField), '0');
      await t.tap(find.text('Preview placement'));
      await t.pumpAndSettle();
      expect(m.options!['clearance'], 0);
      m.state['revision'] = (m.state['revision'] as int) + 1;
      m.notifyListeners();
      await t.pumpAndSettle();
      expect(applyButton(t).onPressed, isNull);
      expect(find.textContaining('Drawing changed'), findsOneWidget);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Cancel preserves document state and compact preview has no overflow',
    (t) async {
      final m = await mount(t, compact: true);
      final before = jsonEncode(m.state);
      await t.tap(find.text('Preview placement'));
      await t.pumpAndSettle();
      await t.runAsync(() async {
        final b = t.renderObject<RenderRepaintBoundary>(
          find.byKey(const ValueKey('cutoff-image')),
        );
        final image = await b.toImage();
        final data = await image.toByteData(format: ui.ImageByteFormat.png);
        Directory('build/ui-review').createSync(recursive: true);
        File(
          'build/ui-review/sheet-cutoff-compact.png',
        ).writeAsBytesSync(data!.buffer.asUint8List());
        image.dispose();
      });
      expect(t.takeException(), isNull);
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Backend apply refusal clears the token and keeps the dialog open',
    (t) async {
      final m = await mount(t);
      await t.tap(find.text('Preview placement'));
      await t.pumpAndSettle();
      m.reject = true;
      await t.tap(find.text('Apply cutoff lines'));
      await t.pumpAndSettle();
      expect(find.byType(SheetCutoffDialog), findsOneWidget);
      expect(applyButton(t).onPressed, isNull);
      expect(find.text(m.message), findsOneWidget);
    },
  );
  testWidgets(
    'Shared Design and Manufacturing cutoff command preserves zoom position selection and grid',
    (t) async {
      final m = CutoffModel();
      addTearDown(m.dispose);
      m.state['readOnly'] = true;
      t.view.physicalSize = const Size(1500, 1000);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      final view = workspace.view as DesignViewport;
      view.scale = 25;
      view.translation = const Offset(120, 180);
      view.grid = false;
      view.snap = true;
      final selected = Set.of(m.selection);
      final command = designGroups.values
          .expand((g) => g)
          .firstWhere((c) => c.id == 'cutoff');
      expect(workspace.enabled(command), true);
      expect(
        manufacturingGroups.values
            .expand((g) => g)
            .any((c) => c.id == 'cutoff'),
        true,
      );
      workspace.performAction('cutoff');
      await t.pumpAndSettle();
      await t.tap(find.text('Preview placement'));
      await t.pumpAndSettle();
      await t.tap(find.text('Cancel'));
      await t.pumpAndSettle();
      expect(view.scale, 25);
      expect(view.translation, const Offset(120, 180));
      expect(view.grid, false);
      expect(view.snap, true);
      expect(m.selection, selected);
      expect(t.takeException(), isNull);
    },
  );
}
