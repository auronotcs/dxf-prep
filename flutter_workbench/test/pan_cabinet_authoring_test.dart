import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/pan_authoring_dialog.dart';
import 'package:workbench_prototype/design/cabinet_authoring_dialog.dart';
import 'package:workbench_prototype/design/footer.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'manufacturing_test.dart' show ManufacturingModel;

class AuthorModel extends ManufacturingModel {
  final Json fixture = jsonDecode(
    File('test/fixtures/pan_cabinet_authoring.json').readAsStringSync(),
  );
  String? requestedCommand;
  Json? requested;
  AuthorModel({bool saved = false}) {
    state['panAuthoring'] = true;
    state['cabinetAuthoring'] = true;
    if (saved) {
      final p = fixture['plan'] as Json;
      fixture['context']['saved'] = <String, dynamic>{p['plan_id']: p};
    }
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    requestedCommand = command;
    requested = jsonDecode(jsonEncode(params));
    if (command == 'design.pan_preview') {
      return {'ok': true, 'preview': fixture['pan']};
    }
    if (command == 'design.cabinet_begin') {
      return {'ok': true, 'context': fixture['context']};
    }
    if (command == 'design.cabinet_preview') {
      return {'ok': true, 'preview': fixture['cabinet']};
    }
    if (command == 'design.cabinet_reregister') {
      return {'ok': true, 'plan': params['plan']};
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
  Future<void> mount(WidgetTester t, Widget w, {bool compact = false}) async {
    t.view.physicalSize = compact
        ? const Size(800, 600)
        : const Size(1250, 1000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('author-image'),
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
  Future<void> enter(WidgetTester t, String label, String value) async {
    final f = input(label);
    await t.ensureVisible(f);
    await t.enterText(f, value);
    await t.pumpAndSettle();
  }

  Future<void> capture(WidgetTester t, String name) async {
    final boundary = t.renderObject<RenderRepaintBoundary>(
      find.byKey(const ValueKey('author-image')),
    );
    await t.runAsync(() async {
      final image = await boundary.toImage();
      final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
      Directory('build/ui-review').createSync(recursive: true);
      File(
        'build/ui-review/$name.png',
      ).writeAsBytesSync(bytes!.buffer.asUint8List());
      image.dispose();
    });
  }

  testWidgets(
    'Pan physical options invalidate preview and reviewed Apply is explicit',
    (t) async {
      final m = AuthorModel();
      addTearDown(m.dispose);
      await mount(t, PanAuthoringDialog(model: m), compact: true);
      await click(t, 'Include engravable bend marks');
      await enter(t, 'Bend-mark offset', '-0.03');
      await click(t, 'Holes: left');
      await click(t, 'Preview pan');
      expect(m.requested?['mark_offset'], -.03);
      expect(m.requested?['holes']['left'], false);
      expect(m.calls, isEmpty);
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply pan'),
            )
            .onPressed,
        isNull,
      );
      await t.ensureVisible(
        find.text(
          'I reviewed the blank, holes, guides and uncalibrated bend settings.',
        ),
      );
      await t.pumpAndSettle();
      await capture(t, 'pan-authoring-compact');
      await enter(t, 'Material thickness', '.08');
      expect(
        find.text(
          'I reviewed the blank, holes, guides and uncalibrated bend settings.',
        ),
        findsNothing,
      );
      await click(t, 'Preview pan');
      await click(
        t,
        'I reviewed the blank, holes, guides and uncalibrated bend settings.',
      );
      await click(t, 'Apply pan');
      expect(m.calls.last, 'design.pan_apply');
      expect(m.applied?['reviewed'], true);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets('Pan units, invalid numbers and external revisions', (t) async {
    final m = AuthorModel();
    m.state['units'] = 'mm';
    addTearDown(m.dispose);
    await mount(t, PanAuthoringDialog(model: m));
    await click(t, 'Preview pan');
    expect(
      (m.requested?['settings']['thickness'] as num),
      closeTo(.063 * 25.4, 1e-9),
    );
    await enter(t, 'K-factor (0–0.5)', 'NaN');
    m.requestedCommand = null;
    await click(t, 'Preview pan');
    expect(m.requestedCommand, isNull);
    m.externalEdit();
    await t.pumpAndSettle();
    expect(find.text('Drawing changed. Reopen DR Pans.'), findsOneWidget);
    expect(
      t
          .widget<OutlinedButton>(
            find.widgetWithText(OutlinedButton, 'Preview pan'),
          )
          .onPressed,
      isNull,
    );
  });
  testWidgets(
    'Existing marks mode uses signed offsets without creating a new face',
    (t) async {
      final m = AuthorModel();
      addTearDown(m.dispose);
      await mount(t, PanAuthoringDialog(model: m));
      await click(t, 'Create pans from selected faces');
      await click(t, 'Create / update marks on selected pans');
      await enter(t, 'Bend-mark offset', '0.04');
      await click(t, 'Preview pan');
      expect(m.requested?['mode'], 'marks');
      expect(m.requested?['marks_enabled'], true);
      expect(m.requested?['mark_offset'], .04);
      expect(input('Material thickness'), findsNothing);
      await click(t, 'Cancel');
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Cabinet authoring sources numeric datum stable pairs and reviewed apply',
    (t) async {
      final m = AuthorModel();
      addTearDown(m.dispose);
      await mount(t, CabinetAuthoringDialog(model: m), compact: true);
      await click(t, 'Add selected backs and holes');
      await enter(t, 'Local U datum', '1');
      await click(t, 'Add tab pair');
      await enter(t, 'Tab center U', '4');
      await enter(t, 'Plane angle', '30');
      await enter(t, 'Total width clearance', '.02');
      await click(t, 'Preview cabinet review');
      final plan = m.requested?['plan'] as Json;
      final pair = plan['tabs'][0]['pair_id'];
      expect(plan['planes'][0]['local_u_origin'], 1);
      expect(plan['planes'][0]['angle_degrees'], 30);
      expect(plan['width_clearance_total'], .02);
      expect(m.calls, isEmpty);
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply cabinet review'),
            )
            .onPressed,
        isNull,
      );
      await t.ensureVisible(find.byType(CheckboxListTile));
      await t.pumpAndSettle();
      await capture(t, 'cabinet-authoring-compact');
      await enter(t, 'Tab width', '2.5');
      await click(t, 'Preview cabinet review');
      expect(m.requested?['plan']['tabs'][0]['pair_id'], pair);
      await click(
        t,
        'I reviewed every source, datum, plane and inward direction. Save references only.',
      );
      await click(t, 'Apply cabinet review');
      expect(m.calls.last, 'design.cabinet_apply');
      expect(m.applied?['reviewed'], true);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Cabinet saved plans explicit reregister and draft-only removal',
    (t) async {
      final m = AuthorModel(saved: true);
      addTearDown(m.dispose);
      await mount(t, CabinetAuthoringDialog(model: m));
      await click(t, 'New plan');
      await click(t, m.fixture['plan']['plan_id']);
      await click(t, 'Re-register current sources');
      expect(find.text('Accept current source geometry?'), findsOneWidget);
      await click(t, 'Accept sources for review');
      expect(m.requestedCommand, 'design.cabinet_reregister');
      expect(m.requested?['confirmed'], true);
      expect(m.calls, isEmpty);
      await click(t, 'Remove pair');
      expect(input('Tab width'), findsNothing);
      await click(t, 'Add tab pair');
      await click(t, 'Remove back from draft');
      expect(input('Local U datum'), findsNothing);
      await click(t, 'Cancel');
      expect(m.calls, isEmpty);
    },
  );
  testWidgets(
    'Cabinet stale revisions prevent apply and review history remains enabled',
    (t) async {
      final m = AuthorModel();
      addTearDown(m.dispose);
      await mount(t, CabinetAuthoringDialog(model: m));
      await click(t, 'Add selected backs and holes');
      await click(t, 'Add tab pair');
      await click(t, 'Preview cabinet review');
      m.externalEdit();
      await t.pumpAndSettle();
      expect(
        find.text('Document changed. Reopen Cabinet Tabs authoring.'),
        findsOneWidget,
      );
      expect(
        t
            .widget<FilledButton>(
              find.widgetWithText(FilledButton, 'Apply cabinet review'),
            )
            .onPressed,
        isNull,
      );
      m.state['readOnly'] = true;
      m.state['manufacturing']['editable'] = false;
      m.state['canUndo'] = true;
      m.state['history'] = {
        'index': 1,
        'entries': ['New', 'Cabinet review'],
      };
      int undo = 0;
      final view = DesignViewport();
      addTearDown(view.dispose);
      await mount(
        t,
        DesignHistoryBar(
          model: m,
          view: view,
          color: Colors.white,
          undo: () => undo++,
          redo: () {},
        ),
      );
      await t.tap(find.byTooltip('Undo'));
      expect(undo, 1);
      expect(t.takeException(), isNull);
    },
  );
  testWidgets(
    'Cabinet workspace menu, save and undo remain usable while geometry is protected',
    (t) async {
      final m = AuthorModel();
      addTearDown(m.dispose);
      m.state['readOnly'] = true;
      m.state['manufacturing']['editable'] = false;
      m.state['savedPath'] = 'isolated-test.camjob.json';
      m.state['canUndo'] = true;
      t.view.physicalSize = const Size(1300, 900);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      await t.tap(find.byType(DesignCanvas));
      await t.pumpAndSettle();
      await t.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
      await t.sendKeyEvent(LogicalKeyboardKey.keyS);
      await t.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
      await t.pumpAndSettle();
      expect(m.calls, contains('project.save'));
      await t.sendKeyDownEvent(LogicalKeyboardKey.controlLeft);
      await t.sendKeyEvent(LogicalKeyboardKey.keyZ);
      await t.sendKeyUpEvent(LogicalKeyboardKey.controlLeft);
      await t.pumpAndSettle();
      expect(m.calls, contains('history.undo'));
      await t.tap(find.text('Macros').first);
      await t.pumpAndSettle();
      await click(t, 'Cabinet Tabs authoring');
      expect(find.byType(CabinetAuthoringDialog), findsOneWidget);
      await click(t, 'Cancel');
      expect(t.takeException(), isNull);
    },
  );
}
