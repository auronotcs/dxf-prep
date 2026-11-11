import 'dart:convert';
import 'dart:io';
import 'dart:ui' as ui;
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/cabinet_fabrication_dialog.dart';
import 'package:workbench_prototype/design/cabinet_cutter_dialog.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'cabinet_fabrication_test.dart' show FabricationModel;
import 'document_tabs_test.dart' show TabModel;

class CabinetJobsModel extends FabricationModel {
  final Json jobs = jsonDecode(
    File('test/fixtures/cabinet_fabrication_jobs.json').readAsStringSync(),
  );
  final requests = <Json>[];
  bool failExport = false, failResolve = false;
  CabinetJobsModel() {
    state['cabinetFabricationJobs'] = true;
    jobs['context']['tools'] = <Json>[];
    jobs['context']['settings']['tool_station'] = -1;
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    requests.add({
      'command': command,
      'params': jsonDecode(jsonEncode(params)),
    });
    if (command == 'manufacturing.catalog_list') {
      return {'ok': true, ...jobs['catalog'] as Json};
    }
    if (command == 'manufacturing.catalog_resolve') {
      if (failResolve) {
        return {'ok': false, 'error': 'Library changed; reopen it.'};
      }
      return {
        'ok': true,
        'tool': {...jobs['tool'] as Json, 'station': params['station']},
      };
    }
    if (command == 'design.cabinet_fabrication_begin') {
      return {'ok': true, 'review': jobs['context']};
    }
    if (command == 'design.cabinet_fabrication_jobs_preview') {
      final r = jsonDecode(jsonEncode(jobs['preview'])) as Json;
      if (params['settings']['registration_reviewed'] != true ||
          params['settings']['fit_reviewed'] != true) {
        r['can_export'] = false;
      }
      return {'ok': true, 'review': r};
    }
    if (command == 'design.cabinet_fabrication_export') {
      if (failExport) {
        return {
          'ok': false,
          'error': 'Fabrication preview expired. Check and preview again.',
        };
      }
      return {
        'ok': true,
        'review': {
          'paths': [
            'C:/evaluation/Tabbed backs.camjob.json',
            'C:/evaluation/Cabinet top and slots.camjob.json',
          ],
          'assembly_path': 'C:/evaluation/Assembly review.camjob.json',
        },
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
    await (FontLoader('Roboto')..addFont(
          File(
            'C:/Windows/Fonts/segoeui.ttf',
          ).readAsBytes().then(ByteData.sublistView),
        ))
        .load();
  });
  Future<void> size(WidgetTester t, [Size value = const Size(800, 600)]) async {
    t.view.physicalSize = value;
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.resetPhysicalSize);
    addTearDown(t.view.resetDevicePixelRatio);
  }

