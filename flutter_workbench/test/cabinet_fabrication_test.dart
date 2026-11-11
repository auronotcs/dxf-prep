import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/cabinet_authoring_dialog.dart';
import 'package:workbench_prototype/design/cabinet_fabrication_dialog.dart';
import 'pan_cabinet_authoring_test.dart' show AuthorModel;

class FabricationModel extends AuthorModel {
  FabricationModel() {
    state['cabinetFabricationInspection'] = true;
  }
  final Json fabrication = jsonDecode(
    File('test/fixtures/cabinet_fabrication.json').readAsStringSync(),
  );
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    if (command.startsWith('design.cabinet_fabrication_')) {
      requestedCommand = command;
      requested = jsonDecode(jsonEncode(params));
      return {
        'ok': true,
        'review': command.endsWith('_begin')
            ? fabrication['context']
            : (params['settings']['keepout_ids'] as List).contains('opening')
            ? fabrication['passed']
            : fabrication['blocked'],
      };
    }
    return super.request(command, params);
  }
}

void main() {
  setUpAll(() async {
    await (FontLoader('MaterialIcons')..addFont(
          File(
            '../.flutter-sdk/bin/cache/artifacts/material_fonts/MaterialIcons-Regular.otf',
          ).readAsBytes().then(ByteData.sublistView),
        ))
        .load();
    for (final name in ['Roboto', 'Segoe UI']) {
      await (FontLoader(name)..addFont(
            File(
              'C:/Windows/Fonts/segoeui.ttf',
            ).readAsBytes().then(ByteData.sublistView),
          ))
          .load();
    }
  });
  Future<void> mount(WidgetTester t, Widget w) async {
    t.view.physicalSize = const Size(800, 600);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('fab-image'),
        child: MaterialApp(home: Scaffold(body: w)),
      ),
    );
    await t.pumpAndSettle();
  }

  Future<void> click(WidgetTester t, String label) async {
    final f = find.text(label);
    await t.ensureVisible(f);
    await t.pumpAndSettle();
    await t.tap(f);
    await t.pumpAndSettle();
  }

  Finder input(String label) => find.byWidgetPredicate(
    (w) => w is TextField && w.decoration?.labelText == label,
  );
  Future<void> capture(WidgetTester t) async {
    final boundary = t.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('fab-image')),
    );
    await t.runAsync(() async {
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      File(
        'build/ui-review/cabinet-fabrication-compact.png',
      ).writeAsBytesSync(bytes!.buffer.asUint8List());
      image.dispose();
    });
  }

  testWidgets(
    'Physical inspection inputs, fresh acknowledgment, source choices and no export',
    (t) async {
      final m = FabricationModel();
      addTearDown(m.dispose);
      final before = jsonEncode(m.state);
      await mount(t, CabinetFabricationDialog(model: m, token: 'preview'));
      await click(t, 'Choose the actual top');
      await click(t, 'top · 0 (rectangle)');
      await click(t, 'Choose a loaded flat end mill');
      await click(t, 'T1 · Slot cutter · Ø 0.125 in');
      await click(t, 'opening · 0');
      await click(
        t,
        'I reviewed the actual top, registration and skin envelope',
      );
      await click(t, 'I reviewed total fit play and extended slot ends');
      await click(t, 'Check & Preview');
      expect(m.requested?['settings']['top_id'], 'top');
      expect(m.requested?['settings']['tool_station'], 1);
      expect(m.requested?['settings']['keepout_ids'], ['opening']);
      expect(m.requested?['settings']['registration_reviewed'], true);
      expect(m.requested?['settings']['fit_reviewed'], true);
      expect(
        find.text(
          'Geometry checks passed for these inputs. Machining-job creation is not enabled here.',
        ),
        findsOneWidget,
      );
      expect(m.calls, isEmpty);
      expect(jsonEncode(m.state), before);
      final f = input('Extra edge clearance');
      await t.ensureVisible(f);
      await t.enterText(f, '.2');
      await t.pumpAndSettle();
      expect(find.byKey(const ValueKey('fabrication-preview')), findsNothing);
      await click(t, 'Check & Preview');
      expect(m.requested?['settings']['edge_clearance'], .2);
      expect(m.requested?['settings']['registration_reviewed'], false);
      expect(m.requested?['settings']['fit_reviewed'], false);
      expect(find.text('Apply'), findsNothing);
      expect(find.text('Export'), findsNothing);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Locate finding uses private canvas and compact inspector stays usable',
    (t) async {
      final m = FabricationModel();
      addTearDown(m.dispose);
      final before = jsonEncode(m.state);
      await mount(t, CabinetFabricationDialog(model: m, token: 'preview'));
      await click(t, 'Check & Preview');
      await click(t, 'Locate');
      final painter =
          t
                  .widget<CustomPaint>(
                    find.byKey(const ValueKey('fabrication-preview')),
                  )
                  .painter
              as FabricationPainter;
      expect(painter.issue?['code'], 'unassigned');
      expect(painter.issue?['point'], {'x': 30.0, 'y': 8.0});
      expect(find.byKey(const ValueKey('located-finding')), findsOneWidget);
      await t.ensureVisible(find.byKey(const ValueKey('fabrication-preview')));
      await t.pumpAndSettle();
      await capture(t);
      await click(t, 'Tabbed backs');
      expect(
        (t
                    .widget<CustomPaint>(
                      find.byKey(const ValueKey('fabrication-preview')),
                    )
                    .painter
                as FabricationPainter)
            .issue,
        isNull,
      );
      await click(t, 'Fit preview');
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Invalid numbers and external changes block fresh inspection', (
    t,
  ) async {
    final m = FabricationModel();
    addTearDown(m.dispose);
    await mount(t, CabinetFabricationDialog(model: m, token: 'preview'));
    final f = input('Cabinet-top thickness');
    await t.ensureVisible(f);
    await t.enterText(f, 'NaN');
    m.requestedCommand = null;
    await click(t, 'Check & Preview');
    expect(m.requestedCommand, isNull);
    expect(
      find.text('Bad state: Enter finite physical dimensions in in.'),
      findsOneWidget,
    );
    m.externalEdit();
    await t.pumpAndSettle();
    expect(
      find.text(
        'Document changed. Close this inspection and preview Cabinet Tabs again.',
      ),
      findsOneWidget,
    );
    expect(
      t
          .widget<FilledButton>(
            find.widgetWithText(FilledButton, 'Check & Preview'),
          )
          .onPressed,
      isNull,
    );
  });
  testWidgets(
    'Inspector opens from the current Cabinet draft and returns without Apply',
    (t) async {
      final m = FabricationModel();
      addTearDown(m.dispose);
      await mount(t, CabinetAuthoringDialog(model: m));
      expect(
        t
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Inspect fabrication...'),
            )
            .onPressed,
        isNull,
      );
      await click(t, 'Add selected backs and holes');
      await click(t, 'Add tab pair');
      await click(t, 'Preview cabinet review');
      await click(t, 'Inspect fabrication...');
      expect(find.byType(CabinetFabricationDialog), findsOneWidget);
      expect(m.requested?['token'], m.fixture['cabinet']['token']);
      await click(t, 'Back to tab layout');
      expect(find.byType(CabinetAuthoringDialog), findsOneWidget);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );
}
