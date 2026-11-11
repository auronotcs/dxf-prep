import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:workbench_prototype/design/cleave_dialog.dart';
import 'package:workbench_prototype/design/controller.dart';
import 'package:workbench_prototype/design/workspace.dart';
import 'package:workbench_prototype/design/canvas.dart';
import 'package:flutter/services.dart';
import 'package:workbench_prototype/design/guide_actions.dart';
import 'design_workspace_test.dart' show TestDesignController;

class CleaveModel extends TestDesignController {
  Json? payload;
  Completer<void>? gate;
  bool rejectPreview = false;
  CleaveModel() {
    state['guideCleave'] = true;
    state['selected'] = ['source'];
    state['entities'] = <Json>[
      {
        'id': 'source',
        'kind': 'rectangle',
        'layer': '0',
        'metadata': <String, dynamic>{},
        'corner_a': {'x': 0, 'y': 0},
        'corner_b': {'x': 10, 'y': 8},
        'bounds': [0, 0, 10, 8],
      },
    ];
    state['guides'] = <Json>[
      {
        'id': 'v',
        'orientation': 'vertical',
        'position': 5,
        'visible': true,
        'locked': true,
        'name': 'Datum',
      },
      {
        'id': 'h',
        'orientation': 'horizontal',
        'position': 3,
        'visible': true,
        'locked': false,
        'name': 'Cross',
      },
      {
        'id': 'hidden',
        'orientation': 'horizontal',
        'position': 7,
        'visible': false,
        'locked': false,
        'name': 'Hidden',
      },
    ];
  }
  @override
  Future<Json> request(String command, [Json params = const {}]) async {
    commands.add(command);
    payload = params;
    if (gate != null) await gate!.future;
    if (rejectPreview) {
      return {
        'ok': false,
        'error': 'The guide does not cross the selected shapes.',
      };
    }
    return {
      'ok': true,
      'preview': {
        'token': 'cleave-token',
        'entities': <Json>[],
        'sources': entities,
        'sourceCount': 1,
        'leftCount': 1,
        'rightCount': 1,
        'holeCount': 0,
        'unaffectedCount': 1,
        'expandedCount': 1,
        'areaBefore': 80,
        'areaAfter': 80,
        'removedCount': 2,
        'reviewedLedPlanIds': ['led'],
        'circleCount': 0,
      },
    };
  }
}