  Future<void> mount(WidgetTester t, CabinetJobsModel m) async {
    await size(t);
    addTearDown(m.dispose);
    await t.pumpWidget(
      RepaintBoundary(
        key: const ValueKey('jobs-image'),
        child: MaterialApp(
          home: Scaffold(
            body: CabinetFabricationDialog(model: m, token: 'preview'),
          ),
        ),
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

  Future<void> cutter(WidgetTester t) async {
    await click(t, 'Choose private library cutter...');
    final menu = find.descendant(
      of: find.byType(CabinetCutterDialog),
      matching: find.byWidgetPredicate(
        (w) => w is DropdownButtonFormField<String>,
      ),
    );
    await t.tap(menu);
    await t.pumpAndSettle();
    await t.tap(find.textContaining('Vortex').last);
    await t.pumpAndSettle();
    await click(t, 'Use private cutter');
  }

  Future<void> acknowledge(WidgetTester t) async {
    await click(t, 'I reviewed the actual top, registration and skin envelope');
    await click(t, 'I reviewed total fit play and extended slot ends');
  }

  testWidgets(
    'Private cutter, paired views, physical edits and export failure stay private',
    (t) async {
      final m = CabinetJobsModel();
      final before = jsonEncode(m.state);
      await mount(t, m);
      expect(
        t
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Save machining pair'),
            )
            .onPressed,
        isNull,
      );
      await cutter(t);
      expect(find.byType(CabinetCutterDialog), findsNothing);
      expect(
        find.text(
          'Private cutter T1 replaces that station only in exported copies.',
        ),
        findsOneWidget,
      );
      await acknowledge(t);
      await click(t, 'Check & Preview');
      final p = m.requests.last['params'] as Json;
      expect(p['private_tool']['id'], 'vortex-5609a');
      expect(p['private_tool']['station'], 1);
      expect(p['settings']['tool_station'], 1);
      await click(t, 'Top job placement');
      final painter =
          t
                  .widget<CustomPaint>(
                    find.byKey(const ValueKey('fabrication-preview')),
                  )
                  .painter
              as FabricationPainter;
      expect(painter.entities, m.jobs['preview']['views']['job1']);
      await t.ensureVisible(find.byKey(const ValueKey('fabrication-preview')));
      await t.pumpAndSettle();
      final boundary = t.renderObject<RenderRepaintBoundary>(
        find.byKey(const ValueKey('jobs-image')),
      );
      await t.runAsync(() async {
        final image = await boundary.toImage();
        final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
        File(
          'build/ui-review/cabinet-jobs-compact.png',
        ).writeAsBytesSync(bytes!.buffer.asUint8List());
        image.dispose();
      });
      m.failExport = true;
      await click(t, 'Save machining pair');
      expect(
        find.textContaining('Fabrication preview expired'),
        findsOneWidget,
      );
      expect(find.text('Open machining jobs'), findsNothing);
      expect(
        t
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Save machining pair'),
            )
            .onPressed,
        isNull,
      );
      final field = find.byWidgetPredicate(
        (w) =>
            w is TextField && w.decoration?.labelText == 'Extra edge clearance',
      );
      await t.ensureVisible(field);
      await t.enterText(field, '.2');
      await t.pumpAndSettle();
      await click(t, 'Check & Preview');
      expect(
        m.requests.last['params']['settings']['registration_reviewed'],
        false,
      );
      expect(m.requests.last['params']['settings']['fit_reviewed'], false);
      expect(
        t
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Save machining pair'),
            )
            .onPressed,
        isNull,
      );
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Successful export shows three paths; new inspection clears opening offer',
    (t) async {
      final m = CabinetJobsModel();
      await mount(t, m);
      await cutter(t);
      await acknowledge(t);
      await click(t, 'Check & Preview');
      await click(t, 'Save machining pair');
      expect(find.text('Open machining jobs'), findsOneWidget);
      expect(
        find.textContaining('Assembly review.camjob.json'),
        findsOneWidget,
      );
      final exported = m.requests.last;
      expect(exported['params'].keys.toSet(), {'token', 'review_token'});
      expect(
        exported['params']['review_token'],
        m.jobs['preview']['review_token'],
      );
      await click(t, 'Check & Preview');
      expect(find.text('Open machining jobs'), findsNothing);
      m.externalEdit();
      await t.pumpAndSettle();
      expect(
        t
            .widget<OutlinedButton>(
              find.widgetWithText(OutlinedButton, 'Save machining pair'),
            )
            .onPressed,
        isNull,
      );
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Library version rejection keeps private picker open without changing assembly',
    (t) async {
      final m = CabinetJobsModel()..failResolve = true;
      await mount(t, m);
      await cutter(t);
      expect(find.byType(CabinetCutterDialog), findsOneWidget);
      expect(
        find.text('Bad state: Library changed; reopen it.'),
        findsOneWidget,
      );
      await click(t, 'Cancel');
      expect(m.calls, isEmpty);
      expect(t.takeException(), isNull);
    },
  );

  testWidgets(
    'Cabinet export opens both tabs and retains dirty assembly camera selection history and grid',
    (t) async {
      await size(t, const Size(1400, 900));
      final m = CabinetJobsModel();
      addTearDown(m.dispose);
      m.state['dirty'] = true;
      m.state['canUndo'] = true;
      final first = TabModel(), second = TabModel();
      var opened = 0;
      await t.pumpWidget(
        DesignApp(
          controller: m,
          startBackend: false,
          controllerFactory: () => opened++ == 0 ? first : second,
        ),
      );
      await t.pumpAndSettle();
      final dynamic host = t.state(find.byType(DocumentTabs));
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      workspace.view.scale = 23.0;
      workspace.view.translation = const Offset(111, 333);
      workspace.view.grid = true;
      workspace.view.snap = true;
      final before = jsonEncode(m.state);
      final action = workspace.action('cabinetAuthor') as Future<void>;
      await t.pumpAndSettle();
      await click(t, 'Add selected backs and holes');
      await click(t, 'Add tab pair');
      await click(t, 'Preview cabinet review');
      await click(t, 'Inspect fabrication...');
      await cutter(t);
      await acknowledge(t);
      await click(t, 'Check & Preview');
      await click(t, 'Save machining pair');
      await click(t, 'Open machining jobs');
      await t.pumpAndSettle();
      await action;
      expect(host.models.length, 3);
      expect(host.active, 2);
      expect(opened, 2);
      expect(first.commands, ['project.open']);
      expect(second.commands, ['project.open']);
      expect(jsonEncode(m.state), before);
      expect(m.calls, isEmpty);
      expect(m.dirty, true);
      host.setState(() => host.active = 0);
      await t.pumpAndSettle();
      expect(identical(t.state(find.byType(DesignWorkspace)), workspace), true);
      expect(workspace.view.scale, 23);
      expect(workspace.view.translation, const Offset(111, 333));
      expect(workspace.view.grid && workspace.view.snap, true);
      expect(t.takeException(), isNull);
    },
  );
}