void main() {
  Future<void> mount(
    WidgetTester tester,
    CleaveModel m, {
    bool menu = false,
    List<Json>? line,
    Size size = const Size(900, 750),
  }) async {
    tester.view.physicalSize = size;
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) => Scaffold(
            body: TextButton(
              onPressed: () => menu
                  ? showGuideMenu(
                      context,
                      m,
                      (m.state['guides'] as List)[0],
                      const Offset(80, 80),
                    )
                  : showDialog<void>(
                      context: context,
                      barrierDismissible: false,
                      builder: (_) => CleaveDialog(model: m, line: line),
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

  testWidgets('Finite line preview sends endpoints and needs no guide', (
    t,
  ) async {
    final m = CleaveModel();
    addTearDown(m.dispose);
    m.state['guides'] = <Json>[];
    final line = <Json>[
      {'x': 5, 'y': -1},
      {'x': 5, 'y': 9},
    ];
    await mount(t, m, line: line);
    expect(find.text('Cleave / Split along drawn line'), findsOneWidget);
    expect(find.byType(DropdownButtonFormField<String>), findsNothing);
    await t.tap(find.text('Preview'));
    await t.pumpAndSettle();
    expect(m.payload, {'kind': 'cleave', 'line': line});
    await t.tap(find.text('Cancel'));
    await t.pumpAndSettle();
    expect(m.commands, ['design.preview_shapes']);
  });

  testWidgets(
    'Two canvas clicks open finite-line review; Escape cancels incomplete line',
    (t) async {
      t.view.physicalSize = const Size(1400, 900);
      t.view.devicePixelRatio = 1;
      addTearDown(t.view.resetPhysicalSize);
      addTearDown(t.view.resetDevicePixelRatio);
      final m = CleaveModel();
      addTearDown(m.dispose);
      await t.pumpWidget(DesignApp(controller: m, startBackend: false));
      await t.pumpAndSettle();
      final dynamic workspace = t.state(find.byType(DesignWorkspace));
      workspace.view.scale = 10.0;
      workspace.view.translation = const Offset(100, 200);
      workspace.view.snap = false;
      await workspace.action('cleaveLine');
      await t.pumpAndSettle();
      final origin = t.getTopLeft(find.byType(DesignCanvas));
      await t.tapAt(origin + const Offset(150, 210));
      await t.pumpAndSettle();
      await t.sendKeyEvent(LogicalKeyboardKey.escape);
      await t.pumpAndSettle();
      expect(find.byType(CleaveDialog), findsNothing);
      expect(m.commands, ['selection.set']);
      m.commands.clear();
      await workspace.action('cleaveLine');
      await t.pumpAndSettle();
      await t.tapAt(origin + const Offset(150, 210));
      await t.pumpAndSettle();
      await t.tapAt(origin + const Offset(150, 110));
      await t.pumpAndSettle();
      expect(find.text('Cleave / Split along drawn line'), findsOneWidget);
      await t.tap(find.text('Preview'));
      await t.pumpAndSettle();
      expect(m.payload, {
        'kind': 'cleave',
        'line': [
          {'x': 5.0, 'y': -1.0},
          {'x': 5.0, 'y': 9.0},
        ],
      });
    },
  );

  bool applyEnabled(WidgetTester t) =>
      t
          .widget<FilledButton>(find.widgetWithText(FilledButton, 'Apply'))
          .onPressed !=
      null;
  testWidgets(
    'Guide preview is explicit, reviews effects and Apply is one transaction in compact layout',
    (tester) async {
      final m = CleaveModel();
      addTearDown(m.dispose);
      await mount(tester, m, size: const Size(760, 700));
      expect(applyEnabled(tester), false);
      expect(find.textContaining('Hidden'), findsNothing);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(m.payload, {'kind': 'cleave', 'guide_id': 'v'});
      expect(
        find.textContaining('2 generated dependent(s) removed'),
        findsOneWidget,
      );
      expect(
        find.textContaining('1 LED plan(s) need regeneration'),
        findsOneWidget,
      );
      expect(applyEnabled(tester), true);
      expect(m.commands, ['design.preview_shapes']);
      await tester.tap(find.text('Apply'));
      await tester.pumpAndSettle();
      expect(m.commands, ['design.preview_shapes', 'design.apply_shapes']);
      expect(find.byType(CleaveDialog), findsNothing);
      expect(tester.takeException(), isNull);
    },
  );
  testWidgets('Changing guide invalidates preview; cancel never applies', (
    tester,
  ) async {
    final m = CleaveModel();
    addTearDown(m.dispose);
    await mount(tester, m);
    await tester.tap(find.text('Preview'));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(DropdownButtonFormField<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('horizontal · 3').last);
    await tester.pumpAndSettle();
    expect(applyEnabled(tester), false);
    await tester.tap(find.text('Preview'));
    await tester.pumpAndSettle();
    expect(m.payload!['guide_id'], 'h');
    await tester.tap(find.text('Cancel'));
    await tester.pumpAndSettle();
    expect(m.commands, everyElement('design.preview_shapes'));
  });
  testWidgets(
    'Stale results, failed previews and readonly or unavailable backends cannot Apply',
    (tester) async {
      final m = CleaveModel();
      addTearDown(m.dispose);
      await mount(tester, m);
      m.rejectPreview = true;
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(find.textContaining('does not cross'), findsOneWidget);
      expect(applyEnabled(tester), false);
      m.rejectPreview = false;
      m.gate = Completer<void>();
      await tester.tap(find.text('Preview'));
      await tester.pump();
      m.state['revision'] = 1;
      m.notifyListeners();
      await tester.pump();
      m.gate!.complete();
      await tester.pumpAndSettle();
      expect(applyEnabled(tester), false);
      expect(
        find.textContaining('Drawing or selection changed'),
        findsOneWidget,
      );
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      for (final mode in [
        'readonly',
        'capability',
        'noGuides',
        'noSelection',
      ]) {
        final other = CleaveModel();
        addTearDown(other.dispose);
        if (mode == 'readonly') other.state['readOnly'] = true;
        if (mode == 'capability') other.state['guideCleave'] = false;
        if (mode == 'noGuides') other.state['guides'] = <Json>[];
        if (mode == 'noSelection') other.state['selected'] = <String>[];
        await mount(tester, other);
        expect(
          tester
              .widget<OutlinedButton>(
                find.widgetWithText(OutlinedButton, 'Preview'),
              )
              .onPressed,
          isNull,
          reason: mode,
        );
        expect(applyEnabled(tester), false);
        await tester.tap(find.text('Cancel'));
        await tester.pumpAndSettle();
      }
    },
  );
  testWidgets(
    'Right-click locked guide offers split without unlocking or changing the guide',
    (tester) async {
      final m = CleaveModel();
      addTearDown(m.dispose);
      await mount(tester, m, menu: true);
      await tester.tap(find.text('Split selection along guide…'));
      await tester.pumpAndSettle();
      expect(find.byType(CleaveDialog), findsOneWidget);
      await tester.tap(find.text('Preview'));
      await tester.pumpAndSettle();
      expect(m.payload!['guide_id'], 'v');
      expect(m.state['guides'][0]['locked'], true);
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(m.selection, {'source'});
    },
  );
  test(
    'Real Dart/Python cleave preserves area/guide, atomic Undo and save/reopen',
    () async {
      final m = DesignController(),
          tmp = Directory.systemTemp.createTempSync('cleave-integration-');
      try {
        await m.start();
        expect(m.ready, true, reason: m.message);
        expect(m.state['guideCleave'], true);
        await m.run('geometry.add_rectangle', {
          'x': 0,
          'y': 0,
          'width': 10,
          'height': 8,
        });
        await m.select({m.entities.first['id'] as String});
        await m.run('design.guide_add', {
          'orientation': 'vertical',
          'position': 3.25,
        });
        final before = jsonEncode(m.entities),
            guide = m.state['guides'][0],
            history = m.state['history']['index'];
        final r = await m.request('design.preview_shapes', {
          'kind': 'cleave',
          'guide_id': guide['id'],
        });
        expect(r['ok'], true, reason: '$r');
        expect(jsonEncode(m.entities), before);
        expect(r['preview']['areaBefore'], r['preview']['areaAfter']);
        expect(
          await m.run('design.apply_shapes', {'token': r['preview']['token']}),
          true,
        );
        expect(m.entities, hasLength(2));
        expect(m.state['history']['index'], history + 1);
        expect(m.state['guides'][0], guide);
        final split = jsonEncode(m.entities);
        await m.run('history.undo');
        expect(jsonEncode(m.entities), before);
        await m.run('history.redo');
        expect(jsonEncode(m.entities), split);
        final path = '${tmp.path}/cleave.camjob.json';
        await m.run('project.save', {'path': path});
        await m.run('project.open', {'path': path});
        expect(jsonEncode(m.entities), split);
        expect(m.state['guides'][0], guide);
      } finally {
        await m.shutdown();
        m.dispose();
        tmp.deleteSync(recursive: true);
      }
    },
  );
}
